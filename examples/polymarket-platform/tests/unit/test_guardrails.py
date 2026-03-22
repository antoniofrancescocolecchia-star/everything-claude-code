"""Unit tests for polymarket_platform.analyst.guardrails."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from polymarket_platform.analyst.guardrails import (
    apply_ambiguity_cap,
    evidence_is_fresh,
    has_evidence,
    passes_confidence,
    passes_edge,
    passes_price_filter,
    validate_budget_coherence,
)
from polymarket_platform.analyst.retrieval import SearchResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_result(age_minutes: float | None = 30.0) -> SearchResult:
    return SearchResult(
        url="https://reuters.com/article",
        title="Test",
        snippet="snippet",
        published_at=None,
        age_minutes=age_minutes,
        domain="reuters.com",
    )


def make_cfg(**kwargs):
    cfg = MagicMock()
    cfg.analyst_enabled = True
    cfg.analyst_max_analyses_per_cycle = kwargs.get("max_analyses", 5)
    cfg.analyst_max_searches_per_cycle = kwargs.get("max_searches", 5)
    cfg.scanner_poll_interval = kwargs.get("poll_interval", 120.0)
    cfg.analyst_max_cost_per_hour_usd = kwargs.get("max_cost_hour", 2.0)
    cfg.analyst_max_cost_per_day_usd = kwargs.get("max_cost_day", 10.0)
    cfg.analyst_max_input_tokens_per_hour = kwargs.get("max_in_toks", 100_000)
    cfg.analyst_max_output_tokens_per_hour = kwargs.get("max_out_toks", 20_000)
    cfg.analyst_max_429s_per_hour = kwargs.get("max_429s", 5)
    return cfg


# ---------------------------------------------------------------------------
# passes_price_filter (G6)
# ---------------------------------------------------------------------------


def test_passes_price_filter_middle():
    assert passes_price_filter(0.5, floor=0.08, ceiling=0.92) is True


def test_passes_price_filter_at_floor():
    # exactly at floor is not strictly greater => False
    assert passes_price_filter(0.08, floor=0.08, ceiling=0.92) is False


def test_passes_price_filter_at_ceiling():
    # exactly at ceiling is not strictly less => False
    assert passes_price_filter(0.92, floor=0.08, ceiling=0.92) is False


def test_passes_price_filter_none():
    # None midpoint passes through (unknown)
    assert passes_price_filter(None, floor=0.08, ceiling=0.92) is True


# ---------------------------------------------------------------------------
# has_evidence (G1)
# ---------------------------------------------------------------------------


def test_has_evidence_empty():
    assert has_evidence([]) is False


def test_has_evidence_nonempty():
    assert has_evidence([make_result()]) is True


# ---------------------------------------------------------------------------
# evidence_is_fresh (G4)
# ---------------------------------------------------------------------------


def test_evidence_is_fresh_recent():
    results = [make_result(age_minutes=30.0)]
    assert evidence_is_fresh(results, max_age_minutes=360.0) is True


def test_evidence_is_fresh_stale():
    results = [make_result(age_minutes=400.0), make_result(age_minutes=500.0)]
    assert evidence_is_fresh(results, max_age_minutes=360.0) is False


def test_evidence_is_fresh_none_age():
    # age_minutes=None is treated as stale
    results = [make_result(age_minutes=None)]
    assert evidence_is_fresh(results, max_age_minutes=360.0) is False


# ---------------------------------------------------------------------------
# apply_ambiguity_cap (G5)
# ---------------------------------------------------------------------------


def test_apply_ambiguity_cap_when_ambiguous():
    # confidence higher than cap should be capped at 0.19
    capped = apply_ambiguity_cap(0.75, is_ambiguous=True)
    assert capped == 0.19


def test_apply_ambiguity_cap_when_not_ambiguous():
    # confidence should be unchanged when not ambiguous
    original = 0.75
    result = apply_ambiguity_cap(original, is_ambiguous=False)
    assert result == original


# ---------------------------------------------------------------------------
# passes_confidence (G2)
# ---------------------------------------------------------------------------


def test_passes_confidence_above_min():
    assert passes_confidence(0.5, min_confidence=0.4) is True


def test_passes_confidence_below_min():
    assert passes_confidence(0.3, min_confidence=0.4) is False


# ---------------------------------------------------------------------------
# passes_edge (G3)
# ---------------------------------------------------------------------------


def test_passes_edge_sufficient():
    assert passes_edge(500.0, min_edge_bps=300.0) is True


def test_passes_edge_insufficient():
    assert passes_edge(200.0, min_edge_bps=300.0) is False


# ---------------------------------------------------------------------------
# validate_budget_coherence
# ---------------------------------------------------------------------------


def test_budget_coherence_analyses_exceed_searches():
    # max_analyses > max_searches => ValueError
    cfg = make_cfg(max_analyses=6, max_searches=5)
    with pytest.raises(ValueError, match="ANALYST_MAX_ANALYSES_PER_CYCLE"):
        validate_budget_coherence(cfg)


def test_budget_coherence_search_cost_exceeds_hourly():
    # Very fast poll + many searches => search cost floor exceeds hourly cap.
    # poll_interval=1s => 3600 cycles/hr; 10 searches/cycle x $0.01 = $360/hr >> $2.00
    cfg = make_cfg(
        max_analyses=5,
        max_searches=10,
        poll_interval=1.0,
        max_cost_hour=2.0,
    )
    with pytest.raises(ValueError, match="Search cost alone"):
        validate_budget_coherence(cfg)
