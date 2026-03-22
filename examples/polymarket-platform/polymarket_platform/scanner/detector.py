"""
Opportunity detector: deterministic signal detection on market snapshots.

Signal types (v1):
  SPREAD_ANOMALY      -- spread_bps > 2.5x historical average
  RAPID_MOVEMENT      -- |midpoint_now - midpoint_oldest| >= 0.05
  EXTREME_PRICE       -- price near 0 or 1 with meaningful remaining lifetime
  COMPLEMENT_SKEW     -- YES_price + NO_price < 1.0 - threshold (both underpriced)

All signals produce structured OpportunityRecord objects with explicit
confidence [0.0-1.0] and expected_edge_bps values.  No hidden magic.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.scanner.catalog import MarketRecord
    from polymarket_platform.scanner.clob_probe import ClobQuote

log = logging.getLogger(__name__)

# Tuning constants -- explicit and inspectable.
_SPREAD_ANOMALY_MULTIPLIER = 2.5   # current > 2.5x avg => anomalous
_MOVEMENT_THRESHOLD = 0.05         # 5 pp midpoint change to flag
_EXTREME_PRICE_LOW = 0.05          # price <= 5c
_EXTREME_PRICE_HIGH = 0.95         # price >= 95c
_EXTREME_MIN_DAYS = 2.0            # only flag if >= 2 days to expiry
_COMPLEMENT_THRESHOLD = 0.05       # |YES + NO - 1.0| > 5% to flag
_MIN_HIST_SAMPLES = 3              # minimum history samples for spread anomaly


@dataclass
class OpportunityRecord:
    token_id: str
    slug: str
    event_slug: str | None
    outcome: str              # "YES" | "NO"
    side: str                 # "BUY" | "SELL"
    confidence: float         # 0.0 - 1.0
    expected_edge_bps: float
    reason: str               # human-readable signal summary
    midpoint: float | None
    spread_bps: float | None
    ts: float


def detect(
    market_records: list[MarketRecord],
    clob_quotes: dict[str, ClobQuote],
    historical_midpoints: dict[str, list[float]],
    historical_spreads: dict[str, list[float]],
    *,
    max_spread_bps: float,
    min_confidence: float = 0.10,
) -> list[OpportunityRecord]:
    """
    Detect trading opportunities from market records and CLOB quotes.

    Args:
        market_records:       Filtered, scored catalog records.
        clob_quotes:          Current CLOB probe results keyed by token_id.
        historical_midpoints: token_id -> list of midpoints (oldest first).
        historical_spreads:   token_id -> list of spread_bps (oldest first).
        max_spread_bps:       Hard limit; tokens with wider spread are skipped.
        min_confidence:       Minimum confidence threshold to emit an opportunity.

    Returns:
        List of OpportunityRecord sorted by confidence descending.
    """
    now = time.time()
    opportunities: list[OpportunityRecord] = []

    # Build condition groups for complement checks.
    condition_groups: dict[str, list[MarketRecord]] = {}
    for rec in market_records:
        condition_groups.setdefault(rec.condition_id, []).append(rec)

    seen_conditions: set[str] = set()

    for rec in market_records:
        quote = clob_quotes.get(rec.token_id)
        if quote is None:
            continue

        mid = quote.midpoint
        spread = quote.spread_bps

        if spread is not None and spread > max_spread_bps:
            continue
        if mid is None:
            continue

        signals: list[tuple[float, float, str]] = []  # (confidence, edge_bps, reason)

        # -- Signal 1: SPREAD_ANOMALY -----------------------------------------
        hist_spreads = historical_spreads.get(rec.token_id, [])
        if (
            spread is not None
            and len(hist_spreads) >= _MIN_HIST_SAMPLES
        ):
            avg = sum(hist_spreads) / len(hist_spreads)
            if avg > 0 and spread > _SPREAD_ANOMALY_MULTIPLIER * avg:
                conf = min((spread / avg - _SPREAD_ANOMALY_MULTIPLIER) / 2.0, 0.80)
                edge = spread - avg
                signals.append(
                    (conf, edge, f"spread_anomaly spread={spread:.0f}bps avg={avg:.0f}bps")
                )

        # -- Signal 2: RAPID_MOVEMENT -----------------------------------------
        hist_mids = historical_midpoints.get(rec.token_id, [])
        if len(hist_mids) >= 2:
            oldest = hist_mids[0]
            movement = abs(mid - oldest)
            if movement >= _MOVEMENT_THRESHOLD:
                conf = min(movement / 0.20, 0.75)
                edge = movement * 10_000.0
                signals.append(
                    (
                        conf,
                        edge,
                        f"rapid_movement delta={movement:.3f} from={oldest:.3f} to={mid:.3f}",
                    )
                )

        # -- Signal 3: EXTREME_PRICE ------------------------------------------
        days = rec.days_to_expiry
        if days is not None and days >= _EXTREME_MIN_DAYS:
            if mid <= _EXTREME_PRICE_LOW:
                conf = min((1.0 - mid / _EXTREME_PRICE_LOW) * 0.50, 0.50)
                edge = (_EXTREME_PRICE_LOW - mid) * 10_000.0
                signals.append(
                    (conf, edge, f"extreme_low price={mid:.3f} days={days:.1f}")
                )
            elif mid >= _EXTREME_PRICE_HIGH:
                conf = min(
                    ((mid - _EXTREME_PRICE_HIGH) / (1.0 - _EXTREME_PRICE_HIGH)) * 0.50, 0.50
                )
                edge = (mid - _EXTREME_PRICE_HIGH) * 10_000.0
                signals.append(
                    (conf, edge, f"extreme_high price={mid:.3f} days={days:.1f}")
                )

        # -- Signal 4: COMPLEMENT_SKEW (once per condition) -------------------
        if rec.condition_id not in seen_conditions:
            seen_conditions.add(rec.condition_id)
            group = condition_groups.get(rec.condition_id, [])
            if len(group) >= 2:
                opp = _check_complement_skew(group, clob_quotes, now)
                if opp is not None:
                    opportunities.append(opp)

        # Aggregate token-level signals
        if not signals:
            continue

        best_conf = max(c for c, _, _ in signals)
        best_edge = max(e for _, e, _ in signals)
        combined_reason = "; ".join(r for _, _, r in signals)

        if best_conf < min_confidence:
            continue

        side = _infer_side(mid, hist_mids)

        opportunities.append(
            OpportunityRecord(
                token_id=rec.token_id,
                slug=rec.slug,
                event_slug=rec.event_slug,
                outcome=rec.outcome,
                side=side,
                confidence=best_conf,
                expected_edge_bps=best_edge,
                reason=combined_reason,
                midpoint=mid,
                spread_bps=spread,
                ts=now,
            )
        )

    opportunities.sort(key=lambda o: o.confidence, reverse=True)
    return opportunities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_side(mid: float, hist_mids: list[float]) -> str:
    """Infer trade direction from price level and recent movement."""
    if hist_mids and len(hist_mids) >= 2:
        if mid < hist_mids[0]:
            return "BUY"   # price fell -- might be cheap
        if mid > hist_mids[0]:
            return "SELL"  # price rose -- might be expensive
    return "BUY" if mid < 0.50 else "SELL"


def _check_complement_skew(
    group: list[MarketRecord],
    clob_quotes: dict[str, ClobQuote],
    now: float,
) -> OpportunityRecord | None:
    """
    Check whether YES + NO price sum deviates meaningfully from 1.0.

    Handles only binary (2-outcome) markets. Uses CLOB midpoints when
    available, falls back to Gamma outcome_prices.

    Returns an opportunity for the underpriced token, or None.
    """
    if len(group) != 2:
        return None

    prices: list[float] = []
    for rec in group:
        q = clob_quotes.get(rec.token_id)
        prices.append(q.midpoint if (q and q.midpoint is not None) else rec.outcome_price)

    price_sum = sum(prices)
    deviation = 1.0 - price_sum  # positive => both underpriced

    if deviation <= _COMPLEMENT_THRESHOLD:
        return None  # no meaningful skew

    # Both tokens are cheap; pick the cheaper one as the better BUY.
    cheapest_idx = prices.index(min(prices))
    target_rec = group[cheapest_idx]
    target_quote = clob_quotes.get(target_rec.token_id)

    conf = min(deviation / 0.20, 0.70)
    edge = deviation * 10_000.0 / 2.0  # split edge across both sides

    return OpportunityRecord(
        token_id=target_rec.token_id,
        slug=target_rec.slug,
        event_slug=target_rec.event_slug,
        outcome=target_rec.outcome,
        side="BUY",
        confidence=conf,
        expected_edge_bps=edge,
        reason=(
            f"complement_skew YES={prices[0]:.3f} NO={prices[1]:.3f} "
            f"sum={price_sum:.3f} dev={deviation:.3f}"
        ),
        midpoint=prices[cheapest_idx],
        spread_bps=target_quote.spread_bps if target_quote else None,
        ts=now,
    )
