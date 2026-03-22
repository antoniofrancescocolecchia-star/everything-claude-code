"""
Multi-market orchestrator: manages a bounded pool of MarketWorkers.

Responsibilities:
  - Drive the scan -> filter -> probe -> detect -> rebalance cycle
  - Enforce global limits: max concurrent workers, max total capital
  - Start workers for high-confidence opportunities
  - Stop workers for markets that drop out of the target set
  - Persist scanner state to SQLite for replay and monitoring

Worker pool contract:
  - Pool size is fixed at max_concurrent_markets
  - One worker per token_id (one TradingEngine per token)
  - Workers removed from the pool have their stop scheduled as a background task
  - No unbounded spawning
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from typing import TYPE_CHECKING

from polymarket_platform.config import Settings
from polymarket_platform.db.store import SqliteStore
from polymarket_platform.scanner.catalog import filter_and_score
from polymarket_platform.scanner.clob_probe import ClobProbe
from polymarket_platform.scanner.detector import OpportunityRecord, detect
from polymarket_platform.scanner.gamma_client import GammaClient
from polymarket_platform.scanner.worker import MarketWorker, WorkerState

if TYPE_CHECKING:
    from polymarket_platform.analyst.analyst import MarketAnalyst
    from polymarket_platform.analyst.signals import PredictionSignal
    from polymarket_platform.scanner.clob_probe import ClobQuote

log = logging.getLogger(__name__)


class Orchestrator:
    """
    Runs the scanner/detector/execution cycle at configurable intervals.

    Single asyncio event loop; workers run as concurrent Tasks in the same loop.
    Gamma and CLOB clients are shared across all scan cycles.
    """

    def __init__(self, cfg: Settings, store: SqliteStore) -> None:
        self._cfg = cfg
        self._store = store
        self._gamma = GammaClient(
            base_url=cfg.gamma_host,
            timeout_seconds=cfg.request_timeout_seconds,
            max_concurrent=4,
        )
        self._probe = ClobProbe(
            clob_host=cfg.clob_host,
            timeout_seconds=cfg.request_timeout_seconds,
            max_concurrent=5,
        )
        self._workers: dict[str, MarketWorker] = {}
        self._analyst: MarketAnalyst | None = self._build_analyst()

    def _build_analyst(self) -> MarketAnalyst | None:
        if not self._cfg.analyst_enabled:
            return None
        from polymarket_platform.analyst.analyst import MarketAnalyst
        from polymarket_platform.analyst.guardrails import validate_budget_coherence
        validate_budget_coherence(self._cfg)
        return MarketAnalyst.from_settings(self._cfg, self._store)

    # ------------------------------------------------------------------
    # Public entry point

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Run scan cycles until `stop` is set, SIGINT, or kill-switch."""
        if stop is None:
            stop = asyncio.Event()

        loop = asyncio.get_running_loop()
        _install_signal_handlers(loop, stop)

        log.info(
            "Orchestrator starting  dry_run=%s  max_markets=%d  poll_interval=%.0fs",
            self._cfg.dry_run,
            self._cfg.max_concurrent_markets,
            self._cfg.scanner_poll_interval,
        )

        try:
            while not stop.is_set():
                if self._kill_switch_active():
                    log.warning("Kill switch detected -- stopping orchestrator")
                    stop.set()
                    break
                await self._run_cycle()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._cfg.scanner_poll_interval)
                except TimeoutError:
                    pass
        finally:
            _remove_signal_handlers(loop)
            await self._shutdown_all_workers()
            await self._gamma.close()
            await self._probe.close()
            if self._analyst is not None:
                await self._analyst.close()
            log.info("Orchestrator stopped")

    # ------------------------------------------------------------------
    # Scan cycle

    async def _run_cycle(self) -> None:
        t0 = time.time()
        log.info("=== Scanner cycle start ===")

        # 1. Fetch raw markets from Gamma
        raw_markets = await self._gamma.fetch_markets(limit=self._cfg.market_discovery_limit)
        log.info("Gamma: %d raw markets", len(raw_markets))

        if not raw_markets:
            log.warning("No markets returned from Gamma -- skipping cycle")
            return

        # 2. Filter and score
        catalog = filter_and_score(
            raw_markets,
            min_liquidity_usd=self._cfg.min_liquidity_usd,
            min_volume_24h_usd=self._cfg.min_volume_24h_usd,
            min_days_to_expiry=self._cfg.min_days_to_expiry,
            limit=self._cfg.market_discovery_limit,
        )
        log.info("Catalog: %d tradable market tokens", len(catalog))

        # 3. Persist catalog to DB
        self._store.upsert_markets(catalog)

        # 4. CLOB probe -- cap at 50 tokens to bound API load
        probe_limit = min(len(catalog), 50)
        token_ids = [r.token_id for r in catalog[:probe_limit]]
        clob_quotes: dict[str, ClobQuote] = await self._probe.probe_batch(token_ids)
        valid = sum(1 for q in clob_quotes.values() if q.is_valid)
        log.info("CLOB probed %d tokens, %d valid quotes", len(token_ids), valid)

        # 5. Persist snapshots
        self._store.insert_market_snapshots(catalog[:probe_limit], clob_quotes)

        # 6. Load historical midpoints and spreads for signal detection
        lookback_ts = time.time() - self._cfg.market_history_lookback_minutes * 60.0
        hist_mids = self._store.get_recent_midpoints(token_ids, since_ts=lookback_ts)
        hist_spreads = self._store.get_recent_spreads(token_ids, since_ts=lookback_ts)

        # 7. Detect opportunities
        opportunities = detect(
            catalog[:probe_limit],
            clob_quotes,
            hist_mids,
            hist_spreads,
            max_spread_bps=self._cfg.max_spread_bps,
            min_confidence=self._cfg.opportunity_min_confidence,
        )

        # Filter by minimum edge
        opportunities = [
            o for o in opportunities
            if o.expected_edge_bps >= self._cfg.opportunity_min_edge_bps
        ]
        log.info(
            "Opportunities: %d detected (conf>=%.2f edge>=%.0fbps)",
            len(opportunities),
            self._cfg.opportunity_min_confidence,
            self._cfg.opportunity_min_edge_bps,
        )

        # 8. Persist opportunities
        self._store.insert_opportunities(opportunities)

        # 9. Layer 3 analyst (optional; only when ANALYST_ENABLED=true)
        signals: list[PredictionSignal] = []
        if self._analyst is not None:
            from polymarket_platform.analyst.analyst import select_analyst_candidates
            candidates = select_analyst_candidates(
                catalog,
                clob_quotes,
                self._cfg.analyst_max_analyses_per_cycle,
            )
            signals = await self._analyst.analyze_batch(candidates, clob_quotes)
            valid = sum(1 for s in signals if s.forwarded)
            log.info(
                "Analyst: %d signals produced, %d forwarded",
                len(signals),
                valid,
            )

        # 10. Rebalance the worker pool
        await self._rebalance(opportunities, signals=signals)

        elapsed = time.time() - t0
        log.info("=== Scanner cycle complete in %.1fs ===", elapsed)

    # ------------------------------------------------------------------
    # Worker pool management

    async def _rebalance(
        self,
        opportunities: list[OpportunityRecord],
        *,
        signals: list[PredictionSignal] | None = None,
    ) -> None:
        """
        Start/stop workers to match the ranked opportunity list.

        When analyst signals are present and ANALYST_TRADING_ENABLED=true
        and unlock conditions are met, tier-1 (analyst-backed) opportunities
        are prioritised over tier-3 (L2 heuristics only).

        Backward compatible: when signals=[] or analyst disabled, behaviour
        is identical to the original L2-only logic.
        """
        # Clean up terminated workers first
        finished_tokens = [
            tid
            for tid, w in self._workers.items()
            if w.state in (WorkerState.STOPPED, WorkerState.ERROR)
        ]
        for tid in finished_tokens:
            w = self._workers.pop(tid)
            self._store.mark_tracked_position_stopped(tid, reason=w.stop_reason or "finished")
            log.debug("Cleaned up terminated worker for %s", tid[:20])

        max_workers = self._cfg.max_concurrent_markets

        # Build analyst signal lookup (VALID + forwarded only)
        sig_map: dict[str, PredictionSignal] = {}
        if signals and self._cfg.analyst_enabled:
            from polymarket_platform.analyst.signals import AnalysisStatus
            sig_map = {
                s.token_id: s
                for s in signals
                if s.status == AnalysisStatus.VALID and s.forwarded
            }

        analyst_live = (
            bool(sig_map)
            and self._cfg.analyst_trading_enabled
            and self._analyst is not None
            and self._analyst.is_live_trading_unlocked()
        )

        # Build priority-ranked opportunity list
        # Tier 1: analyst-backed (if live trading unlocked)
        # Tier 3: L2 heuristics (always available as fallback)
        tier1: list[OpportunityRecord] = []
        tier3: list[OpportunityRecord] = []
        for opp in opportunities:
            passes_l2 = (
                opp.confidence >= self._cfg.opportunity_min_confidence
                and opp.expected_edge_bps >= self._cfg.opportunity_min_edge_bps
            )
            if not passes_l2:
                continue
            if analyst_live and opp.token_id in sig_map:
                tier1.append(opp)
            else:
                tier3.append(opp)

        ranked = tier1 + tier3

        if sig_map and not analyst_live:
            log.info(
                "Analyst signals present but live trading not unlocked "
                "(trading_enabled=%s, signals=%d) -- monitor only",
                self._cfg.analyst_trading_enabled,
                len(sig_map),
            )

        # Target token set: top-N from ranked list
        target: dict[str, OpportunityRecord] = {}
        for opp in ranked:
            if len(target) >= max_workers:
                break
            target[opp.token_id] = opp

        # Stop workers no longer in the target set
        to_evict = [tid for tid in list(self._workers) if tid not in target]
        for tid in to_evict:
            worker = self._workers.pop(tid)
            self._store.mark_tracked_position_stopped(tid, reason="opportunity_expired")
            if worker.is_running:
                asyncio.create_task(
                    worker.stop("opportunity_expired"), name=f"stop-{tid[:12]}"
                )
            log.info("Evicted worker for token %s", tid[:20])

        # Launch new workers for tokens in the target set
        for token_id, opp in target.items():
            if token_id in self._workers:
                continue  # already running

            # Enforce capital budget
            cap_used = self._active_capital_usd() + self._cfg.max_capital_per_market_usd
            if cap_used > self._cfg.max_total_capital_usd:
                log.info(
                    "Capital budget full (active=%.0f max=%.0f) -- not launching more workers",
                    self._active_capital_usd(),
                    self._cfg.max_total_capital_usd,
                )
                break

            log.info(
                "Launching worker  token=%s  conf=%.2f  edge=%.0fbps  reason=%s",
                token_id[:24],
                opp.confidence,
                opp.expected_edge_bps,
                opp.reason[:80],
            )
            worker = MarketWorker(
                token_id=token_id,
                base_cfg=self._cfg,
                store=self._store,
            )
            worker.start()
            self._workers[token_id] = worker
            self._store.upsert_tracked_position(
                token_id=token_id,
                slug=opp.slug,
                capital_usd=self._cfg.max_capital_per_market_usd,
            )

        active = len(self._workers)
        log.info("Worker pool: %d/%d active", active, max_workers)

    def _active_capital_usd(self) -> float:
        return len(self._workers) * self._cfg.max_capital_per_market_usd

    async def _shutdown_all_workers(self) -> None:
        if not self._workers:
            return
        log.info("Shutting down %d workers...", len(self._workers))
        stop_tasks = [
            asyncio.create_task(w.stop("orchestrator_shutdown"))
            for w in self._workers.values()
            if w.is_running
        ]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        self._workers.clear()

    def _kill_switch_active(self) -> bool:
        path = self._cfg.kill_switch_path
        return bool(path and os.path.exists(path))


# ---------------------------------------------------------------------------
# Signal handling (Windows-safe, same pattern as TradingEngine)
# ---------------------------------------------------------------------------


def _install_signal_handlers(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.add_signal_handler(sig, stop.set)


def _remove_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.remove_signal_handler(sig)
