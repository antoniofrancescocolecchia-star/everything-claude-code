"""
Read-only CLOB probing: midpoint and spread for a batch of token IDs.

No authentication required. Uses asyncio.Semaphore to limit concurrency and
avoid hammering the CLOB REST API.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass
class ClobQuote:
    token_id: str
    midpoint: float | None
    spread_bps: float | None

    @property
    def is_valid(self) -> bool:
        return self.midpoint is not None and self.spread_bps is not None


class ClobProbe:
    """
    Probes CLOB /midpoint and /spread for each requested token.
    Max-concurrent requests are bounded by the semaphore.
    Failures are logged at DEBUG and return None fields (not raised).
    """

    def __init__(
        self,
        clob_host: str = "https://clob.polymarket.com",
        timeout_seconds: float = 10.0,
        max_concurrent: int = 5,
    ) -> None:
        self._base = clob_host.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds)
        self._sem = asyncio.Semaphore(max_concurrent)

    async def probe_one(self, token_id: str) -> ClobQuote:
        """Fetch midpoint and spread for one token."""
        midpoint = await self._fetch_midpoint(token_id)
        spread_bps = await self._fetch_spread_bps(token_id, midpoint)
        return ClobQuote(token_id=token_id, midpoint=midpoint, spread_bps=spread_bps)

    async def probe_batch(self, token_ids: list[str]) -> dict[str, ClobQuote]:
        """
        Probe multiple tokens concurrently.
        Always returns an entry for every requested token_id.
        """
        tasks = [
            asyncio.create_task(self.probe_one(tid), name=f"probe-{tid[:10]}")
            for tid in token_ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, ClobQuote] = {}
        for tid, res in zip(token_ids, results):
            if isinstance(res, ClobQuote):
                out[tid] = res
            else:
                log.debug("probe_batch error for %s: %s", tid[:12], res)
                out[tid] = ClobQuote(token_id=tid, midpoint=None, spread_bps=None)
        return out

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------

    async def _fetch_midpoint(self, token_id: str) -> float | None:
        try:
            async with self._sem:
                resp = await self._client.get(
                    f"{self._base}/midpoint", params={"token_id": token_id}
                )
            if resp.status_code == 200:
                return _extract_float(resp.json(), "mid")
        except Exception as exc:
            log.debug("midpoint probe failed %s: %s", token_id[:12], exc)
        return None

    async def _fetch_spread_bps(self, token_id: str, midpoint: float | None) -> float | None:
        try:
            async with self._sem:
                resp = await self._client.get(
                    f"{self._base}/spread", params={"token_id": token_id}
                )
            if resp.status_code == 200:
                spread_abs = _extract_float(resp.json(), "spread")
                if spread_abs is not None and midpoint is not None and midpoint > 1e-9:
                    return (spread_abs / midpoint) * 10_000.0
        except Exception as exc:
            log.debug("spread probe failed %s: %s", token_id[:12], exc)
        return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _extract_float(data: Any, key: str) -> float | None:
    if isinstance(data, (int, float)):
        return float(data)
    if isinstance(data, str):
        try:
            return float(data)
        except ValueError:
            return None
    if isinstance(data, dict):
        val = data.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None
