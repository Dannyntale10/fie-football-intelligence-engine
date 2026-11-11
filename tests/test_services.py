from datetime import datetime, timezone
import pytest
from app.services.quality import grade_data_quality, baseline_signals, evaluate_fixture_signals
from app.services.probabilities import (
    poisson_market_probabilities,
    _poisson_pmf,
    _dixon_coles_tau,
    calculate_bookmaker_margin,
    calculate_expected_value,
    calculate_kelly_stake,
    estimate_lambdas_from_odds,
    analyze_fixture,
)
from app.services.fixtures import make_dedupe_key, clean_team_name, _float_or_none
from app.services.ratings import upsert_team_rating, estimate_lambdas_from_ratings
from app.services.seeder import seed_sample_historical_data
from app.services.backtest import run_backtest
from app.database import SessionLocal

def test_quality_missing_data_does_not_qualify():
    result = grade_data_quality({})
    assert result["score"] == 0
    assert result["grade"] == "D"
    assert result["eligible_for_analysis"] is False

def test_quality_grade_a():
    result = grade_data_quality({
        "historical_records": 1, "recent_statistics": 1, "home_away_splits": 1,
        "team_news": 1, "advanced_stats": 1, "source_freshness": 1, "market_information": 1
    })
    assert result["score"] == 100
    assert result["grade"] == "A"
    assert result["eligible_for_analysis"] is True

def test_quality_grade_b():
    result = grade_data_quality({
        "historical_records": 1, "recent_statistics": 1, "home_away_splits": 1,
        "team_news": 0.8
    })
    # 25 + 20 + 15 + 12 = 72 -> B
    assert result["score"] == 72
    assert result["grade"] == "B"
    assert result["eligible_for_analysis"] is True

def test_probability_outputs_are_bounded():
    result = poisson_market_probabilities(1.5, 1.0)
    for key, value in result["markets"].items():
        assert 0 <= value <= 1

def test_probability_qualifying_markets():
    # With a high goal expectation, Over 0.5 should easily qualify >= 0.85
    result = poisson_market_probabilities(2.5, 1.8, qualification_threshold=0.85)
    assert result["qualifying_count"] > 0
    markets_qual = [item["market"] for item in result["qualifying_markets"]]
    assert "over_0_5" in markets_qual

def test_poisson_negative_lambda_raises():
    with pytest.raises(ValueError):
        poisson_market_probabilities(-1.0, 1.0)

def test_fixture_dedupe_is_stable():
    dt = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    assert make_dedupe_key(dt, "Team A", "Team B", "League") == make_dedupe_key(dt, "Team A", "Team B", "League")

def test_alias_and_competition_dedupe():
    dt = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
    # Man Utd vs Spurs in English Premier League should match Manchester United vs Tottenham Hotspur in Premier League
    key1 = make_dedupe_key(dt, "Man Utd", "Spurs", "English Premier League")
    key2 = make_dedupe_key(dt, "Manchester United", "Tottenham Hotspur", "Premier League")
    assert key1 == key2

def test_team_name_accent_normalization():
    # Atlético Madrid vs Atletico Madrid should yield identical clean names
    assert clean_team_name("Atlético Madrid") == clean_team_name("Atletico Madrid")
    assert clean_team_name("Bayern München") == clean_team_name("Bayern Munchen")
    assert clean_team_name("São Paulo F.C.") == clean_team_name("Sao Paulo FC")

def test_float_or_none_robustness():
    assert _float_or_none(None) is None
    assert _float_or_none("") is None
    assert _float_or_none("NaN") is None
    assert _float_or_none(" 1.95 ") == 1.95
    assert _float_or_none("1,95") == 1.95
    assert _float_or_none(2.5) == 2.5

def test_calculate_bookmaker_margin():
    margin_res = calculate_bookmaker_margin(2.00, 3.40, 3.80)
    assert margin_res["overround"] > 1.0
    assert margin_res["margin_percent"] > 0
    fair_sum = sum(margin_res["fair_implied"].values())
    assert abs(fair_sum - 1.0) < 0.001
    assert margin_res["fair_odds"]["home"] > 1.0

