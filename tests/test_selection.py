from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.betting import selection as selection_mod
from app.database import Base
from app.models.fixture import Fixture


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


@pytest.fixture(autouse=True)
def approved_squad(monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "check_squad_and_injuries",
        lambda **kwargs: {"verdict": "APPROVED", "source": "local", "badge": "ok"},
    )


def _add_fixture(
    db,
    suffix,
    market_odds=None,
    home_odds=None,
    draw_odds=None,
    away_odds=None,
    odds_updated_at="now",
):
    fixture = Fixture(
        dedupe_key=f"test-selection-{suffix}",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(days=1),
        home_team=f"Home {suffix}",
        away_team=f"Away {suffix}",
        competition="Test League",
        country="Test",
        source="test",
        home_odds=home_odds,
        draw_odds=draw_odds,
        away_odds=away_odds,
        market_odds_json=json.dumps(market_odds) if market_odds else None,
        odds_updated_at=datetime.now(timezone.utc) if odds_updated_at == "now" else None,
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def _analysis(probabilities, lambda_source="team_ratings"):
    return {
        "all_markets": probabilities,
        "lambda_source": lambda_source,
        "estimated_lambdas": {"home_lambda": 1.45, "away_lambda": 1.15},
        "probability_blend": {"applied": False},
    }


def test_chooses_the_right_structure_above_the_floor(db, monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(
            {
                "home_win": 0.80,
                "draw": 0.10,
                "away_win": 0.10,
                "double_chance_1x": 0.90,
                "over_1_5": 0.86,
            }
        ),
    )
    fixture = _add_fixture(
        db,
        "structure",
        home_odds=1.20,
        draw_odds=6.00,
        away_odds=12.00,
        market_odds={
            "home_win": 1.20,
            "draw": 6.00,
            "away_win": 12.00,
            "double_chance_1x": 1.10,
            "over_1_5": 1.15,
        },
    )

    result = selection_mod.select_top_picks([fixture], db)

    assert result["qualified"] == 1
    pick = result["picks"][0]
    # Home win is only 80%, so the 90% double chance is the correct structure.
    assert pick["market_key"] == "double_chance_1x"
    assert pick["probability"] >= 0.85
    assert {row["market_key"] for row in pick["runner_ups"]} == {"over_1_5"}
    assert result["threshold"] == 0.85
    assert result["min_confidence"] == 0.60


def test_below_floor_is_rejected_even_when_it_is_the_only_structure(db, monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(
            {"home_win": 0.84, "draw": 0.09, "away_win": 0.07}
        ),
    )
    fixture = _add_fixture(
        db,
        "below-floor",
        home_odds=1.15,
        draw_odds=8.0,
        away_odds=15.0,
        market_odds={"home_win": 1.15, "draw": 8.0, "away_win": 15.0},
    )

    result = selection_mod.select_top_picks([fixture], db)

    assert result["picks"] == []
    assert result["message"] == "NO QUALIFIED SELECTIONS FOUND."
    assert result["rejected"]["below_probability_floor"] >= 1


def test_unrated_fixture_cannot_supply_a_pick(db, monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(
            {"home_win": 0.95, "draw": 0.03, "away_win": 0.02},
            lambda_source="market_draw",
        ),
    )
    fixture = _add_fixture(
        db,
        "unrated",
        home_odds=1.05,
        draw_odds=12.0,
        away_odds=25.0,
        market_odds={"home_win": 1.05, "draw": 12.0, "away_win": 25.0},
        odds_updated_at=None,
    )

    result = selection_mod.select_top_picks([fixture], db)

    assert result["picks"] == []
    assert result["rejected"]["low_data_confidence"] >= 1


def test_the_85_percent_floor_cannot_be_lowered(db, monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(
            {"home_win": 0.80, "draw": 0.10, "away_win": 0.10}
        ),
    )
    fixture = _add_fixture(
        db,
        "floor-lock",
        home_odds=1.20,
        draw_odds=6.0,
        away_odds=12.0,
        market_odds={"home_win": 1.20, "draw": 6.0, "away_win": 12.0},
    )

    result = selection_mod.select_top_picks([fixture], db, threshold=0.50)

    assert result["threshold"] == 0.85
    assert result["picks"] == []


def test_estimated_prices_are_excluded_by_default_and_allowed_when_opted_in(db, monkeypatch):
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(
            {"home_win": 0.95, "draw": 0.03, "away_win": 0.02, "double_chance_1x": 0.98}
        ),
    )
    fixture = _add_fixture(db, "estimated")

    gated = selection_mod.select_top_picks([fixture], db, require_market_odds=True)
    assert gated["picks"] == []
    assert gated["estimates_skipped"] >= 1

    opted_in = selection_mod.select_top_picks([fixture], db, require_market_odds=False)
    assert opted_in["qualified"] == 1
    assert opted_in["picks"][0]["odds_source"] == "synthetic"


