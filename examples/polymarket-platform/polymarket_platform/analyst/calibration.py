"""
Calibration tracking and reporting for Layer 3.

Brier score: (fair_probability - outcome)^2
  0.00 = perfect
  0.25 = random baseline
  1.00 = perfectly wrong

The analyst must beat BOTH:
  1. Random baseline: avg Brier < 0.25
  2. Market-implied baseline: Brier using Polymarket midpoint as the prediction

Confidence calibration must be monotonically increasing:
  mean outcome rate in low-confidence bucket < mean in high-confidence bucket
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polymarket_platform.config import Settings
    from polymarket_platform.db.store import SqliteStore

_BUCKETS = ["0.0-0.3", "0.3-0.5", "0.5-0.7", "0.7-1.0"]
_BUCKET_EDGES = [(0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 1.0)]


def compute_brier_score(fair_probability: float, outcome: int) -> float:
    """Brier score for a single prediction. outcome must be 0 or 1."""
    return (fair_probability - outcome) ** 2


def confidence_bucket(confidence: float) -> str:
    """Return the bucket label for a confidence value."""
    for label, (lo, hi) in zip(_BUCKETS, _BUCKET_EDGES):
        if lo <= confidence <= hi:
            return label
    return _BUCKETS[-1]


@dataclass
class CalibrationReport:
    total_predictions: int
    resolved_predictions: int
    avg_brier_score: float | None         # analyst predictions
    market_implied_brier: float | None    # Polymarket-implied predictions
    brier_by_bucket: dict[str, float | None] = field(default_factory=dict)
    total_api_cost_usd: float = 0.0
    simulated_pnl_usd: float = 0.0       # naive: +edge if correct, -edge if wrong
    beats_random: bool = False
    beats_market: bool = False
    is_monotone_calibrated: bool = False

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        lines.append(f"Total predictions:     {self.total_predictions}")
        lines.append(f"Resolved predictions:  {self.resolved_predictions}")
        if self.avg_brier_score is not None:
            lines.append(
                f"Avg Brier score:       {self.avg_brier_score:.4f}"
                f"  (random=0.25, perfect=0.0)"
            )
        else:
            lines.append("Avg Brier score:       N/A (no resolved predictions)")
        if self.market_implied_brier is not None:
            lines.append(f"Market-implied Brier:  {self.market_implied_brier:.4f}")
        lines.append(f"Beats random:          {self.beats_random}")
        lines.append(f"Beats market:          {self.beats_market}")
        lines.append(f"Monotone calibrated:   {self.is_monotone_calibrated}")
        lines.append(f"Total API cost:        ${self.total_api_cost_usd:.4f}")
        lines.append(f"Simulated PnL:         ${self.simulated_pnl_usd:.4f}")
        if self.brier_by_bucket:
            lines.append("Brier by bucket:")
            for bucket, score in sorted(self.brier_by_bucket.items()):
                s = f"{score:.4f}" if score is not None else "N/A"
                lines.append(f"  {bucket}:  {s}")
        return lines


def build_report(store: SqliteStore) -> CalibrationReport:
    """
    Compute a calibration report from the DB.

    Reads analyst_calibration for resolved rows, analyst_costs for spend.
    """
    total_preds = store.count_analyst_predictions()
    resolved_rows = store.get_resolved_calibration_rows()
    total_cost = store.total_analyst_cost_usd()

    if not resolved_rows:
        return CalibrationReport(
            total_predictions=total_preds,
            resolved_predictions=0,
            avg_brier_score=None,
            market_implied_brier=None,
            total_api_cost_usd=total_cost,
        )

    analyst_scores: list[float] = []
    market_scores: list[float] = []
    bucket_scores: dict[str, list[float]] = {b: [] for b in _BUCKETS}
    simulated_pnl = 0.0

    for row in resolved_rows:
        outcome = row["outcome"]
        if outcome is None:
            continue
        fp = float(row["fair_probability"])
        mp = float(row["market_implied_probability"])
        conf = float(row.get("confidence_value", 0.5))
        edge = (fp - mp) * 10_000.0

        analyst_bs = compute_brier_score(fp, outcome)
        market_bs = compute_brier_score(mp, outcome)
        analyst_scores.append(analyst_bs)
        market_scores.append(market_bs)

        bkt = confidence_bucket(conf)
        bucket_scores[bkt].append(analyst_bs)

        if abs(edge) > 0:
            direction_correct = (edge > 0 and outcome == 1) or (
                edge < 0 and outcome == 0
            )
            simulated_pnl += (
                abs(edge) / 10_000.0 if direction_correct else -abs(edge) / 10_000.0
            )

    n = len(analyst_scores)
    avg_bs = sum(analyst_scores) / n if n else None
    avg_market = sum(market_scores) / n if n else None

    by_bucket: dict[str, float | None] = {}
    for b, scores in bucket_scores.items():
        by_bucket[b] = sum(scores) / len(scores) if scores else None

    beats_random = avg_bs is not None and avg_bs < 0.25
    beats_market = (
        avg_bs is not None and avg_market is not None and avg_bs < avg_market
    )
    is_mono = _check_monotone_calibration(resolved_rows)

    return CalibrationReport(
        total_predictions=total_preds,
        resolved_predictions=n,
        avg_brier_score=avg_bs,
        market_implied_brier=avg_market,
        brier_by_bucket=by_bucket,
        total_api_cost_usd=total_cost,
        simulated_pnl_usd=simulated_pnl,
        beats_random=beats_random,
        beats_market=beats_market,
        is_monotone_calibrated=is_mono,
    )


def is_live_trading_unlocked(store: SqliteStore, cfg: Settings) -> bool:
    """
    Return True only when all five unlock conditions are met:
      1. ANALYST_TRADING_ENABLED=True
      2. Resolved predictions >= ANALYST_MIN_RESOLVED_PREDICTIONS
      3. Brier < 0.25 (beats random)
      4. Brier < market-implied Brier (beats Polymarket)
      5. Monotonically increasing calibration across confidence buckets
    """
    if not cfg.analyst_trading_enabled:
        return False
    report = build_report(store)
    if report.resolved_predictions < cfg.analyst_min_resolved_predictions:
        return False
    if not report.beats_random:
        return False
    if not report.beats_market:
        return False
    return report.is_monotone_calibrated


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_monotone_calibration(rows: list[dict]) -> bool:
    """
    Verify that mean actual-outcome rate is monotonically non-decreasing
    across confidence buckets (low -> high).
    """
    bucket_outcomes: dict[str, list[int]] = {b: [] for b in _BUCKETS}
    for row in rows:
        if row.get("outcome") is None:
            continue
        cv = row.get("confidence_value")
        if cv is None:
            continue
        b = confidence_bucket(float(cv))
        bucket_outcomes[b].append(int(row["outcome"]))

    means: list[float] = []
    for b in _BUCKETS:
        vals = bucket_outcomes[b]
        if vals:
            means.append(sum(vals) / len(vals))

    if len(means) < 2:
        return True  # Not enough data across buckets to evaluate
    return all(means[i] <= means[i + 1] for i in range(len(means) - 1))
