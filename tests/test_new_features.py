from __future__ import annotations
import pytest
from app.analytics.probabilities import poisson_market_probabilities
from app.analytics.value_score import compute_value_score
from app.core.currency import format_stake, format_stake_dual, get_currency_options
from app.betting.pdf_slip import generate_text_slip, generate_combo_pdf
from app.analytics.ml_ensemble import is_model_trained, ensemble_predict

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


def test_ai_squad_and_injuries_checker():
    """Verify pre-combo AI squad performance and injury checking."""
    from app.ai.ai_squad_checker import check_squad_and_injuries

    res = check_squad_and_injuries(
        home_team="Arsenal",
        away_team="Chelsea",
        competition="Premier League",
        selection="Arsenal Win or Draw (1X)",
    )
    assert "verdict" in res
    assert res["verdict"] in ["APPROVED", "CAUTION", "VETO"]
    assert "badge" in res
    assert "injuries" in res
    assert "form_summary" in res


def test_accumulator_win_prob_85_to_100_percent():
    """STRICT: Both AVG TEAM CONFIDENCE and ACCUMULATOR WIN PROB must be >= 85%.
    Any combo below 85% on EITHER metric must be REJECTED. Odds must also be >= 2.00."""
    from app.database import get_db
    from app.betting.combo import generate_high_confidence_combos

    db = next(get_db())
    try:
        for n_legs in [2, 3]:
            res = generate_high_confidence_combos(
                db=db,
                target_date="all",
                num_legs=n_legs,
                min_leg_prob=0.85,
                min_accumulator_prob=0.85,
                min_total_odds=2.00,
            )
            assert "best_combo" in res, f"{n_legs}-leg result missing 'best_combo' key"
            if res["best_combo"]:
                bc = res["best_combo"]
                # Rule 1: TOTAL ODDS >= 2.00 — always, no exceptions
                assert bc["total_odds"] >= 2.00, (
                    f"❌ {n_legs}-leg combo odds {bc['total_odds']:.2f} < 2.00"
                )
                # Rule 2: AVG TEAM CONFIDENCE >= 85% — no exceptions
                assert bc["avg_leg_probability"] >= 0.85, (
                    f"❌ AVG TEAM CONFIDENCE {bc['avg_leg_probability']*100:.1f}% < 85%"
                )
                # Rule 3: ACCUMULATOR WIN PROB >= 85% — no exceptions
                assert bc["combined_probability"] >= 0.85, (
                    f"❌ ACCUMULATOR WIN PROB {bc['combined_probability']*100:.1f}% < 85% — REJECTED"
                )
                assert len(bc["legs"]) == n_legs
                # Default policy: only ingested bookmaker prices are eligible
                assert all(leg.get("odds_source") == "market" for leg in bc["legs"])
                # Every alternative must also pass all 3 rules
                for alt in res.get("alternatives", []):
                    assert alt["total_odds"] >= 2.00
                    assert alt["avg_leg_probability"] >= 0.85
                    assert alt["combined_probability"] >= 0.85
    finally:
        db.close()


def test_evaluate_leg_outcomes():
    """Verify that evaluate_leg_outcome correctly grades every market structure."""
    from app.betting.settlement import evaluate_leg_outcome

    # 1X2
    assert evaluate_leg_outcome("home_win", 2, 0) == "WON"
    assert evaluate_leg_outcome("home_win", 1, 1) == "LOST"
    assert evaluate_leg_outcome("away_win", 0, 1) == "WON"
    assert evaluate_leg_outcome("draw", 2, 2) == "WON"

    # Double Chance
    assert evaluate_leg_outcome("double_chance_1x", 1, 1) == "WON"
    assert evaluate_leg_outcome("double_chance_1x", 2, 0) == "WON"
    assert evaluate_leg_outcome("double_chance_1x", 0, 1) == "LOST"
    assert evaluate_leg_outcome("double_chance_x2", 0, 0) == "WON"
    assert evaluate_leg_outcome("double_chance_x2", 0, 3) == "WON"
    assert evaluate_leg_outcome("double_chance_12", 2, 1) == "WON"
    assert evaluate_leg_outcome("double_chance_12", 1, 1) == "LOST"

    # Team Goals Over 0.5
    assert evaluate_leg_outcome("home_over_0_5", 1, 0) == "WON"
    assert evaluate_leg_outcome("home_over_0_5", 0, 2) == "LOST"
    assert evaluate_leg_outcome("away_over_0_5", 3, 1) == "WON"

    # Match Goals Over 1.5 & Under 3.5
    assert evaluate_leg_outcome("over_1_5", 2, 0) == "WON"
    assert evaluate_leg_outcome("over_1_5", 1, 0) == "LOST"
    assert evaluate_leg_outcome("under_3_5", 2, 1) == "WON"
    assert evaluate_leg_outcome("under_3_5", 3, 2) == "LOST"

    # DNB
    assert evaluate_leg_outcome("dnb_home", 2, 1) == "WON"
    assert evaluate_leg_outcome("dnb_home", 1, 1) == "VOID"
    assert evaluate_leg_outcome("dnb_home", 0, 1) == "LOST"

    # Asian Handicap
    assert evaluate_leg_outcome("ah_away_plus_1_5", 2, 1) == "WON"  # 1 + 1.5 = 2.5 > 2
    assert evaluate_leg_outcome("ah_away_plus_1_5", 3, 0) == "LOST" # 0 + 1.5 = 1.5 < 3


