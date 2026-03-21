from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from polymarket_platform.db.migrations import migrate


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
