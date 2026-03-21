from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class FeedSource(StrEnum):
    WS = "ws"
    REST = "rest"


@dataclass(frozen=True)
class Quote:
    token_id: str
    best_bid: float
    best_ask: float
    ts: float
    source: FeedSource

    @classmethod
    def now(
        cls, *, token_id: str, best_bid: float, best_ask: float, source: FeedSource
    ) -> Quote:
        return cls(
            token_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            ts=time.time(),
            source=source,
        )


class FeedManager(Protocol):
    """A feed pushes Quotes into a queue until the stop event is set."""

    async def run(
        self,
        queue: asyncio.Queue[Quote],
        stop: asyncio.Event,
    ) -> None: ...
