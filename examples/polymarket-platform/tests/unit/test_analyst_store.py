"""Tests for analyst/Layer 3 methods on SqliteStore."""
from __future__ import annotations

import time

import pytest

from polymarket_platform.analyst.signals import (
    AnalysisStatus,
    PredictionSignal,
    Side,
    make_prediction_id,
    make_thesis_id,
)
from polymarket_platform.db.store import SqliteStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store() -> SqliteStore:
    return SqliteStore(":memory:")


def make_signal(
    token_id="tok-1",
    status=AnalysisStatus.VALID,
    forwarded=True,
    midpoint=0.45,
    fair_probability=0.62,
    confidence=0.6,
    edge_bps=1700.0,
) -> PredictionSignal:
    return PredictionSignal(
        prediction_id=make_prediction_id(),
        thesis_id=make_thesis_id(token_id),
        token_id=token_id,
        market_slug="test-slug",
        question="Will it happen?",
        fair_probability=fair_probability,
        confidence=confidence,
        evidence_summary="Test evidence.",
        evidence_age_minutes=30.0,
        sources=["https://example.com"],
        edge_bps=edge_bps,
        side=Side.BUY,
        midpoint_at_analysis=midpoint,
        model_used="claude-sonnet-4-6",
        api_cost_usd=0.05,
        search_cost_usd=0.01,
        input_token_cost_usd=0.03,
        output_token_cost_usd=0.01,
        status=status,
        forwarded=forwarded,
        ts=time.time(),
    )


# ---------------------------------------------------------------------------
# analyst_predictions
# ---------------------------------------------------------------------------


def test_insert_analyst_predictions_persists():
    store = make_store()
    s1 = make_signal(token_id="tok-1")
    s2 = make_signal(token_id="tok-2")
    store.insert_analyst_predictions([s1, s2])
    assert store.count_analyst_predictions() == 2


def test_insert_analyst_predictions_idempotent():
    store = make_store()
    s = make_signal()
    store.insert_analyst_predictions([s])
    # Insert the same signal again — INSERT OR IGNORE should skip
    store.insert_analyst_predictions([s])
    assert store.count_analyst_predictions() == 1


def test_insert_analyst_predictions_seeds_calibration_for_valid():
    store = make_store()
    s = make_signal(status=AnalysisStatus.VALID, forwarded=True, midpoint=0.45)
    store.insert_analyst_predictions([s])
    # Calibration row exists (not yet resolved, so not in get_resolved_calibration_rows)
    # Verify by resolving the outcome and then checking
    store.resolve_calibration_outcome(prediction_id=s.prediction_id, outcome=1)
    rows = store.get_resolved_calibration_rows()
    assert len(rows) == 1
    assert rows[0]["prediction_id"] == s.prediction_id


def test_insert_analyst_predictions_no_calibration_for_invalid():
    store = make_store()
    s = make_signal(status=AnalysisStatus.INVALID_OUTPUT, forwarded=False, midpoint=0.45)
    store.insert_analyst_predictions([s])
    # resolve_calibration_outcome should be a no-op if no calibration row was seeded
    store.resolve_calibration_outcome(prediction_id=s.prediction_id, outcome=1)
    rows = store.get_resolved_calibration_rows()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# analyst_costs
# ---------------------------------------------------------------------------


def test_insert_analyst_costs_persists():
    store = make_store()
    store.insert_analyst_costs(
        prediction_id="p1",
        search_usd=0.01,
        input_token_usd=0.03,
        output_token_usd=0.015,
        input_tokens=10_000,
        output_tokens=1_000,
    )
    # Total: 0.01 + 0.03 + 0.015 = 0.055
    total = store.total_analyst_cost_usd()
    assert pytest.approx(total, rel=1e-6) == 0.055


def test_total_analyst_cost_usd_empty():
    store = make_store()
    assert store.total_analyst_cost_usd() == 0.0


def test_get_analyst_costs_since_filters_by_time():
    store = make_store()
    # We cannot easily control the ts written by insert_analyst_costs (uses time.time()),
    # so we insert and then query with a future threshold to verify the filter works.
    store.insert_analyst_costs(
        prediction_id="p1",
        search_usd=0.01,
        input_token_usd=0.02,
        output_token_usd=0.01,
        input_tokens=1_000,
        output_tokens=500,
    )
    # Query with a far-future since_ts — should return nothing
    future_ts = time.time() + 9_999
    rows_after = store.get_analyst_costs_since(since_ts=future_ts)
    assert len(rows_after) == 0

    # Query with a past since_ts — should return the inserted rows
    past_ts = time.time() - 9_999
    rows_before = store.get_analyst_costs_since(since_ts=past_ts)
    assert len(rows_before) == 3  # SEARCH + INPUT_TOKENS + OUTPUT_TOKENS rows


# ---------------------------------------------------------------------------
# get_recent_analyst_predictions
# ---------------------------------------------------------------------------


def test_get_recent_analyst_predictions():
    store = make_store()
    signals = [make_signal(token_id=f"tok-{i}") for i in range(3)]
    store.insert_analyst_predictions(signals)
    recent = store.get_recent_analyst_predictions(limit=2)
    assert len(recent) == 2


# ---------------------------------------------------------------------------
# resolve_calibration_outcome
# ---------------------------------------------------------------------------


def test_resolve_calibration_outcome_yes():
    store = make_store()
    fair_prob = 0.7
    s = make_signal(fair_probability=fair_prob, forwarded=True, midpoint=0.5)
    store.insert_analyst_predictions([s])
    store.resolve_calibration_outcome(prediction_id=s.prediction_id, outcome=1)
    rows = store.get_resolved_calibration_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == 1
    expected_brier = (fair_prob - 1) ** 2
    assert pytest.approx(row["brier_score"], rel=1e-6) == expected_brier


def test_resolve_calibration_outcome_no():
    store = make_store()
    fair_prob = 0.3
    s = make_signal(fair_probability=fair_prob, forwarded=True, midpoint=0.5)
    store.insert_analyst_predictions([s])
    store.resolve_calibration_outcome(prediction_id=s.prediction_id, outcome=0)
    rows = store.get_resolved_calibration_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == 0
    expected_brier = (fair_prob - 0) ** 2
    assert pytest.approx(row["brier_score"], rel=1e-6) == expected_brier
