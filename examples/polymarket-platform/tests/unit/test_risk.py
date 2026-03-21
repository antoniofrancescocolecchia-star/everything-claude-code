"""Unit tests for RiskManager."""

import pytest

from polymarket_platform.db.store import SqliteStore
from polymarket_platform.risk.manager import RiskLimits, RiskManager


def make_rm(
    *,
    max_pos: float = 100.0,
    max_usd: float = 500.0,
    cooldown: float = 0.0,
    max_orders: int = 99,
    store: SqliteStore | None = None,
) -> RiskManager:
    s = store or SqliteStore(":memory:")
    return RiskManager(
        RiskLimits(
            max_position_shares=max_pos,
            max_usd_spend=max_usd,
            min_seconds_between_orders=cooldown,
            max_open_orders=max_orders,
        ),
        s,
    )


def test_allow_buy_basic() -> None:
    rm = make_rm()
    ok, reason = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=10, open_orders_count=0
    )
    assert ok


def test_hold_always_passes() -> None:
    rm = make_rm()
    ok, _ = rm.allow(
        action="HOLD", position_shares=999, best_ask=0.99, buy_amount_usd=9999, open_orders_count=99
    )
    assert ok


def test_blocks_max_usd_spend() -> None:
    rm = make_rm(max_usd=10)
    ok, reason = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=11, open_orders_count=0
    )
    assert not ok
    assert "usd" in reason.lower()


def test_blocks_max_position_shares() -> None:
    # ask=0.5 → expected_shares = 20/0.5 = 40; current=70 → total=110 > 100
    rm = make_rm(max_pos=100)
    ok, reason = rm.allow(
        action="BUY", position_shares=70, best_ask=0.5, buy_amount_usd=20, open_orders_count=0
    )
    assert not ok
    assert "position" in reason.lower()


def test_blocks_too_many_open_orders() -> None:
    rm = make_rm(max_orders=3)
    ok, reason = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=10, open_orders_count=3
    )
    assert not ok
    assert "open orders" in reason.lower()


def test_cooldown_blocks() -> None:
    rm = make_rm(cooldown=60.0)
    # Send an order to start cooldown
    rm.on_order_sent(action="BUY", buy_amount_usd=10)
    ok, reason = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=10, open_orders_count=0
    )
    assert not ok
    assert "cooldown" in reason.lower()


def test_cooldown_expires() -> None:
    rm = make_rm(cooldown=0.0)
    rm.on_order_sent(action="BUY", buy_amount_usd=10)
    ok, _ = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=10, open_orders_count=0
    )
    assert ok


def test_usd_spent_accumulates() -> None:
    rm = make_rm(max_usd=25)
    rm.on_order_sent(action="BUY", buy_amount_usd=10)
    rm.on_order_sent(action="BUY", buy_amount_usd=10)
    # 20 spent, next 10 would exceed 25
    ok, _ = rm.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=10, open_orders_count=0
    )
    assert not ok


def test_risk_state_persists_across_instances() -> None:
    """Risk state should survive a restart by loading from SQLite."""
    s = SqliteStore(":memory:")
    rm1 = make_rm(max_usd=50, store=s)
    rm1.on_order_sent(action="BUY", buy_amount_usd=30)

    # Simulate restart by creating a new RiskManager on the same store
    rm2 = make_rm(max_usd=50, store=s)
    assert rm2.usd_spent == pytest.approx(30.0, abs=0.01)

    ok, _ = rm2.allow(
        action="BUY", position_shares=0, best_ask=0.5, buy_amount_usd=25, open_orders_count=0
    )
    assert not ok  # 30+25=55 > 50
