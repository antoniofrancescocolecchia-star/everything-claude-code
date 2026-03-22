"""Unit tests for polymarket_platform.analyst.calibration."""
from __future__ import annotations

import time

import pytest

from polymarket_platform.analyst.calibration import (
    build_report,
    compute_brier_score,
    confidence_bucket,
)
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


def make_valid_signal(
    token_id: str = "tok-1",
    fair_probability: float = 0.65,
    confidence: float = 0.7,
    midpoint: float = 0.50,
) -> PredictionSignal:
    """Build a VALID forwarded signal that seeds a calibration row."""
    return PredictionSignal(
        prediction_id=make_prediction_id(),
        thesis_id=make_thesis_id(token_id),
        token_id=token_id,
        market_slug="test-market",
        question="Will X happen?",
        fair_probability=fair_probability,
        confidence=confidence,
        evidence_summary="Some evidence.",
        evidence_age_minutes=60.0,
        sources=["https://reuters.com/a"],
        edge_bps=(fair_probability - midpoint) * 10_000.0,
        side=Side.BUY if fair_probability >= midpoint else Side.SELL,
        midpoint_at_analysis=midpoint,
        model_used="claude-haiku",
        api_cost_usd=0.05,
        search_cost_usd=0.01,
        input_token_cost_usd=0.02,
        output_token_cost_usd=0.02,
        status=AnalysisStatus.VALID,
        forwarded=True,
        ts=time.time(),
    )


# ---------------------------------------------------------------------------
# compute_brier_score
# ---------------------------------------------------------------------------


def test_compute_brier_score_perfect_yes():
    assert compute_brier_score(1.0, 1) == pytest.approx(0.0)


def test_compute_brier_score_perfect_no():
    assert compute_brier_score(0.0, 0) == pytest.approx(0.0)


def test_compute_brier_score_random():
    # fp=0.5 with either outcome => (0.5 - outcome)^2 = 0.25
    assert compute_brier_score(0.5, 0) == pytest.approx(0.25)
    assert compute_brier_score(0.5, 1) == pytest.approx(0.25)


def test_compute_brier_score_wrong():
    # fp=1.0, outcome=0 => (1.0 - 0)^2 = 1.0
    assert compute_brier_score(1.0, 0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# confidence_bucket
# ---------------------------------------------------------------------------


def test_confidence_bucket_low():
    assert confidence_bucket(0.2) == "0.0-0.3"


def test_confidence_bucket_mid_low():
    assert confidence_bucket(0.4) == "0.3-0.5"


def test_confidence_bucket_mid_high():
    assert confidence_bucket(0.6) == "0.5-0.7"


def test_confidence_bucket_high():
    assert confidence_bucket(0.8) == "0.7-1.0"


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def test_build_report_no_data():
    store = make_store()
    report = build_report(store)

    assert report.total_predictions == 0
    assert report.avg_brier_score is None


def test_build_report_with_resolved():
    store = make_store()

    # Insert two VALID forwarded signals — each seeds a calibration row.
    sig1 = make_valid_signal(token_id="tok-1", fair_probability=0.8, midpoint=0.5)
    sig2 = make_valid_signal(token_id="tok-2", fair_probability=0.3, midpoint=0.5)
    store.insert_analyst_predictions([sig1, sig2])

    # Resolve both: sig1 resolves YES (1), sig2 resolves NO (0).
    store.resolve_calibration_outcome(prediction_id=sig1.prediction_id, outcome=1)
    store.resolve_calibration_outcome(prediction_id=sig2.prediction_id, outcome=0)

    report = build_report(store)

    assert report.total_predictions == 2
    assert report.resolved_predictions == 2
    assert report.avg_brier_score is not None
    # (0.8-1)^2 = 0.04, (0.3-0)^2 = 0.09 => avg = 0.065
    assert report.avg_brier_score == pytest.approx(0.065, abs=1e-9)
