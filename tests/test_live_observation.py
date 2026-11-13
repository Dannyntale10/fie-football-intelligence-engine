"""A matched ESPN event stamps live_observed_at even when the score is unchanged."""
from datetime import datetime, timedelta, timezone

from app.data_sources.inplay import _apply
from app.models.fixture import Fixture


def _event(state="in", clock="67:34", home="1", away="0", name="STATUS_IN_PROGRESS"):
    return {
        "id": "9001",
        "competitions": [
            {
                "status": {"displayClock": clock, "type": {"state": state, "name": name}},
                "competitors": [
                    {"homeAway": "home", "score": home, "team": {"displayName": "Alpha FC"}},
                    {"homeAway": "away", "score": away, "team": {"displayName": "Beta FC"}},
                ],
            }
        ],
    }


def test_apply_stamps_observation_when_nothing_else_changes():
    fixture = Fixture(
        dedupe_key="observation-fixture",
        kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=70),
        home_team="Alpha FC",
        away_team="Beta FC",
        competition="Observation League",
        country="Test",
        source="Test",
        status="live",
        live_clock="67:34",
        home_score=1,
        away_score=0,
    )

    changed = _apply(fixture, _event())

    assert changed is True
    assert fixture.live_observed_at is not None
    assert fixture.live_clock == "67:34"
    assert fixture.home_score == 1
