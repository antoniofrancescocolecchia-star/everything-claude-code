"""Tests for GammaClient parsing and fetch logic."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_platform.scanner.gamma_client import GammaClient, _parse_market

# ---------------------------------------------------------------------------
# Unit tests for _parse_market (no network)
# ---------------------------------------------------------------------------


def _raw_market(
    *,
    condition_id: str = "0xabc",
    slug: str = "test-slug",
    active: bool = True,
    closed: bool = False,
    enable_order_book: bool = True,
    liquidity: float = 5000.0,
    volume: float = 20000.0,
    volume_24h: float = 1000.0,
    clob_token_ids: list[str] | None = None,
    outcome_prices: list[float] | None = None,
    end_date: str | None = "2099-01-01T00:00:00Z",
) -> dict:
    tids = clob_token_ids or ["tok-yes", "tok-no"]
    prices = outcome_prices or [0.45, 0.55]
    return {
        "conditionId": condition_id,
        "question": "Will something happen?",
        "slug": slug,
        "active": active,
        "closed": closed,
        "enableOrderBook": enable_order_book,
        "liquidity": liquidity,
        "volume": volume,
        "volume24hr": volume_24h,
        "clobTokenIds": json.dumps(tids),
        "outcomePrices": json.dumps([str(p) for p in prices]),
        "endDate": end_date,
    }


def test_parse_valid_market() -> None:
    raw = _raw_market()
    result = _parse_market(raw)
    assert result is not None
    assert result.condition_id == "0xabc"
    assert result.slug == "test-slug"
    assert result.active is True
    assert result.closed is False
    assert result.enable_order_book is True
    assert result.liquidity == pytest.approx(5000.0)
    assert result.volume_24h == pytest.approx(1000.0)
    assert result.clob_token_ids == ["tok-yes", "tok-no"]
    assert result.outcome_prices == [pytest.approx(0.45), pytest.approx(0.55)]


def test_parse_market_with_list_token_ids() -> None:
    """clobTokenIds can already be a parsed list (not JSON string)."""
    raw = _raw_market()
    raw["clobTokenIds"] = ["tok-a", "tok-b"]  # already a list
    raw["outcomePrices"] = ["0.4", "0.6"]
    result = _parse_market(raw)
    assert result is not None
    assert result.clob_token_ids == ["tok-a", "tok-b"]
    assert result.outcome_prices == [pytest.approx(0.4), pytest.approx(0.6)]


def test_parse_market_missing_condition_id_returns_none() -> None:
    raw = _raw_market()
    raw["conditionId"] = ""
    assert _parse_market(raw) is None


def test_parse_market_empty_token_ids_returns_none() -> None:
    raw = _raw_market()
    raw["clobTokenIds"] = json.dumps([])
    assert _parse_market(raw) is None


def test_parse_market_no_end_date() -> None:
    raw = _raw_market(end_date=None)
    result = _parse_market(raw)
    assert result is not None
    assert result.end_date_ts is None


def test_parse_market_event_slug_from_events_list() -> None:
    raw = _raw_market()
    raw["events"] = [{"slug": "us-election-2024", "id": "evt-1"}]
    result = _parse_market(raw)
    assert result is not None
    assert result.event_slug == "us-election-2024"


def test_parse_market_event_slug_from_eventslug_field() -> None:
    raw = _raw_market()
    raw["eventSlug"] = "my-event"
    result = _parse_market(raw)
    assert result is not None
    assert result.event_slug == "my-event"


def test_parse_market_handles_numeric_prices() -> None:
    raw = _raw_market()
    raw["outcomePrices"] = [0.3, 0.7]  # numeric, not strings
    result = _parse_market(raw)
    assert result is not None
    assert result.outcome_prices == [pytest.approx(0.3), pytest.approx(0.7)]


# ---------------------------------------------------------------------------
# Integration tests using unittest.mock (httpx-compatible)
# ---------------------------------------------------------------------------


def _make_httpx_response(payload: object, status_code: int = 200) -> MagicMock:
    """Build a mock httpx Response that returns payload from .json()."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.mark.asyncio
async def test_fetch_markets_returns_parsed_list() -> None:
    raw = _raw_market(condition_id="0x111", slug="mocked-market")
    mock_resp = _make_httpx_response([raw])
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        client = GammaClient()
        try:
            markets = await client.fetch_markets(limit=10)
        finally:
            await client.close()

    assert len(markets) == 1
    assert markets[0].condition_id == "0x111"
    assert markets[0].slug == "mocked-market"


@pytest.mark.asyncio
async def test_fetch_markets_filters_unparseable() -> None:
    """Records that fail parsing are silently dropped."""
    valid = _raw_market(condition_id="0xgood")
    invalid = {"conditionId": "", "clobTokenIds": "[]"}  # no tokens
    mock_resp = _make_httpx_response([valid, invalid])
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        client = GammaClient()
        try:
            markets = await client.fetch_markets(limit=5)
        finally:
            await client.close()

    assert len(markets) == 1
    assert markets[0].condition_id == "0xgood"


@pytest.mark.asyncio
async def test_fetch_markets_returns_empty_on_http_error() -> None:
    mock_resp = _make_httpx_response({}, status_code=503)
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        client = GammaClient()
        try:
            markets = await client.fetch_markets(limit=10)
        finally:
            await client.close()

    assert markets == []


@pytest.mark.asyncio
async def test_fetch_markets_returns_empty_on_unexpected_shape() -> None:
    """Gamma returning a non-list (e.g. a dict) is handled gracefully."""
    mock_resp = _make_httpx_response({"error": "unexpected"})
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        client = GammaClient()
        try:
            markets = await client.fetch_markets(limit=10)
        finally:
            await client.close()

    assert markets == []


@pytest.mark.asyncio
async def test_fetch_events_returns_list() -> None:
    mock_resp = _make_httpx_response([{"slug": "event-1", "id": "e1"}])
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        client = GammaClient()
        try:
            events = await client.fetch_events(limit=10)
        finally:
            await client.close()

    assert len(events) == 1
    assert events[0]["slug"] == "event-1"
