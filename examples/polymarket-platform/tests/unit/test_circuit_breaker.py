"""Unit tests for CircuitBreaker."""
import pytest

from polymarket_platform.circuit_breaker import CBConfig, CBState, CircuitBreaker
from polymarket_platform.db.store import SqliteStore


def make_cb(
    *,
    max_losses: int = 3,
    max_drawdown: float = 50.0,
    max_latency_ms: float = 500.0,
    store: SqliteStore | None = None,
) -> CircuitBreaker:
    s = store or SqliteStore(":memory:")
    return CircuitBreaker(
        CBConfig(
            max_consecutive_losses=max_losses,
            max_daily_drawdown_usd=max_drawdown,
            max_decision_latency_ms=max_latency_ms,
        ),
        s,
    )


def test_initial_state_closed() -> None:
    cb = make_cb()
    assert cb.state == CBState.CLOSED
    assert cb.ok()


def test_trips_on_consecutive_losses() -> None:
    cb = make_cb(max_losses=3)
    cb.on_fill(pnl_usd=-1.0)
    cb.on_fill(pnl_usd=-1.0)
    assert cb.ok()  # not tripped yet
    cb.on_fill(pnl_usd=-1.0)
    assert not cb.ok()
    assert cb.state == CBState.OPEN


def test_resets_consecutive_on_profit() -> None:
    cb = make_cb(max_losses=3)
    cb.on_fill(pnl_usd=-1.0)
    cb.on_fill(pnl_usd=-1.0)
    cb.on_fill(pnl_usd=+5.0)  # profit resets counter
    cb.on_fill(pnl_usd=-1.0)
    cb.on_fill(pnl_usd=-1.0)
    assert cb.ok()  # only 2 losses since reset


def test_trips_on_daily_drawdown() -> None:
    cb = make_cb(max_losses=99, max_drawdown=50.0)
    cb.on_fill(pnl_usd=-30.0)
    assert cb.ok()
    cb.on_fill(pnl_usd=-25.0)  # total=55 > 50
    assert not cb.ok()


def test_trips_on_feed_stale() -> None:
    cb = make_cb()
    cb.on_feed_stale()
    assert not cb.ok()


def test_trips_on_high_latency() -> None:
    cb = make_cb(max_latency_ms=100.0)
    cb.on_high_latency(150.0)
    assert not cb.ok()


def test_no_trip_on_low_latency() -> None:
    cb = make_cb(max_latency_ms=500.0)
    cb.on_high_latency(300.0)
    assert cb.ok()


def test_manual_reset() -> None:
    cb = make_cb(max_losses=1)
    cb.on_fill(pnl_usd=-1.0)
    assert not cb.ok()
    cb.reset()
    assert cb.ok()
    assert cb.state == CBState.CLOSED


def test_trip_only_once() -> None:
    """Second trip after already OPEN should not overwrite reason."""
    s = SqliteStore(":memory:")
    cb = make_cb(max_losses=1, max_drawdown=10.0, store=s)
    cb.on_fill(pnl_usd=-1.0)
    first_reason = cb.stats.trip_reason
    cb.on_fill(pnl_usd=-20.0)  # would trip on drawdown too
    assert cb.stats.trip_reason == first_reason  # reason unchanged


def test_persists_event_to_db() -> None:
    s = SqliteStore(":memory:")
    cb = make_cb(max_losses=1, store=s)
    cb.on_fill(pnl_usd=-1.0)
    event = s.get_last_circuit_breaker_event()
    assert event is not None
    assert "OPEN" in event["reason"]
