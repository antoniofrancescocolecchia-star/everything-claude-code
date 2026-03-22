"""
Market catalog: filter raw Gamma markets and compute a deterministic tradability score.

Scoring is explicit, inspectable, and requires no external calls.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.scanner.gamma_client import RawMarket

# Scoring weights (must sum to 1.0)
_W_LIQUIDITY = 0.40
_W_VOLUME = 0.35
_W_EXPIRY = 0.25


@dataclass
class MarketRecord:
    """Filtered and scored market token, ready for opportunity detection."""

    token_id: str
    condition_id: str
    slug: str
    event_slug: str | None
    question: str
    outcome: str          # "YES" | "NO" | index-based string
    outcome_price: float  # Current price from Gamma outcomePrices
    end_date_ts: float | None
    days_to_expiry: float | None
    liquidity: float
    volume_24h: float
    score: float          # Composite tradability score [0.0, 1.0]
    # All token IDs for this condition (for complement checks)
    all_token_ids: list[str]
    all_outcome_prices: list[float]


def score_market(
    *,
    liquidity: float,
    volume_24h: float,
    days_to_expiry: float | None,
    min_liquidity: float,
    min_volume: float,
) -> float:
    """
    Compute composite tradability score in [0.0, 1.0].

    Components:
      liquidity_score: clamp(liquidity / (10 * min_liquidity), 0, 1)
      volume_score:    clamp(volume_24h / (5 * min_volume),   0, 1)
      expiry_score:    piecewise based on days_to_expiry
        < 1 day  => 0.0 (too close)
        1-3 days => 0.2 (very near-term)
        3-30 days => 1.0 (sweet spot)
        > 30 days => decays toward 0.3 (too long-dated)
    """
    if liquidity <= 0 or volume_24h <= 0:
        return 0.0

    denom_liq = max(10.0 * min_liquidity, 1.0)
    denom_vol = max(5.0 * min_volume, 1.0)

    liq_score = min(liquidity / denom_liq, 1.0)
    vol_score = min(volume_24h / denom_vol, 1.0)

    if days_to_expiry is None or days_to_expiry <= 0:
        exp_score = 0.0
    elif days_to_expiry < 1.0:
        exp_score = 0.0
    elif days_to_expiry < 3.0:
        exp_score = 0.2
    elif days_to_expiry <= 30.0:
        exp_score = 1.0
    else:
        exp_score = max(0.30, 1.0 - (days_to_expiry - 30.0) / 90.0)

    raw = liq_score * _W_LIQUIDITY + vol_score * _W_VOLUME + exp_score * _W_EXPIRY
    return round(min(raw, 1.0), 6)


def filter_and_score(
    raw_markets: list[RawMarket],
    *,
    min_liquidity_usd: float,
    min_volume_24h_usd: float,
    min_days_to_expiry: float,
    limit: int = 100,
) -> list[MarketRecord]:
    """
    Apply hard filters to raw Gamma markets, compute scores, return sorted list.

    Hard filters (any failure => excluded):
      - active=True, closed=False, enable_order_book=True
      - liquidity >= min_liquidity_usd
      - volume_24h >= min_volume_24h_usd
      - days_to_expiry >= min_days_to_expiry (if end_date is known)

    Returns at most `limit` records, sorted by score descending.
    One MarketRecord is emitted per token (YES and NO tokens are separate records).
    """
    now = time.time()
    records: list[MarketRecord] = []

    for raw in raw_markets:
        if not raw.active or raw.closed or not raw.enable_order_book:
            continue
        if raw.liquidity < min_liquidity_usd:
            continue
        if raw.volume_24h < min_volume_24h_usd:
            continue
        if not raw.clob_token_ids:
            continue

        days_left: float | None = None
        if raw.end_date_ts is not None:
            days_left = (raw.end_date_ts - now) / 86_400.0
            if days_left < min_days_to_expiry:
                continue

        sc = score_market(
            liquidity=raw.liquidity,
            volume_24h=raw.volume_24h,
            days_to_expiry=days_left,
            min_liquidity=min_liquidity_usd,
            min_volume=min_volume_24h_usd,
        )

        for i, token_id in enumerate(raw.clob_token_ids):
            outcome = _outcome_label(i)
            price = raw.outcome_prices[i] if i < len(raw.outcome_prices) else 0.0
            records.append(
                MarketRecord(
                    token_id=token_id,
                    condition_id=raw.condition_id,
                    slug=raw.slug,
                    event_slug=raw.event_slug,
                    question=raw.question,
                    outcome=outcome,
                    outcome_price=price,
                    end_date_ts=raw.end_date_ts,
                    days_to_expiry=days_left,
                    liquidity=raw.liquidity,
                    volume_24h=raw.volume_24h,
                    score=sc,
                    all_token_ids=raw.clob_token_ids,
                    all_outcome_prices=raw.outcome_prices,
                )
            )

    records.sort(key=lambda r: r.score, reverse=True)
    return records[:limit]


def _outcome_label(index: int) -> str:
    return {0: "YES", 1: "NO"}.get(index, str(index))
