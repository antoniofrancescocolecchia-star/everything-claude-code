from __future__ import annotations

from dataclasses import dataclass

from polymarket_bot.utils import Cooldown


@dataclass(frozen=True)
class RiskLimits:
    max_position_shares: float
    max_usd_spend: float
    min_seconds_between_orders: float
    max_open_orders: int


@dataclass(frozen=True)
class RiskState:
    usd_spent: float = 0.0


class RiskManager:
    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._cooldown = Cooldown()
        self._state = RiskState()

    @property
    def state(self) -> RiskState:
        return self._state

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

        if not self._cooldown.ok(self._limits.min_seconds_between_orders):
            return False, "cooldown"

        if action == "BUY":
            if (self._state.usd_spent + buy_amount_usd) > self._limits.max_usd_spend:
                return False, "max usd spend exceeded"

            expected_shares = buy_amount_usd / max(best_ask, 1e-9)
            if (position_shares + expected_shares) > self._limits.max_position_shares:
                return False, "max position shares exceeded"

        return True, "ok"

    def on_order_sent(self, *, action: str, buy_amount_usd: float) -> None:
        self._cooldown.hit()
        if action == "BUY":
            self._state = RiskState(usd_spent=self._state.usd_spent + buy_amount_usd)