def test_calculate_expected_value():
    # If model probability is 60% and odds are 2.00 -> EV = 0.60 * 2.0 - 1 = +20%
    ev_pos = calculate_expected_value(0.60, 2.00)
    assert ev_pos["expected_value_percent"] == 20.0
    assert ev_pos["is_value"] is True
    assert ev_pos["edge"] == 0.10

    # If model probability is 40% and odds are 2.00 -> EV = 0.40 * 2.0 - 1 = -20%
    ev_neg = calculate_expected_value(0.40, 2.00)
    assert ev_neg["expected_value_percent"] == -20.0
    assert ev_neg["is_value"] is False

def test_estimate_lambdas_from_odds():
    # Favorite home team should get higher lambda
    h_lam, a_lam = estimate_lambdas_from_odds(1.50, 4.00, 6.00)
    assert h_lam > a_lam
    assert h_lam + a_lam > 2.0

    # Fallback when odds missing
    def_h, def_a = estimate_lambdas_from_odds(None, None, None)
    assert def_h == 1.45
    assert def_a == 1.15

def test_analyze_fixture_mock():
    class MockFixture:
        id = 1
        home_team = "Arsenal"
        away_team = "Chelsea"
        competition = "Premier League"
        country = "England"
        source = "BookmakerA"
        kickoff_utc = datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc)
        home_odds = 2.10
        draw_odds = 3.30
        away_odds = 3.50

    result = analyze_fixture(MockFixture(), qualification_threshold=0.85)
    assert result["fixture_id"] == 1
    assert "estimated_lambdas" in result
    assert result["bookmaker_margin"] is not None
    assert len(result["value_bets"]) == 3
    assert "data_quality" in result

def test_dixon_coles_adjustment():
    # Tau for 0-0 with negative rho should be > 1.0 (draw boost)
    tau_00 = _dixon_coles_tau(0, 0, 1.5, 1.2, rho=-0.10)
    assert tau_00 > 1.0

    # Tau for 1-1 with negative rho should be > 1.0
    tau_11 = _dixon_coles_tau(1, 1, 1.5, 1.2, rho=-0.10)
    assert tau_11 > 1.0

    # Draw probability under negative rho should be higher than independent Poisson (rho=0.0)
    p_independent = poisson_market_probabilities(1.5, 1.2, rho=0.0)
    p_dc = poisson_market_probabilities(1.5, 1.2, rho=-0.12)
    assert p_dc["markets"]["draw"] > p_independent["markets"]["draw"]

def test_team_ratings_and_lambda_estimation():
    db = SessionLocal()
    try:
        # Create ratings for Arsenal and Chelsea
        upsert_team_rating(db, "Arsenal", "Premier League", attack_rating=1.30, defense_rating=0.80, matches_played=15)
        upsert_team_rating(db, "Chelsea", "Premier League", attack_rating=1.10, defense_rating=0.90, matches_played=15)

        h_lam, a_lam, found = estimate_lambdas_from_ratings(db, "Arsenal", "Chelsea", "Premier League")
        assert found is True
        assert h_lam is not None and a_lam is not None
        # Arsenal attack 1.30 vs Chelsea defense 0.90 * 1.50 = 1.755 -> 1.76
        assert h_lam > a_lam
    finally:
        db.close()

def test_calculate_kelly_stake():
    # Model prob 60%, odds 2.0 -> b=1, p=0.6, q=0.4 -> Full Kelly = (1*0.6 - 0.4)/1 = 0.20 (20%)
    # Quarter Kelly = 5% (capped at 5% max)
    k_res = calculate_kelly_stake(0.60, 2.00, bankroll=1000.0, fraction=0.25, max_stake_percent=5.0)
    assert k_res["full_kelly"] == 0.20
    assert k_res["stake_percent"] == 5.0
    assert k_res["stake_amount"] == 50.0

    # Negative EV: model prob 40%, odds 2.0 -> Full Kelly <= 0 -> stake is 0
    k_neg = calculate_kelly_stake(0.40, 2.00, bankroll=1000.0)
    assert k_neg["stake_percent"] == 0.0
    assert k_neg["stake_amount"] == 0.0

def test_seeder_and_backtest():
    db = SessionLocal()
    try:
        seed_res = seed_sample_historical_data(db)
        assert seed_res["total_processed"] >= 20
        assert seed_res["teams_rated"] > 0

        # Run backtest on the seeded matches
        bt_res = run_backtest(db, strategy="value_bet", min_ev_percent=1.0, min_quality_score=0)
        assert "total_bets" in bt_res
        assert "win_rate_percent" in bt_res
        assert "net_profit" in bt_res
        assert len(bt_res["history"]) == bt_res["total_bets"]
    finally:
        db.close()
