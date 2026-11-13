"""ESPN ingestion keeps the league code needed for score polling."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.data_sources import live_feed
from app.data_sources.fixtures import make_dedupe_key
from app.database import SessionLocal
from app.models.fixture import Fixture

KICKOFF = datetime.now(timezone.utc) + timedelta(days=30)


def _event(home: str, away: str) -> dict:
    return {
        "id": "990001",
        "date": KICKOFF.isoformat().replace("+00:00", "Z"),
        "competitions": [{
            "status": {"type": {"state": "pre"}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ],
        }],
    }


class _Response:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events}


def _fake_get(events):
    def get(url, **_kwargs):
        if "/eng.1/" in url:
            return _Response(events)
        return _Response([])
    return get


def test_ingest_stores_the_league_code(monkeypatch):
    monkeypatch.setattr(
        live_feed.requests,
        "get",
        _fake_get([_event("Code Test FC", "Code Test Reserves")]),
    )
    db = SessionLocal()
    try:
        live_feed.fetch_live_upcoming_fixtures_espn(db, quick_sync=True)
        row = db.scalar(select(Fixture).where(Fixture.home_team == "Code Test FC"))
    finally:
        db.close()

    assert row is not None
    assert row.competition == "Premier League"
    assert row.espn_league_code == "eng.1"


def test_existing_fixture_gets_the_league_code(monkeypatch):
    dedupe = make_dedupe_key(
        KICKOFF,
        "Existing Code FC",
        "Existing Code Reserves",
        "Premier League",
    )
    db = SessionLocal()
    try:
        db.add(Fixture(
            dedupe_key=dedupe,
            kickoff_utc=KICKOFF,
            home_team="Existing Code FC",
            away_team="Existing Code Reserves",
            competition="Premier League",
            country="England",
            source="ESPN",
            status="scheduled",
        ))
        db.commit()

        monkeypatch.setattr(
            live_feed.requests,
            "get",
            _fake_get([_event("Existing Code FC", "Existing Code Reserves")]),
        )
        result = live_feed.fetch_live_upcoming_fixtures_espn(db, quick_sync=True)
        db.expire_all()
        row = db.scalar(select(Fixture).where(Fixture.home_team == "Existing Code FC"))
    finally:
        db.close()

    assert result["updated"] >= 1
    assert row is not None
    assert row.espn_league_code == "eng.1"
