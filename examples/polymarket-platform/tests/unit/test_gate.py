"""Unit tests for ProfitabilityGate."""
import pytest

from polymarket_platform.execution.gate import ProfitabilityGate
from polymarket_platform.strategy.base import Decision


def make_gate(fee_bps: float = 200.0, min_edge_bps: float = 50.0) -> ProfitabilityGate:
    return ProfitabilityGate(fee_taker_bps=fee_bps, min_edge_bps=min_edge_bps)


def decision(action: str, edge_bps: float) -> Decision:
    return Decision(action=action, reason="test", expected_edge_bps=edge_bps)


def test_hold_always_passes() -> None:
    gate = make_gate()
    ok, _ = gate.check(decision("HOLD", 0.0))
    assert ok


def test_passes_when_edge_exceeds_fee_plus_buffer() -> None:
    # fee=200, min=50 → need edge > 250
    gate = make_gate(fee_bps=200, min_edge_bps=50)
    ok, _ = gate.check(decision("BUY", 260.0))
    assert ok


def test_blocks_when_edge_below_threshold() -> None:
    gate = make_gate(fee_bps=200, min_edge_bps=50)
    ok, reason = gate.check(decision("BUY", 240.0))  # net = 40 < 50
    assert not ok
    assert "net_edge" in reason.lower()


def test_blocks_zero_edge() -> None:
    gate = make_gate(fee_bps=200, min_edge_bps=50)
    ok, _ = gate.check(decision("BUY", 0.0))
    assert not ok


def test_negative_edge_blocked() -> None:
    gate = make_gate()
    ok, _ = gate.check(decision("SELL", -10.0))
    assert not ok


def test_exact_boundary_passes() -> None:
    # net_edge = 250 - 200 = 50 = min_edge → gate checks `net < min` → 50 < 50 is False → passes
    gate = make_gate(fee_bps=200, min_edge_bps=50)
    ok, _ = gate.check(decision("BUY", 250.0))
    assert ok


def test_sell_action_checked_same_as_buy() -> None:
    gate = make_gate(fee_bps=200, min_edge_bps=50)
    ok, _ = gate.check(decision("SELL", 300.0))
    assert ok
    ok2, _ = gate.check(decision("SELL", 100.0))
    assert not ok2
