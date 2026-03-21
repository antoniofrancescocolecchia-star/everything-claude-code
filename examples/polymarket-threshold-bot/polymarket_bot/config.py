from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Polymarket hosts
    clob_host: str = Field(default="https://clob.polymarket.com", alias="POLY_CLOB_HOST")
    gamma_host: str = Field(default="https://gamma-api.polymarket.com", alias="POLY_GAMMA_HOST")
    data_host: str = Field(default="https://data-api.polymarket.com", alias="POLY_DATA_HOST")
    chain_id: int = Field(default=137, alias="POLY_CHAIN_ID")

    # Trading identity
    private_key: SecretStr | None = Field(default=None, alias="POLY_PRIVATE_KEY")
    funder_address: str | None = Field(default=None, alias="POLY_FUNDER_ADDRESS")
    signature_type: int = Field(default=0, alias="POLY_SIGNATURE_TYPE")

    # Optional L2 creds (recommended to avoid key rotation)
    api_key: str | None = Field(default=None, alias="POLY_API_KEY")
    api_secret: SecretStr | None = Field(default=None, alias="POLY_API_SECRET")
    api_passphrase: SecretStr | None = Field(default=None, alias="POLY_API_PASSPHRASE")

    # Bot
    dry_run: bool = Field(default=True, alias="BOT_DRY_RUN")
    poll_interval_seconds: float = Field(default=2.0, alias="BOT_POLL_INTERVAL_SECONDS")
    sqlite_path: str = Field(default="bot.sqlite3", alias="BOT_SQLITE_PATH")
    log_json: bool = Field(default=False, alias="BOT_LOG_JSON")
    log_level: str = Field(default="INFO", alias="BOT_LOG_LEVEL")
    kill_switch_path: str = Field(default="./KILL_SWITCH", alias="BOT_KILL_SWITCH_PATH")

    # Heartbeats (mainly for resting orders)
    heartbeat_enabled: bool = Field(default=False, alias="BOT_HEARTBEAT_ENABLED")
    heartbeat_interval_seconds: float = Field(default=5.0, alias="BOT_HEARTBEAT_INTERVAL_SECONDS")

    # Strategy (threshold)
    token_id: str = Field(..., alias="POLY_TOKEN_ID")
    buy_threshold: float = Field(default=0.45, alias="STRAT_BUY_THRESHOLD")
    sell_threshold: float = Field(default=0.55, alias="STRAT_SELL_THRESHOLD")
    buy_amount_usd: float = Field(default=10.0, alias="STRAT_BUY_AMOUNT_USD")
    sell_shares: float = Field(default=5.0, alias="STRAT_SELL_SHARES")
    order_type: str = Field(default="FAK", alias="STRAT_ORDER_TYPE")  # FOK|FAK

    # Risk
    max_position_shares: float = Field(default=50.0, alias="RISK_MAX_POSITION_SHARES")
    max_usd_spend: float = Field(default=200.0, alias="RISK_MAX_USD_SPEND")
    min_seconds_between_orders: float = Field(default=5.0, alias="RISK_MIN_SECONDS_BETWEEN_ORDERS")
    max_open_orders: int = Field(default=10, alias="RISK_MAX_OPEN_ORDERS")

    # Retry
    max_retries: int = Field(default=4, alias="BOT_MAX_RETRIES")
    request_timeout_seconds: float = Field(default=15.0, alias="BOT_REQUEST_TIMEOUT_SECONDS")
