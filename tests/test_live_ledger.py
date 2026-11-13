"""Snapshots are written once per bucket, suppress honestly, and settle once."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.live import ledger
from app.models.fixture import Fixture
from app.models.live import LivePrediction

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
PREFIX = "live-ledger-"


def _fixture(**overrides) -> Fixture:
    values = dict(
        dedupe_key=f"{PREFIX}{datetime.now(timezone.utc).timestamp()}",
        kickoff_utc=NOW - timedelta(minutes=70),
        home_team="Ledger Home",
        away_team="Ledger Away",
        competition="Ledger League",
        country="Test",
        source="Test",
        status="live",
        live_clock="67:34",
        home_score=1,
        away_score=0,
        live_observed_at=NOW,
    )
    values.update(overrides)
    return Fixture(**values)


def _cleanup(db):
    ids = [
        row[0]
        for row in db.query(Fixture.id).filter(Fixture.dedupe_key.like(f"{PREFIX}%")).all()
    ]
    if ids:
        db.query(LivePrediction).filter(LivePrediction.fixture_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _fake_analysis(fixture, db=None, as_of=None):
    return {
        "estimated_lambdas": {"home_lambda": 1.5, "away_lambda": 1.0},
        "lambda_source": "market_odds",
        "all_markets": {"home_win": 0.52, "draw": 0.26, "away_win": 0.22},
    }


@pytest.fixture(autouse=True)
def _clean_ledger():
    db = SessionLocal()
    try:
        _cleanup(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        _cleanup(db)
    finally:
        db.close()


def test_one_snapshot_per_bucket(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    db = SessionLocal()
    try:
        fixture = _fixture()
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        first = ledger.record_live_snapshots(
            db, now=NOW, intensity_provider=lambda state: (1.0, "default")
        )
        second = ledger.record_live_snapshots(
            db, now=NOW + timedelta(minutes=1), intensity_provider=lambda state: (1.0, "default")
        )

        rows = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).all()

        assert first["written"] == 1
        assert second["written"] == 0
        assert second["skipped"] == 1
        assert len(rows) == 1
        row = rows[0]
        assert row.minute == 67
        assert row.minute_bucket == 65
        assert row.status == "ok"
        assert abs(row.prob_home + row.prob_draw + row.prob_away - 1.0) < 1e-3
        assert row.intensity_source == "default"
        assert row.prematch_home == 0.52
    finally:
        db.close()


def test_missing_score_suppresses_probabilities(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    db = SessionLocal()
    try:
        fixture = _fixture(home_score=None, away_score=None)
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        ledger.record_live_snapshots(db, now=NOW)

        row = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).one()
        assert row.status == "no_score"
        assert row.prob_home is None
        assert row.minute_bucket == 65
    finally:
        db.close()


def test_missing_clock_uses_the_negative_bucket(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    monkeypatch.setattr(ledger, "elapsed_minutes", lambda *args, **kwargs: None)
    db = SessionLocal()
    try:
        fixture = _fixture()
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        ledger.record_live_snapshots(db, now=NOW)

        row = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).one()
        assert row.status == "no_clock"
        assert row.minute == 0
        assert row.minute_bucket == -1
        assert row.prob_home is None
    finally:
        db.close()


def test_settlement_scores_each_open_snapshot_once():
    db = SessionLocal()
    try:
        fixture = _fixture(status="finished", home_score=2, away_score=1)
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        for bucket, probs in ((0, (0.80, 0.15, 0.05)), (5, (0.55, 0.30, 0.15))):
            db.add(
                LivePrediction(
                    fixture_id=fixture.id,
                    minute=bucket,
                    minute_bucket=bucket,
                    observed_at=NOW - timedelta(minutes=60 - bucket),
                    home_score=1,
                    away_score=0,
                    prob_home=probs[0],
                    prob_draw=probs[1],
                    prob_away=probs[2],
                    status="ok",
                    model_version="live-dc-1",
                )
            )
        db.commit()

        first = ledger.settle_live_predictions(db, now=NOW)
        second = ledger.settle_live_predictions(db, now=NOW)

        rows = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).all()
        assert first["settled"] == 2
        assert second["settled"] == 0
        for row in rows:
            assert row.result == "home_win"
            assert row.extra_home_goals == 1
            assert row.extra_away_goals == 1
            assert row.top_pick_correct is True
            assert 0.0 <= row.brier <= 2.0
            assert row.log_loss > 0.0
            assert row.settled_at is not None
    finally:
        db.close()
