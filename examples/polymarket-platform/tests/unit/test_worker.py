"""Tests for MarketWorker lifecycle."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_platform.db.store import SqliteStore
from polymarket_platform.scanner.worker import MarketWorker, WorkerState
from tests.conftest import make_settings

TOKEN = "worker-test-token-0x9999"


def make_worker(token_id: str = TOKEN) -> tuple[MarketWorker, SqliteStore]:
    cfg = make_settings()
    store = SqliteStore(":memory:")
    worker = MarketWorker(token_id=token_id, base_cfg=cfg, store=store)
    return worker, store


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_worker_initial_state_is_idle() -> None:
    worker, _ = make_worker()
    assert worker.state == WorkerState.IDLE
    assert not worker.is_running
    assert worker.started_ts is None
    assert worker.token_id == TOKEN


# ---------------------------------------------------------------------------
# Stop before start is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_before_start_is_noop() -> None:
    worker, _ = make_worker()
    # Should not raise; state stays IDLE
    await worker.stop("early_stop")
    assert worker.state == WorkerState.IDLE


# ---------------------------------------------------------------------------
# Start -> Running -> Stopped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_starts_and_stops() -> None:
    worker, _ = make_worker()

    stop_event_holder: list[asyncio.Event] = []

    async def mock_engine_run(stop: asyncio.Event | None = None) -> None:
        if stop is not None:
            stop_event_holder.append(stop)
            await stop.wait()

    mock_engine = MagicMock()
    mock_engine.run = mock_engine_run

    with patch("polymarket_platform.scanner.worker.build_from_settings", return_value=mock_engine):
        worker.start()
        assert worker.state == WorkerState.RUNNING
        assert worker.is_running
        assert worker.started_ts is not None

        # Let the event loop process the started task
        await asyncio.sleep(0)

        await worker.stop("test_requested")

    assert worker.state == WorkerState.STOPPED
    assert not worker.is_running
    assert worker.stop_reason == "test_requested"


# ---------------------------------------------------------------------------
# Double-stop is safe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_stop_is_safe() -> None:
    worker, _ = make_worker()

    async def mock_engine_run(stop: asyncio.Event | None = None) -> None:
        if stop is not None:
            await stop.wait()

    mock_engine = MagicMock()
    mock_engine.run = mock_engine_run

    with patch("polymarket_platform.scanner.worker.build_from_settings", return_value=mock_engine):
        worker.start()
        await asyncio.sleep(0)
        await worker.stop("first")
        # Second stop should be a no-op (state is STOPPED)
        await worker.stop("second")

    assert worker.state == WorkerState.STOPPED


# ---------------------------------------------------------------------------
# Engine error transitions to ERROR state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_error_transitions_to_error_state() -> None:
    worker, _ = make_worker()

    async def mock_engine_run(stop: asyncio.Event | None = None) -> None:
        raise RuntimeError("simulated engine crash")

    mock_engine = MagicMock()
    mock_engine.run = AsyncMock(side_effect=RuntimeError("simulated engine crash"))

    with patch("polymarket_platform.scanner.worker.build_from_settings", return_value=mock_engine):
        worker.start()
        # Wait for the engine task to fail
        await asyncio.sleep(0.05)

    assert worker.state == WorkerState.ERROR


# ---------------------------------------------------------------------------
# Start in STOPPED state (reuse)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_can_restart_after_stop() -> None:
    worker, _ = make_worker()

    async def mock_engine_run(stop: asyncio.Event | None = None) -> None:
        if stop is not None:
            await stop.wait()

    mock_engine = MagicMock()
    mock_engine.run = mock_engine_run

    with patch("polymarket_platform.scanner.worker.build_from_settings", return_value=mock_engine):
        worker.start()
        await asyncio.sleep(0)
        await worker.stop("first_stop")
        assert worker.state == WorkerState.STOPPED

        # Should be able to start again
        worker.start()
        assert worker.state == WorkerState.RUNNING
        await worker.stop("second_stop")

    assert worker.state == WorkerState.STOPPED


# ---------------------------------------------------------------------------
# Per-market capital is applied to worker config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_applies_per_market_capital() -> None:
    """The worker must set max_usd_spend = min(base.max_usd_spend, max_capital_per_market)."""
    cfg = make_settings(RISK_MAX_USD_SPEND="500", MAX_CAPITAL_PER_MARKET_USD="50")
    store = SqliteStore(":memory:")
    worker = MarketWorker(token_id=TOKEN, base_cfg=cfg, store=store)

    captured_cfgs: list = []

    async def mock_engine_run(stop: asyncio.Event | None = None) -> None:
        if stop is not None:
            await stop.wait()

    def mock_build(worker_cfg, _store):  # noqa: ANN001
        captured_cfgs.append(worker_cfg)
        eng = MagicMock()
        eng.run = mock_engine_run
        return eng

    with patch("polymarket_platform.scanner.worker.build_from_settings", side_effect=mock_build):
        worker.start()
        await asyncio.sleep(0)
        await worker.stop()

    assert len(captured_cfgs) == 1
    built_cfg = captured_cfgs[0]
    # Should be capped at MAX_CAPITAL_PER_MARKET_USD=50
    assert built_cfg.max_usd_spend == pytest.approx(50.0)
    # token_id should be overridden
    assert built_cfg.token_id == TOKEN
