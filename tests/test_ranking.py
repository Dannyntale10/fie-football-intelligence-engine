from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.fixture import Fixture
from app.services import combo as combo_mod
from app.services import ranking


def _leg(**overrides):
    leg = {
        "selection": "Home Win",
        "match": "A vs B",
        "odds": 2.0,
        "probability": 0.70,
        "edge_percent": 6.0,
        "expected_value_percent": 40.0,
        "odds_source": "market",
        "model_source": "team_ratings",
        "calibrated": False,
    }
    leg.update(overrides)
    return leg


def _combo(**overrides):
    combo = {
        "legs_count": 2,
        "total_odds": 4.0,
        "combined_probability": 0.49,
        "avg_leg_probability": 0.70,
        "edge_percent": 10.0,
        "expected_value_percent": 96.0,
        "legs": [_leg(), _leg(selection="Away Win")],
    }
    combo.update(overrides)
    return combo


def test_leg_score_rewards_edge_and_probability():
    weak = ranking.leg_score(_leg(edge_percent=1.0, probability=0.55, expected_value_percent=5.0))
    strong = ranking.leg_score(_leg(edge_percent=12.0, probability=0.95, expected_value_percent=60.0))

    assert 0 <= weak["score"] <= 100
    assert strong["score"] > weak["score"]
    assert strong["grade"] in {"A", "B"}


def test_leg_score_counts_real_prices_as_evidence():
    market = ranking.leg_score(_leg(odds_source="market", calibrated=True, model_source="team_ratings"))
    estimated = ranking.leg_score(
        _leg(odds_source="synthetic", calibrated=False, model_source="fallback")
    )

    assert market["components"]["evidence"] > estimated["components"]["evidence"]
    assert market["score"] > estimated["score"]


def test_rank_legs_orders_highest_first_and_numbers_them():
    legs = [
        _leg(selection="low", edge_percent=1.0, probability=0.60, expected_value_percent=5.0),
        _leg(selection="high", edge_percent=11.0, probability=0.90, expected_value_percent=50.0),
        _leg(selection="mid", edge_percent=5.0, probability=0.75, expected_value_percent=20.0),
    ]

    ranked = ranking.rank_legs(legs)

    assert [r["rank"] for r in ranked] == [1, 2, 3]
    assert [r["selection"] for r in ranked] == ["high", "mid", "low"]
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    # Inputs are untouched: ranking is a view, not a mutation of selection data.
    assert "rank" not in legs[0]


def test_certainty_mode_ranks_by_probability_not_edge():
    safe = _combo(
        combined_probability=0.86,
        avg_leg_probability=0.93,
        total_odds=2.05,
        edge_percent=1.0,
        expected_value_percent=76.0,
    )
    risky = _combo(
        combined_probability=0.30,
        avg_leg_probability=0.55,
        total_odds=8.00,
        edge_percent=40.0,
        expected_value_percent=140.0,
    )

    ev_order = ranking.rank_combos([safe, risky], "ev")
    cert_order = ranking.rank_combos([safe, risky], "certainty")

    assert ev_order[0]["total_odds"] == risky["total_odds"]
    assert cert_order[0]["total_odds"] == safe["total_odds"]


def test_rank_combos_assigns_sequential_ranks_and_grade():
    combos = [_combo(total_odds=2.0, edge_percent=2.0, expected_value_percent=10.0), _combo()]

    ranked = ranking.rank_combos(combos, "ev")

    assert [r["rank"] for r in ranked] == [1, 2]
    assert ranked[0]["score"] >= ranked[1]["score"]
    assert ranked[0]["grade"] in {"A", "B", "C", "D"}
    assert ranked[0]["grade_label"]


# ── integration with the combo engine ────────────────────────────────────────

