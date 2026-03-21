from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def quantize(value: float, decimals: int) -> float:
    q = Decimal("1").scaleb(-decimals)  # 10^-decimals
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_DOWN))


def to_float_price(resp: Any) -> float:
    if resp is None:
        raise ValueError("price response is None")
    if isinstance(resp, (int, float)):
        return float(resp)
    if isinstance(resp, str):
        return float(resp)
    if isinstance(resp, dict):
        for key in ("price", "mid", "spread", "last_trade_price"):
            if key in resp:
                return float(resp[key])
    raise ValueError(f"unrecognized price response shape: {type(resp)} {resp}")


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


class Cooldown:
    def __init__(self) -> None:
        self._last: float = 0.0

    def ok(self, min_seconds: float) -> bool:
        return (time.time() - self._last) >= min_seconds

    def hit(self) -> None:
        self._last = time.time()
