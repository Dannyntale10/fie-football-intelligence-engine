"""Market engine: normalization, discovery, analysis, gate and value."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.market_engine.catalogue import ENGINE_VERSION, catalogue_payload, get_definition
from app.market_engine.discovery import (
    discover_from_market_odds,
    discover_from_raw_event,
)
from app.market_engine.market_analysis import analyse_discovery
from app.market_engine.market_registry import evaluate_model, model_probabilities
from app.market_engine.normalizer import (
    build_model_key,
    normalize_model_key,
    normalize_raw_selection,
)
from app.market_engine.qualification import (
    NO_QUALIFIED_SELECTIONS,
    QUALIFICATION_THRESHOLD,
    qualify,
)
from app.market_engine.value_detection import detect_value


class FixtureStub:
    def __init__(self, markets, odds_updated_at=None, source="BetPawa"):
        self.id = 7
        self.source = source
        self.market_odds_json = json.dumps(markets) if isinstance(markets, dict) else markets
        self.odds_updated_at = odds_updated_at or datetime.now(timezone.utc)
        self.home_odds = markets.get("home_win") if isinstance(markets, dict) else None
        self.draw_odds = markets.get("draw") if isinstance(markets, dict) else None
        self.away_odds = markets.get("away_win") if isinstance(markets, dict) else None
        self.home_team = "Home"
        self.away_team = "Away"
        self.competition = "Test League"


RATINGS_CONTEXT = {"home_lambda": 3.0, "away_lambda": 0.2, "lambda_source": "team_ratings"}

STRONG_12_ODDS = {
    "home_win": 1.10,
    "draw": 9.0,
    "away_win": 30.0,
    "over_2_5": 1.55,
    "under_2_5": 2.45,
    "btts_yes": 2.60,
    "btts_no": 1.48,
}


def test_catalogue_covers_every_required_category():
    payload = catalogue_payload()
    categories = {row["category"] for row in payload}
    assert {"result", "goals", "halves", "corners", "cards", "players", "statistics", "combinations"} <= categories
    assert get_definition("total_corners").model == "corners"
    assert get_definition("total_shots").model is None


@pytest.mark.parametrize(
    "key,family,selection,line",
    [
        ("home_win", "match_result", "1", None),
        ("over_2_5", "total_goals", "OVER", 2.5),
        ("under_1_5", "total_goals", "UNDER", 1.5),
        ("ah_home_minus_1_5", "asian_handicap", "HOME_MINUS", 1.5),
        ("dnb_away", "draw_no_bet", "2", None),
        ("corners_home_over_5_5", "team_corners_home", "OVER", 5.5),
        ("cards_over_3_5", "total_cards", "OVER", 3.5),
    ],
)
def test_normalize_stored_model_keys(key, family, selection, line):
    normalized = normalize_model_key(key)
    assert normalized.family == family
    assert normalized.selection == selection
    assert normalized.line == line
    assert normalized.model_key == key


def test_normalize_raw_bookmaker_labels():
    match = normalize_raw_selection("1X2", "1")
    assert (match.family, match.selection, match.model_key) == ("match_result", "1", "home_win")

    totals = normalize_raw_selection("Total Goals Over/Under", "Over", "2.5")
    assert (totals.family, totals.selection, totals.line) == ("total_goals", "OVER", 2.5)
    assert totals.model_key == "over_2_5"

    corners = normalize_raw_selection("Total Corners", "Under", "9.5")
    assert corners.family == "total_corners"
    assert corners.model_key == "corners_under_9_5"

    handicap = normalize_raw_selection("Asian Handicap", "1", "-1.5")
    assert handicap.model_key == "ah_home_minus_1_5"
    invalid_handicap = normalize_raw_selection("Asian Handicap", "1", "+1.5")
    assert invalid_handicap.model_key is None

    unknown = normalize_raw_selection("Solar Flare Intensity", "High")
    assert unknown.family is None


def test_discovery_keeps_attribution_and_reports_missing_data():
    captured = datetime(2026, 11, 12, 6, 30, tzinfo=timezone.utc)
    result = discover_from_market_odds(FixtureStub(STRONG_12_ODDS, captured), bookmaker="BetPawa")
    payload = result.as_dict()
    assert payload["data_status"] == "ok"
    assert payload["discovered_count"] == len(STRONG_12_ODDS)
    assert "match_result" in payload["families"]
    home = next(m for m in payload["markets"] if m["model_key"] == "home_win")
    assert home["bookmaker"] == "BetPawa"
    assert home["source"] == "BetPawa"
    assert home["captured_at"].startswith("2026-11-12T06:30")

    empty = discover_from_market_odds(FixtureStub(None))
    assert empty.data_status == "no_market_data"
    assert empty.as_dict()["discovered_count"] == 0


def test_discovery_from_raw_event_skips_suspended_prices():
    event = {
        "markets": [
            {
                "marketType": {"id": 3743, "name": "1X2"},
                "row": [
                    {
                        "prices": [
                            {"name": "1", "odds": 1.55, "handicap": None},
                            {"name": "X", "odds": 3.8, "handicap": None},
                            {"name": "2", "odds": 5.2, "handicap": None, "suspended": True},
                        ]
                    }
                ],
            },
            {
                "marketType": {"id": 9999, "name": "Space Weather"},
                "row": [{"prices": [{"name": "Sunny", "odds": 2.0}]}],
            },
        ]
    }
    result = discover_from_raw_event(event, bookmaker="BetPawa", source="BetPawa Uganda")
    assert {m.selection for m in result.markets} == {"1", "X"}
    assert result.markets[0].captured_at is not None
    assert result.unmodelled[0]["reason"] == "unknown_family"


def test_dixon_coles_model_probabilities_are_coherent():
    probabilities = evaluate_model("dixon_coles", RATINGS_CONTEXT)
    assert probabilities["home_win"] + probabilities["draw"] + probabilities["away_win"] == pytest.approx(1.0, abs=0.01)
    assert 0.0 <= probabilities["clean_sheet_home"] <= 1.0
    assert probabilities["home_win"] > 0.85
    assert probabilities["result_btts_1_no"] >= probabilities["result_btts_1_yes"]

    no_inputs = model_probabilities({"lambda_source": "missing"})
    assert no_inputs["model_status"]["dixon_coles"] == "insufficient_inputs"
    assert no_inputs["probabilities"] == {}


def test_analysis_qualifies_at_eighty_five_percent_only():
    captured = datetime.now(timezone.utc)
    discovery = discover_from_market_odds(FixtureStub(STRONG_12_ODDS, captured), bookmaker="BetPawa")
    analysis = analyse_discovery(discovery, RATINGS_CONTEXT, now=captured)

    assert analysis["engine_version"] == ENGINE_VERSION
    assert analysis["threshold"] == 0.85
    home = next(row for row in analysis["selections"] if row["model_key"] == "home_win")
    assert home["probability"] >= QUALIFICATION_THRESHOLD
    assert home["qualified"] is True
    assert home["ev"] == pytest.approx(home["probability"] * 1.10 - 1.0, abs=1e-4)
    assert home["devigged_probability"] is not None
    assert home["group_margin"] is not None
    assert home["source"] == "BetPawa"

    below = qualify(0.8499, 0.99)
    assert below == {"qualified": False, "reason": "probability_below_0.85"}
    assert qualify(0.85, 0.99)["qualified"] is True
    assert qualify(0.95, 0.59)["qualified"] is False


def test_analysis_never_claims_unmodelled_markets():
    event = {
        "markets": [
            {
                "marketType": {"name": "Total Shots"},
                "row": [{"prices": [{"name": "Over", "odds": 1.9, "handicap": "20.5"},
                                     {"name": "Under", "odds": 1.9, "handicap": "20.5"}]}],
            }
        ]
    }
    discovery = discover_from_raw_event(event, bookmaker="BetPawa")
    analysis = analyse_discovery(
        discovery,
        {"home_lambda": 1.4, "away_lambda": 1.2, "lambda_source": "market_totals"},
    )
    shots = next(row for row in analysis["selections"] if row["family"] == "total_shots")
    assert shots["model_status"] == "no_model"
    assert shots["qualified"] is False
    assert shots["probability"] is None
    assert analysis["message"] == NO_QUALIFIED_SELECTIONS


def test_corners_require_corner_inputs_and_are_marked_as_such():
    event = {
        "markets": [
            {
                "marketType": {"name": "Total Corners"},
                "row": [{"prices": [{"name": "Over", "odds": 1.85, "handicap": "9.5"},
                                     {"name": "Under", "odds": 1.95, "handicap": "9.5"}]}],
            }
        ]
    }
    discovery = discover_from_raw_event(event, bookmaker="BetPawa")
    without = analyse_discovery(discovery, RATINGS_CONTEXT)
    row = without["selections"][0]
    assert row["family"] == "total_corners"
    assert row["model_status"] == "insufficient_model_data"
    assert row["qualified"] is False

    with_corners = dict(RATINGS_CONTEXT, expected_corners=10.0)
    analysed = analyse_discovery(discovery, with_corners)
    over = next(row for row in analysed["selections"] if row["selection"] == "OVER")
    assert over["model_status"] == "ok"
    assert 0.4 < over["probability"] < 0.6


def test_value_detection_uses_best_price_and_labels_missing_odds():
    rows = [
        {"family": "match_result", "selection": "1", "line": None, "price": 2.10, "probability": 0.5,
         "devigged_probability": 0.47, "implied_probability": 0.4762, "bookmaker": "BetPawa", "source": "x"},
        {"family": "match_result", "selection": "1", "line": None, "price": 2.30, "probability": 0.5,
         "devigged_probability": 0.47, "implied_probability": 0.4348, "bookmaker": "SportsPesa", "source": "y"},
        {"family": "match_result", "selection": "X", "line": None, "price": None, "probability": 0.4},
    ]
    for row in rows:
        if row["price"]:
            row["ev"] = round(row["probability"] * row["price"] - 1.0, 4)
    result = detect_value(rows)
    assert result["ev_available"] is True
    assert result["value_count"] == 1
    assert result["value_selections"][0]["bookmaker"] == "SportsPesa"
    assert result["best_prices"][0]["price"] == 2.30

    unpriced = detect_value([{**rows[2], "ev": None}])
    assert unpriced["ev_available"] is False
    assert unpriced["unavailable_reason"] == "odds_unavailable"


def test_build_model_key_rejects_unsupported_selections():
    assert build_model_key("team_total_away", "UNDER", 1.5) is None
    assert build_model_key("match_result", "HOME", None) is None
    assert build_model_key("total_goals", "OVER", None) is None
    assert build_model_key("btts", "NO", None) == "btts_no"
