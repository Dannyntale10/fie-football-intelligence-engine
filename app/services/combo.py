from __future__ import annotations
"""
combo.py – High-Confidence Accumulator / Combo Slip Engine.

Generates 2-leg and 3-leg multi-bets ("combos") combining high-confidence selections
(≥80-85% win/draw probability) from upcoming fixtures to achieve total target odds
of 2.00+ (or custom), calculating combined probability, mathematical edge (+EV),
and Kelly staking.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import itertools
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.probabilities import analyze_fixture, calculate_kelly_stake

EAT_TZ = timezone(timedelta(hours=3), name="EAT")


def _format_eat(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")


def generate_high_confidence_combos(
    db: Session,
    num_legs: int = 3,
    min_leg_prob: float = 0.80,
    min_total_odds: float = 2.00,
    max_total_odds: float = 5.00,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    days_ahead: int = 14,
    competition: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate optimal 2-leg or 3-leg high-confidence multi-bet slips with combined odds >= 2.00+.

    Args:
        db: SQLAlchemy session.
        num_legs: 2 or 3 teams in the combo.
        min_leg_prob: Minimum individual leg confidence (e.g. 0.80 = 80%, 0.85 = 85%).
        min_total_odds: Target combined accumulator odds (default 2.00+).
        max_total_odds: Cap for total accumulator odds (default 5.00).
        bankroll: Total bankroll for Kelly stake sizing.
        kelly_fraction: Conservative Kelly fraction (0.25 = quarter Kelly).
        days_ahead: Maximum days lookahead.
        competition: Optional league filter substring.

    Returns:
        dict with best_combo, alternative_combos, total_candidate_legs, and summary.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .where(Fixture.kickoff_utc <= cutoff)
        .where(Fixture.home_odds.is_not(None))
        .where(Fixture.away_odds.is_not(None))
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    fixtures = db.scalars(query).all()

    candidates: List[Dict[str, Any]] = []

    for f in fixtures:
        try:
            analysis = analyze_fixture(f, db=db)
        except Exception:
            continue

        probs = analysis.get("all_markets", {})
        h_odds = f.home_odds
        d_odds = f.draw_odds or 3.20
        a_odds = f.away_odds

        # 1. Direct Home Win
        p_hw = probs.get("home_win", 0.0)
        if p_hw >= min_leg_prob and h_odds and h_odds >= 1.18:
            candidates.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "team": f.home_team,
                "selection": f"{f.home_team} to Win",
                "market_key": "home_win",
                "market_type": "Direct Win (1)",
                "odds": round(h_odds, 2),
                "probability": p_hw,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        # 2. Direct Away Win
        p_aw = probs.get("away_win", 0.0)
        if p_aw >= min_leg_prob and a_odds and a_odds >= 1.18:
            candidates.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "team": f.away_team,
                "selection": f"{f.away_team} to Win",
                "market_key": "away_win",
                "market_type": "Direct Win (2)",
                "odds": round(a_odds, 2),
                "probability": p_aw,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        # 3. Double Chance 1X (Home Win or Draw) - Ultra High Safety
        p_1x = probs.get("double_chance_1x", 0.0)
        if h_odds and d_odds:
            dc_1x_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / d_odds)), 2)
            if p_1x >= max(0.85, min_leg_prob) and dc_1x_odds >= 1.15:
                candidates.append({
                    "fixture_id": f.id,
                    "match": f"{f.home_team} vs {f.away_team}",
                    "competition": f.competition,
                    "team": f.home_team,
                    "selection": f"{f.home_team} Win or Draw (1X)",
                    "market_key": "double_chance_1x",
                    "market_type": "Double Chance (1X)",
                    "odds": dc_1x_odds,
                    "probability": p_1x,
                    "kickoff_utc": f.kickoff_utc.isoformat(),
                    "kickoff_eat": _format_eat(f.kickoff_utc),
                })

        # 4. Double Chance X2 (Away Win or Draw) - Ultra High Safety
        p_x2 = probs.get("double_chance_x2", 0.0)
        if a_odds and d_odds:
            dc_x2_odds = round(1.0 / ((1.0 / a_odds) + (1.0 / d_odds)), 2)
            if p_x2 >= max(0.85, min_leg_prob) and dc_x2_odds >= 1.15:
                candidates.append({
                    "fixture_id": f.id,
                    "match": f"{f.home_team} vs {f.away_team}",
                    "competition": f.competition,
                    "team": f.away_team,
                    "selection": f"{f.away_team} Win or Draw (X2)",
                    "market_key": "double_chance_x2",
                    "market_type": "Double Chance (X2)",
                    "odds": dc_x2_odds,
                    "probability": p_x2,
                    "kickoff_utc": f.kickoff_utc.isoformat(),
                    "kickoff_eat": _format_eat(f.kickoff_utc),
                })

    # Generate combos of size num_legs (2 or 3)
    k = max(2, min(3, num_legs))
    combos: List[Dict[str, Any]] = []

    for combo_legs in itertools.combinations(candidates, k):
        # Ensure no duplicate fixtures in the same combo
        fixture_ids = {leg["fixture_id"] for leg in combo_legs}
        if len(fixture_ids) < k:
            continue

        tot_odds = 1.0
        tot_prob = 1.0
        for leg in combo_legs:
            tot_odds *= leg["odds"]
            tot_prob *= leg["probability"]

        tot_odds = round(tot_odds, 2)
        tot_prob = round(tot_prob, 4)

        if min_total_odds <= tot_odds <= max_total_odds:
            implied_prob = round(1.0 / tot_odds, 4)
            ev = round(((tot_prob * tot_odds) - 1.0) * 100, 2)
            edge = round((tot_prob - implied_prob) * 100, 2)

            kelly = calculate_kelly_stake(
                model_probability=tot_prob,
                bookmaker_odds=tot_odds,
                bankroll=bankroll,
                fraction=kelly_fraction,
                max_stake_percent=5.0,
            )

            combos.append({
                "legs_count": k,
                "total_odds": tot_odds,
                "combined_probability": tot_prob,
                "implied_probability": implied_prob,
                "expected_value_percent": ev,
                "edge_percent": edge,
                "kelly_stake_amount": kelly.get("stake_amount", 0.0),
                "kelly_stake_percent": kelly.get("stake_percent", 0.0),
                "legs": list(combo_legs),
            })

    # Sort combos by highest combined win probability, then highest EV
    combos.sort(key=lambda x: (x["combined_probability"], x["expected_value_percent"]), reverse=True)

    best_combo = combos[0] if combos else None
    alternatives = combos[1:6] if len(combos) > 1 else []

    return {
        "params": {
            "num_legs": k,
            "min_leg_prob": min_leg_prob,
            "min_total_odds": min_total_odds,
            "bankroll": bankroll,
            "kelly_fraction": kelly_fraction,
        },
        "total_candidate_legs": len(candidates),
        "total_combos_generated": len(combos),
        "best_combo": best_combo,
        "alternatives": alternatives,
    }
