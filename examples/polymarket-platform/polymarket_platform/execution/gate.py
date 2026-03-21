from __future__ import annotations

from polymarket_platform.strategy.base import Decision


class ProfitabilityGate:
    """
    Blocks trades where the expected gross edge does not exceed fees + buffer.

    Formula:
        net_edge_bps = decision.expected_edge_bps - fee_taker_bps
        passes       = net_edge_bps >= min_edge_bps

    For a threshold strategy:
        BUY  edge_bps = (buy_threshold - ask) / ask * 10_000
        SELL edge_bps = (bid - sell_threshold) / sell_threshold * 10_000

    With typical taker fee ~200 bps and min_edge_bps=50, a BUY at ask=0.44
    with buy_threshold=0.45 produces edge≈227 bps → net 27 bps — marginal.
    Increase thresholds or buffer to ensure positive EV.
    """

    def __init__(self, *, fee_taker_bps: float, min_edge_bps: float) -> None:
        self._fee_bps = fee_taker_bps
        self._min_edge_bps = min_edge_bps

    def check(self, decision: Decision) -> tuple[bool, str]:
        if not decision.is_actionable():
            return True, "hold — gate not applicable"

        net_edge_bps = decision.expected_edge_bps - self._fee_bps
        if net_edge_bps < self._min_edge_bps:
            return False, (
                f"net_edge={net_edge_bps:.1f}bps < min={self._min_edge_bps:.1f}bps "
                f"(gross={decision.expected_edge_bps:.1f}bps - fee={self._fee_bps:.1f}bps)"
            )
        return True, f"net_edge={net_edge_bps:.1f}bps >= min={self._min_edge_bps:.1f}bps"