def test_automated_bet_settlement_and_ledger():
    """Verify end-to-end recording of tracked bets, automated settlement and ledger calculation."""
    from datetime import datetime, timezone
    from app.database import get_db
    from app.models.fixture import Fixture
    from app.models.bet import TrackedBet
    from app.betting.settlement import (
        record_tracked_bet,
        settle_pending_bets,
        get_performance_ledger_summary,
        format_settlement_message,
    )
    import uuid

    db = next(get_db())
    try:
        # Create 2 test fixtures in DB
        u = uuid.uuid4().hex[:6]
        f1 = Fixture(
            dedupe_key=f"settle_test_1_{u}",
            kickoff_utc=datetime.now(timezone.utc),
            home_team=f"HomeTeam_{u}",
            away_team=f"AwayTeam_{u}",
            competition="Premier League",
            source="Test",
            home_odds=1.45,
            draw_odds=3.50,
            away_odds=5.00,
            status="scheduled",
        )
        f2 = Fixture(
            dedupe_key=f"settle_test_2_{u}",
            kickoff_utc=datetime.now(timezone.utc),
            home_team=f"Alpha_{u}",
            away_team=f"Beta_{u}",
            competition="Premier League",
            source="Test",
            home_odds=1.50,
            draw_odds=3.60,
            away_odds=4.80,
            status="scheduled",
        )
        db.add_all([f1, f2])
        db.commit()

        # Build mock winning combo
        combo = {
            "legs_count": 2,
            "total_odds": 2.15,
            "avg_leg_probability": 0.88,
            "combined_probability": 0.86,
            "expected_value_percent": 18.5,
            "kelly_stake_amount": 100000.0,
            "kelly_stake_percent": 5.0,
            "legs": [
                {
                    "fixture_id": f1.id,
                    "match": f"{f1.home_team} vs {f1.away_team}",
                    "selection": f"{f1.home_team} Win or Draw (1X)",
                    "market_key": "double_chance_1x",
                    "odds": 1.45,
                    "probability": 0.90,
                    "kickoff_eat": "Today, 21:00 EAT",
                    "competition": "Premier League",
                },
                {
                    "fixture_id": f2.id,
                    "match": f"{f2.home_team} vs {f2.away_team}",
                    "selection": f"{f2.home_team} to Score (Over 0.5 Goals)",
                    "market_key": "home_over_0_5",
                    "odds": 1.48,
                    "probability": 0.91,
                    "kickoff_eat": "Today, 21:00 EAT",
                    "competition": "Premier League",
                },
            ]
        }

        # 1. Record bet into ledger
        tracked = record_tracked_bet(combo, "Sat 14 Nov 2026", db, currency="UGX")
        assert tracked.id is not None
        assert tracked.status == "PENDING"
        assert tracked.total_odds == 2.15

        # 2. Simulate matches finishing with winning scores
        f1.home_score = 2
        f1.away_score = 1
        f1.status = "finished"

        f2.home_score = 1
        f2.away_score = 0
        f2.status = "finished"
        db.commit()

        # 3. Settle pending bets
        settled = settle_pending_bets(db)
        assert len(settled) >= 1
        found = next((b for b in settled if b.id == tracked.id), None)
        assert found is not None
        assert found.status == "WON"
        assert found.profit_loss > 0  # Net profit generated

        # 4. Check ledger summary
        stats = get_performance_ledger_summary(db)
        assert stats["won_count"] >= 1
        assert stats["win_rate"] > 0

        # 5. Check settlement WhatsApp message formatting
        msg = format_settlement_message(found, stats, currency="UGX")
        assert "FIE SLIP SETTLEMENT RESULT" in msg
        assert "WON" in msg
        assert "Win Rate:" in msg
    finally:
        db.close()


