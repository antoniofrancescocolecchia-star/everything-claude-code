from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Position:
    asset: str
    size: float
    avg_price: float | None
    title: str | None
    outcome: str | None


class DataApiClient:
    """Async client for the Polymarket Data API."""

    def __init__(self, base_url: str, timeout_seconds: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def get_positions(self, user: str, limit: int = 200) -> list[Position]:
        """GET /positions?user=ADDRESS — returns all positions for the address."""
        resp = await self._client.get(
            f"{self._base_url}/positions",
            params={"user": user, "limit": limit},
        )
        resp.raise_for_status()
        data: list[dict[str, Any]] = resp.json()
        return [
            Position(
                asset=str(row.get("asset") or ""),
                size=float(row.get("size") or 0.0),
                avg_price=(
                    float(row["avgPrice"]) if row.get("avgPrice") is not None else None
                ),
                title=(str(row["title"]) if row.get("title") is not None else None),
                outcome=(str(row["outcome"]) if row.get("outcome") is not None else None),
            )
            for row in data
        ]

    async def close(self) -> None:
        await self._client.aclose()
