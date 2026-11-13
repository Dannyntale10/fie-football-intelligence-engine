"""GET /fixtures/live serves stored live rows and survives a feed outage."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.database import SessionLocal
from app.main import app
from app.models.fixture import Fixture

client = TestClient(app)


@pytest.fixture(autouse=True)
def _cleanup_fixtures():
    yield
    db = SessionLocal()
    try:
        ids = [
            row[0]
            for row in db.query(Fixture.id)
            .filter(Fixture.dedupe_key.like("live-endpoint-%"))
            .all()
        ]
        if ids:
            from app.models.live import LivePrediction

            db.query(LivePrediction).filter(
                LivePrediction.fixture_id.in_(ids)
            ).delete(synchronize_session=False)
            db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _insert_live_fixture() -> int:
    db = SessionLocal()
    try:
        fixture = Fixture(
            dedupe_key=f"live-endpoint-{datetime.now(timezone.utc).timestamp()}",
            kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=20),
            home_team="Endpoint Live FC",
            away_team="Endpoint Live Reserves",
            competition="Fixture Payload League",
            country="Test",
            source="Test",
            external_fixture_id="770001",
            home_score=2,
            away_score=1,
            live_clock="67'",
            status="live",
        )
        db.add(fixture)
        db.commit()
        db.refresh(fixture)
        return fixture.id
    finally:
        db.close()


def test_live_endpoint_returns_scores_and_clock(monkeypatch):
    fixture_id = _insert_live_fixture()
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {"refreshed": True, "updated": 0, "skipped": 0, "warning": None},
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["refreshed"] is True
    assert payload["warning"] is None
    row = next(item for item in payload["fixtures"] if item["id"] == fixture_id)
    assert row["home_score"] == 2
    assert row["away_score"] == 1
    assert row["live_clock"] == "67'"
    assert row["status"] == "live"


def test_live_endpoint_survives_a_feed_outage(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {
            "refreshed": False,
            "updated": 0,
            "skipped": 0,
            "warning": "feed down",
        },
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    assert response.json()["warning"] == "feed down"


def test_live_endpoint_includes_the_prediction_block(monkeypatch):
    from app.live import ledger

    fixture_id = _insert_live_fixture()
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {"refreshed": True, "updated": 0, "skipped": 0, "warning": None},
    )
    monkeypatch.setattr(
        ledger,
        "analyze_fixture",
        lambda fixture, db=None, as_of=None: {
            "estimated_lambdas": {"home_lambda": 1.6, "away_lambda": 0.9},
            "lambda_source": "market_odds",
            "all_markets": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        },
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    row = next(
        item for item in response.json()["fixtures"] if item["id"] == fixture_id
    )
    block = row["live_prediction"]
    assert block["status"] == "ok"
    assert block["minute"] == 67
    assert block["freshness"] == "fresh"
    assert set(block["probabilities"]) == {"home_win", "draw", "away_win"}
    assert abs(sum(block["probabilities"].values()) - 1.0) < 1e-3
    assert block["model_version"] == "live-dc-1"


def test_fixture_live_history_is_404_for_unknown_fixture():
    assert client.get("/fixtures/999999/live").status_code == 404


def _insert_history_rows(fixture_id: int) -> None:
    """Three buckets: one settled, one unsettled, one suppressed for no lambda."""
    from app.models.live import LivePrediction

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add_all(
            [
                LivePrediction(
                    fixture_id=fixture_id,
                    minute=5,
                    minute_bucket=5,
                    observed_at=now - timedelta(minutes=40),
                    home_score=0,
                    away_score=0,
                    lambda_home=1.5,
                    lambda_away=1.1,
                    prob_home=0.52,
                    prob_draw=0.24,
                    prob_away=0.24,
                    status="ok",
                    model_version="live-dc-1",
                    result="home",
                    extra_home_goals=2,
                    extra_away_goals=1,
                    brier=0.2,
                    log_loss=0.6,
                    top_pick_correct=True,
                    settled_at=now,
                ),
                LivePrediction(
                    fixture_id=fixture_id,
                    minute=10,
                    minute_bucket=10,
                    observed_at=now - timedelta(minutes=35),
                    home_score=1,
                    away_score=0,
                    lambda_home=1.4,
                    lambda_away=1.2,
                    prob_home=0.68,
                    prob_draw=0.20,
                    prob_away=0.12,
                    status="ok",
                    model_version="live-dc-1",
                    result=None,
                ),
                LivePrediction(
                    fixture_id=fixture_id,
                    minute=15,
                    minute_bucket=15,
                    observed_at=now - timedelta(minutes=30),
                    home_score=1,
                    away_score=0,
                    status="no_lambda",
                    model_version="live-dc-1",
                    result=None,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()


def test_fixture_live_history_includes_every_snapshot_in_the_window():
    fixture_id = _insert_live_fixture()
    _insert_history_rows(fixture_id)

    response = client.get(f"/fixtures/{fixture_id}/live")

    assert response.status_code == 200
    history = response.json()["history"]
    assert [row["minute_bucket"] for row in history] == [5, 10, 15]
    assert history[0]["status"] == "ok"
    assert set(history[0]["probabilities"]) == {"home_win", "draw", "away_win"}
    assert history[0]["result"] == "home"
    # The unsettled row is part of the live history, not hidden until settlement.
    assert history[1]["status"] == "ok"
    assert history[1]["probabilities"] is not None
    assert history[1]["result"] is None
    # The suppressed row stays honest: no fabricated three-null probability dict.
    assert history[2]["status"] == "no_lambda"
    assert history[2]["probabilities"] is None


def test_fixture_live_history_is_empty_without_snapshots():
    db = SessionLocal()
    try:
        fixture = Fixture(
            dedupe_key=f"live-endpoint-history-{datetime.now(timezone.utc).timestamp()}",
            kickoff_utc=datetime.now(timezone.utc) + timedelta(hours=3),
            home_team="History Home",
            away_team="History Away",
            competition="Fixture Payload League",
            country="Test",
            source="Test",
            status="scheduled",
        )
        db.add(fixture)
        db.commit()
        db.refresh(fixture)
        fixture_id = fixture.id
    finally:
        db.close()

    response = client.get(f"/fixtures/{fixture_id}/live")

    assert response.status_code == 200
    assert response.json()["latest"] is None
    assert response.json()["history"] == []
