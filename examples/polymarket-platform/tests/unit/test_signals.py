"""Unit tests for polymarket_platform.analyst.signals."""
from __future__ import annotations

import re
from datetime import date

from polymarket_platform.analyst.signals import (
    AnalysisStatus,
    PredictionSignal,
    Side,
    compute_edge,
    make_invalid_signal,
    make_prediction_id,
    make_thesis_id,
)

# ---------------------------------------------------------------------------
# make_prediction_id
# ---------------------------------------------------------------------------

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def test_make_prediction_id_is_uuid4():
    pid = make_prediction_id()
    assert isinstance(pid, str)
    assert _UUID4_RE.match(pid), f"Not a UUID4: {pid!r}"


# ---------------------------------------------------------------------------
# make_thesis_id
# ---------------------------------------------------------------------------


def test_make_thesis_id_deterministic():
    d = date(2025, 6, 15)
    id1 = make_thesis_id("tok-abc", d)
    id2 = make_thesis_id("tok-abc", d)
    assert id1 == id2


def test_make_thesis_id_different_days():
    d1 = date(2025, 6, 15)
    d2 = date(2025, 6, 16)
    assert make_thesis_id("tok-abc", d1) != make_thesis_id("tok-abc", d2)


def test_make_thesis_id_different_tokens():
    d = date(2025, 6, 15)
    assert make_thesis_id("tok-aaa", d) != make_thesis_id("tok-bbb", d)


# ---------------------------------------------------------------------------
# compute_edge
# ---------------------------------------------------------------------------


def test_compute_edge_buy():
    # fair_prob > midpoint => positive edge_bps, Side.BUY
    edge_bps, side = compute_edge(0.65, 0.50)
    assert edge_bps > 0
    assert side == Side.BUY


def test_compute_edge_sell():
    # fair_prob < midpoint => negative edge_bps, Side.SELL
    edge_bps, side = compute_edge(0.35, 0.50)
    assert edge_bps < 0
    assert side == Side.SELL


def test_compute_edge_zero():
    # fair_prob == midpoint => edge_bps == 0, Side.BUY (>= 0)
    edge_bps, side = compute_edge(0.50, 0.50)
    assert edge_bps == 0.0
    assert side == Side.BUY


# ---------------------------------------------------------------------------
# make_invalid_signal
# ---------------------------------------------------------------------------


def test_make_invalid_signal_fields():
    sig = make_invalid_signal(
        token_id="tok-1",
        market_slug="some-market",
        question="Will X happen?",
        status=AnalysisStatus.LOW_CONFIDENCE,
        midpoint=0.55,
    )
    assert isinstance(sig, PredictionSignal)
    assert sig.status == AnalysisStatus.LOW_CONFIDENCE
    assert sig.forwarded is False
    assert sig.edge_bps == 0.0


def test_make_invalid_signal_no_evidence():
    sig = make_invalid_signal(
        token_id="tok-2",
        market_slug="other-market",
        question="Will Y happen?",
        status=AnalysisStatus.NO_EVIDENCE,
    )
    assert sig.status == AnalysisStatus.NO_EVIDENCE
    assert sig.fair_probability == 0.0
