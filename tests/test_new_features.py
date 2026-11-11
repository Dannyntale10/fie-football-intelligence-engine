from __future__ import annotations
import pytest
from app.services.probabilities import poisson_market_probabilities
from app.services.value_score import compute_value_score
from app.services.currency import format_stake, format_stake_dual, get_currency_options
from app.services.pdf_slip import generate_text_slip, generate_combo_pdf
from app.services.ml_ensemble import is_model_trained, ensemble_predict

def test_correct_score_and_asian_handicap():
    res = poisson_market_probabilities(home_lambda=1.8, away_lambda=0.9)
    # Correct score
    assert "correct_score_top5" in res
    assert len(res["correct_score_top5"]) > 0
    top_score = res["correct_score_top5"][0]
    assert "score" in top_score
    assert "probability" in top_score
    assert "odds_equiv" in top_score

    # Asian Handicap
    mkts = res["markets"]
    assert "ah_home_minus_0_5" in mkts
    assert "ah_home_minus_1_5" in mkts
    assert "ah_away_plus_0_5" in mkts
    assert "ah_away_plus_1_5" in mkts
    assert mkts["ah_home_minus_0_5"] > 0
    assert mkts["ah_away_plus_0_5"] > 0

def test_currency_formatting():
    assert "UGX" in get_currency_options()
    assert "USD" in get_currency_options()
    ugx_str = format_stake(10.0, "UGX")
    assert "UGX" in ugx_str
    assert "37,000" in ugx_str

    dual_str = format_stake_dual(10.0, "UGX")
    assert "$10.00" in dual_str
    assert "UGX 37,000" in dual_str

def test_value_score_computation():
    class MockFixture:
        id = 1
        source = "test"
        kickoff_utc = None
        home_odds = 1.35
        draw_odds = 4.50
        away_odds = 8.50

    mock_analysis = {
        "value_bets": [{"is_value": True, "expected_value_percent": 12.5}],
        "qualifying_markets": [{"market": "home_win", "probability": 0.88}],
        "all_markets": {"home_win": 0.88, "draw": 0.08, "away_win": 0.04},
    }
    vs = compute_value_score(MockFixture(), mock_analysis)
    assert "score" in vs
    assert 0 <= vs["score"] <= 100
    assert "grade" in vs
    assert "label" in vs

def test_text_and_pdf_slip_generation():
    mock_combo = {
        "legs_count": 2,
        "total_odds": 2.15,
        "combined_probability": 0.62,
        "expected_value_percent": 8.5,
        "kelly_stake_amount": 15.0,
        "avg_leg_probability": 0.89,
        "legs": [
            {
                "match": "Arsenal vs Chelsea",
                "selection": "Arsenal Win or Draw (1X)",
                "odds": 1.25,
                "probability": 0.90,
                "kickoff_eat": "Sat 17:00 EAT",
            },
            {
                "match": "Real Madrid vs Girona",
                "selection": "Real Madrid to Win",
                "odds": 1.72,
                "probability": 0.88,
                "kickoff_eat": "Sat 21:00 EAT",
            }
        ]
    }
    txt = generate_text_slip(mock_combo, "Saturday", currency="UGX")
    assert "FIE DAILY BANKER" in txt
    assert "Arsenal vs Chelsea" in txt
    assert "UGX" in txt

    pdf_bytes = generate_combo_pdf(mock_combo, "Saturday", currency="UGX")
    assert pdf_bytes is not None
    assert len(pdf_bytes) > 100
    assert pdf_bytes.startswith(b"%PDF")