_VALIDATED_REPORT = {
    "verdict": "model_beats_market",
    "n_matches": 3576,
    "model": {"brier": 0.5600},
    "market": {"brier": 0.5663},
    "skill": {"validated": True},
    "edge_threshold_multiplier": 1.0,
    "generated_at": "2026-11-01T00:00:00+00:00",
}


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
def _stub_reports(monkeypatch):
    from app.services import calibration, learning

    monkeypatch.setattr(calibration, "load_report", lambda: _VALIDATED_REPORT)
    monkeypatch.setattr(learning, "load_report", lambda: None)


@pytest.fixture()
def stub_engine(monkeypatch):
    def fake_analysis(fixture, **kwargs):
        return {
            "all_markets": {"home_win": 0.70, "draw": 0.18, "away_win": 0.12},
            "lambda_source": "team_ratings",
        }

    monkeypatch.setattr(combo_mod, "analyze_fixture", fake_analysis)
    monkeypatch.setattr(
        combo_mod, "check_squad_and_injuries", lambda **kwargs: {"verdict": "APPROVED"}
    )


def _add_fixture(db, suffix, home_odds=2.10, draw_odds=3.50, away_odds=3.40):
    fixture = Fixture(
        dedupe_key=f"test-ranking-{suffix}",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(days=1),
        home_team=f"Home {suffix}",
        away_team=f"Away {suffix}",
        competition="Test League",
        country="Test",
        source="test",
        home_odds=home_odds,
        draw_odds=draw_odds,
        away_odds=away_odds,
        market_odds_json=json.dumps(
            {"home_win": home_odds, "draw": draw_odds, "away_win": away_odds}
        ),
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def test_combo_response_ranks_slips_and_matches(db, stub_engine):
    for suffix in ("1", "2", "3"):
        _add_fixture(db, suffix)

    res = combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.55,
        min_total_odds=1.50,
        min_edge_percent=3.0,
        max_edge_percent=50.0,
        selection_mode="ev",
    )

    block = res["ranking"]
    assert block["version"] == ranking.RANKING_VERSION
    assert block["total_combos_ranked"] == res["total_combos_generated"] > 0
    assert block["total_legs_ranked"] == res["total_candidate_legs"] > 0

    assert res["best_combo"]["rank"] == 1
    assert block["combos"][0]["rank"] == res["best_combo"]["rank"]
    assert block["combos"][0]["rank_score"] == res["best_combo"]["score"]

    combo_scores = [row["rank_score"] for row in block["combos"]]
    assert combo_scores == sorted(combo_scores, reverse=True)
    assert [row["rank"] for row in block["combos"]] == list(range(1, len(block["combos"]) + 1))

    leg_scores = [row["rank_score"] for row in block["matches"]]
    assert leg_scores == sorted(leg_scores, reverse=True)
    assert block["matches"][0]["rank"] == 1

    alt_ranks = [alt["rank"] for alt in res["alternatives"]]
    assert alt_ranks == list(range(2, 2 + len(alt_ranks)))


def test_blocked_slip_reports_empty_ranking(db, stub_engine, monkeypatch):
    from app.services import calibration

    monkeypatch.setattr(
        calibration,
        "load_report",
        lambda: {
            "verdict": "market_beats_model",
            "n_matches": 100,
            "model": {"brier": 0.62},
            "market": {"brier": 0.56},
            "skill": {"validated": False},
            "edge_threshold_multiplier": 1.0,
            "generated_at": "2026-11-01T00:00:00+00:00",
        },
    )
    for suffix in ("1", "2"):
        _add_fixture(db, suffix)

    res = combo_mod.generate_high_confidence_combos(
        db=db,
        target_date="all",
        num_legs=2,
        min_leg_prob=0.55,
        min_total_odds=1.50,
        min_edge_percent=3.0,
        max_edge_percent=50.0,
        selection_mode="ev",
    )

    assert res["blocked_reason"]
    assert res["best_combo"] is None
    assert res["ranking"]["combos"] == []
    assert res["ranking"]["matches"] == []
