"""The weekly scorecard: results, CLV, Brier versus the de-vigged market and
hit rate by tier. A losing week is only readable when the process metrics
sit next to the profit number, so every block must report its own sample."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.price_snapshot import PriceSnapshot  # noqa: F401  (registers the table)
from app.betting.scorecard import build_scorecard

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def mem_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _fixture(**overrides) -> Fixture:
    defaults = dict(
        dedupe_key=overrides.get("dedupe_key", "scorecard-key"),
        kickoff_utc=datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc),
        home_team="Alpha",
        away_team="Beta",
        competition="Premier League",
        country="England",
        source="test",
        home_odds=2.0,
        draw_odds=3.5,
        away_odds=4.0,
        status="finished",
    )
    defaults.update(overrides)
    return Fixture(**defaults)


def _bet(*, status, legs, tier=0.85, settled_days_ago=1, label="Sat 12 Nov 2026"):
    settled_at = NOW - timedelta(days=settled_days_ago)
    return TrackedBet(
        slip_type="2-Team Combo",
        matchday_label=label,
        total_odds=2.4,
        avg_confidence=85.0,
        accum_probability=72.0,
        stake_amount=100000.0,
        stake_currency="UGX",
        stake_usd=27.0,
        status=status,
        profit_loss=0.0,
        profit_loss_usd=0.0,
        legs_json=json.dumps(legs),
        placed_at=settled_at - timedelta(hours=6),
        settled_at=settled_at,
        tier=tier,
    )


def _leg(*, status, probability=0.6, market_key="home_win", fixture_id=None):
    return {
        "match": "Alpha vs Beta",
        "competition": "Premier League",
        "selection": "Alpha to Win",
        "market_key": market_key,
        "probability": probability,
        "odds": 1.6,
        "fixture_id": fixture_id,
        "status": status,
    }


def test_an_empty_window_reports_that_it_has_nothing(mem_db):
    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["status"] == "insufficient_sample"
    assert card["message"] == "NO SETTLED SELECTIONS IN THIS WINDOW."
    assert card["results"]["slips"] == 0
    assert card["results"]["win_rate"] is None
    assert card["brier"]["n"] == 0
    assert card["brier"]["model"] is None
    assert card["brier"]["vs_market"]["n"] == 0
    assert card["brier"]["vs_market"]["skill"] is None
    assert card["hit_rate_by_tier"] == []
    assert card["clv"]["n_selections"] == 0


def test_scorecard_scores_the_model_against_the_de_vigged_market(mem_db):
    fixture = _fixture()
    mem_db.add(fixture)
    mem_db.commit()

    mem_db.add_all([
        _bet(status="WON", legs=[_leg(status="WON", fixture_id=fixture.id)]),
        _bet(status="LOST", legs=[_leg(status="LOST", fixture_id=fixture.id)], settled_days_ago=2),
    ])
    mem_db.commit()

    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["status"] == "ok"
    assert card["results"]["slips"] == 2
    assert card["results"]["won"] == 1
    assert card["results"]["lost"] == 1
    assert card["results"]["win_rate"] == pytest.approx(50.0)
    assert card["selections"]["legs"] == 2
    assert card["selections"]["scored_legs"] == 2
    assert card["selections"]["hit_rate_percent"] == pytest.approx(50.0)

    # Brier: two legs at p=0.6, one won one lost -> (0.16 + 0.36) / 2.
    assert card["brier"]["n"] == 2
    assert card["brier"]["model"] == pytest.approx(0.26)
    # De-vigged home price at 2.0 in a 2.0/3.5/4.0 market is ~0.4828.
    market = card["brier"]["vs_market"]
    assert market["n"] == 2
    assert market["market"] == pytest.approx(0.2503, abs=1e-4)
    assert market["skill"] == pytest.approx(-0.0388, abs=1e-3)

    assert card["hit_rate_by_tier"] == [
        {"tier": 0.85, "slips": 2, "won": 1, "lost": 1, "win_rate": 50.0}
    ]


def test_synthetic_and_out_of_window_slips_are_excluded(mem_db):
    mem_db.add_all([
        _bet(status="WON", legs=[_leg(status="WON")], label="Test Matchday"),
        _bet(status="WON", legs=[_leg(status="WON")], settled_days_ago=30),
    ])
    mem_db.commit()

    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["status"] == "insufficient_sample"
    assert card["results"]["slips"] == 0
    assert card["brier"]["n"] == 0


def test_a_leg_without_a_probability_still_counts_as_a_result(mem_db):
    mem_db.add(_bet(status="WON", legs=[_leg(status="WON", probability=None)]))
    mem_db.commit()

    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["results"]["slips"] == 1
    assert card["results"]["won"] == 1
    assert card["selections"]["legs"] == 1
    assert card["selections"]["scored_legs"] == 0
    assert card["brier"]["n"] == 0
    assert card["hit_rate_by_tier"][0]["win_rate"] == 100.0


def test_a_non_1x2_leg_has_no_market_brier(mem_db):
    mem_db.add(_bet(status="LOST", legs=[_leg(status="LOST", market_key="over_2_5")]))
    mem_db.commit()

    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["brier"]["n"] == 1
    assert card["brier"]["model"] == pytest.approx(0.36)
    assert card["brier"]["vs_market"]["n"] == 0
    assert card["brier"]["vs_market"]["skill"] is None
    assert "1X2" in card["brier"]["vs_market"]["note"]


def test_the_endpoint_serves_the_scorecard():
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get("/evaluation/scorecard?days=7")

    assert response.status_code == 200
    body = response.json()
    assert {"status", "results", "selections", "clv", "brier", "hit_rate_by_tier"} <= set(body)
    assert body["window_days"] == 7


def test_clv_uses_only_the_window_slips(mem_db):
    fixture = _fixture()
    mem_db.add(fixture)
    mem_db.commit()

    old = _bet(status="WON", legs=[_leg(status="WON", fixture_id=fixture.id)], settled_days_ago=30)
    recent = _bet(status="LOST", legs=[_leg(status="LOST", fixture_id=fixture.id)])
    mem_db.add_all([old, recent])
    mem_db.commit()

    from app.betting.clv import record_selection_prices

    record_selection_prices(mem_db, old.id, [{"fixture_id": fixture.id, "market_key": "home_win", "odds": 2.1}])
    record_selection_prices(mem_db, recent.id, [{"fixture_id": fixture.id, "market_key": "home_win", "odds": 2.1}])
    mem_db.add(PriceSnapshot(fixture_id=fixture.id, market_key="home_win", price=2.0, kind="close"))
    mem_db.commit()

    card = build_scorecard(mem_db, days=7, now=NOW)

    assert card["clv"]["n_selections"] == 1
