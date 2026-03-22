"""
MarketAnalyst: Layer 3 main orchestration class.

Responsibilities:
  - Group Layer 2 candidates by event (event_grouper)
  - Enforce per-cycle analysis and search budgets (G7, G8)
  - Apply pre-LLM guardrails: G6 (price), G1 (no evidence), G4 (stale)
  - Call retrieval provider for evidence
  - Call LLM client for structured probability estimate
  - Apply post-LLM guardrails: G2 (confidence), G3 (edge), G5 (ambiguous)
  - Persist PredictionSignal, evidence, and costs
  - Return list[PredictionSignal] to Layer 2

Layer 3 is prediction only. It does NOT start/stop workers, allocate capital,
or modify TradingEngine behavior. The only output is PredictionSignal records.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from polymarket_platform.analyst.calibration import is_live_trading_unlocked
from polymarket_platform.analyst.cost_tracker import CostTracker
from polymarket_platform.analyst.event_grouper import EventGroup, group_by_event
from polymarket_platform.analyst.guardrails import (
    apply_ambiguity_cap,
    evidence_is_fresh,
    freshest_age_minutes,
    has_evidence,
    passes_confidence,
    passes_edge,
    passes_price_filter,
)
from polymarket_platform.analyst.llm import AnthropicAnalystClient, token_cost_usd
from polymarket_platform.analyst.prompts import (
    SYSTEM_PROMPT,
    build_event_group_prompt,
    build_single_market_prompt,
)
from polymarket_platform.analyst.retrieval import (
    AnthropicWebSearchProvider,
    MockRetrievalProvider,
    RetrievalProvider,
    SearchResult,
)
from polymarket_platform.analyst.signals import (
    AnalysisStatus,
    PredictionSignal,
    Side,
    compute_edge,
    make_invalid_signal,
    make_prediction_id,
    make_thesis_id,
)

if TYPE_CHECKING:
    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore
    from polymarket_platform.scanner.catalog import MarketRecord
    from polymarket_platform.scanner.clob_probe import ClobQuote

log = logging.getLogger(__name__)


class MarketAnalyst:
    """
    Layer 3 analyst. Stateless between cycles; state lives in DB.

    Usage:
        analyst = MarketAnalyst.from_settings(cfg, store)
        signals = await analyst.analyze_batch(catalog_top_n, clob_quotes)
    """

    def __init__(
        self,
        cfg: Settings,
        store: SqliteStore,
        retrieval: RetrievalProvider | None = None,
        llm_client: AnthropicAnalystClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._store = store
        self._cost = CostTracker(cfg, store)
        self._retrieval: RetrievalProvider = retrieval or self._build_retrieval()
        self._llm: AnthropicAnalystClient | None = llm_client or self._build_llm()

    @classmethod
    def from_settings(cls, cfg: Settings, store: SqliteStore) -> MarketAnalyst:
        return cls(cfg=cfg, store=store)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def analyze_batch(
        self,
        candidates: list[MarketRecord],
        quotes: dict[str, ClobQuote],
    ) -> list[PredictionSignal]:
        """
        Analyse up to analyst_max_analyses_per_cycle event groups.

        Returns all produced PredictionSignal objects (VALID and non-VALID).
        Only VALID signals with forwarded=True should influence allocation.
        """
        if not candidates:
            return []

        groups = group_by_event(candidates)

        # G8: cap at max_analyses_per_cycle (measured in event groups)
        groups = groups[: self._cfg.analyst_max_analyses_per_cycle]

        signals: list[PredictionSignal] = []
        searches_used = 0

        for group in groups:
            # G7: cost budget headroom
            if not self._cost.has_headroom():
                log.info("Analyst cost budget exhausted -- stopping batch early")
                break

            # G8: search budget
            if searches_used >= self._cfg.analyst_max_searches_per_cycle:
                log.debug("Analyst search budget reached for this cycle")
                break

            group_signals = await self._analyze_group(group, quotes)
            signals.extend(group_signals)
            searches_used += group.search_count

        return signals

    def is_live_trading_unlocked(self) -> bool:
        """Check all five unlock conditions for analyst-driven allocation."""
        return is_live_trading_unlocked(self._store, self._cfg)

    async def close(self) -> None:
        if self._llm is not None:
            await self._llm.close()
        if hasattr(self._retrieval, "close"):
            await self._retrieval.close()  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Group analysis
    # ------------------------------------------------------------------

    async def _analyze_group(
        self,
        group: EventGroup,
        quotes: dict[str, ClobQuote],
    ) -> list[PredictionSignal]:
        if not group.markets:
            return []

        # G6: filter markets in the group by price
        tradable = [
            m for m in group.markets
            if passes_price_filter(
                _midpoint(quotes.get(m.token_id)),
                floor=self._cfg.analyst_price_floor,
                ceiling=self._cfg.analyst_price_ceiling,
            )
        ]
        skipped = [m for m in group.markets if m not in tradable]
        skip_signals = [
            make_invalid_signal(
                token_id=m.token_id,
                market_slug=m.slug,
                question=m.question,
                status=AnalysisStatus.SKIP_PRICE,
                midpoint=_midpoint(quotes.get(m.token_id)),
                model_used=self._cfg.analyst_model,
            )
            for m in skipped
        ]
        if not tradable:
            self._store.insert_analyst_predictions(skip_signals)
            return skip_signals

        # Build a sub-group from the tradable subset
        tradable_group = EventGroup(
            event_slug=group.event_slug, markets=tradable
        )

        # Retrieve evidence (one search per group)
        evidence, search_in_toks, search_out_toks = await self._retrieve_evidence(
            tradable_group
        )

        # G1: no evidence
        if not has_evidence(evidence):
            no_ev_signals = [
                make_invalid_signal(
                    token_id=m.token_id,
                    market_slug=m.slug,
                    question=m.question,
                    status=AnalysisStatus.NO_EVIDENCE,
                    midpoint=_midpoint(quotes.get(m.token_id)),
                    model_used=self._cfg.analyst_model,
                )
                for m in tradable
            ]
            self._store.insert_analyst_predictions(no_ev_signals)
            return skip_signals + no_ev_signals

        # G4: stale evidence
        if not evidence_is_fresh(
            evidence, max_age_minutes=self._cfg.analyst_max_evidence_age_minutes
        ):
            stale_signals = [
                make_invalid_signal(
                    token_id=m.token_id,
                    market_slug=m.slug,
                    question=m.question,
                    status=AnalysisStatus.STALE,
                    midpoint=_midpoint(quotes.get(m.token_id)),
                    model_used=self._cfg.analyst_model,
                )
                for m in tradable
            ]
            self._store.insert_analyst_predictions(stale_signals)
            return skip_signals + stale_signals

        # LLM analysis
        if tradable_group.is_multi:
            analysis_signals = await self._call_event_llm(
                tradable_group, quotes, evidence, search_in_toks, search_out_toks
            )
        else:
            analysis_signals = await self._call_single_llm(
                tradable_group, quotes, evidence, search_in_toks, search_out_toks
            )

        all_signals = skip_signals + analysis_signals
        self._store.insert_analyst_predictions(
            [s for s in analysis_signals]
        )
        self._store.insert_analyst_evidence(analysis_signals, evidence)
        return all_signals

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    async def _call_single_llm(
        self,
        group: EventGroup,
        quotes: dict[str, ClobQuote],
        evidence: list[SearchResult],
        search_in_toks: int,
        search_out_toks: int,
    ) -> list[PredictionSignal]:
        market = group.markets[0]
        mid = _midpoint(quotes.get(market.token_id))
        prompt = build_single_market_prompt(market, mid, evidence)

        if self._llm is None:
            return [
                make_invalid_signal(
                    token_id=market.token_id,
                    market_slug=market.slug,
                    question=market.question,
                    status=AnalysisStatus.INVALID_OUTPUT,
                    midpoint=mid,
                    model_used=self._cfg.analyst_model,
                )
            ]

        try:
            tool_input, in_toks, out_toks = await self._llm.analyze_single(
                model=self._cfg.analyst_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
            )
        except ValueError as exc:
            log.warning("LLM analysis failed for %s: %s", market.slug, exc)
            if "RATE_LIMITED" in str(exc):
                self._cost.record_429()
            signal = make_invalid_signal(
                token_id=market.token_id,
                market_slug=market.slug,
                question=market.question,
                status=AnalysisStatus.INVALID_OUTPUT,
                midpoint=mid,
                model_used=self._cfg.analyst_model,
            )
            self._cost.record(
                prediction_id=signal.prediction_id,
                search_usd=self._retrieval.cost_per_search_usd(),
                input_tokens=search_in_toks,
                output_tokens=search_out_toks,
                model=self._cfg.analyst_model,
            )
            return [signal]

        total_in = search_in_toks + in_toks
        total_out = search_out_toks + out_toks
        signal = self._build_signal(
            market=market,
            mid=mid,
            tool_input=tool_input,
            evidence=evidence,
            in_toks=total_in,
            out_toks=total_out,
            search_usd=self._retrieval.cost_per_search_usd(),
        )
        self._cost.record(
            prediction_id=signal.prediction_id,
            search_usd=self._retrieval.cost_per_search_usd(),
            input_tokens=total_in,
            output_tokens=total_out,
            model=self._cfg.analyst_model,
        )
        return [signal]

    async def _call_event_llm(
        self,
        group: EventGroup,
        quotes: dict[str, ClobQuote],
        evidence: list[SearchResult],
        search_in_toks: int,
        search_out_toks: int,
    ) -> list[PredictionSignal]:
        prompt = build_event_group_prompt(group, quotes, evidence)
        token_ids = [m.token_id for m in group.markets]

        if self._llm is None:
            return [
                make_invalid_signal(
                    token_id=m.token_id,
                    market_slug=m.slug,
                    question=m.question,
                    status=AnalysisStatus.INVALID_OUTPUT,
                    midpoint=_midpoint(quotes.get(m.token_id)),
                    model_used=self._cfg.analyst_model,
                )
                for m in group.markets
            ]

        try:
            analyses, in_toks, out_toks = await self._llm.analyze_event(
                model=self._cfg.analyst_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                expected_token_ids=token_ids,
            )
        except ValueError as exc:
            log.warning("LLM event analysis failed for %s: %s", group.event_slug, exc)
            if "RATE_LIMITED" in str(exc):
                self._cost.record_429()
            signals = [
                make_invalid_signal(
                    token_id=m.token_id,
                    market_slug=m.slug,
                    question=m.question,
                    status=AnalysisStatus.INVALID_OUTPUT,
                    midpoint=_midpoint(quotes.get(m.token_id)),
                    model_used=self._cfg.analyst_model,
                )
                for m in group.markets
            ]
            first_id = signals[0].prediction_id if signals else make_prediction_id()
            self._cost.record(
                prediction_id=first_id,
                search_usd=self._retrieval.cost_per_search_usd(),
                input_tokens=search_in_toks,
                output_tokens=search_out_toks,
                model=self._cfg.analyst_model,
            )
            return signals

        total_in = search_in_toks + in_toks
        total_out = search_out_toks + out_toks
        mkt_by_tid = {m.token_id: m for m in group.markets}

        signals: list[PredictionSignal] = []
        for analysis in analyses:
            tid = analysis.get("token_id", "")
            market = mkt_by_tid.get(tid)
            if market is None:
                continue
            mid = _midpoint(quotes.get(tid))
            # Pro-rate token costs evenly across the group
            per_n = len(group.markets)
            signal = self._build_signal(
                market=market,
                mid=mid,
                tool_input=analysis,
                evidence=evidence,
                in_toks=total_in // per_n,
                out_toks=total_out // per_n,
                search_usd=self._retrieval.cost_per_search_usd() / per_n,
            )
            signals.append(signal)

        if signals:
            self._cost.record(
                prediction_id=signals[0].prediction_id,
                search_usd=self._retrieval.cost_per_search_usd(),
                input_tokens=total_in,
                output_tokens=total_out,
                model=self._cfg.analyst_model,
            )
        return signals

    # ------------------------------------------------------------------
    # Evidence retrieval
    # ------------------------------------------------------------------

    async def _retrieve_evidence(
        self, group: EventGroup
    ) -> tuple[list[SearchResult], int, int]:
        """Retrieve evidence for the group. Returns (results, in_toks, out_toks)."""
        query = group.primary_question
        try:
            # AnthropicWebSearchProvider returns (results, in_toks, out_toks)
            # MockRetrievalProvider returns just list[SearchResult]
            result = await self._retrieval.search(query, max_results=5)
            if isinstance(result, tuple):
                return result  # (list, int, int)
            return result, 0, 0
        except Exception as exc:
            log.warning("Evidence retrieval failed: %s", exc)
            return [], 0, 0

    # ------------------------------------------------------------------
    # Signal builder
    # ------------------------------------------------------------------

    def _build_signal(
        self,
        *,
        market: MarketRecord,
        mid: float | None,
        tool_input: dict,
        evidence: list[SearchResult],
        in_toks: int,
        out_toks: int,
        search_usd: float,
    ) -> PredictionSignal:
        """Apply post-LLM guardrails and build a PredictionSignal."""
        model = self._cfg.analyst_model
        fair_prob = float(tool_input.get("fair_probability", 0.5))
        raw_confidence = float(tool_input.get("confidence", 0.0))
        is_ambiguous = bool(tool_input.get("is_ambiguous", False))
        ev_summary = str(tool_input.get("evidence_summary", ""))[:500]

        # G5: ambiguity cap
        confidence = apply_ambiguity_cap(raw_confidence, is_ambiguous=is_ambiguous)

        age_min = freshest_age_minutes(evidence)
        sources = [r.url for r in evidence]

        # Compute edge
        edge_bps, side = (
            compute_edge(fair_prob, mid) if mid is not None else (0.0, Side.NONE)
        )

        _, in_usd, out_usd = token_cost_usd(model, in_toks, out_toks)
        total_usd = search_usd + in_usd + out_usd

        # Determine status and whether signal is forwarded
        status = AnalysisStatus.VALID
        if not passes_confidence(confidence, min_confidence=self._cfg.analyst_min_confidence):
            status = AnalysisStatus.LOW_CONFIDENCE
        elif mid is not None and not passes_edge(
            edge_bps, min_edge_bps=self._cfg.analyst_min_edge_bps
        ):
            status = AnalysisStatus.LOW_EDGE

        forwarded = (
            status == AnalysisStatus.VALID
            and mid is not None
            and self._cfg.analyst_enabled
        )

        pred_id = make_prediction_id()
        signal = PredictionSignal(
            prediction_id=pred_id,
            thesis_id=make_thesis_id(market.token_id),
            token_id=market.token_id,
            market_slug=market.slug,
            question=market.question,
            fair_probability=fair_prob,
            confidence=confidence,
            evidence_summary=ev_summary,
            evidence_age_minutes=age_min if age_min != float("inf") else 0.0,
            sources=sources,
            edge_bps=edge_bps,
            side=side,
            midpoint_at_analysis=mid,
            model_used=model,
            api_cost_usd=total_usd,
            search_cost_usd=search_usd,
            input_token_cost_usd=in_usd,
            output_token_cost_usd=out_usd,
            status=status,
            forwarded=forwarded,
            ts=time.time(),
            evidence_records=[r.to_evidence_record() for r in evidence],
        )
        return signal

    # ------------------------------------------------------------------
    # Provider builders
    # ------------------------------------------------------------------

    def _build_retrieval(self) -> RetrievalProvider:
        provider = self._cfg.analyst_retrieval_provider
        if provider == "anthropic_web_search":
            api_key = self._cfg.anthropic_api_key
            if not api_key:
                log.warning(
                    "ANTHROPIC_API_KEY not set; using MockRetrievalProvider "
                    "(dry-run / test mode)"
                )
                return MockRetrievalProvider()
            return AnthropicWebSearchProvider(api_key=api_key)
        log.warning(
            "Unknown ANALYST_RETRIEVAL_PROVIDER %r; using MockRetrievalProvider",
            provider,
        )
        return MockRetrievalProvider()

    def _build_llm(self) -> AnthropicAnalystClient | None:
        api_key = self._cfg.anthropic_api_key
        if not api_key:
            log.warning(
                "ANTHROPIC_API_KEY not set; LLM analysis disabled "
                "(dry-run / test mode -- signals will be INVALID_OUTPUT)"
            )
            return None
        return AnthropicAnalystClient(
            api_key=api_key,
            timeout_seconds=self._cfg.request_timeout_seconds,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _midpoint(quote: ClobQuote | None) -> float | None:
    if quote is None:
        return None
    return quote.midpoint


def select_analyst_candidates(
    catalog: list[MarketRecord],
    quotes: dict[str, ClobQuote],
    max_groups: int,
) -> list[MarketRecord]:
    """
    Select the top candidates for Layer 3 analysis.

    Sorted by L2 tradability score descending; returns at most
    max_groups * 2 markets (allowing for event grouping to fill groups).
    """
    with_quotes = [m for m in catalog if quotes.get(m.token_id) is not None]
    without_quotes = [m for m in catalog if quotes.get(m.token_id) is None]
    ordered = sorted(with_quotes, key=lambda r: r.score, reverse=True)
    ordered.extend(without_quotes)
    # Return up to 2x max_groups to give event_grouper room to bundle
    return ordered[: max_groups * 2]
