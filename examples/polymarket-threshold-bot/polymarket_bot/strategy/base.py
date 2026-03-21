from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketSnapshot:
    best_bid: float
    best_ask: float
    position_shares: float


@dataclass(frozen=True)
class Decision:
    action: str  # BUY|SELL|HOLD
    reason: str


class Strategy:
    def decide(self, snap: MarketSnapshot) -> Decision:
        raise NotImplementedError
