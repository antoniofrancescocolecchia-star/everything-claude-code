"""
Guardrails G1-G8 for Layer 3 analyst.

All functions are pure (no side effects, no DB access).
The analyst.py orchestrator decides what to do with the result.

Budget coherence check (startup):
  validate_budget_coherence() raises ValueError if config is self-contradictory.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.analyst.retrieval import SearchResult
    from polymarket_platform.config import Settings

_SEARCH_COST_USD = 0.01  # Per web search call


# ---------------------------------------------------------------------------
# G6 - Price filter (skip near-resolved markets)
# ---------------------------------------------------------------------------


def passes_price_filter(
    midpoint: float | None,
    *,
    floor: float,
    ceiling: float,
) -> bool:
    """
    G6: return False if midpoint is outside (floor, ceiling).

    Markets at 0.01 or 0.99 are effectively resolved; the edge is illusory.
    None midpoint is treated as unknown -> allowed through (other guards apply).
    """
    if midpoint is None:
        return True
    return floor < midpoint < ceiling


# ---------------------------------------------------------------------------
# G1 - Evidence existence
# ---------------------------------------------------------------------------


def has_evidence(results: list[SearchResult]) -> bool:
    """G1: at least one result is required before calling the LLM."""
    return len(results) > 0


# ---------------------------------------------------------------------------
# G4 - Evidence recency
# ---------------------------------------------------------------------------


def evidence_is_fresh(
    results: list[SearchResult],
    *,
    max_age_minutes: float,
) -> bool:
    """
    G4: True if at least one result is within max_age_minutes.

    Results with age_minutes=None are counted as stale.
    """
    return any(
        r.age_minutes is not None and r.age_minutes <= max_age_minutes
        for r in results
    )


def freshest_age_minutes(results: list[SearchResult]) -> float:
    """Return the smallest age_minutes among results, or inf if all are None."""
    ages = [r.age_minutes for r in results if r.age_minutes is not None]
    return min(ages) if ages else float("inf")


# ---------------------------------------------------------------------------
# G5 - Ambiguity
# ---------------------------------------------------------------------------


def apply_ambiguity_cap(confidence: float, *, is_ambiguous: bool) -> float:
    """
    G5: if the market question is ambiguous, cap confidence at 0.19.
    """
    if is_ambiguous:
        return min(confidence, 0.19)
    return confidence


# ---------------------------------------------------------------------------
# G2 - Confidence floor
# ---------------------------------------------------------------------------


def passes_confidence(confidence: float, *, min_confidence: float) -> bool:
    """G2: confidence must meet the minimum threshold."""
    return confidence >= min_confidence


# ---------------------------------------------------------------------------
# G3 - Edge floor
# ---------------------------------------------------------------------------


def passes_edge(edge_bps: float, *, min_edge_bps: float) -> bool:
    """G3: absolute edge must cover fees plus safety buffer."""
    return abs(edge_bps) >= min_edge_bps


# ---------------------------------------------------------------------------
# Budget coherence (startup check)
# ---------------------------------------------------------------------------


def validate_budget_coherence(cfg: Settings) -> None:
    """
    Validate that analyst budget settings are self-consistent.

    Called at startup when ANALYST_ENABLED=true.
    Raises ValueError with a descriptive message on the first violation.

    Fixed rule (post-architecture review):
      Budget unit = one retrieval search per event group.
      max_analyses_per_cycle <= max_searches_per_cycle
      (one search per event group, max_analyses = max event groups to analyze)
    """
    if not cfg.analyst_enabled:
        return

    # Searches per cycle must cover the groups to analyze
    if cfg.analyst_max_analyses_per_cycle > cfg.analyst_max_searches_per_cycle:
        raise ValueError(
            f"ANALYST_MAX_ANALYSES_PER_CYCLE ({cfg.analyst_max_analyses_per_cycle}) "
            f"must be <= ANALYST_MAX_SEARCHES_PER_CYCLE "
            f"({cfg.analyst_max_searches_per_cycle}): "
            "each event group (analysis unit) requires one retrieval search."
        )

    # Search cost floor vs hourly cap
    cycles_per_hour = 3600.0 / max(cfg.scanner_poll_interval, 1.0)
    searches_per_hour = cycles_per_hour * cfg.analyst_max_searches_per_cycle
    search_cost_floor = searches_per_hour * _SEARCH_COST_USD
    if search_cost_floor > cfg.analyst_max_cost_per_hour_usd:
        raise ValueError(
            f"Search cost alone at configured rate "
            f"({search_cost_floor:.3f}/hr = "
            f"{cycles_per_hour:.1f} cycles/hr x "
            f"{cfg.analyst_max_searches_per_cycle} searches/cycle x "
            f"${_SEARCH_COST_USD}/search) "
            f"exceeds ANALYST_MAX_COST_PER_HOUR_USD "
            f"({cfg.analyst_max_cost_per_hour_usd:.2f}). "
            "Reduce ANALYST_MAX_SEARCHES_PER_CYCLE, increase SCANNER_POLL_INTERVAL_SECONDS, "
            "or raise ANALYST_MAX_COST_PER_HOUR_USD."
        )

    # Daily cap must be reachable from hourly cap
    if cfg.analyst_max_cost_per_hour_usd * 24 > cfg.analyst_max_cost_per_day_usd * 2:
        raise ValueError(
            f"ANALYST_MAX_COST_PER_HOUR_USD ({cfg.analyst_max_cost_per_hour_usd:.2f}) "
            f"x 24h = {cfg.analyst_max_cost_per_hour_usd * 24:.2f}, which is more than "
            f"2x ANALYST_MAX_COST_PER_DAY_USD ({cfg.analyst_max_cost_per_day_usd:.2f}). "
            "Either lower the hourly cap or raise the daily cap."
        )

    # Token throughput floor: at least 1000 input tokens per analysis
    analyses_per_hour = cycles_per_hour * cfg.analyst_max_analyses_per_cycle
    if analyses_per_hour > 0:
        tokens_per_analysis = cfg.analyst_max_input_tokens_per_hour / analyses_per_hour
        if tokens_per_analysis < 1000:
            raise ValueError(
                f"ANALYST_MAX_INPUT_TOKENS_PER_HOUR ({cfg.analyst_max_input_tokens_per_hour}) "
                f"allows only {tokens_per_analysis:.0f} input tokens per analysis "
                f"({analyses_per_hour:.1f} analyses/hr). "
                "Minimum is 1000 tokens per analysis. "
                "Raise ANALYST_MAX_INPUT_TOKENS_PER_HOUR or reduce analysis frequency."
            )
