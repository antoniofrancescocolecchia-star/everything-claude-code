from __future__ import annotations

from dataclasses import dataclass

from polymarket_platform.strategy.base import Decision, MarketSnapshot, StrategyPlugin


@dataclass(frozen=True)
class ThresholdParams:
    buy_threshold: float   # our estimated fair value for BUY entry
    sell_threshold: float  # our estimated fair value for SELL exit


class ThresholdStrategy(StrategyPlugin):
    """
    Default threshold strategy.

    BUY when ask <= buy_threshold.
      edge_bps = (buy_threshold - ask) / ask * 10_000

    SELL when bid >= sell_threshold AND we hold shares.
      edge_bps = (bid - sell_threshold) / sell_threshold * 10_000

    Both edges represent the gross advantage over our "fair value" estimate
    before deducting fees. The ProfitabilityGate will check that
    edge_bps > fee_taker_bps + buffer.

    Hysteresis: the SELL threshold is above the BUY threshold by design,
    creating a dead-band that prevents oscillating in/out around the mid.
    """

    def __init__(self, params: ThresholdParams) -> None:
        self._p = params

    @property
    def name(self) -> str:
        return "threshold"

    def decide(self, snap: MarketSnapshot) -> Decision:
        if snap.best_ask <= self._p.buy_threshold:
            edge_bps = (self._p.buy_threshold - snap.best_ask) / max(snap.best_ask, 1e-9) * 10_000
            return Decision(
                action="BUY",
                reason=(
                    f"best_ask={snap.best_ask:.4f} <= buy_threshold={self._p.buy_threshold:.4f}"
                ),
                expected_edge_bps=round(edge_bps, 2),
                metadata={
                    "buy_threshold": self._p.buy_threshold,
                    "best_ask": snap.best_ask,
                },
            )

        if snap.best_bid >= self._p.sell_threshold and snap.position_shares > 0.0:
            edge_bps = (
                (snap.best_bid - self._p.sell_threshold)
                / max(self._p.sell_threshold, 1e-9)
                * 10_000
            )
            return Decision(
                action="SELL",
                reason=(
                    f"best_bid={snap.best_bid:.4f} >= sell_threshold={self._p.sell_threshold:.4f}"
                    f" and pos={snap.position_shares:.2f}"
                ),
                expected_edge_bps=round(edge_bps, 2),
                metadata={
                    "sell_threshold": self._p.sell_threshold,
                    "best_bid": snap.best_bid,
                    "position_shares": snap.position_shares,
                },
            )

        return Decision(
            action="HOLD",
            reason="no threshold crossed",
            expected_edge_bps=0.0,
        )
