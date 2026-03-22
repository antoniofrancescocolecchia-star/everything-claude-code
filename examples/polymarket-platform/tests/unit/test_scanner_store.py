"""Tests for scanner-specific SqliteStore methods."""
from __future__ import annotations

import time

import pytest

from polymarket_platform.db.store import SqliteStore
from polymarket_platform.scanner.catalog import MarketRecord
from polymarket_platform.scanner.clob_probe import ClobQuote
from polymarket_platform.scanner.detector import OpportunityRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store() -> SqliteStore:
    return SqliteStore(":memory:")


def make_record(
    token_id: str = "tok-1",
    condition_id: str = "cond-1",
    slug: str = "test",
    score: float = 0.75,
) -> MarketRecord:
    return MarketRecord(
        token_id=token_id,
        condition_id=condition_id,
        slug=slug,
        event_slug=None,
        question="Test?",
        outcome="YES",
        outcome_price=0.45,
        end_date_ts=time.time() + 86_400 * 10,
        days_to_expiry=10.0,
        liquidity=5000.0,
        volume_24h=1000.0,
        score=score,
        all_token_ids=[token_id],
        all_outcome_prices=[0.45],
    )


def make_quote(
    token_id: str = "tok-1", midpoint: float = 0.45, spread_bps: float = 120.0
) -> ClobQuote:
    return ClobQuote(token_id=token_id, midpoint=midpoint, spread_bps=spread_bps)


def make_opportunity(token_id: str = "tok-1") -> OpportunityRecord:
    return OpportunityRecord(
        token_id=token_id,
        slug="test",
        event_slug=None,
        outcome="YES",
        side="BUY",
        confidence=0.65,
        expected_edge_bps=150.0,
        reason="spread_anomaly spread=300bps avg=100bps",
        midpoint=0.45,
        spread_bps=300.0,
        ts=time.time(),
    )


# ---------------------------------------------------------------------------
# markets table
# ---------------------------------------------------------------------------


def test_upsert_markets_inserts() -> None:
    store = make_store()
    rec = make_record()
    store.upsert_markets([rec])
    catalog = store.get_market_catalog()
    assert len(catalog) == 1
    assert catalog[0]["token_id"] == "tok-1"
    assert catalog[0]["score"] == pytest.approx(0.75)


def test_upsert_markets_updates_score() -> None:
    store = make_store()
    rec = make_record(score=0.5)
    store.upsert_markets([rec])

    rec_updated = make_record(score=0.9)
    store.upsert_markets([rec_updated])

    catalog = store.get_market_catalog()
    assert len(catalog) == 1
    assert catalog[0]["score"] == pytest.approx(0.9)


def test_upsert_markets_multiple() -> None:
    store = make_store()
    recs = [make_record(token_id=f"tok-{i}", condition_id=f"cond-{i}") for i in range(5)]
    store.upsert_markets(recs)
    catalog = store.get_market_catalog(limit=10)
    assert len(catalog) == 5


def test_get_market_catalog_sorted_by_score() -> None:
    store = make_store()
    store.upsert_markets([
        make_record(token_id="low",  condition_id="c1", score=0.3),
        make_record(token_id="high", condition_id="c2", score=0.9),
        make_record(token_id="mid",  condition_id="c3", score=0.6),
    ])
    catalog = store.get_market_catalog()
    scores = [r["score"] for r in catalog]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# market_snapshots table
# ---------------------------------------------------------------------------


def test_insert_market_snapshots() -> None:
    store = make_store()
    rec = make_record()
    quote = make_quote()
    store.upsert_markets([rec])
    store.insert_market_snapshots([rec], {"tok-1": quote})

    # Use store internal query to verify
    with store._lock:
        rows = store._conn.execute("SELECT * FROM market_snapshots").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["token_id"] == "tok-1"
    assert row["midpoint"] == pytest.approx(0.45)
    assert row["spread_bps"] == pytest.approx(120.0)


def test_get_recent_midpoints_returns_correct_tokens() -> None:
    store = make_store()
    rec_a = make_record(token_id="tok-a", condition_id="ca")
    rec_b = make_record(token_id="tok-b", condition_id="cb")
    quote_a = make_quote(token_id="tok-a", midpoint=0.40)
    quote_b = make_quote(token_id="tok-b", midpoint=0.60)
    store.upsert_markets([rec_a, rec_b])
    store.insert_market_snapshots([rec_a, rec_b], {"tok-a": quote_a, "tok-b": quote_b})

    since = time.time() - 10.0
    mids = store.get_recent_midpoints(["tok-a", "tok-b"], since_ts=since)
    assert "tok-a" in mids
    assert "tok-b" in mids
    assert mids["tok-a"] == [pytest.approx(0.40)]
    assert mids["tok-b"] == [pytest.approx(0.60)]


