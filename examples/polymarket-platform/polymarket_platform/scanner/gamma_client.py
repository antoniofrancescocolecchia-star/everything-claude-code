"""
Async client for the Polymarket Gamma API.

Public API -- no authentication required.

Endpoints used:
    GET /markets  -- paginated list of active markets
    GET /events   -- events with nested markets (event-level grouping)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

_ACTIVE_PARAMS: dict[str, Any] = {
    "active": "true",
    "enableOrderBook": "true",
    "closed": "false",
}


@dataclass
class RawMarket:
    """Minimally parsed record from GET /markets."""

    condition_id: str
    question: str
    slug: str
    event_slug: str | None
    active: bool
    closed: bool
    enable_order_book: bool
    liquidity: float
    volume: float
    volume_24h: float
    end_date_ts: float | None
    clob_token_ids: list[str]
    outcome_prices: list[float]
    raw: dict[str, Any] = field(default_factory=dict)


class GammaClient:
    """
    Async HTTP client for the Polymarket Gamma API.
    All endpoints are public (no credentials needed).
    """

    def __init__(
        self,
        base_url: str = "https://gamma-api.polymarket.com",
        timeout_seconds: float = 20.0,
        max_concurrent: int = 4,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._sem = asyncio.Semaphore(max_concurrent)

    async def fetch_markets(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RawMarket]:
        """Fetch active order-book-enabled markets from Gamma."""
        params: dict[str, Any] = {**_ACTIVE_PARAMS, "limit": limit, "offset": offset}
        try:
            async with self._sem:
                resp = await self._client.get(f"{self._base}/markets", params=params)
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Gamma /markets fetch failed: %s", exc)
            return []

        data = resp.json()
        if not isinstance(data, list):
            log.warning("Gamma /markets unexpected shape: %s", type(data).__name__)
            return []

        markets: list[RawMarket] = []
        for raw in data:
            parsed = _parse_market(raw)
            if parsed is not None:
                markets.append(parsed)
        log.debug("Gamma returned %d parseable markets (of %d)", len(markets), len(data))
        return markets

    async def fetch_events(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch events with nested markets for event-level grouping."""
        try:
            async with self._sem:
                resp = await self._client.get(
                    f"{self._base}/events",
                    params={"active": "true", "limit": limit},
                )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Gamma /events fetch failed: %s", exc)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []

    async def close(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_json_list(val: Any) -> list[Any]:
    """Parse a value that may be a JSON-encoded string or already a list."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _parse_end_date(raw: Any) -> float | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            continue
    return None


def _parse_market(raw: dict[str, Any]) -> RawMarket | None:
    """Parse one raw Gamma market dict. Returns None if the record is unusable."""
    try:
        cond_id = str(raw.get("conditionId") or raw.get("condition_id") or "").strip()
        if not cond_id:
            return None

        clob_raw = _parse_json_list(raw.get("clobTokenIds") or raw.get("clob_token_ids") or [])
        token_ids = [str(t) for t in clob_raw if t]
        if not token_ids:
            return None

        prices_raw = _parse_json_list(
            raw.get("outcomePrices") or raw.get("outcome_prices") or []
        )
        outcome_prices = [_safe_float(p) for p in prices_raw]
        # Ensure list lengths match
        while len(outcome_prices) < len(token_ids):
            outcome_prices.append(0.0)

        end_ts = _parse_end_date(raw.get("endDate") or raw.get("end_date"))

        # Event slug: may be nested under "events" list
        event_slug: str | None = None
        events_raw = raw.get("events") or []
        if isinstance(events_raw, list) and events_raw:
            es = events_raw[0].get("slug") if isinstance(events_raw[0], dict) else None
            if es:
                event_slug = str(es)
        if not event_slug:
            es2 = raw.get("eventSlug") or raw.get("event_slug")
            event_slug = str(es2) if es2 else None

        return RawMarket(
            condition_id=cond_id,
            question=str(raw.get("question") or ""),
            slug=str(raw.get("slug") or ""),
            event_slug=event_slug,
            active=bool(raw.get("active", True)),
            closed=bool(raw.get("closed", False)),
            enable_order_book=bool(
                raw.get("enableOrderBook") or raw.get("enable_order_book", False)
            ),
            liquidity=_safe_float(raw.get("liquidity")),
            volume=_safe_float(raw.get("volume")),
            volume_24h=_safe_float(raw.get("volume24hr") or raw.get("volume_24h")),
            end_date_ts=end_ts,
            clob_token_ids=token_ids,
            outcome_prices=outcome_prices,
            raw=raw,
        )
    except Exception as exc:
        log.debug("market parse error: %s — %s", exc, str(raw)[:80])
        return None
