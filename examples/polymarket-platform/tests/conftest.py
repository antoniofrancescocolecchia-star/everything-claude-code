"""
Shared fixtures for unit and integration tests.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from polymarket_platform.config import Settings
from polymarket_platform.db.store import SqliteStore
from polymarket_platform.feed.base import FeedSource, Quote
from polymarket_platform.market.data_api import Position

TOKEN_ID = "test-token-0x1234"


def make_settings(**overrides: Any) -> Settings:
    """Build a minimal Settings object suitable for tests."""
    defaults = dict(
        POLY_TOKEN_ID=TOKEN_ID,
        BOT_DRY_RUN="true",
        BOT_SQLITE_PATH=":memory:",
        BUY_THRESHOLD="0.45",
        SELL_THRESHOLD="0.55",
        STRAT_BUY_AMOUNT_USD="10.0",
        STRAT_SELL_SHARES="5.0",
        FEE_TAKER_BPS="200",
        MIN_EXPECTED_EDGE_BPS="50",
        RISK_MAX_POSITION_SHARES="100",
        RISK_MAX_USD_SPEND="1000",
        RISK_MIN_SECONDS_BETWEEN_ORDERS="0",  # no cooldown in tests
        RISK_MAX_OPEN_ORDERS="99",
        CB_MAX_CONSECUTIVE_LOSSES="5",
        CB_MAX_DAILY_DRAWDOWN_USD="500",
        CB_MAX_DECISION_LATENCY_MS="10000",  # very high in tests
        BOT_HEARTBEAT_ENABLED="false",
        BOT_MAX_RETRIES="1",
        BOT_REQUEST_TIMEOUT_SECONDS="5",
        FEED_MAX_STALENESS_SECONDS="60",
        BOT_POSITION_REFRESH_INTERVAL_SECONDS="999",
    )
    defaults.update({k.upper(): str(v) for k, v in overrides.items()})
    return Settings(**{k: v for k, v in defaults.items()})  # type: ignore[arg-type]


@pytest.fixture
def store() -> SqliteStore:
    return SqliteStore(":memory:")


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def make_quote(
    *,
    bid: float = 0.44,
    ask: float = 0.46,
    token_id: str = TOKEN_ID,
    source: FeedSource = FeedSource.WS,
) -> Quote:
    import time

    return Quote(token_id=token_id, best_bid=bid, best_ask=ask, ts=time.time(), source=source)


class MockClobExecutor:
    """Fake ClobExecutor for testing — no real HTTP calls."""

    def __init__(
        self,
        price_bid: float = 0.44,
        price_ask: float = 0.46,
        orders: list[dict] | None = None,
    ) -> None:
        self._bid = price_bid
        self._ask = price_ask
        self._orders = orders or []
        self.market_order_calls: list[dict] = []
        self.cancel_all_called = False

    async def get_price(self, token_id: str, side: str) -> float:
        return self._ask if side == "BUY" else self._bid

    async def get_orders(self) -> list[dict]:
        return self._orders

    async def cancel_all(self) -> dict:
        self.cancel_all_called = True
        return {"cancelled": True}

    async def post_heartbeat(self, heartbeat_id: Any) -> dict:
        return {"heartbeat_id": heartbeat_id}

    async def market_order(
        self, *, token_id: str, side: str, amount: float, order_type: Any
    ) -> dict:
        call = {"token_id": token_id, "side": side, "amount": amount, "order_type": order_type}
        self.market_order_calls.append(call)
        return {"status": "matched", "price": self._ask, "size_matched": amount}

    def shutdown(self) -> None:
        pass

    @staticmethod
    def side_buy() -> str:
        return "BUY"

    @staticmethod
    def side_sell() -> str:
        return "SELL"


class MockDataApiClient:
    """Fake DataApiClient for testing."""

    def __init__(self, positions: list[Position] | None = None) -> None:
        self._positions = positions or []

    async def get_positions(self, user: str, limit: int = 200) -> list[Position]:
        return self._positions

    async def close(self) -> None:
        pass
