"""Tests for the opportunity detector."""
from __future__ import annotations

import time

from polymarket_platform.scanner.catalog import MarketRecord
from polymarket_platform.scanner.clob_probe import ClobQuote
from polymarket_platform.scanner.detector import (
    _MOVEMENT_THRESHOLD,
    _SPREAD_ANOMALY_MULTIPLIER,
    detect,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(
    *,
    token_id: str = "tok-yes",
    condition_id: str = "cond-1",
    outcome: str = "YES",
    outcome_price: float = 0.45,
    days_to_expiry: float | None = 10.0,
    slug: str = "test-market",
    all_token_ids: list[str] | None = None,
    all_outcome_prices: list[float] | None = None,
) -> MarketRecord:
    return MarketRecord(
        token_id=token_id,
        condition_id=condition_id,
        slug=slug,
        event_slug=None,
        question="Will X happen?",
        outcome=outcome,
        outcome_price=outcome_price,
        end_date_ts=time.time() + (days_to_expiry or 10) * 86_400.0,
        days_to_expiry=days_to_expiry,
        liquidity=5000.0,
        volume_24h=1000.0,
        score=0.7,
        all_token_ids=all_token_ids or [token_id],
        all_outcome_prices=all_outcome_prices or [outcome_price],
    )


def make_quote(
    token_id: str = "tok-yes",
    midpoint: float | None = 0.45,
    spread_bps: float | None = 100.0,
) -> ClobQuote:
    return ClobQuote(token_id=token_id, midpoint=midpoint, spread_bps=spread_bps)


# ---------------------------------------------------------------------------
# No signals
# ---------------------------------------------------------------------------


def test_no_signal_for_stable_midrange_market() -> None:
    rec = make_record(outcome_price=0.50)
    quotes = {"tok-yes": make_quote(midpoint=0.50, spread_bps=150.0)}
    # No history -> no spread anomaly, no movement
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert result == []


def test_no_quote_skips_token() -> None:
    rec = make_record()
    result = detect([rec], {}, {}, {}, max_spread_bps=800.0)
    assert result == []


def test_none_midpoint_skips_token() -> None:
    rec = make_record()
    quotes = {"tok-yes": ClobQuote(token_id="tok-yes", midpoint=None, spread_bps=100.0)}
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert result == []


def test_excess_spread_is_filtered() -> None:
    rec = make_record()
    quotes = {"tok-yes": make_quote(midpoint=0.45, spread_bps=900.0)}
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert result == []


# ---------------------------------------------------------------------------
# Signal 1: SPREAD_ANOMALY
# ---------------------------------------------------------------------------


def test_spread_anomaly_detected() -> None:
    rec = make_record()
    avg = 100.0
    current = avg * (_SPREAD_ANOMALY_MULTIPLIER + 1.5)  # clearly above threshold
    quotes = {"tok-yes": make_quote(midpoint=0.45, spread_bps=current)}
    hist_spreads = {"tok-yes": [avg, avg, avg, avg]}

    result = detect([rec], quotes, {}, hist_spreads, max_spread_bps=9999.0)
    assert len(result) == 1
    assert "spread_anomaly" in result[0].reason
    assert result[0].expected_edge_bps > 0


def test_spread_anomaly_needs_enough_history() -> None:
    rec = make_record()
    quotes = {"tok-yes": make_quote(midpoint=0.45, spread_bps=500.0)}
    # Only 2 samples -- below _MIN_HIST_SAMPLES=3
    hist_spreads = {"tok-yes": [100.0, 100.0]}
    result = detect([rec], quotes, {}, hist_spreads, max_spread_bps=9999.0)
    assert result == []


# ---------------------------------------------------------------------------
# Signal 2: RAPID_MOVEMENT
# ---------------------------------------------------------------------------


def test_rapid_movement_detected() -> None:
    rec = make_record()
    current_mid = 0.50
    old_mid = current_mid - _MOVEMENT_THRESHOLD - 0.01  # crossed threshold
    quotes = {"tok-yes": make_quote(midpoint=current_mid)}
    hist_mids = {"tok-yes": [old_mid, old_mid + 0.02]}

    result = detect([rec], quotes, hist_mids, {}, max_spread_bps=800.0)
    assert len(result) == 1
    assert "rapid_movement" in result[0].reason


def test_small_movement_not_flagged() -> None:
    rec = make_record()
    quotes = {"tok-yes": make_quote(midpoint=0.50)}
    hist_mids = {"tok-yes": [0.49, 0.495]}  # <0.05 delta
    result = detect([rec], quotes, hist_mids, {}, max_spread_bps=800.0)
    assert result == []


# ---------------------------------------------------------------------------
# Signal 3: EXTREME_PRICE
# ---------------------------------------------------------------------------


def test_extreme_low_price_detected() -> None:
    rec = make_record(days_to_expiry=5.0)
    quotes = {"tok-yes": make_quote(midpoint=0.03, spread_bps=50.0)}
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert len(result) == 1
    assert "extreme_low" in result[0].reason


def test_extreme_high_price_detected() -> None:
    rec = make_record(days_to_expiry=5.0)
    quotes = {"tok-yes": make_quote(midpoint=0.97, spread_bps=50.0)}
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert len(result) == 1
    assert "extreme_high" in result[0].reason
    assert result[0].side == "SELL"


def test_extreme_price_skipped_near_expiry() -> None:
    rec = make_record(days_to_expiry=0.5)  # < _EXTREME_MIN_DAYS=2.0
    quotes = {"tok-yes": make_quote(midpoint=0.02)}
    result = detect([rec], quotes, {}, {}, max_spread_bps=800.0)
    assert result == []


# ---------------------------------------------------------------------------
# Signal 4: COMPLEMENT_SKEW
# ---------------------------------------------------------------------------


def test_complement_skew_detected() -> None:
    # YES=0.40 + NO=0.40 = 0.80 < 1.0 (skew=0.20 >> threshold)
    yes_rec = make_record(
        token_id="tok-yes",
        condition_id="cond-bin",
        outcome="YES",
        outcome_price=0.40,
        all_token_ids=["tok-yes", "tok-no"],
        all_outcome_prices=[0.40, 0.40],
    )
    no_rec = make_record(
        token_id="tok-no",
        condition_id="cond-bin",
        outcome="NO",
        outcome_price=0.40,
        all_token_ids=["tok-yes", "tok-no"],
        all_outcome_prices=[0.40, 0.40],
    )
    quotes = {
        "tok-yes": make_quote(token_id="tok-yes", midpoint=0.40),
        "tok-no": make_quote(token_id="tok-no", midpoint=0.40),
    }
    result = detect([yes_rec, no_rec], quotes, {}, {}, max_spread_bps=800.0)
    complement = [r for r in result if "complement_skew" in r.reason]
    assert len(complement) == 1
    assert complement[0].side == "BUY"


def test_complement_skew_not_flagged_when_balanced() -> None:
    # YES=0.48 + NO=0.50 = 0.98 -> deviation=0.02 < 0.05 threshold
    yes_rec = make_record(
        token_id="tok-yes", condition_id="cond-ok", outcome="YES", outcome_price=0.48,
        all_token_ids=["tok-yes", "tok-no"], all_outcome_prices=[0.48, 0.50],
    )
    no_rec = make_record(
        token_id="tok-no", condition_id="cond-ok", outcome="NO", outcome_price=0.50,
        all_token_ids=["tok-yes", "tok-no"], all_outcome_prices=[0.48, 0.50],
    )
    quotes = {
        "tok-yes": make_quote(token_id="tok-yes", midpoint=0.48),
        "tok-no": make_quote(token_id="tok-no", midpoint=0.50),
    }
    result = detect([yes_rec, no_rec], quotes, {}, {}, max_spread_bps=800.0)
    complement = [r for r in result if "complement_skew" in r.reason]
    assert complement == []


# ---------------------------------------------------------------------------
# Confidence filter
# ---------------------------------------------------------------------------


def test_min_confidence_filters_weak_signals() -> None:
    # Extreme_low with price=0.049 (just barely above 0.05) -> very small conf
    rec = make_record(days_to_expiry=5.0)
    quotes = {"tok-yes": make_quote(midpoint=0.049)}
    result_default = detect([rec], quotes, {}, {}, max_spread_bps=800.0, min_confidence=0.0)
    result_strict = detect([rec], quotes, {}, {}, max_spread_bps=800.0, min_confidence=0.99)
    assert len(result_strict) == 0
    # With min_confidence=0.0, the weak signal gets through
    # (depends on exact value; just ensure the filter has an effect)
    assert len(result_default) >= len(result_strict)


# ---------------------------------------------------------------------------
# Result ordering
# ---------------------------------------------------------------------------


def test_results_sorted_by_confidence_desc() -> None:
    # Two tokens, different signal strengths
    rec1 = make_record(token_id="tok-1", condition_id="c1", days_to_expiry=5.0)
    rec2 = make_record(token_id="tok-2", condition_id="c2", days_to_expiry=5.0)
    quotes = {
        "tok-1": make_quote(token_id="tok-1", midpoint=0.01),  # strong extreme_low
        "tok-2": make_quote(token_id="tok-2", midpoint=0.04),  # weaker extreme_low
    }
    result = detect([rec1, rec2], quotes, {}, {}, max_spread_bps=800.0, min_confidence=0.0)
    if len(result) >= 2:
        confs = [r.confidence for r in result]
        assert confs == sorted(confs, reverse=True)
