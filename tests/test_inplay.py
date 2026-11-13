"""The live poller persists ESPN state without creating fixtures."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.data_sources import inplay
from app.database import SessionLocal
from app.models.fixture import Fixture


class _Response:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events}


def _event(ev_id, home, away, state, type_name, clock, home_score, away_score):
    return {
        "id": ev_id,
        "date": "2026-07-04T18:00Z",
        "competitions": [{
            "status": {
                "displayClock": clock,
                "period": 2,
                "type": {
                    "state": state,
                    "name": type_name,
                    "shortDetail": type_name,
                },
            },
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
                {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
            ],
        }],
    }


@pytest.fixture(autouse=True)
def _reset_throttle():
    inplay._last_refresh_monotonic = None
    yield
    inplay._last_refresh_monotonic = None
    db = SessionLocal()
    try:
        db.query(Fixture).filter(Fixture.dedupe_key.like("inplay-%")).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _add_fixture(db, suffix: str, competition: str, external_id: str | None) -> Fixture:
    fixture = Fixture(
        dedupe_key=f"inplay-{suffix}",
        kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=30),
        home_team=f"{suffix} Home",
        away_team=f"{suffix} Away",
        competition=competition,
        country="Test",
        source="Test",
        external_fixture_id=external_id,
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def test_refresh_persists_live_state_and_scores(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Live", "Premier League", "501")
        event = _event("501", fixture.home_team, fixture.away_team, "in", "STATUS_IN_PROGRESS", "67'", "2", "1")
        monkeypatch.setattr(inplay.requests, "get", lambda *_a, **_k: _Response([event]))

        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(fixture)
    finally:
        db.close()

    assert result["refreshed"] is True
    assert result["warning"] is None
    assert result["updated"] == 1
    assert fixture.status == "live"
    assert fixture.home_score == 2
    assert fixture.away_score == 1
    assert fixture.live_clock == "67'"


def test_refresh_maps_halftime_and_full_time(monkeypatch):
    db = SessionLocal()
    try:
        halftime = _add_fixture(db, "Halftime", "Premier League", "502")
        finished = _add_fixture(db, "Finished", "Premier League", "503")
        events = [
            _event("502", halftime.home_team, halftime.away_team, "in", "STATUS_HALFTIME", "", "0", "0"),
            _event("503", finished.home_team, finished.away_team, "post", "STATUS_FULL_TIME", "", "3", "2"),
        ]
        monkeypatch.setattr(inplay.requests, "get", lambda *_a, **_k: _Response(events))

        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(halftime)
        db.refresh(finished)
    finally:
        db.close()

    assert result["updated"] == 2
    assert halftime.status == "halftime"
    assert halftime.live_clock == "HT"
    assert finished.status == "finished"
    assert finished.live_clock == "FT"
    assert (finished.home_score, finished.away_score) == (3, 2)


def test_refresh_skips_fixtures_without_a_league_code(monkeypatch):
    db = SessionLocal()
    try:
        _add_fixture(db, "Unknown", "Unmapped Competition", "504")

        def _boom(*_a, **_k):
            raise AssertionError("no ESPN call expected")

        monkeypatch.setattr(inplay.requests, "get", _boom)
        result = inplay.refresh_live_scores(db, force=True)
    finally:
        db.close()

    assert result["skipped"] >= 1
    assert result["updated"] == 0


def test_refresh_is_throttled_to_one_minute(monkeypatch):
    db = SessionLocal()
    try:
        _add_fixture(db, "Throttled", "Premier League", "505")
        calls = []

        def _count(*_a, **_k):
            calls.append(1)
            return _Response([])

        monkeypatch.setattr(inplay.requests, "get", _count)
        first = inplay.refresh_live_scores(db, force=True)
        second = inplay.refresh_live_scores(db)
    finally:
        db.close()

    assert first["refreshed"] is True
    assert second["refreshed"] is False
    assert second["warning"] is None
    assert len(calls) == 1


def test_feed_failure_returns_a_warning_and_keeps_the_row(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Outage", "Premier League", "506")

        def _fail(*_a, **_k):
            raise RuntimeError("ESPN unreachable")

        monkeypatch.setattr(inplay.requests, "get", _fail)
        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(fixture)
    finally:
        db.close()

    assert result["refreshed"] is False
    assert "ESPN unreachable" in (result["warning"] or "")
    assert fixture.status == "scheduled"
    assert fixture.home_score is None


def test_fetch_live_scores_returns_the_streamlit_shape(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Shape", "Premier League", "507")
        fixture.status = "live"
        fixture.home_score = 1
        fixture.away_score = 0
        fixture.live_clock = "54'"
        db.commit()

        monkeypatch.setattr(inplay, "refresh_live_scores", lambda db, force=False: {
            "refreshed": False, "updated": 0, "skipped": 0, "warning": None,
        })
        rows = inplay.fetch_live_scores(db)
    finally:
        db.close()

    ids = {row["fixture_id"] for row in rows}
    assert fixture.id in ids
    row = next(item for item in rows if item["fixture_id"] == fixture.id)
    assert row["is_live"] is True
    assert row["home_score"] == "1"
    assert row["away_score"] == "0"
    assert row["clock"] == "54'"
    assert row["state"] == "in"
