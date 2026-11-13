"""High-odds 6.0 slips: only published when every leg beats its price."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.betting import high_odds as ho
from app.database import Base
from app.main import app
from app.market_engine.qualification import NO_QUALIFIED_SELECTIONS
from app.models.fixture import Fixture

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
# 1.8, 3.3, 5.4 implies 1.044 — a coherent market that carries a real margin.
ODDS = {"home_win": 1.8, "draw": 3.3, "away_win": 5.4}


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


def _prices(odds):
    entry = {
        "bookmaker": "BetPawa",
        "source": "BETPAWA",
        "source_url": "https://www.betpawa.ug/",
        "captured_at": (NOW - timedelta(minutes=30)).isoformat(),
    }
    return json.dumps({key: {**entry, "price": price} for key, price in odds.items()})


def _add(db, home, away, kickoff, *, attributed=True, odds=None):
    prices = odds or ODDS
    fixture = Fixture(
        dedupe_key=f"{home}-{away}-{kickoff.isoformat()}",
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition="Premier League",
        country="England",
        source="BETPAWA",
        status="scheduled",
        home_odds=prices["home_win"],
        draw_odds=prices["draw"],
        away_odds=prices["away_win"],
        max_home_odds=prices["home_win"],
        max_draw_odds=prices["draw"],
        max_away_odds=prices["away_win"],
        market_odds_json=_prices(prices) if attributed else None,
    )
    db.add(fixture)
    db.commit()
    return fixture


def _analysis(home_probability=0.60, blend=True):
    return {
        "all_markets": {
            "home_win": home_probability,
            "draw": 0.22,
            "away_win": 0.12,
        },
        "probability_blend": {
            "applied": blend,
            "weight": 0.04,
            "model": {"home_win": home_probability, "draw": 0.22, "away_win": 0.12},
            "market": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
            "competition": "Premier League",
            "verdict": "market-led",
        },
        "estimated_lambdas": {"home_lambda": 1.6, "away_lambda": 1.0},
        "lambda_source": "market_odds",
    }


def test_three_positive_ev_legs_build_a_six_odds_slip(db):
    kickoff = NOW + timedelta(hours=11)
    for index in range(3):
        _add(db, f"Home {index}", f"Away {index}", kickoff + timedelta(minutes=index))

    report = ho.build_high_odds(
        db, now=NOW, analyse=lambda fixture, db=None: _analysis()
    )

    assert report["count"] == 1
    assert report["message"] != NO_QUALIFIED_SELECTIONS
    slip = report["slips"][0]
    assert slip["size"] == 3
    assert slip["total_odds"] == pytest.approx(5.83, abs=0.01)
    assert slip["combined_probability"] == pytest.approx(0.216, abs=0.001)
    assert slip["expected_value_percent"] > 0
    for leg in slip["legs"]:
        assert leg["expected_value"] > 0
        assert leg["bookmaker"] == "BetPawa"
        assert leg["captured_at"]
        assert leg["selection"].endswith("to win")
        assert leg["market_overround"] == pytest.approx(1.0438, abs=0.001)
    assert report["positive_ev_legs"] == 3
    assert report["rejected"]["negative_ev_leg"] == 0
    assert report["rejected"]["inconsistent_market_prices"] == 0


def test_negative_ev_slate_returns_the_exact_empty_message(db):
    kickoff = NOW + timedelta(hours=11)
    for index in range(3):
        _add(db, f"Home {index}", f"Away {index}", kickoff + timedelta(minutes=index))

    report = ho.build_high_odds(
        db, now=NOW, analyse=lambda fixture, db=None: _analysis(home_probability=0.50)
    )

    assert report["slips"] == []
    assert report["count"] == 0
    assert report["message"] == NO_QUALIFIED_SELECTIONS
    assert report["rejected"]["negative_ev_leg"] == 3
    assert report["diagnostics"]["best_leg_ev"] < 0
    assert len(report["closest_legs"]) == 3


def test_unattributed_prices_and_a_missing_blend_are_rejected(db):
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Home A", "Away A", kickoff, attributed=False)
    _add(db, "Home B", "Away B", kickoff)

    report = ho.build_high_odds(
        db, now=NOW, analyse=lambda fixture, db=None: _analysis(blend=False)
    )

    assert report["slips"] == []
    assert report["rejected"]["no_priced_market"] == 1
    assert report["rejected"]["no_fitted_blend"] == 1


def test_duplicate_feed_rows_collapse_before_combining(db):
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Czechia", "Spain", kickoff)
    _add(db, "Czech Republic", "Spain", kickoff + timedelta(minutes=5))

    report = ho.build_high_odds(
        db, now=NOW, analyse=lambda fixture, db=None: _analysis()
    )

    assert report["rejected"]["duplicate_match"] == 1
    assert report["count"] == 0


def test_prices_stitched_across_bookmakers_are_not_treated_as_a_market(db):
    kickoff = NOW + timedelta(hours=11)
    # 2.75 / 3.35 / 6.65 implies only 0.81: best prices from different books.
    # De-vigging this mixture would invent an edge, so it is refused outright.
    stitched = {"home_win": 2.75, "draw": 3.35, "away_win": 6.65}
    for index in range(3):
        _add(
            db,
            f"Home {index}",
            f"Away {index}",
            kickoff + timedelta(minutes=index),
            odds=stitched,
        )

    report = ho.build_high_odds(
        db, now=NOW, analyse=lambda fixture, db=None: _analysis(home_probability=0.75)
    )

    assert report["slips"] == []
    assert report["message"] == NO_QUALIFIED_SELECTIONS
    assert report["rejected"]["inconsistent_market_prices"] == 3
    assert report["legs_evaluated"] == 0
    assert report["params"]["min_market_overround"] == ho.MIN_MARKET_OVERROUND


def test_high_odds_route_serves_the_cap(monkeypatch):
    from app.api import markets as markets_api

    seen = {}

    def fake(db, limit, target_date):
        seen.update(limit=limit, target_date=target_date)
        return {"count": 0, "slips": [], "message": NO_QUALIFIED_SELECTIONS}

    monkeypatch.setattr(markets_api, "build_high_odds", fake)

    body = TestClient(app).get("/markets/high-odds", params={"limit": 2}).json()

    assert body["message"] == NO_QUALIFIED_SELECTIONS
    assert seen == {"limit": 2, "target_date": None}
