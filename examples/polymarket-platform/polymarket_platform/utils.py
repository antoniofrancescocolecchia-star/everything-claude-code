from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Backoff:
    base_seconds: float = 0.4
    max_seconds: float = 5.0
    jitter: float = 0.25

    def compute(self, attempt: int) -> float:
        raw = min(self.max_seconds, self.base_seconds * (2 ** max(0, attempt - 1)))
        return max(0.0, raw + random.uniform(-self.jitter, self.jitter))


async def with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int,
    backoff: Backoff | None = None,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    backoff = backoff or Backoff()
    last_exc: BaseException | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return await fn()
        except retry_on as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            await asyncio.sleep(backoff.compute(attempt))
    assert last_exc is not None
    raise last_exc
