"""Tests for polymarket_platform/analyst/cost_tracker.py."""
from __future__ import annotations

from unittest.mock import MagicMock

from polymarket_platform.analyst.cost_tracker import CostTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cfg():
    cfg = MagicMock()
    cfg.analyst_max_cost_per_hour_usd = 2.0
    cfg.analyst_max_cost_per_day_usd = 10.0
    cfg.analyst_max_input_tokens_per_hour = 100_000
    cfg.analyst_max_output_tokens_per_hour = 20_000
    cfg.analyst_max_429s_per_hour = 5
    return cfg


def make_tracker():
    store = MagicMock()
    cfg = make_cfg()
    return CostTracker(cfg=cfg, store=store), store, cfg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_initial_state_has_headroom():
    tracker, _, _ = make_tracker()
    assert tracker.has_headroom() is True


def test_hourly_cost_accumulates():
    tracker, _, _ = make_tracker()
    tracker.record(
        prediction_id="p1",
        search_usd=0.01,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-6",
    )
    tracker.record(
        prediction_id="p2",
        search_usd=0.02,
        input_tokens=200,
        output_tokens=100,
        model="claude-sonnet-4-6",
    )
    hourly = tracker.hourly_cost()
    # search_usd contributes 0.03 total; token costs are small but positive
    assert hourly > 0.03
    # Should be less than some reasonable upper bound
    assert hourly < 0.10


def test_daily_cost_accumulates():
    tracker, _, _ = make_tracker()
    tracker.record(
        prediction_id="p1",
        search_usd=0.05,
        input_tokens=500,
        output_tokens=200,
        model="claude-sonnet-4-6",
    )
    daily = tracker.daily_cost()
    assert daily >= 0.05


def test_has_headroom_false_when_cost_exceeded():
    tracker, _, cfg = make_tracker()
    # hourly cap is 2.0 USD; record enough search cost to exceed it
    for i in range(5):
        tracker.record(
            prediction_id=f"p{i}",
            search_usd=0.50,  # 5 * 0.50 = 2.50 > 2.0
            input_tokens=0,
            output_tokens=0,
            model="claude-sonnet-4-6",
        )
    assert tracker.has_headroom() is False


def test_has_headroom_false_when_429s_exceeded():
    tracker, _, cfg = make_tracker()
    # cap is 5; call record_429 exactly 5 times
    for _ in range(5):
        tracker.record_429()
    assert tracker.has_headroom() is False


def test_has_headroom_false_when_input_tokens_exceeded():
    tracker, _, cfg = make_tracker()
    # cap is 100_000 input tokens per hour; record 110_000 total
    tracker.record(
        prediction_id="p1",
        search_usd=0.0,
        input_tokens=110_000,
        output_tokens=0,
        model="claude-sonnet-4-6",
    )
    assert tracker.has_headroom() is False


def test_record_calls_store_insert():
    tracker, store, _ = make_tracker()
    tracker.record(
        prediction_id="p1",
        search_usd=0.01,
        input_tokens=100,
        output_tokens=50,
        model="claude-sonnet-4-6",
    )
    store.insert_analyst_costs.assert_called_once()
    call_kwargs = store.insert_analyst_costs.call_args.kwargs
    assert call_kwargs["prediction_id"] == "p1"
    assert call_kwargs["search_usd"] == 0.01
    assert call_kwargs["input_tokens"] == 100
    assert call_kwargs["output_tokens"] == 50
