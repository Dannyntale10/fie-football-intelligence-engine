"""Aggregate settled snapshots and expose the export the fit consumes."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.live.engine import load_intensity
from app.models.fixture import Fixture
from app.models.live import LivePrediction

OUTCOMES = ("home_win", "draw", "away_win")
BUCKETS = (
    ("0-14", 0, 14),
    ("15-29", 15, 29),
    ("30-44", 30, 44),
    ("45-59", 45, 59),
    ("60-74", 60, 74),
    ("75-90", 75, 90),
)
MIN_SAMPLE = 200
MARGIN = 0.002


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _probabilities(row: LivePrediction) -> Dict[str, float]:
    return {
        "home_win": float(row.prob_home),
        "draw": float(row.prob_draw),
        "away_win": float(row.prob_away),
    }


def _prematch(row: LivePrediction) -> Dict[str, float]:
    return {
        "home_win": float(row.prematch_home),
        "draw": float(row.prematch_draw),
        "away_win": float(row.prematch_away),
    }


def _metrics(rows: List[LivePrediction]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {
            "n": 0,
            "brier_live": None,
            "brier_prematch": None,
            "log_loss_live": None,
            "log_loss_prematch": None,
            "top_pick_accuracy": None,
        }

    brier_live = brier_prematch = log_live = log_prematch = 0.0
    correct = 0
    for row in rows:
        index = OUTCOMES.index(row.result)
        probs = _probabilities(row)
        prematch = _prematch(row)
        actual = {name: 1.0 if i == index else 0.0 for i, name in enumerate(OUTCOMES)}
        brier_live += sum((probs[name] - actual[name]) ** 2 for name in OUTCOMES)
        brier_prematch += sum((prematch[name] - actual[name]) ** 2 for name in OUTCOMES)
        log_live -= math.log(max(1e-9, probs[OUTCOMES[index]]))
        log_prematch -= math.log(max(1e-9, prematch[OUTCOMES[index]]))
        if max(OUTCOMES, key=lambda name: probs[name]) == OUTCOMES[index]:
            correct += 1

    return {
        "n": n,
        "brier_live": round(brier_live / n, 6),
        "brier_prematch": round(brier_prematch / n, 6),
        "log_loss_live": round(log_live / n, 6),
        "log_loss_prematch": round(log_prematch / n, 6),
        "top_pick_accuracy": round(correct / n, 4),
    }


def verdict_for(n: int, brier_live: float, brier_prematch: float) -> str:
    if n < MIN_SAMPLE:
        return "insufficient_sample"
    improvement = brier_prematch - brier_live
    if improvement >= MARGIN:
        return "conditional_beats_prematch"
    if improvement <= -MARGIN:
        return "prematch_better"
    return "tie"


def _settled_rows(
    db: Session, *, days: int, now: datetime, with_fixture: bool = False
):
    cutoff = now - timedelta(days=days)
    if with_fixture:
        statement = (
            select(LivePrediction, Fixture)
            .join(Fixture, Fixture.id == LivePrediction.fixture_id)
            .where(LivePrediction.result.isnot(None))
            .order_by(LivePrediction.observed_at.asc())
        )
        return [
            (row, fixture)
            for row, fixture in db.execute(statement).all()
            if _utc(row.observed_at) >= cutoff
        ]

    rows = db.scalars(
        select(LivePrediction)
        .where(LivePrediction.result.isnot(None))
        .order_by(LivePrediction.minute_bucket.asc())
    ).all()
    return [row for row in rows if _utc(row.observed_at) >= cutoff]


def calibration_report(
    db: Session, days: int = 90, now: Optional[datetime] = None
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = _settled_rows(db, days=days, now=now)

    overall = _metrics(rows)
    buckets = []
    for label, low, high in BUCKETS:
        subset = [row for row in rows if low <= row.minute <= high]
        # Every bucket is published, empty or not: on a calibration surface the
        # absence of settled snapshots is itself a signal.
        buckets.append({"bucket": label, **_metrics(subset)})

    if overall["n"]:
        verdict = verdict_for(
            overall["n"], overall["brier_live"], overall["brier_prematch"]
        )
    else:
        verdict = "insufficient_sample"

    return {
        "generated_at": now.isoformat(),
        "sample": {
            "snapshots": overall["n"],
            "matches": len({row.fixture_id for row in rows}),
        },
        "verdict": verdict,
        "overall": overall,
        "buckets": buckets,
        "intensity": load_intensity(),
    }


def export_rows(
    db: Session, days: int = 90, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [
        {
            "fixture_id": row.fixture_id,
            "competition": fixture.competition,
            "kickoff_utc": fixture.kickoff_utc.isoformat()
            if fixture.kickoff_utc
            else None,
            "minute": row.minute,
            "minute_bucket": row.minute_bucket,
            "home_score": row.home_score,
            "away_score": row.away_score,
            "lambda_home": row.lambda_home,
            "lambda_away": row.lambda_away,
            "remaining_home_lambda": row.remaining_home_lambda,
            "remaining_away_lambda": row.remaining_away_lambda,
            "intensity": row.intensity,
            "extra_home_goals": row.extra_home_goals,
            "extra_away_goals": row.extra_away_goals,
            "result": row.result,
            "prob_home": row.prob_home,
            "prob_draw": row.prob_draw,
            "prob_away": row.prob_away,
        }
        for row, fixture in _settled_rows(db, days=days, now=now, with_fixture=True)
    ]
