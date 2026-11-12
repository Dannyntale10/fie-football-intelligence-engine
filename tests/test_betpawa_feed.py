from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.fixture import Fixture
from app.data_sources import betpawa_feed as bp


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _kickoff(days=2):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=19, minute=45, second=0, microsecond=0
    )


def _event(event_id, home, away, competition, kickoff, odds=(2.0, 3.3, 3.6), suspended=None, region="Germany", extra_markets=None):
    prices = []
    for label, value in zip(("1", "X", "2"), odds):
        prices.append(
            {"name": label, "odds": value, "suspended": label == suspended}
        )
    markets = [
        {
            "marketType": {"id": "3743", "name": "1X2 - FT"},
            "row": [{"prices": prices}],
        }
    ]
    markets.extend(extra_markets or [])
    return {
        "id": event_id,
        "startTime": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "participants": [
            {"name": home, "position": 1},
            {"name": away, "position": 2},
        ],
        "competition": {"name": competition},
        "region": {"name": region},
        "markets": markets,
    }


def _extra_markets():
    def price(name, odds, handicap=None):
        return {"name": name, "odds": odds, "handicap": handicap, "suspended": False}

    return [
        {
            "marketType": {"id": "4693", "name": "Double Chance - FT"},
            "row": [{"prices": [price("1X", 1.35), price("X2", 1.50), price("12", 1.34)]}],
        },
        {
            "marketType": {"id": "3795", "name": "Both Teams To Score - FT"},
            "row": [{"prices": [price("Yes", 1.85), price("No", 1.80)]}],
        },
        {
            "marketType": {"id": "5000", "name": "Total Score Over/Under - FT"},
            "row": [
                {"prices": [price("Over", 2.20, "2.5"), price("Under", 1.60, "2.5")]},
                # Whole-goal line can push, so it must not be stored.
                {"prices": [price("Over", 1.90, "2"), price("Under", 1.90, "2")]},
            ],
        },
        {
            "marketType": {"id": "3774", "name": "Asian Handicap - FT"},
            "row": [
                {"prices": [price("1", 4.80, "-1.5"), price("2", 1.12, "+1.5")]},
            ],
        },
        {
            "marketType": {"id": "4703", "name": "Draw No Bet - FT"},
            "row": [{"prices": [price("1", 1.63), price("2", 2.06)]}],
        },
    ]


def test_parse_event_maps_positions_to_home_and_away():
    kickoff = _kickoff()
    record = bp._parse_event(_event("1", "VfL Wolfsburg", "1. FC Union Berlin", "Bundesliga", kickoff))

    assert record is not None
    assert record["home_team"] == "VfL Wolfsburg"
    assert record["away_team"] == "1. FC Union Berlin"
    assert (record["home_odds"], record["draw_odds"], record["away_odds"]) == (2.0, 3.3, 3.6)
    assert record["known_competition"] == "Bundesliga"


def test_suspended_price_rejects_the_event():
    kickoff = _kickoff()
    event = _event("1", "A", "B", "Bundesliga", kickoff, suspended="X")
    assert bp._parse_event(event) is None


def test_competition_requires_matching_region():
    kickoff = _kickoff()
    england = bp._parse_event(_event("1", "A", "B", "Premier League", kickoff, region="England"))
    jamaica = bp._parse_event(_event("2", "A", "B", "Premier League", kickoff, region="Jamaica"))

    assert england["known_competition"] == "Premier League"
    assert jamaica["known_competition"] is None


def test_market_odds_are_keyed_for_the_model():
    kickoff = _kickoff()
    record = bp._parse_event(
        _event("1", "A", "B", "Bundesliga", kickoff, extra_markets=_extra_markets())
    )
    odds = record["market_odds"]

    assert (odds["home_win"], odds["draw"], odds["away_win"]) == (2.0, 3.3, 3.6)
    assert odds["double_chance_1x"] == 1.35
    assert odds["btts_no"] == 1.80
    assert odds["over_2_5"] == 2.20 and odds["under_2_5"] == 1.60
    assert odds["ah_home_minus_1_5"] == 4.80
    assert odds["ah_away_plus_1_5"] == 1.12
    assert odds["dnb_home"] == 1.63
    # Whole-goal totals can push and must be dropped.
    assert "over_2_0" not in odds and "over_2" not in odds


def test_ingest_updates_match_inserts_tracked_and_skips_untracked(monkeypatch, db):
    kickoff = _kickoff()
    existing = Fixture(
        dedupe_key="existing-fixture",
        kickoff_utc=kickoff,
        home_team="Wolfsburg",
        away_team="Union Berlin",
        competition="Bundesliga",
        country="Germany",
        source="ESPN_DraftKings",
        status="scheduled",
    )
    db.add(existing)
    db.commit()

    events = [
        _event("10", "VfL Wolfsburg", "1. FC Union Berlin", "Bundesliga", kickoff, odds=(2.1, 3.4, 3.5), extra_markets=_extra_markets()),
        _event("11", "Bayern Munich", "Borussia Dortmund", "Bundesliga", _kickoff(3)),
        _event("12", "Some Club", "Other Club", "Club Friendlies", _kickoff(3)),
    ]
    monkeypatch.setattr(bp, "fetch_upcoming_events", lambda **_kwargs: events)

    stats = bp.ingest_betpawa_odds(db)

    assert stats["fetched_events"] == 3
    assert stats["matched_existing"] == 1
    assert stats["inserted_new"] == 1
    assert stats["skipped_unknown_competition"] == 1
    assert stats["with_extra_markets"] == 1

    db.refresh(existing)
    assert (existing.home_odds, existing.draw_odds, existing.away_odds) == (2.1, 3.4, 3.5)
    assert existing.source == "ESPN_DraftKings"
    payload = json.loads(existing.market_odds_json)
    assert payload["over_2_5"]["price"] == 2.2
    assert payload["over_2_5"]["source"] == "BETPAWA"
    assert payload["over_2_5"]["bookmaker"] == "BetPawa"
    assert payload["over_2_5"]["source_url"].startswith("https://www.betpawa.ug")

    inserted = db.query(Fixture).filter(Fixture.home_team == "Bayern Munich").one()
    assert inserted.source == "BETPAWA"
    assert inserted.external_fixture_id == "11"
    assert inserted.competition == "Bundesliga"
