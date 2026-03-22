from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- Hosts ----------------------------------------------------------------
    clob_host: str = Field(default="https://clob.polymarket.com", alias="POLY_CLOB_HOST")
    clob_ws_url: str = Field(
        default="wss://ws-subscriptions-clob.polymarket.com/ws/market",
        alias="POLY_CLOB_WS_URL",
    )
    data_host: str = Field(default="https://data-api.polymarket.com", alias="POLY_DATA_HOST")
    gamma_host: str = Field(default="https://gamma-api.polymarket.com", alias="POLY_GAMMA_HOST")
    chain_id: int = Field(default=137, alias="POLY_CHAIN_ID")

    # -- Identity -------------------------------------------------------------
    private_key: SecretStr | None = Field(default=None, alias="POLY_PRIVATE_KEY")
    funder_address: str | None = Field(default=None, alias="POLY_FUNDER_ADDRESS")
    signature_type: int = Field(default=0, alias="POLY_SIGNATURE_TYPE")
    api_key: str | None = Field(default=None, alias="POLY_API_KEY")
    api_secret: SecretStr | None = Field(default=None, alias="POLY_API_SECRET")
    api_passphrase: SecretStr | None = Field(default=None, alias="POLY_API_PASSPHRASE")

    # -- Market (optional when scanner discovers tokens dynamically) ----------
    # Required for single-token mode (polymarket run).
    # Optional for scanner mode (polymarket scan).
    token_id: str | None = Field(default=None, alias="POLY_TOKEN_ID")

    # -- Bot mode -------------------------------------------------------------
    dry_run: bool = Field(default=True, alias="BOT_DRY_RUN")
    sqlite_path: str = Field(default="platform.sqlite3", alias="BOT_SQLITE_PATH")
    log_json: bool = Field(default=False, alias="BOT_LOG_JSON")
    log_level: str = Field(default="INFO", alias="BOT_LOG_LEVEL")
    kill_switch_path: str = Field(default="./KILL_SWITCH", alias="BOT_KILL_SWITCH_PATH")

    # -- Feed -----------------------------------------------------------------
    feed_fallback_after_failures: int = Field(default=5, alias="FEED_FALLBACK_AFTER_FAILURES")
    feed_poll_interval_seconds: float = Field(default=2.0, alias="FEED_POLL_INTERVAL_SECONDS")
    feed_max_staleness_seconds: float = Field(default=30.0, alias="FEED_MAX_STALENESS_SECONDS")
    feed_ws_max_reconnect_backoff: float = Field(
        default=60.0, alias="FEED_WS_MAX_RECONNECT_BACKOFF"
    )

    # -- Strategy (threshold) -------------------------------------------------
    buy_threshold: float = Field(default=0.45, alias="BUY_THRESHOLD")
    sell_threshold: float = Field(default=0.55, alias="SELL_THRESHOLD")
    buy_amount_usd: float = Field(default=10.0, alias="STRAT_BUY_AMOUNT_USD")
    sell_shares: float = Field(default=5.0, alias="STRAT_SELL_SHARES")
    order_type: str = Field(default="FAK", alias="STRAT_ORDER_TYPE")

    # -- Profitability gate ---------------------------------------------------
    fee_taker_bps: float = Field(default=200.0, alias="FEE_TAKER_BPS")
    min_expected_edge_bps: float = Field(default=50.0, alias="MIN_EXPECTED_EDGE_BPS")

    # -- Risk -----------------------------------------------------------------
    max_position_shares: float = Field(default=50.0, alias="RISK_MAX_POSITION_SHARES")
    max_usd_spend: float = Field(default=200.0, alias="RISK_MAX_USD_SPEND")
    min_seconds_between_orders: float = Field(default=5.0, alias="RISK_MIN_SECONDS_BETWEEN_ORDERS")
    max_open_orders: int = Field(default=10, alias="RISK_MAX_OPEN_ORDERS")

    # -- Circuit breaker ------------------------------------------------------
    cb_max_consecutive_losses: int = Field(default=5, alias="CB_MAX_CONSECUTIVE_LOSSES")
    cb_max_daily_drawdown_usd: float = Field(default=50.0, alias="CB_MAX_DAILY_DRAWDOWN_USD")
    cb_max_decision_latency_ms: float = Field(default=500.0, alias="CB_MAX_DECISION_LATENCY_MS")

    # -- Heartbeat ------------------------------------------------------------
    heartbeat_enabled: bool = Field(default=False, alias="BOT_HEARTBEAT_ENABLED")
    heartbeat_interval_seconds: float = Field(default=15.0, alias="BOT_HEARTBEAT_INTERVAL_SECONDS")

    # -- Position refresh -----------------------------------------------------
    position_refresh_interval_seconds: float = Field(
        default=10.0, alias="BOT_POSITION_REFRESH_INTERVAL_SECONDS"
    )

    # -- Retry ----------------------------------------------------------------
    max_retries: int = Field(default=4, alias="BOT_MAX_RETRIES")
    request_timeout_seconds: float = Field(default=15.0, alias="BOT_REQUEST_TIMEOUT_SECONDS")

    # =========================================================================
    # Scanner / Layer 2 settings
    # =========================================================================

    # Master switch: enable autonomous market scanning.
    # When false, the bot operates on POLY_TOKEN_ID only (single-token mode).
    scanner_enabled: bool = Field(default=False, alias="SCANNER_ENABLED")

    # How often the scanner polls Gamma for new markets (seconds).
    scanner_poll_interval: float = Field(
        default=120.0, alias="SCANNER_POLL_INTERVAL_SECONDS"
    )

    # Market filters (hard limits; markets that fail any are excluded).
    min_liquidity_usd: float = Field(default=1000.0, alias="MIN_LIQUIDITY_USD")
    min_volume_24h_usd: float = Field(default=200.0, alias="MIN_VOLUME_24H_USD")
    max_spread_bps: float = Field(default=800.0, alias="MAX_SPREAD_BPS")
    min_days_to_expiry: float = Field(default=1.0, alias="MIN_DAYS_TO_EXPIRY")

    # Worker pool limits.
    max_concurrent_markets: int = Field(default=3, alias="MAX_CONCURRENT_MARKETS")
    max_total_capital_usd: float = Field(default=100.0, alias="MAX_TOTAL_CAPITAL_USD")
    max_capital_per_market_usd: float = Field(default=30.0, alias="MAX_CAPITAL_PER_MARKET_USD")

    # Opportunity quality thresholds.
    opportunity_min_confidence: float = Field(default=0.20, alias="OPPORTUNITY_MIN_CONFIDENCE")
    opportunity_min_edge_bps: float = Field(default=50.0, alias="OPPORTUNITY_MIN_EDGE_BPS")

    # How far back to look when computing historical baselines.
    market_history_lookback_minutes: int = Field(
        default=60, alias="MARKET_HISTORY_LOOKBACK_MINUTES"
    )

    # Maximum number of markets to fetch from Gamma per poll cycle.
    market_discovery_limit: int = Field(default=100, alias="MARKET_DISCOVERY_LIMIT")

    # How often the orchestrator rebalances its worker pool (seconds).
    # If 0, rebalancing happens every cycle (i.e. controlled by scanner_poll_interval).
    orchestrator_rebalance_seconds: float = Field(
        default=0.0, alias="ORCHESTRATOR_REBALANCE_SECONDS"
    )

    # =========================================================================
    # Analyst / Layer 3 settings
    # =========================================================================

    # Anthropic API key for the analyst LLM and web search.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # Master switch: enable Layer 3 analyst.
    # When False, scanner runs Layer 2 heuristics only.
    analyst_enabled: bool = Field(default=False, alias="ANALYST_ENABLED")

    # Live-trading unlock: when False, analyst signals are monitor-only
    # and do NOT influence worker allocation.
    analyst_trading_enabled: bool = Field(
        default=False, alias="ANALYST_TRADING_ENABLED"
    )

    # Anthropic model used for analysis.
    analyst_model: str = Field(
        default="claude-sonnet-4-6", alias="ANALYST_MODEL"
    )

    # Maximum event groups (analysis units) per scanner cycle.
    # Each group costs one retrieval search.
    analyst_max_analyses_per_cycle: int = Field(
        default=5, alias="ANALYST_MAX_ANALYSES_PER_CYCLE"
    )

    # Maximum web search calls per scanner cycle.
    # Must be >= analyst_max_analyses_per_cycle (budget coherence).
    analyst_max_searches_per_cycle: int = Field(
        default=5, alias="ANALYST_MAX_SEARCHES_PER_CYCLE"
    )

    # Minimum model confidence to forward a signal to the orchestrator.
    analyst_min_confidence: float = Field(
        default=0.40, alias="ANALYST_MIN_CONFIDENCE"
    )

    # Minimum absolute edge (bps) to forward a signal.
    # Must exceed taker fee (~200bps) plus safety buffer.
    analyst_min_edge_bps: float = Field(
        default=300.0, alias="ANALYST_MIN_EDGE_BPS"
    )

    # Maximum evidence age in minutes before a result is considered stale.
    analyst_max_evidence_age_minutes: float = Field(
        default=360.0, alias="ANALYST_MAX_EVIDENCE_AGE_MINUTES"
    )

    # Hourly API cost cap (searches + tokens combined, USD).
    analyst_max_cost_per_hour_usd: float = Field(
        default=2.00, alias="ANALYST_MAX_COST_PER_HOUR_USD"
    )

    # Daily API cost cap (USD).
    analyst_max_cost_per_day_usd: float = Field(
        default=10.00, alias="ANALYST_MAX_COST_PER_DAY_USD"
    )

    # Hourly input token cap.
    analyst_max_input_tokens_per_hour: int = Field(
        default=100_000, alias="ANALYST_MAX_INPUT_TOKENS_PER_HOUR"
    )

    # Hourly output token cap.
    analyst_max_output_tokens_per_hour: int = Field(
        default=20_000, alias="ANALYST_MAX_OUTPUT_TOKENS_PER_HOUR"
    )

    # Maximum 429 rate-limit events tolerated per hour before pausing.
    analyst_max_429s_per_hour: int = Field(
        default=5, alias="ANALYST_MAX_429S_PER_HOUR"
    )

    # Skip analysis for markets below this price (near-resolved YES).
    analyst_price_floor: float = Field(
        default=0.08, alias="ANALYST_PRICE_FLOOR"
    )

    # Skip analysis for markets above this price (near-resolved NO).
    analyst_price_ceiling: float = Field(
        default=0.92, alias="ANALYST_PRICE_CEILING"
    )

    # Minimum resolved predictions before live-trading unlock is considered.
    analyst_min_resolved_predictions: int = Field(
        default=100, alias="ANALYST_MIN_RESOLVED_PREDICTIONS"
    )

    # Which retrieval provider to use. Currently: "anthropic_web_search".
    analyst_retrieval_provider: str = Field(
        default="anthropic_web_search", alias="ANALYST_RETRIEVAL_PROVIDER"
    )

    # Enforce Anthropic tool-use schema validation (recommended: True).
    analyst_require_schema_validation: bool = Field(
        default=True, alias="ANALYST_REQUIRE_SCHEMA_VALIDATION"
    )

    # Require analyst to beat market-implied Brier baseline before live trading.
    analyst_market_baseline_required: bool = Field(
        default=True, alias="ANALYST_MARKET_BASELINE_REQUIRED"
    )
