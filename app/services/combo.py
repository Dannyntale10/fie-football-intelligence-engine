from __future__ import annotations
"""
combo.py – Daily High-Confidence Multi-Bet / Combo Engine.

Builds same-day accumulator tickets (2-leg, 3-leg, or 4-leg) strictly from
matches playing on a specific matchday in Ugandan Time (East Africa Time, EAT),
ensuring bets settle on the same day.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import itertools
from collections import defaultdict
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


def get_available_matchdays(db: Session) -> List[Dict[str, Any]]:
    """Return a list of all active upcoming matchdays in Ugandan Time (EAT) with match counts."""
    now = datetime.now(timezone.utc)
    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .order_by(Fixture.kickoff_utc)
    ).all()

    counts: Dict[str, Dict[str, Any]] = {}
    for f in fixtures:
        eat_dt = f.kickoff_utc.astimezone(EAT_TZ)
        day_key = eat_dt.strftime("%Y-%m-%d")
        if day_key not in counts:
            counts[day_key] = {
                "date": day_key,
                "label": eat_dt.strftime("%A, %d %b %Y"),
                "short_label": eat_dt.strftime("%a %d %b"),
                "count": 0,
            }
        counts[day_key]["count"] += 1

    return list(counts.values())


def generate_high_confidence_combos(
    db: Session,
    target_date: Optional[str] = None,
    num_legs: int = 3,
    min_leg_prob: float = 0.75,
    min_total_odds: float = 2.00,
    max_total_odds: float = 6.00,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    competition: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate optimal same-day high-confidence multi-bet slips (Odds >= 2.00+).

    Args:
        db: SQLAlchemy session.
        target_date: Specific YYYY-MM-DD matchday (in EAT), or None for next active matchday.
        num_legs: Target number of legs (2, 3, or 4).
        min_leg_prob: Minimum individual leg win/draw probability.
        min_total_odds: Target combined accumulator odds (e.g. 2.00).
        max_total_odds: Cap for total accumulator odds.
        bankroll: Total bankroll for Kelly stake sizing.
        kelly_fraction: Kelly fraction (0.25 = quarter Kelly).
        competition: Optional competition name filter.

    Returns:
        dict with target_matchday, best_combo, alternatives, candidate_legs, and available_matchdays.
    """
    now = datetime.now(timezone.utc)
    matchdays = get_available_matchdays(db)

    # If no target date provided, default to next active matchday
    if not target_date and matchdays:
        target_date = matchdays[0]["date"]

    target_matchday_info = next((m for m in matchdays if m["date"] == target_date), None)
    if not target_matchday_info and matchdays:
        target_date = matchdays[0]["date"]
        target_matchday_info = matchdays[0]

    # Query fixtures
    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .where(Fixture.home_odds.is_not(None))
        .where(Fixture.away_odds.is_not(None))
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    fixtures = db.scalars(query).all()

    # Filter to target matchday if set
    if target_date and target_date != "all":
        fixtures = [
            f for f in fixtures
            if f.kickoff_utc.astimezone(EAT_TZ).strftime("%Y-%m-%d") == target_date
        ]

    candidates: List[Dict[str, Any]] = []

    # Use threshold to filter candidates, allowing safe candidates down to 0.70 so max-3-team combos can reach >= 2.00 odds
    cand_thresh = min(0.70, min_leg_prob)

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
        if p_hw >= cand_thresh and h_odds and h_odds >= 1.15:
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
        if p_aw >= cand_thresh and a_odds and a_odds >= 1.15:
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
            if p_1x >= cand_thresh and dc_1x_odds >= 1.10:
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
            if p_x2 >= cand_thresh and dc_x2_odds >= 1.10:
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

        # 5. Double Chance 12 (Either Team to Win / No Draw)
        p_12 = probs.get("double_chance_12", 0.0)
        if h_odds and a_odds:
            dc_12_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / a_odds)), 2)
            if p_12 >= cand_thresh and dc_12_odds >= 1.12:
                candidates.append({
                    "fixture_id": f.id,
                    "match": f"{f.home_team} vs {f.away_team}",
                    "competition": f.competition,
                    "team": f"{f.home_team} / {f.away_team}",
                    "selection": f"{f.home_team} or {f.away_team} to Win (12)",
                    "market_key": "double_chance_12",
                    "market_type": "Double Chance (12)",
                    "odds": dc_12_odds,
                    "probability": p_12,
                    "kickoff_utc": f.kickoff_utc.isoformat(),
                    "kickoff_eat": _format_eat(f.kickoff_utc),
                })

        # 6. Team to score at least 1 goal (Team Over 0.5) - Banker safety (88-92%+)
        p_ho05 = probs.get("home_over_0_5", 0.0)
        if p_ho05 >= max(0.85, cand_thresh) and h_odds and h_odds <= 1.45:
            t_odds = round(max(1.10, min(1.28, 1.0 / (p_ho05 * 0.94))), 2)
            candidates.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "team": f.home_team,
                "selection": f"{f.home_team} to Score (Over 0.5 Goals)",
                "market_key": "home_over_0_5",
                "market_type": "Team Goals (Over 0.5)",
                "odds": t_odds,
                "probability": p_ho05,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        p_ao05 = probs.get("away_over_0_5", 0.0)
        if p_ao05 >= max(0.85, cand_thresh) and a_odds and a_odds <= 1.45:
            t_odds = round(max(1.10, min(1.28, 1.0 / (p_ao05 * 0.94))), 2)
            candidates.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "team": f.away_team,
                "selection": f"{f.away_team} to Score (Over 0.5 Goals)",
                "market_key": "away_over_0_5",
                "market_type": "Team Goals (Over 0.5)",
                "odds": t_odds,
                "probability": p_ao05,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

    # Search combinations for requested leg count (strictly 2 or 3 teams, max 3)
    k = max(2, min(3, num_legs))
    combos: List[Dict[str, Any]] = []

    # Strictly 2 or 3 teams only to respect max 3-team multi-bet constraint
    leg_counts_to_try = [k]
    for alt_k in [3, 2]:
        if alt_k not in leg_counts_to_try:
            leg_counts_to_try.append(alt_k)

    for try_k in leg_counts_to_try:
        for combo_legs in itertools.combinations(candidates, try_k):
            # Ensure distinct fixtures
            fixture_ids = {leg["fixture_id"] for leg in combo_legs}
            if len(fixture_ids) < try_k:
                continue

            tot_odds = 1.0
            tot_prob = 1.0
            for leg in combo_legs:
                tot_odds *= leg["odds"]
                tot_prob *= leg["probability"]

            tot_odds = round(tot_odds, 2)
            tot_prob = round(tot_prob, 4)

            # Accept combos that hit min_total_odds
            if tot_odds >= min_total_odds and tot_odds <= max_total_odds:
                implied_prob = round(1.0 / tot_odds, 4)
                ev = round(((tot_prob * tot_odds) - 1.0) * 100, 2)
                edge = round((tot_prob - implied_prob) * 100, 2)
                avg_prob = round(sum(l["probability"] for l in combo_legs) / len(combo_legs), 4)
                min_prob = round(min(l["probability"] for l in combo_legs), 4)

                kelly = calculate_kelly_stake(
                    model_probability=tot_prob,
                    bookmaker_odds=tot_odds,
                    bankroll=bankroll,
                    fraction=kelly_fraction,
                    max_stake_percent=5.0,
                )

                combos.append({
                    "legs_count": try_k,
                    "total_odds": tot_odds,
                    "combined_probability": tot_prob,
                    "avg_leg_probability": avg_prob,
                    "min_leg_probability": min_prob,
                    "implied_probability": implied_prob,
                    "expected_value_percent": ev,
                    "edge_percent": edge,
                    "kelly_stake_amount": kelly.get("stake_amount", 0.0),
                    "kelly_stake_percent": kelly.get("stake_percent", 0.0),
                    "legs": list(combo_legs),
                })

        if len(combos) >= 5 and try_k >= k:
            break

    # Sort combos by highest average team confidence, then highest joint win probability, then EV
    combos.sort(
        key=lambda x: (
            x["avg_leg_probability"],
            x["combined_probability"],
            x["min_leg_probability"],
            x["expected_value_percent"]
        ),
        reverse=True
    )

    best_combo = combos[0] if combos else None
    alternatives = combos[1:7] if len(combos) > 1 else []

    return {
        "target_date": target_date,
        "matchday_label": target_matchday_info["label"] if target_matchday_info else (target_date or "All Matchdays"),
        "matchday_matches_count": target_matchday_info["count"] if target_matchday_info else len(fixtures),
        "available_matchdays": matchdays,
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
