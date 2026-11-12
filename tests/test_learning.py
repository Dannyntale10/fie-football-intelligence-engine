from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.services import learning
from app.services.match_analysis import build_match_dossier


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


def _pairs(true_a: float, n: int = 1200, seed: int = 7):
    rng = random.Random(seed)
    pairs = []
    for _ in range(n):
        p = rng.uniform(0.15, 0.85)
        z = math.log(p / (1 - p))
        q = 1.0 / (1.0 + math.exp(-true_a * z))
        pairs.append((p, 1.0 if rng.random() < q else 0.0))
    return pairs


def test_fit_platt_learns_overconfidence():
    a, b = learning.fit_platt(_pairs(true_a=0.35))
    assert 0.2 < a < 0.9, f"expected shrinkage toward the middle, got a={a}"
    assert abs(b) < 0.6


def test_fit_market_requires_enough_history():
    result = learning.fit_market(_pairs(true_a=0.4, n=80))
    assert result["applied"] is False
    assert result["a"] == 1.0 and result["b"] == 0.0
    assert "insufficient history" in result["reason"]


def test_fit_market_validates_improvement_on_held_out_slice():
    result = learning.fit_market(_pairs(true_a=0.35, n=2000))
    assert result["n_train"] > result["n_validation"] > 0
    assert result["improvement"] > 0
    assert result["applied"] is True
    assert result["a"] < 1.0


def test_apply_calibration_is_identity_without_report():
    prob, applied = learning.apply_calibration("home_win", 0.81, report={})
    assert prob == 0.81
    assert applied is False


def test_apply_calibration_only_uses_applied_markets():
    report = {
        "markets": {
            "home_win": {"a": 0.7, "b": 0.1, "applied": True},
            "draw": {"a": 0.5, "b": 0.0, "applied": False},
        }
    }
    prob, applied = learning.apply_calibration("home_win", 0.8, report=report)
    assert applied is True
    assert prob != 0.8

    draw_prob, draw_applied = learning.apply_calibration("draw", 0.3, report=report)
    assert draw_applied is False
    assert draw_prob == 0.3


def test_calibrate_markets_renormalises_outcome_groups():
    report = {
        "markets": {
            "home_win": {"a": 1.4, "b": 0.2, "applied": True},
            "away_win": {"a": 1.4, "b": 0.2, "applied": True},
            "draw": {"a": 1.0, "b": 0.0, "applied": False},
        }
    }
    result = learning.calibrate_markets(
        {"home_win": 0.5, "draw": 0.3, "away_win": 0.2}, report
    )
    total = sum(prob for prob, _ in result.values())
    assert abs(total - 1.0) < 1e-3, f"outcome group drifted to {total}"
    assert result["home_win"][1] is True


def test_calibrate_markets_leaves_ungrouped_market_alone():
    report = {"markets": {"btts_yes": {"a": 0.8, "b": 0.0, "applied": True}}}
    result = learning.calibrate_markets({"btts_yes": 0.55, "ah_home_minus_0_5": 0.6}, report)
    assert result["ah_home_minus_0_5"] == (0.6, False)
    assert result["btts_yes"][1] is True


def test_apply_calibration_stays_inside_bounds():
    report = {"markets": {"home_win": {"a": 4.0, "b": 1.5, "applied": True}}}
    high, _ = learning.apply_calibration("home_win", 0.999, report=report)
    low, _ = learning.apply_calibration("home_win", 0.001, report=report)
    assert high <= 0.995
    assert low >= 0.005


def test_edge_premium_needs_minimum_sample_and_is_capped():
    small = [{"market_key": "over_2_5", "probability": 0.9, "won": False} for _ in range(10)]
    assert learning._edge_premiums(small) == {}

    many = [{"market_key": "over_2_5", "probability": 0.9, "won": False} for _ in range(1000)]
    premiums = learning._edge_premiums(many)
    assert premiums["over_2_5"]["premium"] == learning.MAX_EDGE_PREMIUM
    assert premiums["over_2_5"]["observed_win_rate"] == 0.0