def test_get_recent_midpoints_respects_since_ts() -> None:
    store = make_store()
    rec = make_record()
    quote = make_quote(midpoint=0.45)
    store.upsert_markets([rec])
    store.insert_market_snapshots([rec], {"tok-1": quote})

    # since_ts in the future => no results
    mids = store.get_recent_midpoints(["tok-1"], since_ts=time.time() + 9999.0)
    assert mids.get("tok-1", []) == []


def test_get_recent_spreads() -> None:
    store = make_store()
    rec = make_record()
    quote = make_quote(spread_bps=200.0)
    store.upsert_markets([rec])
    store.insert_market_snapshots([rec], {"tok-1": quote})

    spreads = store.get_recent_spreads(["tok-1"], since_ts=time.time() - 10.0)
    assert "tok-1" in spreads
    assert spreads["tok-1"] == [pytest.approx(200.0)]


def test_get_recent_midpoints_empty_list() -> None:
    store = make_store()
    result = store.get_recent_midpoints([], since_ts=0.0)
    assert result == {}


# ---------------------------------------------------------------------------
# opportunities table
# ---------------------------------------------------------------------------


def test_insert_opportunities() -> None:
    store = make_store()
    opp = make_opportunity()
    store.insert_opportunities([opp])
    recent = store.get_recent_opportunities(limit=5)
    assert len(recent) == 1
    assert recent[0]["token_id"] == "tok-1"
    assert recent[0]["side"] == "BUY"
    assert recent[0]["confidence"] == pytest.approx(0.65)
    assert recent[0]["expected_edge_bps"] == pytest.approx(150.0)


def test_insert_multiple_opportunities() -> None:
    store = make_store()
    opps = [make_opportunity(token_id=f"tok-{i}") for i in range(5)]
    store.insert_opportunities(opps)
    recent = store.get_recent_opportunities(limit=10)
    assert len(recent) == 5


def test_get_recent_opportunities_sorted_desc() -> None:
    store = make_store()
    for _ in range(3):
        store.insert_opportunities([make_opportunity()])
    recent = store.get_recent_opportunities()
    ts_vals = [r["ts"] for r in recent]
    assert ts_vals == sorted(ts_vals, reverse=True)


# ---------------------------------------------------------------------------
# tracked_positions table
# ---------------------------------------------------------------------------


def test_upsert_tracked_position_inserts() -> None:
    store = make_store()
    store.upsert_tracked_position(token_id="tok-x", slug="my-market", capital_usd=25.0)
    positions = store.get_active_tracked_positions()
    assert len(positions) == 1
    assert positions[0]["token_id"] == "tok-x"
    assert positions[0]["capital_usd"] == pytest.approx(25.0)
    assert positions[0]["status"] == "active"


def test_upsert_tracked_position_idempotent() -> None:
    store = make_store()
    store.upsert_tracked_position(token_id="tok-x", slug="s", capital_usd=10.0)
    store.upsert_tracked_position(token_id="tok-x", slug="s", capital_usd=20.0)
    positions = store.get_active_tracked_positions()
    assert len(positions) == 1
    assert positions[0]["capital_usd"] == pytest.approx(20.0)


def test_mark_tracked_position_stopped() -> None:
    store = make_store()
    store.upsert_tracked_position(token_id="tok-x", slug="s", capital_usd=10.0)
    store.mark_tracked_position_stopped("tok-x", reason="test_reason")

    positions = store.get_active_tracked_positions()
    assert positions == []

    with store._lock:
        row = store._conn.execute(
            "SELECT status, stop_reason FROM tracked_positions WHERE token_id='tok-x'"
        ).fetchone()
    assert dict(row)["status"] == "stopped"
    assert dict(row)["stop_reason"] == "test_reason"


def test_upsert_reactivates_stopped_position() -> None:
    store = make_store()
    store.upsert_tracked_position(token_id="tok-y", slug="s", capital_usd=10.0)
    store.mark_tracked_position_stopped("tok-y", reason="evicted")
    assert store.get_active_tracked_positions() == []

    # Reactivate
    store.upsert_tracked_position(token_id="tok-y", slug="s", capital_usd=15.0)
    positions = store.get_active_tracked_positions()
    assert len(positions) == 1
    assert positions[0]["status"] == "active"
    assert positions[0]["stop_reason"] is None
