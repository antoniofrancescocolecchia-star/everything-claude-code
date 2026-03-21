from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OrderRecord:
    ts: float
    token_id: str
    side: str
    order_type: str
    amount: float
    status: str
    raw_response: dict[str, Any] | None


class SqliteStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  token_id TEXT NOT NULL,
                  best_bid REAL,
                  best_ask REAL,
                  position_shares REAL,
                  action TEXT NOT NULL,
                  reason TEXT NOT NULL
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  token_id TEXT NOT NULL,
                  side TEXT NOT NULL,
                  order_type TEXT NOT NULL,
                  amount REAL NOT NULL,
                  status TEXT NOT NULL,
                  raw_response TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS heartbeats (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL NOT NULL,
                  heartbeat_id TEXT,
                  raw_response TEXT
                );
                """
            )
            self._conn.commit()

    def insert_decision(
        self,
        *,
        token_id: str,
        best_bid: float | None,
        best_ask: float | None,
        position_shares: float | None,
        action: str,
        reason: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO decisions (ts, token_id, best_bid, best_ask, position_shares, action, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (time.time(), token_id, best_bid, best_ask, position_shares, action, reason),
            )
            self._conn.commit()

    def insert_order(self, rec: OrderRecord) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO orders (ts, token_id, side, order_type, amount, status, raw_response)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    rec.ts,
                    rec.token_id,
                    rec.side,
                    rec.order_type,
                    rec.amount,
                    rec.status,
                    json.dumps(rec.raw_response, ensure_ascii=False) if rec.raw_response else None,
                ),
            )
            self._conn.commit()

    def insert_heartbeat(self, heartbeat_id: str | None, raw: dict[str, Any] | None) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO heartbeats (ts, heartbeat_id, raw_response)
                VALUES (?, ?, ?);
                """,
                (time.time(), heartbeat_id, json.dumps(raw, ensure_ascii=False) if raw else None),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
