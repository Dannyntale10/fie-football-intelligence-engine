from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.fixture import Fixture
from app.services import combo as combo_mod


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


def _add_fixture(db, suffix, home_odds=None, draw_odds=None, away_odds=None, market_odds=None):
    fixture = Fixture(
        dedupe_key=f"test-odds-policy-{suffix}",
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
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def _fake_analysis(fixture, **kwargs):
    return {
        "all_markets": {
            "home_win": 0.95,
            "draw": 0.03,
            "away_win": 0.02,
            "double_chance_1x": 0.96,
            "double_chance_x2": 0.02,
            "double_chance_12": 0.02,
            "home_over_0_5": 0.10,
            "away_over_0_5": 0.02,
            "over_1_5": 0.10,
            "under_3_5": 0.10,
            "ah_away_plus_1_5": 0.05,
        },
        "value_bets": [],
        "qualifying_markets": [],
        "data_quality": {"score": 90, "grade": "A"},
    }


@pytest.fixture()
def stub_engine(monkeypatch):
    monkeypatch.setattr(combo_mod, "analyze_fixture", _fake_analysis)
    monkeypatch.setattr(
        combo_mod,
        "check_squad_and_injuries",
        lambda **kwargs: {"verdict": "APPROVED", "badge": "ok"},
    )


def _generate(db, require_market_odds):
    return combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.85,
        min_accumulator_prob=0.85,
        min_total_odds=2.00,
        require_market_odds=require_market_odds,
    )


def test_market_gate_excludes_estimated_legs(db, stub_engine):
    f1 = _add_fixture(db, "1", home_odds=1.80, draw_odds=3.60, away_odds=7.00)
    f2 = _add_fixture(db, "2", home_odds=1.80, draw_odds=3.60, away_odds=7.00)
    _add_fixture(db, "3")  # no prices at all -> only estimates available

    res = _generate(db, require_market_odds=True)
    best = res["best_combo"]

    assert best is not None
    assert all(leg["odds_source"] == "market" for leg in best["legs"])
    assert {leg["fixture_id"] for leg in best["legs"]} == {f1.id, f2.id}
    assert res["odds_policy"]["require_market_odds"] is True
    assert res["odds_policy"]["synthetic_legs_excluded"] > 0


def test_estimated_legs_are_allowed_and_labelled_when_opted_in(db, stub_engine):
    _add_fixture(db, "1", home_odds=1.80, draw_odds=3.60, away_odds=7.00)
    _add_fixture(db, "2", home_odds=1.80, draw_odds=3.60, away_odds=7.00)

    res = _generate(db, require_market_odds=False)
    best = res["best_combo"]

    assert best is not None
    assert res["odds_policy"]["require_market_odds"] is False
    assert all(leg["odds_source"] in {"market", "synthetic"} for leg in best["legs"])
    assert any(leg["odds_source"] == "synthetic" for leg in best["legs"])


def test_devig_removes_bookmaker_overround():
    implied, fair = combo_mod._devigged_probabilities(
        {"home_win": 2.0, "draw": 3.5, "away_win": 4.0}
    )

    assert sum(fair.values()) == pytest.approx(1.0)
    raw_total = sum(implied.values())
    assert raw_total > 1.0
    assert fair["home_win"] == pytest.approx(implied["home_win"] / raw_total)


def test_ev_mode_selects_legs_that_beat_the_market(db, monkeypatch):
    monkeypatch.setattr(
        combo_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: {
            "all_markets": {"home_win": 0.70, "draw": 0.18, "away_win": 0.12},
        },
    )
    monkeypatch.setattr(
        combo_mod, "check_squad_and_injuries", lambda **kwargs: {"verdict": "APPROVED"}
    )

    for suffix in ("1", "2"):
        _add_fixture(
            db,
            suffix,
            home_odds=2.10,
            draw_odds=3.50,
            away_odds=3.40,
            market_odds={"home_win": 2.10, "draw": 3.50, "away_win": 3.40},
        )

    res = combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.55,
        min_total_odds=1.50,
        min_edge_percent=3.0,
        selection_mode="ev",
    )

    best = res["best_combo"]
    assert res["diagnostics"]["mode"] == "ev"
    assert best is not None
    assert all(leg["edge_percent"] > 0 for leg in best["legs"])
    assert all(leg["market_key"] == "home_win" for leg in best["legs"])
    assert best["expected_value_percent"] > 0


def test_ev_mode_rejects_legs_without_an_edge(db, monkeypatch):
    monkeypatch.setattr(
        combo_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: {
            "all_markets": {"home_win": 0.70, "draw": 0.18, "away_win": 0.12},
        },
    )
    monkeypatch.setattr(
        combo_mod, "check_squad_and_injuries", lambda **kwargs: {"verdict": "APPROVED"}
    )

    # 1.25 implies 80% raw, ~75% after de-vig: the model's 70% is below the market.
    for suffix in ("1", "2"):
        _add_fixture(
            db,
            suffix,
            home_odds=1.25,
            draw_odds=5.50,
            away_odds=12.00,
            market_odds={"home_win": 1.25, "draw": 5.50, "away_win": 12.00},
        )

    res = combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.55,
        min_total_odds=1.50,
        min_edge_percent=3.0,
        selection_mode="ev",
    )

    assert res["best_combo"] is None
    assert res["diagnostics"]["candidate_legs"] == 0


def test_ev_mode_uses_non_1x2_market_prices(db, monkeypatch):
    monkeypatch.setattr(
        combo_mod,
        "analyze_fixture",
        lambda fixture, **kwargs: {
            "all_markets": {"home_win": 0.40, "draw": 0.27, "away_win": 0.33, "btts_no": 0.68},
        },
    )
    monkeypatch.setattr(
        combo_mod, "check_squad_and_injuries", lambda **kwargs: {"verdict": "APPROVED"}
    )

    for suffix in ("1", "2"):
        _add_fixture(
            db,
            suffix,
            home_odds=2.50,
            draw_odds=3.30,
            away_odds=2.80,
            market_odds={
                "home_win": 2.50,
                "draw": 3.30,
                "away_win": 2.80,
                "btts_no": 1.70,
            },
        )

    res = combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.55,
        min_total_odds=1.50,
        min_edge_percent=3.0,
        selection_mode="ev",
    )

    best = res["best_combo"]
    assert best is not None
    # 1/1.70 = 58.8% implied vs the model's 68% -> a real edge on BTTS No.
    assert {leg["market_key"] for leg in best["legs"]} == {"btts_no"}
