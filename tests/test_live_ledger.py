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


def test_the_prior_is_frozen_from_the_first_snapshot(monkeypatch):
    """Rebuilding ratings mid-match must not move the numbers beside the score."""
    calls = []

    def drifting_analysis(fixture, db=None, as_of=None):
        calls.append(as_of)
        shift = 0.1 * len(calls)
        return {
            "estimated_lambdas": {"home_lambda": 1.5 + shift, "away_lambda": 1.0},
            "lambda_source": "market_odds",
            "all_markets": {
                "home_win": 0.50 + shift,
                "draw": 0.27,
                "away_win": 0.23 - shift,
            },
        }

    monkeypatch.setattr(ledger, "analyze_fixture", drifting_analysis)
    db = SessionLocal()
    try:
        fixture = _fixture()
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        ledger.record_live_snapshots(
            db, now=NOW, intensity_provider=lambda state: (1.0, "default")
        )
        fixture.live_clock = "72:00"
        db.commit()
        ledger.record_live_snapshots(
            db,
            now=NOW + timedelta(minutes=5),
            intensity_provider=lambda state: (1.0, "default"),
        )

        rows = (
            db.query(LivePrediction)
            .filter(LivePrediction.fixture_id == fixture.id)
            .order_by(LivePrediction.minute_bucket)
            .all()
        )
        assert len(rows) == 2
        assert len(calls) == 1, "the second bucket re-analysed the fixture"
        assert rows[0].lambda_home == rows[1].lambda_home == 1.6
        assert rows[0].lambda_away == rows[1].lambda_away == 1.0
        assert rows[0].prematch_home == pytest.approx(rows[1].prematch_home)
        assert rows[0].prematch_home == pytest.approx(0.6)
    finally:
        db.close()


def test_one_bad_fixture_does_not_cost_the_slate(monkeypatch):
    def flaky_analysis(fixture, db=None, as_of=None):
        if fixture.home_team.startswith("Boom"):
            raise RuntimeError("unreadable analysis")
        return _fake_analysis(fixture, db=db, as_of=as_of)

    monkeypatch.setattr(ledger, "analyze_fixture", flaky_analysis)
    db = SessionLocal()
    try:
        good = _fixture()
        bad = _fixture(home_team="Boom Home", away_team="Boom Away")
        db.add_all([good, bad])
        db.commit()
        db.refresh(good)
        db.refresh(bad)

        result = ledger.record_live_snapshots(
            db, now=NOW, intensity_provider=lambda state: (1.0, "default")
        )

        assert result == {"fixtures": 2, "written": 1, "skipped": 0, "failed": 1}
        assert (
            db.query(LivePrediction)
            .filter(LivePrediction.fixture_id == good.id)
            .count()
            == 1
        )
        assert (
            db.query(LivePrediction)
            .filter(LivePrediction.fixture_id == bad.id)
            .count()
            == 0
        )
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


def test_settlement_reaches_a_row_whose_fixture_finished_days_ago():
    db = SessionLocal()
    try:
        fixture = _fixture(
            status="finished",
            kickoff_utc=NOW - timedelta(days=30),
            home_score=0,
            away_score=2,
        )
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        db.add(
            LivePrediction(
                fixture_id=fixture.id,
                minute=60,
                minute_bucket=60,
                observed_at=NOW - timedelta(days=30) + timedelta(minutes=60),
                home_score=0,
                away_score=0,
                prob_home=0.60,
                prob_draw=0.25,
                prob_away=0.15,
                status="ok",
                model_version="live-dc-1",
            )
        )
        db.commit()

        ledger.settle_live_predictions(db, now=NOW)

        row = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).one()
        assert row.result == "away_win"
        assert row.extra_home_goals == 0
        assert row.extra_away_goals == 2
        assert row.top_pick_correct is False
        assert abs(row.brier - (0.36 + 0.0625 + 0.7225)) < 1e-6
        assert row.settled_at is not None
    finally:
        db.close()
