from __future__ import annotations

from dataclasses import dataclass

from polymarket_bot.strategy.base import Decision, MarketSnapshot, Strategy


@dataclass(frozen=True)
class ThresholdParams:
    buy_threshold: float
    sell_threshold: float


class ThresholdStrategy(Strategy):
    def __init__(self, params: ThresholdParams) -> None:
        self._p = params

    def decide(self, snap: MarketSnapshot) -> Decision:
        if snap.best_ask <= self._p.buy_threshold:
            return Decision(
                "BUY",
                f"best_ask={snap.best_ask:.4f} <= buy_threshold={self._p.buy_threshold:.4f}",
            )
        if snap.best_bid >= self._p.sell_threshold and snap.position_shares > 0.0:
            return Decision(
                "SELL",
                (
                    f"best_bid={snap.best_bid:.4f} >= sell_threshold={self._p.sell_threshold:.4f}"
                    " and pos>0"
                ),
            )
        return Decision("HOLD", "no threshold crossed")