def test_one_top_structure_per_fixture_with_global_rank(db, monkeypatch):
    probabilities = {
        "home_win": 0.96,
        "draw": 0.02,
        "away_win": 0.02,
        "double_chance_1x": 0.98,
        "over_1_5": 0.90,
    }
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(probabilities),
    )
    fixtures = [
        _add_fixture(
            db,
            suffix,
            home_odds=1.04,
            draw_odds=15.0,
            away_odds=30.0,
            market_odds={"home_win": 1.04, "draw": 15.0, "away_win": 30.0, "over_1_5": 1.10},
        )
        for suffix in ("one", "two")
    ]

    result = selection_mod.select_top_picks(fixtures, db)

    assert result["qualified"] == 2
    assert [pick["fixture_id"] for pick in result["picks"]] == [row.id for row in fixtures]
    assert len({pick["fixture_id"] for pick in result["picks"]}) == 2
    assert [pick["rank"] for pick in result["picks"]] == [1, 2]
    scores = [pick["score"] for pick in result["picks"]]
    assert scores == sorted(scores, reverse=True)


def test_verified_squad_veto_falls_back_to_the_next_structure(db, monkeypatch):
    probabilities = {
        "home_win": 0.96,
        "draw": 0.02,
        "away_win": 0.02,
        "double_chance_1x": 0.98,
        "over_1_5": 0.90,
    }
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: _analysis(probabilities),
    )

    def fake_squad(**kwargs):
        if kwargs["selection"].startswith("Home"):
            return {"verdict": "VETO", "source": "verified"}
        return {"verdict": "APPROVED", "source": "local"}

    monkeypatch.setattr(selection_mod, "check_squad_and_injuries", fake_squad)
    fixture = _add_fixture(
        db,
        "veto",
        home_odds=1.04,
        draw_odds=15.0,
        away_odds=30.0,
        market_odds={"home_win": 1.04, "draw": 15.0, "away_win": 30.0, "over_1_5": 1.10},
    )

    result = selection_mod.select_top_picks([fixture], db)

    assert result["qualified"] == 1
    # The vetoed home win falls through to the next eligible real price.
    assert result["picks"][0]["market_key"] == "over_1_5"
    assert result["picks"][0]["ai_squad"]["verdict"] == "APPROVED"
    assert result["vetoed_fixtures"] == 0


def test_blend_fields_are_reported_for_1x2(db, monkeypatch):
    analysis = _analysis({"home_win": 0.90, "draw": 0.06, "away_win": 0.04})
    analysis["probability_blend"] = {
        "applied": True,
        "weight": 0.003,
        "model": {"home_win": 0.95, "draw": 0.03, "away_win": 0.02},
    }
    monkeypatch.setattr(
        selection_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: analysis,
    )
    fixture = _add_fixture(
        db,
        "blend",
        home_odds=1.10,
        draw_odds=8.0,
        away_odds=18.0,
        market_odds={"home_win": 1.10, "draw": 8.0, "away_win": 18.0},
    )

    result = selection_mod.select_top_picks([fixture], db)
    pick = result["picks"][0]

    assert pick["blended"] is True
    assert pick["blend_weight"] == pytest.approx(0.003)
    assert pick["probability_model"] == pytest.approx(0.95)
