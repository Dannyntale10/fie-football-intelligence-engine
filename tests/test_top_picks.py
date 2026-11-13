"""Top-five 85% match picks: ranking, attribution and the unchanged gate."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.betting import top_picks as tp
from app.database import Base
from app.main import app
from app.market_engine.qualification import NO_QUALIFIED_SELECTIONS
from app.models.fixture import Fixture

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
FAVOURITE = {"home_win": 1.06, "draw": 13.0, "away_win": 34.0}
EVEN = {"home_win": 2.5, "draw": 3.4, "away_win": 2.8}


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


def _prices(odds, *, captured=True):
    entry = {
        "bookmaker": "BetPawa",
        "source": "BETPAWA",
        "source_url": "https://www.betpawa.ug/",
    }
    if captured:
        entry["captured_at"] = (NOW - timedelta(minutes=30)).isoformat()
    return json.dumps({key: {**entry, "price": price} for key, price in odds.items()})


def _add(db, home, away, kickoff, odds, *, attributed=True, captured=True):
    fixture = Fixture(
        dedupe_key=f"{home}-{away}-{kickoff.isoformat()}",
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition="Premier League",
        country="England",
        source="BETPAWA",
        status="scheduled",
        home_odds=odds["home_win"],
        draw_odds=odds["draw"],
        away_odds=odds["away_win"],
        max_home_odds=odds["home_win"],
        max_draw_odds=odds["draw"],
        max_away_odds=odds["away_win"],
        market_odds_json=_prices(odds, captured=captured) if attributed else None,
    )
    db.add(fixture)
    db.commit()
    return fixture


def test_top_picks_rank_five_attributed_match_winners(db):
    kickoff = NOW + timedelta(hours=11)
    for index in range(7):
        _add(db, f"Home {index}", f"Away {index}", kickoff + timedelta(minutes=index), FAVOURITE)

    report = tp.build_top_picks(db, now=NOW)

    assert report["count"] == 5
    assert report["fixtures_scanned"] == 7
    assert [pick["rank"] for pick in report["picks"]] == [1, 2, 3, 4, 5]
    probabilities = [pick["probability"] for pick in report["picks"]]
    assert probabilities == sorted(probabilities, reverse=True)
    first = report["picks"][0]
    assert first["market_key"] == "home_win"
    assert first["selection"].endswith("to win")
    assert first["probability"] >= 0.85
    assert first["odds"] == 1.06
    assert first["bookmaker"] == "BetPawa"
    assert first["source_url"] == "https://www.betpawa.ug/"
    assert first["captured_at"]
    assert first["price_age_hours"] == pytest.approx(0.5, abs=0.01)
    assert first["confidence"] >= 0.60
    assert first["confidence_reasons"]
    assert "double chance" not in first["selection"].lower()


def test_top_picks_keep_supporting_markets_separate_from_the_headline(db):
    _add(db, "Arsenal", "Chelsea", NOW + timedelta(hours=11), FAVOURITE)

    pick = tp.build_top_picks(db, now=NOW)["picks"][0]

    labels = [row["label"] for row in pick["also_qualifying"]]
    assert pick["selection"] == "Arsenal to win"
    assert pick["selection"] not in labels
    assert any("Double chance" in label for label in labels)
    assert all(0.0 <= row["probability"] <= 1.0 for row in pick["also_qualifying"])


def test_top_picks_reject_the_ungated_and_unattributed(db):
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Home A", "Away A", kickoff, EVEN)
    _add(db, "Home B", "Away B", kickoff, FAVOURITE, attributed=False)
    _add(db, "Home C", "Away C", kickoff, FAVOURITE, captured=False)
    # Priced, but only for a market other than the winning outright result.
    other_market = _add(db, "Home D", "Away D", kickoff, FAVOURITE)
    other_market.market_odds_json = json.dumps(
        {"over_2_5": {"price": 1.9, "bookmaker": "BetPawa", "captured_at": NOW.isoformat()}}
    )
    db.commit()

    report = tp.build_top_picks(db, now=NOW)

    assert report["picks"] == []
    assert report["count"] == 0
    assert report["message"] == NO_QUALIFIED_SELECTIONS
    assert report["rejected"]["below_gate"] == 1
    assert report["rejected"]["no_priced_market"] == 1
    assert report["rejected"]["unattributed_price"] == 1
    assert report["rejected"]["low_data_confidence"] == 1


def test_top_picks_collapse_duplicate_feed_rows(db):
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Czechia", "Spain", kickoff, FAVOURITE)
    _add(db, "Czech Republic", "Spain", kickoff + timedelta(minutes=5), FAVOURITE)

    report = tp.build_top_picks(db, now=NOW)

    assert report["count"] == 1
    assert report["rejected"]["duplicate_match"] == 1


def test_top_picks_route_serves_the_cap(monkeypatch):
    from app.api import markets as markets_api

    seen = {}

    def fake(db, limit, target_date):
        seen.update(limit=limit, target_date=target_date)
        return {"count": 0, "picks": [], "message": NO_QUALIFIED_SELECTIONS}

    monkeypatch.setattr(markets_api, "build_top_picks", fake)

    body = TestClient(app).get("/markets/top-picks", params={"limit": 3}).json()

    assert body["message"] == NO_QUALIFIED_SELECTIONS
    assert seen == {"limit": 3, "target_date": None}
