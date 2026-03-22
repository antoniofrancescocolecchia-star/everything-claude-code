"""
Group Layer 2 MarketRecords by Gamma event slug for co-event analysis.

Budget rule: one retrieval search per EventGroup, regardless of token count.
  - Event groups (multiple tokens sharing an event_slug) cost 1 search.
  - Isolated markets (no event_slug) each cost 1 search.

Budget coherence check:
  max_event_groups_per_cycle <= max_searches_per_cycle
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.scanner.catalog import MarketRecord


@dataclass
class EventGroup:
    """
    A set of markets analysed together in one LLM call.

    event_slug=None  => isolated market (no Gamma event parent).
    search_count     => always 1, regardless of how many tokens are in the group.
    """

    event_slug: str | None
    markets: list[MarketRecord] = field(default_factory=list)

    @property
    def is_multi(self) -> bool:
        return len(self.markets) > 1

    @property
    def search_count(self) -> int:
        """Budget unit: always 1 per group."""
        return 1

    @property
    def primary_question(self) -> str:
        """Question used as the retrieval query."""
        return self.markets[0].question if self.markets else ""


def group_by_event(markets: list[MarketRecord]) -> list[EventGroup]:
    """
    Group markets by event_slug.

    Markets that share an event_slug are analysed together.
    Markets without an event_slug each form a singleton group.

    Ordering:
      1. Multi-market groups first (higher analytical value per search cost).
      2. Within each tier, sorted by highest market score descending.
    """
    buckets: dict[str, list[MarketRecord]] = {}
    for m in markets:
        key = m.event_slug if m.event_slug else f"__iso__{m.token_id}"
        buckets.setdefault(key, []).append(m)

    groups: list[EventGroup] = []
    for key, mlist in buckets.items():
        event_slug = None if key.startswith("__iso__") else key
        mlist.sort(key=lambda r: r.score, reverse=True)
        groups.append(EventGroup(event_slug=event_slug, markets=mlist))

    groups.sort(
        key=lambda g: (-len(g.markets), -(g.markets[0].score if g.markets else 0.0))
    )
    return groups
