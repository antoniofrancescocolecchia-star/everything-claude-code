"""
API cost and rate-limit budget tracker for Layer 3.

Keeps an in-memory sliding window for fast per-cycle checks.
DB queries provide accurate hourly/daily totals after restarts.

Budget dimensions tracked:
  - cost per hour (USD)
  - cost per day (USD)
  - input tokens per hour
  - output tokens per hour
  - 429 rate-limit events per hour
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore

log = logging.getLogger(__name__)

_HOUR = 3600.0
_DAY = 86_400.0


@dataclass
class _CostEvent:
    ts: float
    search_usd: float
    input_token_usd: float
    output_token_usd: float
    input_tokens: int
    output_tokens: int


@dataclass
class _Window:
    """Sliding-window accumulator for one time period."""

    duration_seconds: float
    events: deque[_CostEvent] = field(default_factory=deque)
    rate_limit_events: deque[float] = field(default_factory=deque)

    def _evict(self, now: float) -> None:
        cutoff = now - self.duration_seconds
        while self.events and self.events[0].ts < cutoff:
            self.events.popleft()
        while self.rate_limit_events and self.rate_limit_events[0] < cutoff:
            self.rate_limit_events.popleft()

    def add(self, event: _CostEvent) -> None:
        self.events.append(event)

    def add_429(self, ts: float) -> None:
        self.rate_limit_events.append(ts)

    def total_cost(self, now: float) -> float:
        self._evict(now)
        return sum(
            e.search_usd + e.input_token_usd + e.output_token_usd
            for e in self.events
        )

    def total_input_tokens(self, now: float) -> int:
        self._evict(now)
        return sum(e.input_tokens for e in self.events)

    def total_output_tokens(self, now: float) -> int:
        self._evict(now)
        return sum(e.output_tokens for e in self.events)

    def total_429s(self, now: float) -> int:
        self._evict(now)
        return len(self.rate_limit_events)


class CostTracker:
    """
    Thread-compatible (but not thread-safe) cost tracker.

    Call record() after each analysis. Call has_headroom() before each.
    The store is written for persistence across restarts; the in-memory
    deques provide fast intra-process budget checks.
    """

    def __init__(self, cfg: Settings, store: SqliteStore) -> None:
        self._cfg = cfg
        self._store = store
        self._hourly = _Window(duration_seconds=_HOUR)
        self._daily = _Window(duration_seconds=_DAY)

    def record(
        self,
        *,
        prediction_id: str,
        search_usd: float,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None:
        """Record costs for one analysis attempt."""
        from polymarket_platform.analyst.llm import token_cost_usd

        _, input_usd, output_usd = token_cost_usd(model, input_tokens, output_tokens)
        event = _CostEvent(
            ts=time.time(),
            search_usd=search_usd,
            input_token_usd=input_usd,
            output_token_usd=output_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self._hourly.add(event)
        self._daily.add(event)
        # Persist to DB for cross-restart accuracy
        self._store.insert_analyst_costs(
            prediction_id=prediction_id,
            search_usd=search_usd,
            input_token_usd=input_usd,
            output_token_usd=output_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def record_429(self) -> None:
        ts = time.time()
        self._hourly.add_429(ts)
        self._daily.add_429(ts)

    def hourly_cost(self) -> float:
        return self._hourly.total_cost(time.time())

    def daily_cost(self) -> float:
        return self._daily.total_cost(time.time())

    def hourly_input_tokens(self) -> int:
        return self._hourly.total_input_tokens(time.time())

    def hourly_output_tokens(self) -> int:
        return self._hourly.total_output_tokens(time.time())

    def hourly_429s(self) -> int:
        return self._hourly.total_429s(time.time())

    def has_headroom(self) -> bool:
        """
        Return True if all budget dimensions have remaining capacity.
        Logs the first dimension that is over budget.
        """
        cfg = self._cfg
        now = time.time()

        hourly_cost = self._hourly.total_cost(now)
        if hourly_cost >= cfg.analyst_max_cost_per_hour_usd:
            log.warning(
                "Analyst hourly cost cap reached: $%.3f >= $%.3f",
                hourly_cost,
                cfg.analyst_max_cost_per_hour_usd,
            )
            return False

        daily_cost = self._daily.total_cost(now)
        if daily_cost >= cfg.analyst_max_cost_per_day_usd:
            log.warning(
                "Analyst daily cost cap reached: $%.3f >= $%.3f",
                daily_cost,
                cfg.analyst_max_cost_per_day_usd,
            )
            return False

        input_tokens = self._hourly.total_input_tokens(now)
        if input_tokens >= cfg.analyst_max_input_tokens_per_hour:
            log.warning(
                "Analyst hourly input token cap: %d >= %d",
                input_tokens,
                cfg.analyst_max_input_tokens_per_hour,
            )
            return False

        output_tokens = self._hourly.total_output_tokens(now)
        if output_tokens >= cfg.analyst_max_output_tokens_per_hour:
            log.warning(
                "Analyst hourly output token cap: %d >= %d",
                output_tokens,
                cfg.analyst_max_output_tokens_per_hour,
            )
            return False

        rate_limits = self._hourly.total_429s(now)
        if rate_limits >= cfg.analyst_max_429s_per_hour:
            log.warning(
                "Analyst hourly 429 cap: %d >= %d",
                rate_limits,
                cfg.analyst_max_429s_per_hour,
            )
            return False

        return True
