"""
Prompt builders for the Layer 3 analyst LLM calls.

Two entry points:
  build_single_market_prompt()  - for a single MarketRecord
  build_event_group_prompt()    - for a co-event group of MarketRecords

The system prompt is a module-level constant used for all analysis calls.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.analyst.event_grouper import EventGroup
    from polymarket_platform.analyst.retrieval import SearchResult
    from polymarket_platform.scanner.catalog import MarketRecord
    from polymarket_platform.scanner.clob_probe import ClobQuote

SYSTEM_PROMPT = """You are a probability analyst. Your job is to estimate the probability \
that a prediction market question resolves YES.

Base your estimate on the evidence provided. If evidence is insufficient, say so and \
assign low confidence.

Output calibrated probabilities. 0.70 means you expect YES 70% of the time in similar \
situations with similar evidence.

Do not hallucinate facts. If you do not know, say confidence is low.

You MUST respond using the provided tool. Do not include any text outside the tool call."""


def build_single_market_prompt(
    market: MarketRecord,
    midpoint: float | None,
    evidence: list[SearchResult],
) -> str:
    """Build the user-role prompt for single-market analysis."""
    lines: list[str] = []
    lines.append(f"Market Question: {market.question}")

    if market.days_to_expiry is not None:
        lines.append(f"Days to Expiry: {market.days_to_expiry:.1f}")

    if midpoint is not None:
        lines.append(f"Current Market Price (YES): {midpoint:.4f}")

    lines.append("")
    _append_evidence(lines, evidence)
    return "\n".join(lines)


def build_event_group_prompt(
    group: EventGroup,
    quotes: dict[str, ClobQuote],
    evidence: list[SearchResult],
) -> str:
    """
    Build the user-role prompt for a co-event group analysis.

    Lists each market in the group with its current price, then
    instructs the model to return consistent estimates for all tokens.
    """
    lines: list[str] = []
    lines.append(f"Event: {group.event_slug or 'Unknown'}")
    lines.append(f"Number of related markets: {len(group.markets)}")
    lines.append("")
    lines.append("Markets in this event:")
    for i, m in enumerate(group.markets, 1):
        q = quotes.get(m.token_id)
        mid_str = f"{q.midpoint:.4f}" if q and q.midpoint is not None else "unknown"
        days_str = f"{m.days_to_expiry:.1f}d" if m.days_to_expiry is not None else "?"
        lines.append(
            f"  [{i}] token_id={m.token_id}"
            f"  outcome={m.outcome}"
            f"  price={mid_str}"
            f"  expiry={days_str}"
        )
        lines.append(f"       Question: {m.question}")
    lines.append("")
    lines.append(
        "IMPORTANT: Your probability estimates across related markets must be "
        "consistent. For binary YES/NO pairs, the two probabilities should "
        "sum to approximately 1.0."
    )
    lines.append("")
    _append_evidence(lines, evidence)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_evidence(lines: list[str], evidence: list[SearchResult]) -> None:
    if not evidence:
        lines.append("Evidence: None retrieved.")
        return
    lines.append(f"Evidence ({len(evidence)} sources):")
    lines.append("")
    for i, src in enumerate(evidence, 1):
        age_str = (
            f"{src.age_minutes:.0f} minutes ago"
            if src.age_minutes is not None
            else "unknown age"
        )
        lines.append(f"[{i}] {src.title}")
        lines.append(f"    URL: {src.url}")
        lines.append(f"    Published: {src.published_at or 'unknown'} ({age_str})")
        lines.append(f"    {src.snippet}")
        lines.append("")
