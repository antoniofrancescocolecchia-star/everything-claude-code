from __future__ import annotations

import asyncio
import logging

import httpx

from polymarket_platform.feed.base import FeedSource, Quote

log = logging.getLogger(__name__)


def _parse_price(resp: object) -> float:
    """Extract float price from various CLOB /price response shapes."""
    if isinstance(resp, (int, float)):
        return float(resp)
    if isinstance(resp, str):
        return float(resp)
    if isinstance(resp, dict):
        for key in ("price", "mid", "last_trade_price"):
            if key in resp:
                return float(resp[key])  # type: ignore[arg-type]
    raise ValueError(f"unrecognised price shape: {type(resp)} {resp!r}")


class RestFeed:
    """
    Polls CLOB REST /price endpoint as fallback when WS is unavailable.
    Uses the side semantics: GET /price?token_id=X&side=BUY  → best ask
                             GET /price?token_id=X&side=SELL → best bid
    """

    def __init__(
        self,
        *,
        clob_host: str,
        token_id: str,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._url = clob_host.rstrip("/") + "/price"
        self._token_id = token_id
        self._interval = poll_interval_seconds
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def run(
        self,
        queue: asyncio.Queue[Quote],
        stop: asyncio.Event,
    ) -> None:
        log.info("REST feed started (fallback mode), interval=%.1fs", self._interval)
        while not stop.is_set():
            try:
                best_ask_raw = await self._client.get(
                    self._url, params={"token_id": self._token_id, "side": "BUY"}
                )
                best_ask_raw.raise_for_status()

                best_bid_raw = await self._client.get(
                    self._url, params={"token_id": self._token_id, "side": "SELL"}
                )
                best_bid_raw.raise_for_status()

                best_ask = _parse_price(best_ask_raw.json())
                best_bid = _parse_price(best_bid_raw.json())

                await queue.put(
                    Quote.now(
                        token_id=self._token_id,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        source=FeedSource.REST,
                    )
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("REST feed error: %s", exc)

            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        await self._client.aclose()
