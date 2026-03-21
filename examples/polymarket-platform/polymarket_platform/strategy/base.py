from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MarketSnapshot:
    """Everything a strategy needs to make a decision."""

    token_id: str
    best_bid: float
    best_ask: float
    position_shares: float
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Decision:
    """
    Normalised strategy output sufficient for execution, risk checks,
    auditing, and replay.

    Fields:
        action:             BUY | SELL | HOLD
        reason:             Human-readable explanation for logging/audit
        expected_edge_bps:  Estimated gross edge in basis points; used by
                            ProfitabilityGate. 0 for HOLD.
        metadata:           Extensible dict for plugin-specific context.
        ts:                 Decision timestamp.
    """

    action: str
    reason: str
    expected_edge_bps: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def is_actionable(self) -> bool:
        return self.action in ("BUY", "SELL")


class StrategyPlugin(ABC):
    """
    Interface for trading strategy plugins.

    Implementations receive a MarketSnapshot and return a Decision.
    The engine will call decide() on every incoming Quote after position refresh.

    To implement a custom strategy:
        class MyStrategy(StrategyPlugin):
            @property
            def name(self) -> str:
                return "my-strategy"

            def decide(self, snap: MarketSnapshot) -> Decision:
                ...
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def decide(self, snap: MarketSnapshot) -> Decision: ...
