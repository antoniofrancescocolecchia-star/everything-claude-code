from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polymarket_platform.db.migrations import migrate

if TYPE_CHECKING:
    from polymarket_platform.scanner.catalog import MarketRecord
    from polymarket_platform.scanner.clob_probe import ClobQuote
    from polymarket_platform.scanner.detector import OpportunityRecord


@dataclass
class RiskState:
    usd_spent: float = 0.0
    last_order_ts: float = 0.0


class SqliteStore:
    """
    Thread-safe SQLite store. All writes go through a threading.Lock.
    WAL mode allows concurrent reads while a write is in progress.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        with self._lock:
            migrate(self._conn)

    # ── Quotes ────────────────────────────────────────────────────────────────

    def insert_quote(
        self,
        *,
        token_id: str,
        best_bid: float,
        best_ask: float,
        feed_source: str,
        ts: float | None = None,
    ) -> int:
        ts = ts if ts is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO quotes"
                " (ts, token_id, best_bid, best_ask, feed_source) VALUES (?,?,?,?,?)",
                (ts, token_id, best_bid, best_ask, feed_source),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    # ── Decisions ─────────────────────────────────────────────────────────────

    def insert_decision(
        self,
        *,
        quote_id: int | None,
        token_id: str,
        action: str,
        reason: str,
        best_bid: float | None,
        best_ask: float | None,
        position_shares: float | None,
        expected_edge_bps: float | None,
        gate_blocked: bool = False,
        gate_block_reason: str | None = None,
        risk_blocked: bool = False,
        risk_block_reason: str | None = None,
        ts: float | None = None,
    ) -> int:
        ts = ts if ts is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO decisions
                  (ts, quote_id, token_id, action, reason, best_bid, best_ask,
                   position_shares, expected_edge_bps,
                   gate_blocked, gate_block_reason, risk_blocked, risk_block_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    quote_id,
                    token_id,
                    action,
                    reason,
                    best_bid,
                    best_ask,
                    position_shares,
                    expected_edge_bps,
                    int(gate_blocked),
                    gate_block_reason,
                    int(risk_blocked),
                    risk_block_reason,
                ),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    # ── Orders ────────────────────────────────────────────────────────────────

    def insert_order(
        self,
        *,
        decision_id: int | None,
        token_id: str,
        side: str,
        order_type: str,
        amount: float,
        status: str,
        fill_price: float | None = None,
        fill_amount: float | None = None,
        fee_usd: float | None = None,
        raw_response: dict[str, Any] | None = None,
        ts: float | None = None,
    ) -> int:
        ts = ts if ts is not None else time.time()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO orders
                  (ts, decision_id, token_id, side, order_type, amount, status,
                   fill_price, fill_amount, fee_usd, raw_response)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    decision_id,
                    token_id,
                    side,
                    order_type,
                    amount,
                    status,
                    fill_price,
                    fill_amount,
                    fee_usd,
                    json.dumps(raw_response, ensure_ascii=False) if raw_response else None,
                ),
            )
            self._conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def update_order_fill(
        self,
        order_id: int,
        *,
        status: str,
        fill_price: float,
        fill_amount: float,
        fee_usd: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE orders SET status=?, fill_price=?, fill_amount=?, fee_usd=? WHERE id=?",
                (status, fill_price, fill_amount, fee_usd, order_id),
            )
            self._conn.commit()

    # ── PnL ───────────────────────────────────────────────────────────────────

    def insert_pnl(
        self,
        *,
        token_id: str,
        order_id: int,
        realized_pnl_usd: float,
        fee_usd: float,
        ts: float | None = None,
    ) -> None:
        ts = ts if ts is not None else time.time()
        net = realized_pnl_usd - fee_usd
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO pnl (ts, token_id, order_id, realized_pnl_usd, fee_usd, net_pnl_usd)
                VALUES (?,?,?,?,?,?)
                """,
                (ts, token_id, order_id, realized_pnl_usd, fee_usd, net),
            )
            self._conn.commit()

    # ── Risk state ────────────────────────────────────────────────────────────

    def load_risk_state(self) -> RiskState:
        with self._lock:
            row = self._conn.execute(
                "SELECT usd_spent, last_order_ts FROM risk_state WHERE id=1"
            ).fetchone()
        if row is None:
            return RiskState()
        return RiskState(usd_spent=row["usd_spent"], last_order_ts=row["last_order_ts"])

    def save_risk_state(self, state: RiskState) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO risk_state (id, usd_spent, last_order_ts, updated_ts)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE
                  SET usd_spent=excluded.usd_spent,
                      last_order_ts=excluded.last_order_ts,
                      updated_ts=excluded.updated_ts
                """,
                (state.usd_spent, state.last_order_ts, time.time()),
            )
            self._conn.commit()

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def insert_circuit_breaker_event(self, reason: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO circuit_breaker_events (ts, reason) VALUES (?,?)",
                (time.time(), reason),
            )
            self._conn.commit()

    # ── Heartbeats ────────────────────────────────────────────────────────────

    def insert_heartbeat(
        self, heartbeat_id: str | None, raw: dict[str, Any] | None
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO heartbeats (ts, heartbeat_id, raw_response) VALUES (?,?,?)",
                (time.time(), heartbeat_id, json.dumps(raw) if raw else None),
            )
            self._conn.commit()

    # ── Query helpers (for CLI status + tests) ────────────────────────────────

    def get_decisions(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_orders(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM orders ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_pnl_summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                  COUNT(*)             AS total_fills,
                  SUM(realized_pnl_usd) AS gross_pnl_usd,
                  SUM(fee_usd)          AS total_fees_usd,
                  SUM(net_pnl_usd)      AS net_pnl_usd,
                  MIN(net_pnl_usd)      AS worst_trade_usd,
                  MAX(net_pnl_usd)      AS best_trade_usd,
                  SUM(CASE WHEN net_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
                  SUM(CASE WHEN net_pnl_usd <= 0 THEN 1 ELSE 0 END) AS losses
                FROM pnl
                """
            ).fetchone()
        return dict(row) if row else {}

    def get_last_circuit_breaker_event(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM circuit_breaker_events ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # =========================================================================
    # Scanner / Layer 2 methods
    # =========================================================================

    # -- markets --------------------------------------------------------------

    def upsert_markets(self, records: list[MarketRecord]) -> None:
        """Insert or update market catalog entries."""
        now = time.time()
        with self._lock:
            for rec in records:
                self._conn.execute(
                    """
                    INSERT INTO markets
                      (token_id, condition_id, slug, event_slug, question, outcome,
                       end_date_ts, days_to_expiry, liquidity, volume_24h, score,
                       last_scan_ts, created_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(token_id) DO UPDATE SET
                      condition_id  = excluded.condition_id,
                      slug          = excluded.slug,
                      event_slug    = excluded.event_slug,
                      question      = excluded.question,
                      outcome       = excluded.outcome,
                      end_date_ts   = excluded.end_date_ts,
                      days_to_expiry= excluded.days_to_expiry,
                      liquidity     = excluded.liquidity,
                      volume_24h    = excluded.volume_24h,
                      score         = excluded.score,
                      last_scan_ts  = excluded.last_scan_ts
                    """,
                    (
                        rec.token_id,
                        rec.condition_id,
                        rec.slug,
                        rec.event_slug,
                        rec.question,
                        rec.outcome,
                        rec.end_date_ts,
                        rec.days_to_expiry,
                        rec.liquidity,
                        rec.volume_24h,
                        rec.score,
                        now,
                        now,
                    ),
                )
            self._conn.commit()

    def get_market_catalog(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return top markets by score."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM markets ORDER BY score DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- market_snapshots -----------------------------------------------------

    def insert_market_snapshots(
        self,
        records: list[MarketRecord],
        clob_quotes: dict[str, ClobQuote],
    ) -> None:
        """Persist one price/spread observation per market token."""
        now = time.time()
        with self._lock:
            for rec in records:
                quote = clob_quotes.get(rec.token_id)
                self._conn.execute(
                    """
                    INSERT INTO market_snapshots
                      (ts, token_id, midpoint, spread_bps, liquidity, volume_24h)
                    VALUES (?,?,?,?,?,?)
                    """,
                    (
                        now,
                        rec.token_id,
                        quote.midpoint if quote else None,
                        quote.spread_bps if quote else None,
                        rec.liquidity,
                        rec.volume_24h,
                    ),
                )
            self._conn.commit()

    def get_recent_midpoints(
        self, token_ids: list[str], *, since_ts: float
    ) -> dict[str, list[float]]:
        """
        Return historical midpoints for given tokens since `since_ts`.
        Result is ordered oldest-first per token.
        """
        out: dict[str, list[float]] = {}
        if not token_ids:
            return out
        placeholders = ",".join("?" * len(token_ids))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT token_id, midpoint FROM market_snapshots
                WHERE token_id IN ({placeholders})
                  AND ts >= ?
                  AND midpoint IS NOT NULL
                ORDER BY token_id, ts ASC
                """,
                (*token_ids, since_ts),
            ).fetchall()
        for row in rows:
            out.setdefault(row["token_id"], []).append(float(row["midpoint"]))
        return out

    def get_recent_spreads(
        self, token_ids: list[str], *, since_ts: float
    ) -> dict[str, list[float]]:
        """Return historical spread_bps for given tokens since `since_ts` (oldest first)."""
        out: dict[str, list[float]] = {}
        if not token_ids:
            return out
        placeholders = ",".join("?" * len(token_ids))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT token_id, spread_bps FROM market_snapshots
                WHERE token_id IN ({placeholders})
                  AND ts >= ?
                  AND spread_bps IS NOT NULL
                ORDER BY token_id, ts ASC
                """,
                (*token_ids, since_ts),
            ).fetchall()
        for row in rows:
            out.setdefault(row["token_id"], []).append(float(row["spread_bps"]))
        return out

    # -- opportunities --------------------------------------------------------

    def insert_opportunities(self, records: list[OpportunityRecord]) -> None:
        """Persist detected opportunity signals."""
        with self._lock:
            for rec in records:
                self._conn.execute(
                    """
                    INSERT INTO opportunities
                      (ts, token_id, slug, event_slug, outcome, side,
                       confidence, expected_edge_bps, reason, midpoint, spread_bps)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rec.ts,
                        rec.token_id,
                        rec.slug,
                        rec.event_slug,
                        rec.outcome,
                        rec.side,
                        rec.confidence,
                        rec.expected_edge_bps,
                        rec.reason,
                        rec.midpoint,
                        rec.spread_bps,
                    ),
                )
            self._conn.commit()

    def get_recent_opportunities(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM opportunities ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- tracked_positions ----------------------------------------------------

    def upsert_tracked_position(
        self,
        *,
        token_id: str,
        slug: str | None = None,
        capital_usd: float = 0.0,
    ) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tracked_positions
                  (token_id, slug, started_ts, status, capital_usd)
                VALUES (?,?,?,?,?)
                ON CONFLICT(token_id) DO UPDATE SET
                  slug        = excluded.slug,
                  status      = 'active',
                  capital_usd = excluded.capital_usd,
                  started_ts  = CASE WHEN tracked_positions.status != 'active'
                                     THEN excluded.started_ts
                                     ELSE tracked_positions.started_ts END,
                  stopped_ts  = NULL,
                  stop_reason = NULL
                """,
                (token_id, slug, now, "active", capital_usd),
            )
            self._conn.commit()

    def mark_tracked_position_stopped(self, token_id: str, *, reason: str = "") -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE tracked_positions
                SET status = 'stopped', stopped_ts = ?, stop_reason = ?
                WHERE token_id = ?
                """,
                (time.time(), reason, token_id),
            )
            self._conn.commit()

    def get_active_tracked_positions(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tracked_positions WHERE status = 'active' ORDER BY started_ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]
