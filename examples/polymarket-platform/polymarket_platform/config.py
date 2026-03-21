from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Hosts ──────────────────────────────────────────────────────────────────
    clob_host: str = Field(default="https://clob.polymarket.com", alias="POLY_CLOB_HOST")
    clob_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        alias="POLY_CLOB_WS_URL",
    )
    data_host: str = Field(default="https://data-api.polymarket.com", alias="POLY_DATA_HOST")
    gamma_host: str = Field(default="https://gamma-api.polymarket.com", alias="POLY_GAMMA_HOST")
    chain_id: int = Field(default=137, alias="POLY_CHAIN_ID")

    # ── Identity ───────────────────────────────────────────────────────────────
    private_key: SecretStr | None = Field(default=None, alias="POLY_PRIVATE_KEY")
    funder_address: str | None = Field(default=None, alias="POLY_FUNDER_ADDRESS")
    signature_type: int = Field(default=0, alias="POLY_SIGNATURE_TYPE")
    api_key: str | None = Field(default=None, alias="POLY_API_KEY")
    api_secret: SecretStr | None = Field(default=None, alias="POLY_API_SECRET")
    api_passphrase: SecretStr | None = Field(default=None, alias="POLY_API_PASSPHRASE")

    # ── Market ─────────────────────────────────────────────────────────────────
    token_id: str = Field(..., alias="POLY_TOKEN_ID")

    # ── Bot mode ───────────────────────────────────────────────────────────────
    dry_run: bool = Field(default=True, alias="BOT_DRY_RUN")
    sqlite_path: str = Field(default="platform.sqlite3", alias="BOT_SQLITE_PATH")
    log_json: bool = Field(default=False, alias="BOT_LOG_JSON")
    log_level: str = Field(default="INFO", alias="BOT_LOG_LEVEL")
    kill_switch_path: str = Field(default="./KILL_SWITCH", alias="BOT_KILL_SWITCH_PATH")

    # ── Feed ───────────────────────────────────────────────────────────────────
    feed_fallback_after_failures: int = Field(default=5, alias="FEED_FALLBACK_AFTER_FAILURES")
    feed_poll_interval_seconds: float = Field(default=2.0, alias="FEED_POLL_INTERVAL_SECONDS")
    feed_max_staleness_seconds: float = Field(default=30.0, alias="FEED_MAX_STALENESS_SECONDS")
    feed_ws_max_reconnect_backoff: float = Field(
        default=60.0, alias="FEED_WS_MAX_RECONNECT_BACKOFF"
    )

    # ── Strategy (threshold) ──────────────────────────────────────────────────
    buy_threshold: float = Field(default=0.45, alias="BUY_THRESHOLD")
    sell_threshold: float = Field(default=0.55, alias="SELL_THRESHOLD")
    buy_amount_usd: float = Field(default=10.0, alias="STRAT_BUY_AMOUNT_USD")
    sell_shares: float = Field(default=5.0, alias="STRAT_SELL_SHARES")
    order_type: str = Field(default="FAK", alias="STRAT_ORDER_TYPE")

    # ── Profitability gate ─────────────────────────────────────────────────────
    fee_taker_bps: float = Field(default=200.0, alias="FEE_TAKER_BPS")
    min_expected_edge_bps: float = Field(default=50.0, alias="MIN_EXPECTED_EDGE_BPS")

    # ── Risk ───────────────────────────────────────────────────────────────────
    max_position_shares: float = Field(default=50.0, alias="RISK_MAX_POSITION_SHARES")
    max_usd_spend: float = Field(default=200.0, alias="RISK_MAX_USD_SPEND")
    min_seconds_between_orders: float = Field(default=5.0, alias="RISK_MIN_SECONDS_BETWEEN_ORDERS")
    max_open_orders: int = Field(default=10, alias="RISK_MAX_OPEN_ORDERS")

    # ── Circuit breaker ────────────────────────────────────────────────────────
    cb_max_consecutive_losses: int = Field(default=5, alias="CB_MAX_CONSECUTIVE_LOSSES")
    cb_max_daily_drawdown_usd: float = Field(default=50.0, alias="CB_MAX_DAILY_DRAWDOWN_USD")
    cb_max_decision_latency_ms: float = Field(default=500.0, alias="CB_MAX_DECISION_LATENCY_MS")

    # ── Heartbeat ─────────────────────────────────────────────────────────────
    heartbeat_enabled: bool = Field(default=False, alias="BOT_HEARTBEAT_ENABLED")
    heartbeat_interval_seconds: float = Field(default=15.0, alias="BOT_HEARTBEAT_INTERVAL_SECONDS")

    # ── Position refresh ──────────────────────────────────────────────────────
    position_refresh_interval_seconds: float = Field(
        default=10.0, alias="BOT_POSITION_REFRESH_INTERVAL_SECONDS"
    )

    # ── Retry ─────────────────────────────────────────────────────────────────
    max_retries: int = Field(default=4, alias="BOT_MAX_RETRIES")
    request_timeout_seconds: float = Field(default=15.0, alias="BOT_REQUEST_TIMEOUT_SECONDS")
