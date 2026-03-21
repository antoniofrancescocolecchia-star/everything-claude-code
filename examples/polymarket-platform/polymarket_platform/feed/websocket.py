from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
import websockets.exceptions

from polymarket_platform.feed.base import FeedSource, Quote

log = logging.getLogger(__name__)

# Exponential backoff delays (seconds) for reconnects
_BACKOFF = [0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0]


def _extract_quote(msg: dict[str, Any], token_id: str) -> Quote | None:
    """
    Parse a Polymarket market WS message into a Quote.

    With custom_feature_enabled=true the book snapshot includes top-level
    best_bid / best_ask. Fall back to extracting them from bids/asks arrays.
    """
    asset = msg.get("asset_id") or msg.get("assetId") or ""
    if asset and asset != token_id:
        return None

    # Direct best bid/ask (custom_feature_enabled=true)
    if "best_bid" in msg and "best_ask" in msg:
        try:
            bid = float(msg["best_bid"])
            ask = float(msg["best_ask"])
            if bid > 0 and ask > 0:
                return Quote.now(
                    token_id=token_id,
                    best_bid=bid,
                    best_ask=ask,
                    source=FeedSource.WS,
                )
        except (ValueError, TypeError):
            pass

    # Extract from bids/asks arrays (book snapshot)
    bids = msg.get("bids") or []
    asks = msg.get("asks") or []
    if bids and asks:
        try:
            best_bid = max(float(b["price"]) for b in bids if b.get("size", "0") != "0")
            best_ask = min(float(a["price"]) for a in asks if a.get("size", "0") != "0")
            if best_bid > 0 and best_ask > 0:
                return Quote.now(
                    token_id=token_id,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    source=FeedSource.WS,
                )
        except (ValueError, TypeError, ValueError):
            pass

    return None


class WsFeed:
    """
    Connects to Polymarket market WebSocket, subscribes to a token, and pushes
    Quotes into the provided asyncio.Queue.

    Reconnects with exponential backoff. After `fallback_after_failures`
    consecutive failures the caller (engine) switches to RestFeed.
    """

    def __init__(
        self,
        *,
        ws_url: str,
        token_id: str,
        fallback_after_failures: int = 5,
        max_backoff_seconds: float = 60.0,
    ) -> None:
        self._url = ws_url
        self._token_id = token_id
        self._fallback_after = fallback_after_failures
        self._max_backoff = max_backoff_seconds
        self.consecutive_failures: int = 0

    async def run(
        self,
        queue: asyncio.Queue[Quote],
        stop: asyncio.Event,
    ) -> None:
        attempt = 0
        while not stop.is_set():
            try:
                await self._connect(queue, stop)
                attempt = 0
                self.consecutive_failures = 0
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.consecutive_failures += 1
                if self.consecutive_failures >= self._fallback_after:
                    log.warning(
                        "WS %d consecutive failures, signalling fallback: %s",
                        self.consecutive_failures,
                        exc,
                    )
                    # Caller monitors consecutive_failures to switch to REST
                    return
                delay = min(_BACKOFF[min(attempt, len(_BACKOFF) - 1)], self._max_backoff)
                log.warning("WS error (attempt %d), reconnect in %.1fs: %s", attempt, delay, exc)
                attempt += 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except TimeoutError:
                    pass

    async def _connect(
        self,
        queue: asyncio.Queue[Quote],
        stop: asyncio.Event,
    ) -> None:
        subscribe_msg = json.dumps(
            {
                "assets_ids": [self._token_id],
                "type": "Market",
                "custom_feature_enabled": True,
            }
        )
        log.info("WS connecting to %s", self._url)
        async with websockets.connect(  # type: ignore[attr-defined]
            self._url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            log.info("WS connected; subscribing token_id=%s", self._token_id)
            await ws.send(subscribe_msg)

            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=25.0)
                except TimeoutError:
                    log.debug("WS recv timeout — sending ping")
                    await ws.ping()
                    continue

                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Polymarket may send a list of events or a single dict
                events: list[dict] = data if isinstance(data, list) else [data]
                for event in events:
                    quote = _extract_quote(event, self._token_id)
                    if quote is not None:
                        await queue.put(quote)
