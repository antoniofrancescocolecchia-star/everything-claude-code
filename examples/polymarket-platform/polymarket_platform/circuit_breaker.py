from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.db.store import SqliteStore


class CBState(str, Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Tripped — all trading halted


@dataclass
class CBConfig:
    max_consecutive_losses: int = 5
    max_daily_drawdown_usd: float = 50.0
    max_decision_latency_ms: float = 500.0
    # Feed staleness is checked in the engine directly; CB trip is via on_feed_stale()


@dataclass
class CBStats:
    consecutive_losses: int = 0
    daily_drawdown_usd: float = 0.0
    daily_reset_ts: float = field(default_factory=time.time)
    trip_reason: str = ""
    state: CBState = CBState.CLOSED


class CircuitBreaker:
    """
    State machine: CLOSED → OPEN on any trip condition.
    Recovery is manual (reset via CLI) or automatic after a configurable cooldown
    (not implemented in MVP — manual reset only for safety).

    Trip conditions:
    1. N consecutive loss trades
    2. Daily drawdown exceeds threshold USD
    3. Feed staleness > configured limit (signalled by engine)
    4. Decision-to-order latency exceeds limit
    """

    def __init__(self, cfg: CBConfig, store: SqliteStore) -> None:
        self._cfg = cfg
        self._store = store
        self._log = logging.getLogger(self.__class__.__name__)
        self._stats = CBStats()

    @property
    def state(self) -> CBState:
        return self._stats.state

    @property
    def stats(self) -> CBStats:
        return self._stats

    def ok(self) -> bool:
        """Return True if trading is allowed (circuit is CLOSED)."""
        return self._stats.state == CBState.CLOSED

    def on_fill(self, *, pnl_usd: float) -> None:
        """Call after a confirmed fill with the realized PnL (negative = loss)."""
        self._maybe_reset_daily()
        if pnl_usd < 0:
            self._stats.consecutive_losses += 1
            self._stats.daily_drawdown_usd += abs(pnl_usd)
            if self._stats.consecutive_losses >= self._cfg.max_consecutive_losses:
                self._trip(f"consecutive losses: {self._stats.consecutive_losses}")
                return
            if self._stats.daily_drawdown_usd >= self._cfg.max_daily_drawdown_usd:
                self._trip(
                    f"daily drawdown ${self._stats.daily_drawdown_usd:.2f} "
                    f">= ${self._cfg.max_daily_drawdown_usd:.2f}"
                )
        else:
            self._stats.consecutive_losses = 0

    def on_feed_stale(self) -> None:
        """Call when the feed has not delivered a quote for too long."""
        self._trip("feed stale: no quote received within staleness window")

    def on_high_latency(self, latency_ms: float) -> None:
        """Call when decision→order latency exceeds threshold."""
        if latency_ms > self._cfg.max_decision_latency_ms:
            self._trip(
                f"decision latency {latency_ms:.0f}ms > {self._cfg.max_decision_latency_ms:.0f}ms"
            )

    def reset(self) -> None:
        """Manually reset the circuit breaker (e.g., via CLI)."""
        old_state = self._stats.state
        self._stats = CBStats()
        if old_state == CBState.OPEN:
            self._store.insert_circuit_breaker_event("RESET: manual")
            self._log.warning("circuit breaker RESET manually")

    def _trip(self, reason: str) -> None:
        if self._stats.state == CBState.OPEN:
            return  # Already open
        self._stats.state = CBState.OPEN
        self._stats.trip_reason = reason
        self._store.insert_circuit_breaker_event(f"OPEN: {reason}")
        self._log.error("CIRCUIT BREAKER OPEN: %s", reason)

    def _maybe_reset_daily(self) -> None:
        """Reset daily drawdown counter at midnight UTC."""
        now = time.time()
        elapsed = now - self._stats.daily_reset_ts
        if elapsed >= 86400:
            self._stats.daily_drawdown_usd = 0.0
            self._stats.daily_reset_ts = now
