from __future__ import annotations

import sqlite3

# Each entry is (version, sql). Applied in order, skipped if already applied.
# To add a migration: append (N+1, "ALTER TABLE ...") — never modify existing entries.
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quotes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            token_id    TEXT    NOT NULL,
            best_bid    REAL    NOT NULL,
            best_ask    REAL    NOT NULL,
            feed_source TEXT    NOT NULL   -- 'ws' | 'rest'
        );
        CREATE INDEX IF NOT EXISTS quotes_ts       ON quotes(ts);
        CREATE INDEX IF NOT EXISTS quotes_token_ts ON quotes(token_id, ts);

        CREATE TABLE IF NOT EXISTS decisions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                  REAL    NOT NULL,
            quote_id            INTEGER REFERENCES quotes(id),
            token_id            TEXT    NOT NULL,
            action              TEXT    NOT NULL,   -- BUY | SELL | HOLD
            reason              TEXT    NOT NULL,
            best_bid            REAL,
            best_ask            REAL,
            position_shares     REAL,
            expected_edge_bps   REAL,
            gate_blocked        INTEGER NOT NULL DEFAULT 0,
            gate_block_reason   TEXT,
            risk_blocked        INTEGER NOT NULL DEFAULT 0,
            risk_block_reason   TEXT
        );
        CREATE INDEX IF NOT EXISTS decisions_ts ON decisions(ts);

        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            decision_id INTEGER REFERENCES decisions(id),
            token_id    TEXT    NOT NULL,
            side        TEXT    NOT NULL,
            order_type  TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            status      TEXT    NOT NULL,   -- DRY_RUN | SENT | FILLED | CANCELLED | FAILED
            fill_price  REAL,
            fill_amount REAL,
            fee_usd     REAL,
            raw_response TEXT
        );
        CREATE INDEX IF NOT EXISTS orders_ts ON orders(ts);

        CREATE TABLE IF NOT EXISTS pnl (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL    NOT NULL,
            token_id        TEXT    NOT NULL,
            order_id        INTEGER REFERENCES orders(id),
            realized_pnl_usd REAL   NOT NULL,
            fee_usd         REAL    NOT NULL,
            net_pnl_usd     REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS risk_state (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            usd_spent       REAL    NOT NULL DEFAULT 0.0,
            last_order_ts   REAL    NOT NULL DEFAULT 0.0,
            updated_ts      REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS circuit_breaker_events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      REAL    NOT NULL,
            reason  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS heartbeats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           REAL    NOT NULL,
            heartbeat_id TEXT,
            raw_response TEXT
        );
        """,
    ),
    # -----------------------------------------------------------------
    # Migration 2 — Scanner / Layer 2 tables
    # -----------------------------------------------------------------
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS markets (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id         TEXT    NOT NULL UNIQUE,
            condition_id     TEXT    NOT NULL,
            slug             TEXT,
            event_slug       TEXT,
            question         TEXT,
            outcome          TEXT,
            end_date_ts      REAL,
            days_to_expiry   REAL,
            liquidity        REAL    NOT NULL DEFAULT 0.0,
            volume_24h       REAL    NOT NULL DEFAULT 0.0,
            score            REAL    NOT NULL DEFAULT 0.0,
            last_scan_ts     REAL    NOT NULL DEFAULT 0.0,
            created_ts       REAL    NOT NULL DEFAULT 0.0
        );
        CREATE INDEX IF NOT EXISTS markets_score    ON markets(score DESC);
        CREATE INDEX IF NOT EXISTS markets_token_id ON markets(token_id);

        CREATE TABLE IF NOT EXISTS market_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            token_id    TEXT    NOT NULL,
            midpoint    REAL,
            spread_bps  REAL,
            liquidity   REAL,
            volume_24h  REAL
        );
        CREATE INDEX IF NOT EXISTS msnap_token_ts ON market_snapshots(token_id, ts);

        CREATE TABLE IF NOT EXISTS opportunities (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            ts                 REAL    NOT NULL,
            token_id           TEXT    NOT NULL,
            slug               TEXT,
            event_slug         TEXT,
            outcome            TEXT,
            side               TEXT    NOT NULL,
            confidence         REAL    NOT NULL,
            expected_edge_bps  REAL    NOT NULL,
            reason             TEXT    NOT NULL,
            midpoint           REAL,
            spread_bps         REAL
        );
        CREATE INDEX IF NOT EXISTS opp_ts    ON opportunities(ts DESC);
        CREATE INDEX IF NOT EXISTS opp_token ON opportunities(token_id, ts DESC);

        CREATE TABLE IF NOT EXISTS tracked_positions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id    TEXT    NOT NULL UNIQUE,
            slug        TEXT,
            started_ts  REAL    NOT NULL DEFAULT 0.0,
            stopped_ts  REAL,
            status      TEXT    NOT NULL DEFAULT 'active',
            capital_usd REAL    NOT NULL DEFAULT 0.0,
            stop_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS tpos_status ON tracked_positions(status);
        """,
    ),
]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply all pending migrations. Safe to call on every startup."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
        """
    )
    conn.commit()

    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current_version: int = row[0] if row[0] is not None else 0

    for version, sql in _MIGRATIONS:
        if version <= current_version:
            continue
        # Execute multi-statement DDL inside a single transaction
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()
