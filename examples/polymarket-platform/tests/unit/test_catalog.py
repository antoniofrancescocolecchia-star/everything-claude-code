"""Tests for market catalog filtering and scoring."""
from __future__ import annotations

import time

import pytest

from polymarket_platform.scanner.catalog import filter_and_score, score_market
from polymarket_platform.scanner.gamma_client import RawMarket

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_raw(
    *,
    condition_id: str = "cond-1",
    active: bool = True,
    closed: bool = False,
    enable_order_book: bool = True,
    liquidity: float = 5000.0,
    volume: float = 20000.0,
    volume_24h: float = 1000.0,
    days_from_now: float | None = 10.0,
    token_ids: list[str] | None = None,
    outcome_prices: list[float] | None = None,
    slug: str = "test-market",
) -> RawMarket:
    end_ts = (time.time() + days_from_now * 86_400.0) if days_from_now is not None else None
    return RawMarket(
        condition_id=condition_id,
        question="Will X happen?",
        slug=slug,
        event_slug=None,
        active=active,
        closed=closed,
        enable_order_book=enable_order_book,
        liquidity=liquidity,
        volume=volume,
        volume_24h=volume_24h,
        end_date_ts=end_ts,
        clob_token_ids=token_ids or ["tok-yes", "tok-no"],
        outcome_prices=outcome_prices or [0.45, 0.55],
    )


# ---------------------------------------------------------------------------
# score_market
# ---------------------------------------------------------------------------


def test_score_market_zero_for_no_volume() -> None:
    assert score_market(
        liquidity=10_000.0,
        volume_24h=0.0,
        days_to_expiry=7.0,
        min_liquidity=1000.0,
        min_volume=200.0,
    ) == 0.0


def test_score_market_zero_for_no_liquidity() -> None:
    assert score_market(
        liquidity=0.0,
        volume_24h=500.0,
        days_to_expiry=7.0,
        min_liquidity=1000.0,
        min_volume=200.0,
    ) == 0.0


def test_score_market_sweet_spot_returns_high() -> None:
    sc = score_market(
        liquidity=50_000.0,
        volume_24h=10_000.0,
        days_to_expiry=15.0,
        min_liquidity=1_000.0,
        min_volume=200.0,
    )
    assert sc > 0.7


def test_score_market_zero_days_gives_low() -> None:
    sc = score_market(
        liquidity=50_000.0,
        volume_24h=10_000.0,
        days_to_expiry=0.5,
        min_liquidity=1_000.0,
        min_volume=200.0,
    )
    # expiry component is 0; max possible is liq_w + vol_w = 0.75
    assert sc < 0.76


def test_score_market_very_long_dated_lower_than_sweet_spot() -> None:
    sc_sweet = score_market(
        liquidity=10_000.0, volume_24h=1_000.0, days_to_expiry=15.0,
        min_liquidity=1_000.0, min_volume=200.0,
    )
    sc_long = score_market(
        liquidity=10_000.0, volume_24h=1_000.0, days_to_expiry=180.0,
        min_liquidity=1_000.0, min_volume=200.0,
    )
    assert sc_sweet > sc_long


def test_score_market_none_expiry_penalised() -> None:
    sc_none = score_market(
        liquidity=10_000.0, volume_24h=1_000.0, days_to_expiry=None,
        min_liquidity=1_000.0, min_volume=200.0,
    )
    sc_known = score_market(
        liquidity=10_000.0, volume_24h=1_000.0, days_to_expiry=15.0,
        min_liquidity=1_000.0, min_volume=200.0,
    )
    assert sc_known > sc_none


# ---------------------------------------------------------------------------
# filter_and_score
# ---------------------------------------------------------------------------


_F = dict(min_liquidity_usd=100.0, min_volume_24h_usd=10.0, min_days_to_expiry=0.5)


def test_filter_excludes_inactive() -> None:
    raw = make_raw(active=False)
    result = filter_and_score([raw], **_F)
    assert result == []


def test_filter_excludes_closed() -> None:
    raw = make_raw(closed=True)
    result = filter_and_score([raw], **_F)
    assert result == []


def test_filter_excludes_no_order_book() -> None:
    raw = make_raw(enable_order_book=False)
    result = filter_and_score([raw], **_F)
    assert result == []


def test_filter_excludes_low_liquidity() -> None:
    raw = make_raw(liquidity=50.0)
    result = filter_and_score(
        [raw], min_liquidity_usd=1000.0, min_volume_24h_usd=10.0, min_days_to_expiry=0.5
    )
    assert result == []


def test_filter_excludes_low_volume() -> None:
    raw = make_raw(volume_24h=10.0)
    result = filter_and_score(
        [raw], min_liquidity_usd=100.0, min_volume_24h_usd=200.0, min_days_to_expiry=0.5
    )
    assert result == []


def test_filter_excludes_expired() -> None:
    raw = make_raw(days_from_now=0.1)
    result = filter_and_score(
        [raw], min_liquidity_usd=100.0, min_volume_24h_usd=10.0, min_days_to_expiry=1.0
    )
    assert result == []


def test_filter_accepts_no_end_date() -> None:
    raw = make_raw(days_from_now=None)
    result = filter_and_score(
        [raw], min_liquidity_usd=100.0, min_volume_24h_usd=10.0, min_days_to_expiry=1.0
    )
    # No end_date -> pass the expiry filter
    assert len(result) == 2  # YES and NO tokens


def test_filter_emits_two_records_for_binary_market() -> None:
    raw = make_raw()
    result = filter_and_score([raw], **_F)
    assert len(result) == 2
    outcomes = {r.outcome for r in result}
    assert outcomes == {"YES", "NO"}


def test_filter_result_sorted_by_score_desc() -> None:
    raw_good = make_raw(
        condition_id="good", liquidity=50_000.0, volume_24h=10_000.0, slug="good"
    )
    raw_poor = make_raw(
        condition_id="poor", liquidity=500.0, volume_24h=50.0,
        token_ids=["tok-a", "tok-b"], slug="poor"
    )
    result = filter_and_score(
        [raw_poor, raw_good],
        min_liquidity_usd=100.0,
        min_volume_24h_usd=10.0,
        min_days_to_expiry=0.5,
    )
    scores = [r.score for r in result]
    assert scores == sorted(scores, reverse=True)


def test_filter_respects_limit() -> None:
    raws = [
        make_raw(condition_id=f"c{i}", token_ids=[f"yes-{i}", f"no-{i}"], slug=f"s{i}")
        for i in range(10)
    ]
    result = filter_and_score(raws, **_F, limit=4)
    assert len(result) <= 4


def test_market_record_fields() -> None:
    raw = make_raw(
        token_ids=["yes-tok"], outcome_prices=[0.42], condition_id="cond-x"
    )
    result = filter_and_score([raw], **_F)
    assert len(result) == 1
    rec = result[0]
    assert rec.token_id == "yes-tok"
    assert rec.outcome == "YES"
    assert rec.outcome_price == pytest.approx(0.42)
    assert rec.condition_id == "cond-x"
    assert rec.all_token_ids == ["yes-tok"]
