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
        db.query(Fixture).filter(
            Fixture.dedupe_key.like("live-endpoint-%")
        ).delete(synchronize_session=False)
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
