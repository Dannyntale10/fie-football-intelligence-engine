"""Model-market blending: pooling, honest fitting and analysis wiring."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analytics import blending
from app.analytics.blending import (
    OUTCOMES,
    blend_1x2,
    blend_status,
    build_report,
    fit_parameters,
    logit,
    sigmoid,
)
from app.analytics.probabilities import analyze_fixture
from app.market_engine.discovery import discover_from_market_odds
from app.market_engine.market_analysis import analyse_discovery

MARKET_ODDS = {"home_win": 1.10, "draw": 9.0, "away_win": 30.0, "over_2_5": 1.55, "under_2_5": 2.45}
RATINGS = {"home_lambda": 3.0, "away_lambda": 0.2, "lambda_source": "team_ratings"}


class FixtureStub:
    def __init__(self, competition: str = "Test League") -> None:
        self.id = 11
        self.source = "TestBook"
        self.home_team = "Home"
        self.away_team = "Away"
        self.competition = competition
        self.home_odds = MARKET_ODDS["home_win"]
        self.draw_odds = MARKET_ODDS["draw"]
        self.away_odds = MARKET_ODDS["away_win"]
        self.market_odds_json = json.dumps(MARKET_ODDS)
        self.odds_updated_at = datetime.now(timezone.utc)
        self.kickoff_utc = datetime.now(timezone.utc) + timedelta(days=1)


def _write_report(path, weight: float, temperature: float = 1.0, per_competition=None) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "fitted",
                "scope": "1x2",
                "verdict": "blend_matches_market",
                "weights": {
                    "default": weight,
                    "temperature": temperature,
                    "per_competition": per_competition or {},
                },
                "generated_at": "2026-11-12T00:00:00+00:00",
            }
        )
    )


def test_logit_and_sigmoid_round_trip():
    for probability in (0.01, 0.2, 0.5, 0.81, 0.99):
        assert sigmoid(logit(probability)) == pytest.approx(probability, abs=1e-9)
    assert sigmoid(logit(0.0)) < 0.01
    assert sigmoid(logit(1.0)) > 0.99


def test_blend_endpoints_and_normalisation():
    model = {"home_win": 0.82, "draw": 0.12, "away_win": 0.06}
    market = {"home_win": 0.70, "draw": 0.20, "away_win": 0.10}

    pure_market = blend_1x2(model, market, 0.0)
    pure_model = blend_1x2(model, market, 1.0)
    half = blend_1x2(model, market, 0.5)

    assert pure_market["home_win"] == pytest.approx(0.70, abs=1e-6)
    assert pure_model["home_win"] == pytest.approx(0.82, abs=1e-3)
    assert 0.70 < half["home_win"] < 0.82
    for probabilities in (pure_market, pure_model, half):
        assert sum(probabilities.values()) == pytest.approx(1.0, abs=1e-5)


def test_blend_falls_back_to_model_without_a_market_price():
    model = {"home_win": 0.6, "draw": 0.25, "away_win": 0.15}
    blended = blend_1x2(model, {"home_win": 0.5}, 0.3)
    assert 0.5 < blended["home_win"] < 0.6
    assert blended["draw"] / blended["away_win"] == pytest.approx(0.25 / 0.15, abs=1e-6)
    assert sum(blended.values()) == pytest.approx(1.0, abs=1e-5)


def _synthetic_predictions(count: int, weight: float, seed: int = 7):
    rng = random.Random(seed)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        raw_model = [rng.uniform(0.05, 0.9) for _ in OUTCOMES]
        raw_market = [rng.uniform(0.05, 0.9) for _ in OUTCOMES]
        model = {key: value / sum(raw_model) for key, value in zip(OUTCOMES, raw_model)}
        market = {key: value / sum(raw_market) for key, value in zip(OUTCOMES, raw_market)}
        truth = blend_1x2(model, market, weight)
        label = rng.choices(OUTCOMES, weights=[truth[key] for key in OUTCOMES])[0]
        kickoff = start + timedelta(hours=3 * index)
        rows.append(
            {
                "fixture": SimpleNamespace(kickoff_utc=kickoff),
                "competition": "Alpha" if index % 2 else "Beta",
                "model": model,
                "market": market,
                "label": label,
            }
        )
    return rows


def test_fit_parameters_recovers_a_known_weight():
    rows = _synthetic_predictions(1500, weight=0.6)
    weight, temperature, loss = fit_parameters(rows, [row["label"] for row in rows])
    assert weight == pytest.approx(0.6, abs=0.15)
    assert temperature == pytest.approx(1.0, abs=0.6)
    assert loss < 1.05


def test_build_report_splits_chronologically_and_scores_the_blend():
    rows = _synthetic_predictions(400, weight=0.5)
    report = build_report(rows)

    assert report["status"] == "fitted"
    assert report["scope"] == "1x2"
    assert report["weights"]["default"] == pytest.approx(0.5, abs=0.15)
    assert report["holdout"]["n_matches"] >= 60
    assert report["holdout"]["from"] > report["train"]["to"]
    assert report["holdout"]["blend"]["brier"] is not None
    assert report["holdout"]["skill"]["validated"] in (True, False)
    assert "message" in report


def test_build_report_refuses_to_fit_on_too_little_history():
    report = build_report(_synthetic_predictions(30, weight=0.5))
    assert report["status"] == "insufficient_data"
    assert report["n_matches"] == 30


def test_blend_status_prefers_the_per_competition_weight(tmp_path, monkeypatch):
    path = tmp_path / "blending.json"
    _write_report(path, weight=0.2, per_competition={"Test League": 0.8})
    monkeypatch.setattr(blending, "REPORT_PATH", path)
    monkeypatch.setattr(blending, "_REPORT_CACHE", {"mtime": None, "report": None})

    status = blend_status("Test League")
    assert status["fitted"] is True
    assert status["weight"] == pytest.approx(0.8)
    assert status["weight_source"] == "per_competition"
    assert status["temperature"] == pytest.approx(1.0)

    other = blend_status("Other League")
    assert other["weight"] == pytest.approx(0.2)
    assert other["weight_source"] == "default"


def test_blend_status_without_a_report_never_invents_a_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(blending, "REPORT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(blending, "_REPORT_CACHE", {"mtime": None, "report": None})
    status = blend_status("Test League")
    assert status["fitted"] is False
    assert status["weight"] is None
    assert status["reason"] == "no_fitted_blend_report"


def test_analysis_blends_only_1x2_and_uses_the_blended_probability_for_ev():
    discovery = discover_from_market_odds(FixtureStub(), bookmaker="TestBook")
    context = dict(RATINGS, blend_weight=0.0, blend_temperature=1.0)
    analysis = analyse_discovery(discovery, context)

    assert analysis["blend"]["applied"] is True
    assert analysis["blend"]["weight_source"] == "context_override"

    home = next(row for row in analysis["selections"] if row["model_key"] == "home_win")
    assert home["blended"] is True
    assert home["probability"] == pytest.approx(home["devigged_probability"], abs=1e-4)
    assert home["probability"] != pytest.approx(home["probability_model"], abs=1e-4)
    assert home["ev"] == pytest.approx(home["probability"] * home["price"] - 1.0, abs=1e-4)

    totals = next(row for row in analysis["selections"] if row["model_key"] == "over_2_5")
    assert totals["blended"] is False
    assert totals["probability"] == totals["probability_model"]


def test_analysis_with_full_model_weight_keeps_the_model_probability():
    discovery = discover_from_market_odds(FixtureStub(), bookmaker="TestBook")
    context = dict(RATINGS, blend_weight=1.0)
    analysis = analyse_discovery(discovery, context)

    home = next(row for row in analysis["selections"] if row["model_key"] == "home_win")
    assert home["probability"] == pytest.approx(home["probability_model"], abs=1e-3)
    assert home["probability"] > 0.85


def test_analyze_fixture_reports_and_applies_the_blend(tmp_path, monkeypatch):
    path = tmp_path / "blending.json"
    _write_report(path, weight=1.0, per_competition={"Test League": 1.0})
    monkeypatch.setattr(blending, "REPORT_PATH", path)
    monkeypatch.setattr(blending, "_REPORT_CACHE", {"mtime": None, "report": None})

    analysis = analyze_fixture(FixtureStub(), home_lambda=3.0, away_lambda=0.2)
    blend = analysis["probability_blend"]

    assert blend["applied"] is True
    assert blend["weight"] == pytest.approx(1.0)
    assert blend["scope"] == "1x2"
    home_ev = next(row for row in analysis["value_bets"] if row["market_key"] == "home_win")
    assert home_ev["blended"] is True
    assert home_ev["model_probability"] == pytest.approx(blend["model"]["home_win"], abs=1e-4)
    # Double chance and DNB are re-derived from the blended 1X2 triple.
    assert analysis["all_markets"]["double_chance_1x"] == pytest.approx(
        blend["blended"]["home_win"] + blend["blended"]["draw"], abs=1e-4
    )
