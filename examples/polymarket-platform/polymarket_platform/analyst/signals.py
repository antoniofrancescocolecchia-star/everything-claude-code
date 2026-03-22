"""
PredictionSignal and supporting types for Layer 3 analyst output.

prediction_id: uuid4, unique per analysis attempt.
thesis_id:     sha256-derived, stable per (token_id, calendar-day).
               Groups repeated analyses of the same market across a day.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class AnalysisStatus(StrEnum):
    VALID = "VALID"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    STALE = "STALE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    LOW_EDGE = "LOW_EDGE"
    SKIP_PRICE = "SKIP_PRICE"
    NO_EVIDENCE = "NO_EVIDENCE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


@dataclass
class EvidenceRecord:
    """One retrieved source record (persisted in analyst_evidence table)."""

    url: str
    title: str
    snippet: str
    published_at: str | None
    age_minutes: float | None
    domain: str


@dataclass
class PredictionSignal:
    """
    The interface between Layer 3 (analyst) and Layer 2 (orchestrator).

    Only signals with status=VALID and forwarded=True are passed to
    the orchestrator for allocation decisions.
    """

    prediction_id: str          # uuid4 per attempt
    thesis_id: str              # stable per (token_id, day)
    token_id: str
    market_slug: str
    question: str
    fair_probability: float
    confidence: float
    evidence_summary: str
    evidence_age_minutes: float
    sources: list[str]
    edge_bps: float
    side: Side
    midpoint_at_analysis: float | None
    model_used: str
    api_cost_usd: float
    search_cost_usd: float
    input_token_cost_usd: float
    output_token_cost_usd: float
    status: AnalysisStatus
    forwarded: bool
    ts: float
    # Used only for writing to analyst_evidence; not persisted in predictions
    evidence_records: list[EvidenceRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_prediction_id() -> str:
    """Generate a unique prediction ID (uuid4 hex string)."""
    return str(uuid.uuid4())


def make_thesis_id(token_id: str, analysis_date: date | None = None) -> str:
    """
    Deterministic thesis ID: same token + same calendar day -> same ID.
    Derived from sha256(token_id:YYYY-MM-DD), hex[:16].
    """
    d = analysis_date or date.today()
    raw = f"{token_id}:{d.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_edge(fair_probability: float, midpoint: float) -> tuple[float, Side]:
    """
    edge_bps > 0  ->  BUY  (fair price above market; market is undervaluing YES)
    edge_bps < 0  ->  SELL (fair price below market; market is overvaluing YES)
    """
    edge_bps = (fair_probability - midpoint) * 10_000.0
    side = Side.BUY if edge_bps >= 0 else Side.SELL
    return edge_bps, side


def make_invalid_signal(
    *,
    token_id: str,
    market_slug: str,
    question: str,
    status: AnalysisStatus,
    midpoint: float | None = None,
    model_used: str = "",
) -> PredictionSignal:
    """Construct a non-VALID signal for guardrail failures."""
    return PredictionSignal(
        prediction_id=make_prediction_id(),
        thesis_id=make_thesis_id(token_id),
        token_id=token_id,
        market_slug=market_slug,
        question=question,
        fair_probability=0.0,
        confidence=0.0,
        evidence_summary="",
        evidence_age_minutes=0.0,
        sources=[],
        edge_bps=0.0,
        side=Side.NONE,
        midpoint_at_analysis=midpoint,
        model_used=model_used,
        api_cost_usd=0.0,
        search_cost_usd=0.0,
        input_token_cost_usd=0.0,
        output_token_cost_usd=0.0,
        status=status,
        forwarded=False,
        ts=time.time(),
    )
