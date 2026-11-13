"""The gate's measurement half is tested with real walk-forward output.

`gate_decision` is unit-tested against hand-built dicts; this module covers the
function that produces those dicts, so a window or aggregation bug cannot hide
behind a fabricated result.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analytics.calibration import brier_score, iter_walkforward_predictions, log_loss
from app.analytics.model_gate import evaluate_config
from app.data_sources.fixtures import clean_competition_name
from app.database import Base
from app.models.fixture import Fixture

TEAMS = ("Alpha", "Bravo", "Charlie", "Delta")
PAIRS = ((0, 1), (2, 3), (0, 2), (1, 3), (0, 3), (1, 2))
COMPETITION = "Gate Eval League"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    start = datetime.now(timezone.utc) - timedelta(days=140)
    try:
        for day in range(120):
            home, away = PAIRS[day % len(PAIRS)]
            home_goals, away_goals = (1, 0) if day % 3 else (0, 1)
            session.add(
                Fixture(
                    dedupe_key=f"gate-eval-{day}",
                    kickoff_utc=start + timedelta(days=day),
                    home_team=TEAMS[home],
                    away_team=TEAMS[away],
                    competition=COMPETITION,
                    country="Test",
                    source="test",
                    status="finished",
                    home_score=home_goals,
                    away_score=away_goals,
                    home_odds=2.1,
                    draw_odds=3.3,
                    away_odds=3.8,
                )
            )
        session.commit()
        yield session
    finally:
        session.close()
        engine.dispose()


def _replay(db):
    return list(
        iter_walkforward_predictions(db, refit_days=14, half_life_days=150.0)
    )


def test_evaluate_config_aggregates_the_walk_forward(db):
    now = datetime.now(timezone.utc)

    result = evaluate_config(
        db, half_life_days=150.0, window_days=730, refit_days=14, now=now
    )
    predictions = _replay(db)

    assert result["n_matches"] == len(predictions) > 0
    assert result["brier"] == brier_score(
        [row["model"] for row in predictions], [row["label"] for row in predictions]
    )
    assert result["log_loss"] == log_loss(
        [row["model"] for row in predictions], [row["label"] for row in predictions]
    )
    assert 0.0 < result["brier"] < 2.0

    bucket = result["per_competition"][clean_competition_name(COMPETITION)]
    assert bucket["n"] == len(predictions)
    assert bucket["brier"] == result["brier"]


def test_evaluate_config_respects_the_window(db):
    now = datetime.now(timezone.utc)

    full = evaluate_config(
        db, half_life_days=150.0, window_days=730, refit_days=14, now=now
    )
    narrow = evaluate_config(
        db, half_life_days=150.0, window_days=40, refit_days=14, now=now
    )

    assert 0 < narrow["n_matches"] < full["n_matches"]


def _aware(value):
    # SQLite hands datetimes back naive; the fixtures are stored in UTC.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def test_the_since_cutoff_yields_only_later_kickoffs(db):
    cutoff = datetime.now(timezone.utc) - timedelta(days=60)

    replay = list(
        iter_walkforward_predictions(
            db, refit_days=14, half_life_days=150.0, since=cutoff
        )
    )

    assert replay
    assert all(_aware(row["fixture"].kickoff_utc) >= cutoff for row in replay)
    assert len(replay) < len(_replay(db))


def test_the_window_is_identical_to_filtering_the_full_replay(db):
    """Since is an optimisation: the same matches, scored the same way."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=60)

    manual = [
        row for row in _replay(db) if _aware(row["fixture"].kickoff_utc) >= cutoff
    ]
    result = evaluate_config(
        db, half_life_days=150.0, window_days=60, refit_days=14, now=now
    )

    assert result["n_matches"] == len(manual) > 0
    assert result["brier"] == brier_score(
        [row["model"] for row in manual], [row["label"] for row in manual]
    )
    assert result["log_loss"] == log_loss(
        [row["model"] for row in manual], [row["label"] for row in manual]
    )
