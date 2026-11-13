"""The calibration report states a verdict only when the sample supports one."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.live.report import calibration_report, export_rows, verdict_for
from app.models.fixture import Fixture
from app.models.live import LivePrediction

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
PREFIX = "live-calibration-"


def test_verdict_thresholds():
    assert verdict_for(199, 0.40, 0.60) == "insufficient_sample"
    assert verdict_for(200, 0.60, 0.40) == "prematch_better"
    assert verdict_for(200, 0.4005, 0.4000) == "tie"
    assert verdict_for(200, 0.390, 0.400) == "conditional_beats_prematch"
    assert verdict_for(200, 0.410, 0.400) == "prematch_better"


@pytest.fixture
def seeded():
    db = SessionLocal()
    # The suite shares one database and both helpers aggregate every settled
    # snapshot in the window, so a teardown leak elsewhere would surface here
    # as an unexplained count mismatch. Fail loudly and early instead.
    baseline = calibration_report(db, days=90, now=NOW)
    assert baseline["sample"]["snapshots"] == 0, (
        "another test leaked settled live_predictions rows into the shared database"
    )
    fixture = Fixture(
        dedupe_key=f"{PREFIX}{datetime.now(timezone.utc).timestamp()}",
        kickoff_utc=NOW - timedelta(hours=2),
        home_team="Calibration Home",
        away_team="Calibration Away",
        competition="Calibration League",
        country="Test",
        source="Test",
        status="finished",
        home_score=2,
        away_score=1,
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)

    for bucket in range(0, 60, 5):
        db.add(
            LivePrediction(
                fixture_id=fixture.id,
                minute=bucket,
                minute_bucket=bucket,
                observed_at=NOW - timedelta(minutes=45 - bucket),
                home_score=1,
                away_score=0,
                lambda_home=1.5,
                lambda_away=1.0,
                remaining_home_lambda=0.5,
                remaining_away_lambda=0.3,
                intensity=1.0,
                intensity_source="default",
                prob_home=0.70,
                prob_draw=0.20,
                prob_away=0.10,
                prematch_home=0.45,
                prematch_draw=0.28,
                prematch_away=0.27,
                status="ok",
                model_version="live-dc-1",
                result="home_win",
                extra_home_goals=1,
                extra_away_goals=1,
                brier=0.14,
                log_loss=0.357,
                top_pick_correct=True,
                settled_at=NOW - timedelta(minutes=10),
            )
        )
    db.commit()
    try:
        yield fixture.id, db
    finally:
        db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).delete(synchronize_session=False)
        db.query(Fixture).filter(Fixture.id == fixture.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_report_shape_and_buckets(seeded):
    fixture_id, db = seeded
    report = calibration_report(db, days=90, now=NOW)

    assert report["sample"]["snapshots"] == 12
    assert report["sample"]["matches"] == 1
    assert report["verdict"] == "insufficient_sample"
    assert report["overall"]["n"] == 12
    assert [row["bucket"] for row in report["buckets"]] == [
        "0-14", "15-29", "30-44", "45-59", "60-74", "75-90"
    ]
    assert all(row["n"] == 0 for row in report["buckets"][4:])
    assert report["buckets"][0]["n"] == 3
    assert report["intensity"]["source"] in {"default", "fitted"}


def test_export_carries_the_fit_inputs(seeded):
    fixture_id, db = seeded
    rows = export_rows(db, days=90, now=NOW)

    assert len(rows) == 12
    row = rows[0]
    assert row["fixture_id"] == fixture_id
    assert row["competition"] == "Calibration League"
    assert row["remaining_home_lambda"] == 0.5
    assert row["remaining_away_lambda"] == 0.3
    assert row["extra_home_goals"] == 1
    assert row["extra_away_goals"] == 1
    assert row["result"] == "home_win"
