"""Unit tests for polymarket_platform.analyst.event_grouper."""
from __future__ import annotations

import time

from polymarket_platform.analyst.event_grouper import EventGroup, group_by_event
from polymarket_platform.scanner.catalog import MarketRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(
    token_id: str = "tok-1",
    event_slug: str | None = None,
    score: float = 0.5,
    question: str = "Will X?",
) -> MarketRecord:
    return MarketRecord(
        token_id=token_id,
        condition_id="cond",
        slug="slug",
        event_slug=event_slug,
        question=question,
        outcome="YES",
        outcome_price=0.5,
        end_date_ts=time.time() + 86400,
        days_to_expiry=7.0,
        liquidity=1000.0,
        volume_24h=200.0,
        score=score,
        all_token_ids=[token_id],
        all_outcome_prices=[0.5],
    )


# ---------------------------------------------------------------------------
# group_by_event
# ---------------------------------------------------------------------------


def test_group_by_event_single_isolated():
    market = make_record(token_id="tok-1", event_slug=None)
    groups = group_by_event([market])

    assert len(groups) == 1
    grp = groups[0]
    assert grp.event_slug is None
    assert grp.is_multi is False
    assert len(grp.markets) == 1


def test_group_by_event_multi_same_event():
    m1 = make_record(token_id="tok-1", event_slug="event-a")
    m2 = make_record(token_id="tok-2", event_slug="event-a")
    groups = group_by_event([m1, m2])

    assert len(groups) == 1
    grp = groups[0]
    assert grp.event_slug == "event-a"
    assert grp.is_multi is True
    assert len(grp.markets) == 2


def test_group_by_event_different_events():
    m1 = make_record(token_id="tok-1", event_slug="event-a")
    m2 = make_record(token_id="tok-2", event_slug="event-b")
    groups = group_by_event([m1, m2])

    assert len(groups) == 2
    slugs = {g.event_slug for g in groups}
    assert slugs == {"event-a", "event-b"}


def test_group_by_event_mixed():
    # Two markets in the same event + one isolated market.
    # Multi-market group should appear first.
    m1 = make_record(token_id="tok-1", event_slug="event-a", score=0.6)
    m2 = make_record(token_id="tok-2", event_slug="event-a", score=0.5)
    m3 = make_record(token_id="tok-3", event_slug=None, score=0.9)
    groups = group_by_event([m1, m2, m3])

    assert len(groups) == 2
    # Multi-market group first
    assert groups[0].is_multi is True
    assert groups[0].event_slug == "event-a"
    # Isolated market second
    assert groups[1].is_multi is False
    assert groups[1].event_slug is None


# ---------------------------------------------------------------------------
# EventGroup properties
# ---------------------------------------------------------------------------


def test_event_group_search_count_always_1():
    # search_count is always 1 regardless of how many markets are in the group
    m1 = make_record(token_id="tok-1", event_slug="event-x")
    m2 = make_record(token_id="tok-2", event_slug="event-x")
    m3 = make_record(token_id="tok-3", event_slug="event-x")
    grp = EventGroup(event_slug="event-x", markets=[m1, m2, m3])
    assert grp.search_count == 1


def test_primary_question_first_market():
    m1 = make_record(token_id="tok-1", question="Will Alpha happen?", score=0.8)
    m2 = make_record(token_id="tok-2", question="Will Beta happen?", score=0.4)
    # Test EventGroup directly with known order
    grp = EventGroup(event_slug=None, markets=[m1, m2])
    assert grp.primary_question == "Will Alpha happen?"
