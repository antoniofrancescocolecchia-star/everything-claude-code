"""
Integration tests: full decision pipeline with all external dependencies mocked.

Tests verify that quotes injected into the queue flow correctly through
strategy → gate → risk → order_manager and produce the expected DB records.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from polymarket_platform.circuit_breaker import CBConfig, CircuitBreaker
from polymarket_platform.db.store import SqliteStore
from polymarket_platform.engine import TradingEngine
from polymarket_platform.execution.gate import ProfitabilityGate
from polymarket_platform.execution.order_manager import OrderManager
from polymarket_platform.feed.base import FeedSource, Quote
from polymarket_platform.risk.manager import RiskLimits, RiskManager
from polymarket_platform.strategy.threshold import ThresholdParams, ThresholdStrategy
from tests.conftest import MockClobExecutor, MockDataApiClient, TOKEN_ID, make_settings


def build_engine(
    cfg_overrides: dict | None = None,
    *,
    clob: MockClobExecutor | None = None,
    data_api: MockDataApiClient | None = None,
    store: SqliteStore | None = None,
) -> tuple[TradingEngine, asyncio.Queue[Quote], SqliteStore]:
    """Build a fully-wired TradingEngine with mocked external dependencies."""
    overrides = cfg_overrides or {}
    cfg = make_settings(**overrides)
    s = store or SqliteStore(":memory:")
    mock_clob = clob or MockClobExecutor()
    mock_data = data_api or MockDataApiClient()

    strategy = ThresholdStrategy(
        ThresholdParams(buy_threshold=cfg.buy_threshold, sell_threshold=cfg.sell_threshold)
    )
    risk = RiskManager(
        RiskLimits(
            max_position_shares=cfg.max_position_shares,
            max_usd_spend=cfg.max_usd_spend,
            min_seconds_between_orders=cfg.min_seconds_between_orders,
            max_open_orders=cfg.max_open_orders,
        ),
        s,
    )
    gate = ProfitabilityGate(fee_taker_bps=cfg.fee_taker_bps, min_edge_bps=cfg.min_expected_edge_bps)
    cb = CircuitBreaker(
        CBConfig(
            max_consecutive_losses=cfg.cb_max_consecutive_losses,
            max_daily_drawdown_usd=cfg.cb_max_daily_drawdown_usd,
            max_decision_latency_ms=cfg.cb_max_decision_latency_ms,
        ),
        s,
    )
    orders = OrderManager(clob=mock_clob, store=s, dry_run=cfg.dry_run)  # type: ignore[arg-type]
    queue: asyncio.Queue[Quote] = asyncio.Queue()

    engine = TradingEngine(
        cfg=cfg,
        clob=mock_clob,  # type: ignore[arg-type]
        data_api=mock_data,
        store=s,
        strategy=strategy,
        risk=risk,
        gate=gate,
        cb=cb,
        orders=orders,
        quote_queue=queue,
    )
    return engine, queue, s


async def run_with_quotes(
    quotes: list[Quote],
    cfg_overrides: dict | None = None,
    **kwargs: Any,
) -> SqliteStore:
    """Run the engine for the given list of quotes then stop."""
    engine, queue, store = build_engine(cfg_overrides, **kwargs)
    stop = asyncio.Event()

    async def feed_quotes() -> None:
        for q in quotes:
            await queue.put(q)
        # Give engine time to process
        await asyncio.sleep(0.1)
        stop.set()

    await asyncio.gather(
        engine.run(stop),
        feed_quotes(),
    )
    return store


def q(bid: float, ask: float) -> Quote:
    return Quote(
        token_id=TOKEN_ID, best_bid=bid, best_ask=ask, ts=time.time(), source=FeedSource.WS
    )


# ── Basic decision recording ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hold_decision_recorded() -> None:
    """A quote in the dead-band produces a HOLD decision."""
    store = await run_with_quotes([q(bid=0.49, ask=0.51)])
    decisions = store.get_decisions(limit=5)
    assert len(decisions) >= 1
    assert decisions[0]["action"] == "HOLD"
    assert store.get_orders(limit=5) == []  # no orders for HOLD


# ── BUY signal ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_signal_produces_dry_run_order() -> None:
    """ask <= buy_threshold with sufficient edge creates a DRY_RUN order."""
    # fee=200 bps, min_edge=50 bps
    # edge = (0.45 - 0.40) / 0.40 * 10000 = 1250 bps >> 250
    store = await run_with_quotes([q(bid=0.38, ask=0.40)])
    orders = store.get_orders(limit=5)
    assert len(orders) >= 1
    assert orders[0]["status"] == "DRY_RUN"
    assert orders[0]["side"] == "BUY"


@pytest.mark.asyncio
async def test_buy_gate_blocks_low_edge() -> None:
    """Edge just above threshold but below fee + buffer is blocked by gate."""
    # buy_threshold=0.45, ask=0.449 → edge≈22 bps << fee 200+50=250
    store = await run_with_quotes(
        [q(bid=0.44, ask=0.449)],
        # Override so thresholds are very close to current price
        {"BUY_THRESHOLD": "0.45", "MIN_EXPECTED_EDGE_BPS": "200"},
    )
    decisions = store.get_decisions(limit=5)
    buy_decisions = [d for d in decisions if d["action"] == "BUY"]
    assert all(d["gate_blocked"] for d in buy_decisions)
    assert store.get_orders(limit=5) == []


# ── SELL signal ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sell_requires_position() -> None:
    """SELL signal is HOLD when no position is held."""
    store = await run_with_quotes([q(bid=0.60, ask=0.62)])
    decisions = store.get_decisions(limit=5)
    # bid=0.60 >= sell_threshold=0.55 but no position → HOLD
    assert all(d["action"] == "HOLD" for d in decisions)


@pytest.mark.asyncio
async def test_sell_with_position_creates_order() -> None:
    """SELL signal creates DRY_RUN order when position is cached."""
    from polymarket_platform.market.data_api import Position

    pos = [Position(asset=TOKEN_ID, size=10.0, avg_price=0.40, title=None, outcome=None)]
    mock_data = MockDataApiClient(positions=pos)

    engine, queue, store = build_engine(data_api=mock_data)
    stop = asyncio.Event()

    # Pre-populate position cache by injecting into state directly
    engine._state.position_cache[TOKEN_ID] = 10.0

    async def feed() -> None:
        await queue.put(q(bid=0.60, ask=0.62))  # bid >= sell_threshold=0.55
        await asyncio.sleep(0.1)
        stop.set()

    await asyncio.gather(engine.run(stop), feed())

    orders = store.get_orders(limit=5)
    assert any(o["side"] == "SELL" for o in orders)


# ── Risk blocking ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_blocks_when_max_usd_exceeded() -> None:
    """Risk manager blocks BUY when max_usd_spend would be exceeded."""
    store = await run_with_quotes(
        [q(bid=0.38, ask=0.40)],
        {
            "RISK_MAX_USD_SPEND": "5",       # $5 limit
            "STRAT_BUY_AMOUNT_USD": "10",    # trying to spend $10
        },
    )
    decisions = store.get_decisions(limit=5)
    buy_decisions = [d for d in decisions if d["action"] == "BUY"]
    assert all(d["risk_blocked"] for d in buy_decisions)
    assert store.get_orders(limit=5) == []


# ── Deduplication ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deduplication_prevents_repeat_orders() -> None:
    """Two identical buy quotes within the dedupe window → only one order."""
    mock_clob = MockClobExecutor()
    store = await run_with_quotes(
        [q(bid=0.38, ask=0.40), q(bid=0.38, ask=0.40)],
        clob=mock_clob,
    )
    orders = store.get_orders(limit=10)
    buy_orders = [o for o in orders if o["side"] == "BUY"]
    assert len(buy_orders) == 1


# ── Circuit breaker ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_open_blocks_all_trades() -> None:
    """When CB is tripped, quotes are consumed but no orders are placed."""
    engine, queue, store = build_engine()
    stop = asyncio.Event()

    # Trip the circuit breaker before running
    engine._cb._stats.state = __import__(
        "polymarket_platform.circuit_breaker", fromlist=["CBState"]
    ).CBState.OPEN

    async def feed() -> None:
        await queue.put(q(bid=0.38, ask=0.40))  # would be BUY
        await asyncio.sleep(0.1)
        stop.set()

    await asyncio.gather(engine.run(stop), feed())

    assert store.get_orders(limit=5) == []


# ── Multiple quotes ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_quotes_all_decisions_recorded() -> None:
    """All quotes should produce a decision record."""
    quotes = [
        q(bid=0.40, ask=0.50),  # HOLD
        q(bid=0.38, ask=0.40),  # BUY (large edge)
        q(bid=0.48, ask=0.51),  # HOLD
    ]
    store = await run_with_quotes(quotes)
    decisions = store.get_decisions(limit=10)
    assert len(decisions) == len(quotes)
