"""
MarketWorker: manages one TradingEngine instance for one token_id.

The Orchestrator creates workers, assigns token_ids, and controls their lifecycle.
Each worker is fully independent: its own engine, feed, clob executor, risk manager.
The SqliteStore is shared (it is thread-safe internally).

Lifecycle:
    IDLE -> start() -> RUNNING -> stop() -> STOPPING -> STOPPED
                                             (task error) -> ERROR
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from enum import StrEnum

from polymarket_platform.config import Settings
from polymarket_platform.db.store import SqliteStore
from polymarket_platform.engine import build_from_settings

log = logging.getLogger(__name__)


class WorkerState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class MarketWorker:
    """
    Wraps one TradingEngine for one token_id.

    The worker builds its own TradingEngine using a copy of base_cfg with
    token_id and max_usd_spend overridden for per-market capital isolation.
    The shared SqliteStore records quotes/decisions/orders alongside scanner tables.
    """

    def __init__(
        self,
        token_id: str,
        base_cfg: Settings,
        store: SqliteStore,
    ) -> None:
        self._token_id = token_id
        self._base_cfg = base_cfg
        self._store = store
        self._state = WorkerState.IDLE
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._started_ts: float | None = None
        self.stop_reason: str = ""

    # ------------------------------------------------------------------
    # Properties

    @property
    def token_id(self) -> str:
        return self._token_id

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == WorkerState.RUNNING

    @property
    def started_ts(self) -> float | None:
        return self._started_ts

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self) -> None:
        """
        Schedule the engine asyncio task.
        Must be called from a running event loop (i.e. inside an async context).
        Safe to call only in IDLE, STOPPED, or ERROR state.
        """
        if self._state not in (WorkerState.IDLE, WorkerState.STOPPED, WorkerState.ERROR):
            log.warning(
                "MarketWorker.start() called in unexpected state %s for %s",
                self._state,
                self._token_id[:16],
            )
            return

        self._stop_event = asyncio.Event()
        self._state = WorkerState.RUNNING
        self._started_ts = time.time()
        self._task = asyncio.create_task(
            self._run_engine(),
            name=f"worker-{self._token_id[:16]}",
        )
        log.info("MarketWorker started token=%s", self._token_id[:20])

    async def stop(self, reason: str = "requested") -> None:
        """
        Signal the engine to stop and wait for the task to finish (up to 10s).
        Safe to call from any state; no-op if not running.
        """
        if self._state != WorkerState.RUNNING:
            return
        self._state = WorkerState.STOPPING
        self.stop_reason = reason
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=10.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
        self._state = WorkerState.STOPPED
        log.info("MarketWorker stopped token=%s reason=%s", self._token_id[:20], reason)

    # ------------------------------------------------------------------
    # Internal

    async def _run_engine(self) -> None:
        """Build and run the TradingEngine for this token."""
        per_market_cap = min(
            self._base_cfg.max_usd_spend,
            self._base_cfg.max_capital_per_market_usd,
        )
        worker_cfg = self._base_cfg.model_copy(
            update={"token_id": self._token_id, "max_usd_spend": per_market_cap}
        )
        try:
            engine = build_from_settings(worker_cfg, self._store)
            assert self._stop_event is not None
            await engine.run(self._stop_event)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error(
                "MarketWorker engine error token=%s: %s",
                self._token_id[:20],
                exc,
                exc_info=True,
            )
            self._state = WorkerState.ERROR
            return
        finally:
            if self._state not in (WorkerState.ERROR,):
                self._state = WorkerState.STOPPED
