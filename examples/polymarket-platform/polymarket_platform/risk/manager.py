from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.db.store import RiskState, SqliteStore


@dataclass(frozen=True)
class RiskLimits:
    max_position_shares: float
    max_usd_spend: float          # cumulative across restarts (persisted)
    min_seconds_between_orders: float
    max_open_orders: int


class RiskManager:
    """
    Validates orders against configurable limits.

    State (usd_spent, last_order_ts) is persisted in SQLite and loaded on
    __init__ so limits survive process restarts.
    """

    def __init__(self, limits: RiskLimits, store: "SqliteStore") -> None:
        self._limits = limits
        self._store = store
        # Load persisted state; falls back to zero if no row exists
        self._state: RiskState = store.load_risk_state()

    @property
    def usd_spent(self) -> float:
        return self._state.usd_spent

    @property
    def last_order_ts(self) -> float:
        return self._state.last_order_ts

    def allow(
        self,
        *,
        action: str,
        position_shares: float,
        best_ask: float,
        buy_amount_usd: float,
        open_orders_count: int,
    ) -> tuple[bool, str]:
        if action == "HOLD":
            return True, "ok"

        if open_orders_count >= self._limits.max_open_orders:
            return False, f"too many open orders ({open_orders_count})"

        elapsed = time.time() - self._state.last_order_ts
        if elapsed < self._limits.min_seconds_between_orders:
            remaining = self._limits.min_seconds_between_orders - elapsed
            return False, f"cooldown ({remaining:.1f}s remaining)"

        if action == "BUY":
            new_usd = self._state.usd_spent + buy_amount_usd
            if new_usd > self._limits.max_usd_spend:
                return False, (
                    f"max usd spend exceeded "
                    f"(spent={self._state.usd_spent:.2f}, limit={self._limits.max_usd_spend:.2f})"
                )
            expected_shares = buy_amount_usd / max(best_ask, 1e-9)
            new_shares = position_shares + expected_shares
            if new_shares > self._limits.max_position_shares:
                return False, (
                    f"max position shares exceeded "
                    f"(current={position_shares:.2f}, expected+={expected_shares:.2f}, "
                    f"limit={self._limits.max_position_shares:.2f})"
                )

        return True, "ok"

    def on_order_sent(self, *, action: str, buy_amount_usd: float) -> None:
        """Call after a successful order submission to update and persist state."""
        from polymarket_platform.db.store import RiskState

        new_usd = self._state.usd_spent + buy_amount_usd if action == "BUY" else self._state.usd_spent
        self._state = RiskState(usd_spent=new_usd, last_order_ts=time.time())
        self._store.save_risk_state(self._state)
