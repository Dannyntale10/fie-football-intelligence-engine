"""Write and settle the append-only live forecast ledger."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.probabilities import analyze_fixture
from app.live.clock import elapsed_minutes
from app.live.engine import (
    INTENSITY_BOUNDS,
    LiveState,
    REGULATION_MINUTES,
    fitted_intensity,
    outcome_probabilities,
)
from app.models.fixture import Fixture
from app.models.live import LivePrediction

LIVE_WINDOW_HOURS = 3
BUCKET_MINUTES = 5
MODEL_VERSION = "live-dc-1"
OUTCOMES = ("home_win", "draw", "away_win")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _number(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _populate(
    db: Session,
    row: LivePrediction,
    fixture: Fixture,
    minute: Optional[int],
    provider: Callable[[LiveState], tuple],
) -> str:
    if fixture.home_score is None or fixture.away_score is None:
        return "no_score"
    if minute is None:
        return "no_clock"

    analysis = analyze_fixture(fixture, db=db, as_of=fixture.kickoff_utc)
    lambdas = analysis.get("estimated_lambdas") or {}
    home_lambda = _number(lambdas.get("home_lambda"))
    away_lambda = _number(lambdas.get("away_lambda"))
    source = analysis.get("lambda_source")
    row.lambda_source = source

    if (
        source in (None, "fallback")
        or home_lambda is None
        or away_lambda is None
        or home_lambda <= 0.0
        or away_lambda <= 0.0
    ):
        return "no_lambda"

    row.lambda_home = home_lambda
    row.lambda_away = away_lambda

    state = LiveState(
        home_goals=int(fixture.home_score),
        away_goals=int(fixture.away_score),
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        minute=int(minute),
    )
    k, intensity_source = provider(state)
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], float(k)))

    fraction = max(0.0, (REGULATION_MINUTES - int(minute)) / REGULATION_MINUTES)
    row.remaining_home_lambda = home_lambda * fraction * k
    row.remaining_away_lambda = away_lambda * fraction * k
    row.intensity = k
    row.intensity_source = intensity_source

    probabilities = outcome_probabilities(
        home_goals=state.home_goals,
        away_goals=state.away_goals,
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        remaining_fraction=fraction,
        intensity=k,
    )
    row.prob_home = probabilities["home_win"]
    row.prob_draw = probabilities["draw"]
    row.prob_away = probabilities["away_win"]

    markets = analysis.get("all_markets") or {}
    row.prematch_home = _number(markets.get("home_win"))
    row.prematch_draw = _number(markets.get("draw"))
    row.prematch_away = _number(markets.get("away_win"))
    return "ok"


def record_live_snapshots(
    db: Session,
    now: Optional[datetime] = None,
    intensity_provider: Optional[Callable[[LiveState], tuple]] = None,
) -> Dict[str, int]:
    """Write at most one forecast per fixture per five-minute bucket."""
    now = now or datetime.now(timezone.utc)
    provider = intensity_provider or fitted_intensity

    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(("live", "halftime")))
        .where(Fixture.kickoff_utc >= now - timedelta(hours=LIVE_WINDOW_HOURS))
        .order_by(Fixture.kickoff_utc)
    ).all()

    written = skipped = 0
    for fixture in fixtures:
        minute = elapsed_minutes(
            fixture.live_clock, fixture.kickoff_utc, fixture.status, now
        )
        bucket = -1 if minute is None else (minute // BUCKET_MINUTES) * BUCKET_MINUTES
        exists = db.scalar(
            select(LivePrediction.id).where(
                LivePrediction.fixture_id == fixture.id,
                LivePrediction.minute_bucket == bucket,
            )
        )
        if exists is not None:
            skipped += 1
            continue

        row = LivePrediction(
            fixture_id=fixture.id,
            minute=minute if minute is not None else 0,
            minute_bucket=bucket,
            observed_at=fixture.live_observed_at or now,
            home_score=fixture.home_score,
            away_score=fixture.away_score,
            status="ok",
            model_version=MODEL_VERSION,
        )
        row.status = _populate(db, row, fixture, minute, provider)
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:  # scheduler and HTTP racing on the same bucket
            skipped += 1
            continue
        written += 1

    db.commit()
    return {"fixtures": len(fixtures), "written": written, "skipped": skipped}


def _label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home_win"
    if home_goals == away_goals:
        return "draw"
    return "away_win"


def settle_live_predictions(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, int]:
    """Score open forecasts against the final result exactly once."""
    now = now or datetime.now(timezone.utc)

    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status == "finished")
        .where(Fixture.home_score.isnot(None))
        .where(Fixture.away_score.isnot(None))
    ).all()

    settled = 0
    for fixture in fixtures:
        open_rows = db.scalars(
            select(LivePrediction).where(
                LivePrediction.fixture_id == fixture.id,
                LivePrediction.result.is_(None),
                LivePrediction.prob_home.isnot(None),
            )
        ).all()
        if not open_rows:
            continue

        home_goals = int(fixture.home_score)
        away_goals = int(fixture.away_score)
        label = _label(home_goals, away_goals)
        index = OUTCOMES.index(label)

        for row in open_rows:
            probabilities = (row.prob_home, row.prob_draw, row.prob_away)
            row.result = label
            row.extra_home_goals = home_goals - int(row.home_score or 0)
            row.extra_away_goals = away_goals - int(row.away_score or 0)
            row.brier = round(
                sum(
                    (probability - (1.0 if position == index else 0.0)) ** 2
                    for position, probability in enumerate(probabilities)
                ),
                6,
            )
            row.log_loss = round(-math.log(max(1e-9, probabilities[index])), 6)
            row.top_pick_correct = max(range(3), key=lambda i: probabilities[i]) == index
            row.settled_at = now
            settled += 1

    db.commit()
    return {"settled": settled}
