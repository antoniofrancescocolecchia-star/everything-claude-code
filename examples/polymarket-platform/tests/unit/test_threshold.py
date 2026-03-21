"""Unit tests for ThresholdStrategy."""
import pytest

from polymarket_platform.strategy.base import MarketSnapshot
from polymarket_platform.strategy.threshold import ThresholdParams, ThresholdStrategy

TOKEN = "tok"
PARAMS = ThresholdParams(buy_threshold=0.45, sell_threshold=0.55)


def snap(bid: float, ask: float, pos: float = 0.0) -> MarketSnapshot:
    import time
    return MarketSnapshot(token_id=TOKEN, best_bid=bid, best_ask=ask, position_shares=pos, ts=time.time())


def test_buy_at_threshold() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.40, ask=0.45))
    assert d.action == "BUY"
    assert d.expected_edge_bps > 0


def test_buy_below_threshold() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.38, ask=0.42))
    assert d.action == "BUY"


def test_no_buy_above_threshold() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.44, ask=0.46))
    assert d.action == "HOLD"


def test_sell_requires_position() -> None:
    s = ThresholdStrategy(PARAMS)
    # bid above sell threshold but no position
    d = s.decide(snap(bid=0.55, ask=0.60, pos=0.0))
    assert d.action == "HOLD"


def test_sell_with_position() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.56, ask=0.60, pos=10.0))
    assert d.action == "SELL"
    assert d.expected_edge_bps > 0


def test_sell_at_threshold() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.55, ask=0.58, pos=5.0))
    assert d.action == "SELL"


def test_hold_in_dead_band() -> None:
    s = ThresholdStrategy(PARAMS)
    d = s.decide(snap(bid=0.48, ask=0.50, pos=10.0))
    assert d.action == "HOLD"


def test_edge_calculation_buy() -> None:
    s = ThresholdStrategy(PARAMS)
    # buy_threshold=0.45, ask=0.44 → edge = (0.45-0.44)/0.44*10000 ≈ 227 bps
    d = s.decide(snap(bid=0.40, ask=0.44))
    expected = (0.45 - 0.44) / 0.44 * 10_000
    assert abs(d.expected_edge_bps - round(expected, 2)) < 0.1


def test_edge_calculation_sell() -> None:
    s = ThresholdStrategy(PARAMS)
    # sell_threshold=0.55, bid=0.57 → edge = (0.57-0.55)/0.55*10000 ≈ 363 bps
    d = s.decide(snap(bid=0.57, ask=0.60, pos=10.0))
    expected = (0.57 - 0.55) / 0.55 * 10_000
    assert abs(d.expected_edge_bps - round(expected, 2)) < 0.1


def test_strategy_name() -> None:
    assert ThresholdStrategy(PARAMS).name == "threshold"