def _tracked_bet(db, *, label, competition, status="LOST", market_key="home_win"):
    bet = TrackedBet(
        slip_type="2-Team Combo",
        matchday_label=label,
        total_odds=2.5,
        avg_confidence=0.8,
        accum_probability=0.64,
        stake_amount=10.0,
        legs_json=json.dumps([{
            "match": "A vs B",
            "selection": "A to Win",
            "competition": competition,
            "market_key": market_key,
            "probability": 0.8,
            "odds": 1.6,
            "status": status,
        }]),
        status=status,
    )
    db.add(bet)
    db.commit()
    return bet


def test_settled_legs_exclude_synthetic_slips(db):
    _tracked_bet(db, label="Test Matchday", competition="Test League")
    _tracked_bet(db, label="Sat 14 Nov 2026", competition="Premier League")

    legs = learning.settled_legs(db)
    assert len(legs) == 1
    assert legs[0]["market_key"] == "home_win"
    assert legs[0]["won"] is False


def test_build_report_uses_out_of_sample_predictions(db, monkeypatch):
    from app.services import calibration as calibration_mod

    def fake_walkforward(_db, **_kwargs):
        rng = random.Random(11)
        for i in range(400):
            p = rng.uniform(0.2, 0.8)
            label = "home_win" if rng.random() < p else "draw"
            yield {
                "model": {"home_win": p, "draw": (1 - p) * 0.7, "away_win": (1 - p) * 0.3},
                "label": label,
                "kickoff_utc": datetime.now(timezone.utc) - timedelta(days=400 - i),
            }

    monkeypatch.setattr(calibration_mod, "iter_walkforward_predictions", fake_walkforward)

    report = learning.build_report(db)
    assert report["version"] == learning.LEARNING_VERSION
    assert report["sources"]["walkforward_matches"] == 400
    assert report["sources"]["settled_legs"] == 0
    assert set(report["markets"]) >= {"home_win", "draw", "away_win"}
    assert "No settled real slips yet" in " ".join(report["notes"])


def _add_finished(db, home, away, home_goals, away_goals, days_ago):
    match = Fixture(
        dedupe_key=f"{home}-{away}-{days_ago}",
        kickoff_utc=datetime.now(timezone.utc) - timedelta(days=days_ago),
        home_team=home,
        away_team=away,
        competition="Premier League",
        country="England",
        source="test",
        home_score=home_goals,
        away_score=away_goals,
        status="finished",
    )
    db.add(match)
    db.commit()
    return match


def _add_upcoming(db):
    fixture = Fixture(
        dedupe_key="dossier-upcoming",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(days=1),
        home_team="Alpha",
        away_team="Beta",
        competition="Premier League",
        country="England",
        source="test",
        home_odds=1.90,
        draw_odds=3.50,
        away_odds=4.20,
        market_odds_json=json.dumps({"over_2_5": 1.95, "under_2_5": 1.90, "btts_yes": 1.80}),
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def test_match_dossier_assembles_evidence_and_risks(db):
    _add_finished(db, "Alpha", "Gamma", 2, 0, 10)
    _add_finished(db, "Delta", "Alpha", 1, 1, 20)
    _add_finished(db, "Alpha", "Beta", 3, 1, 30)
    _add_finished(db, "Beta", "Gamma", 0, 2, 12)
    fixture = _add_upcoming(db)

    dossier = build_match_dossier(db, fixture)

    assert dossier["fixture"]["id"] == fixture.id
    assert dossier["form"]["home"]["record"] == "2W-1D-0L"
    assert dossier["form"]["home"]["form"] == "WDW"
    assert dossier["form"]["home"]["goals_for_per_match"] > 0
    assert any(m["venue"] == "home" for m in dossier["form"]["home"]["matches"])

    assert dossier["h2h"]["total"] >= 1
    assert dossier["markets"], "priced markets should produce rows"
    assert all(r["price"] > 1.0 for r in dossier["markets"])
    assert dossier["player_form"]["available"] is False

    codes = {r["code"] for r in dossier["risks"]}
    assert "no_team_ratings" in codes
    assert "advisory_team_news" in codes

    assert dossier["verdict"]["confidence"] in {"low", "medium", "high"}
    assert dossier["team_news"]["advisory"] is True
