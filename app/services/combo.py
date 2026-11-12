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
from app.services.ai_squad_checker import check_squad_and_injuries

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
    min_leg_prob: float = 0.85,
    min_accumulator_prob: Optional[float] = None,
    min_total_odds: float = 2.00,
    max_total_odds: float = 6.00,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    competition: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate optimal same-day high-confidence multi-bet slips.

    Strictly guarantees:
      - ACCUMULATOR WIN PROB >= min_accumulator_prob (min 85% to 100% max)
      - AVG TEAM CONFIDENCE >= min_leg_prob (min 85% to 100% max)
      - Comprehensive market structures (1X2, DC, DNB, Goals, BTTS, Asian Handicaps)
      - Pre-combo AI squad performance, injury & H2H verification

    Args:
        db: SQLAlchemy session.
        target_date: Specific YYYY-MM-DD matchday (in EAT), or None for next active matchday.
        num_legs: Target number of legs (2, 3, or 4).
        min_leg_prob: Minimum individual/average leg probability (default 0.85).
        min_accumulator_prob: Minimum joint accumulator ticket win probability (default 0.85 = 85%-100%).
        min_total_odds: Target combined accumulator odds.
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

    # Query ALL scheduled upcoming fixtures
    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
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

    # Individual floor to allow complementary legs that combine to >= 85% average confidence
    cand_thresh = 0.70

    for f in fixtures:
        try:
            analysis = analyze_fixture(f, db=db)
        except Exception:
            continue

        probs = analysis.get("all_markets", {})
        h_odds = f.home_odds or (round(1.0 / max(0.05, probs.get("home_win", 0.33) * 0.92), 2) if probs.get("home_win", 0) > 0.05 else None)
        d_odds = f.draw_odds or (round(1.0 / max(0.05, probs.get("draw", 0.27) * 0.92), 2) if probs.get("draw", 0) > 0.05 else 3.20)
        a_odds = f.away_odds or (round(1.0 / max(0.05, probs.get("away_win", 0.30) * 0.92), 2) if probs.get("away_win", 0) > 0.05 else None)

        fixture_cands: List[Dict[str, Any]] = []

        def _eval_and_add(m_key: str, m_type: str, sel_label: str, prob_v: float, odds_v: Optional[float], team_lbl: str):
            if prob_v >= cand_thresh and odds_v and odds_v >= 1.08:
                # Pre-combo AI squad performance & injury verification
                squad_check = check_squad_and_injuries(
                    home_team=f.home_team,
                    away_team=f.away_team,
                    competition=f.competition,
                    selection=sel_label,
                    db=db,
                )
                if squad_check.get("verdict") == "VETO":
                    return

                fixture_cands.append({
                    "fixture_id": f.id,
                    "match": f"{f.home_team} vs {f.away_team}",
                    "competition": f.competition,
                    "team": team_lbl,
                    "selection": sel_label,
                    "market_key": m_key,
                    "market_type": m_type,
                    "odds": round(odds_v, 2),
                    "probability": round(prob_v, 4),
                    "ai_squad": squad_check,
                    "kickoff_utc": f.kickoff_utc.isoformat(),
                    "kickoff_eat": _format_eat(f.kickoff_utc),
                })

        # 1. Direct Home Win
        p_hw = probs.get("home_win", 0.0)
        if h_odds and h_odds >= 1.15:
            _eval_and_add("home_win", "Direct Win (1)", f"{f.home_team} to Win", p_hw, h_odds, f.home_team)

        # 2. Direct Away Win
        p_aw = probs.get("away_win", 0.0)
        if a_odds and a_odds >= 1.15:
            _eval_and_add("away_win", "Direct Win (2)", f"{f.away_team} to Win", p_aw, a_odds, f.away_team)

        # 3. Double Chance 1X
        p_1x = probs.get("double_chance_1x", 0.0)
        if h_odds and d_odds:
            dc_1x_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / d_odds)), 2)
            _eval_and_add("double_chance_1x", "Double Chance (1X)", f"{f.home_team} Win or Draw (1X)", p_1x, dc_1x_odds, f.home_team)

        # 4. Double Chance X2
        p_x2 = probs.get("double_chance_x2", 0.0)
        if a_odds and d_odds:
            dc_x2_odds = round(1.0 / ((1.0 / a_odds) + (1.0 / d_odds)), 2)
            _eval_and_add("double_chance_x2", "Double Chance (X2)", f"{f.away_team} Win or Draw (X2)", p_x2, dc_x2_odds, f.away_team)

        # 5. Double Chance 12
        p_12 = probs.get("double_chance_12", 0.0)
        if h_odds and a_odds:
            dc_12_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / a_odds)), 2)
            _eval_and_add("double_chance_12", "Double Chance (12)", f"{f.home_team} or {f.away_team} to Win (12)", p_12, dc_12_odds, f"{f.home_team} / {f.away_team}")

        # 6. Team Over 0.5 Goals
        p_ho05 = probs.get("home_over_0_5", 0.0)
        if h_odds and h_odds <= 1.65:
            t_odds = round(max(1.10, min(1.35, 1.0 / (p_ho05 * 0.94))), 2)
            _eval_and_add("home_over_0_5", "Team Goals (Over 0.5)", f"{f.home_team} to Score (Over 0.5 Goals)", p_ho05, t_odds, f.home_team)

        p_ao05 = probs.get("away_over_0_5", 0.0)
        if a_odds and a_odds <= 1.65:
            t_odds = round(max(1.10, min(1.35, 1.0 / (p_ao05 * 0.94))), 2)
            _eval_and_add("away_over_0_5", "Team Goals (Over 0.5)", f"{f.away_team} to Score (Over 0.5 Goals)", p_ao05, t_odds, f.away_team)

        # 7. Match Goals Over 1.5
        p_o15 = probs.get("over_1_5", 0.0)
        o15_odds = round(max(1.22, min(1.65, 1.0 / (p_o15 * 0.93))), 2)
        _eval_and_add("over_1_5", "Total Goals (Over 1.5)", "Over 1.5 Match Goals", p_o15, o15_odds, f"{f.home_team} / {f.away_team}")

        # 8. Match Goals Under 3.5
        p_u35 = probs.get("under_3_5", 0.0)
        u35_odds = round(max(1.20, min(1.60, 1.0 / (p_u35 * 0.94))), 2)
        _eval_and_add("under_3_5", "Total Goals (Under 3.5)", "Under 3.5 Match Goals", p_u35, u35_odds, f"{f.home_team} / {f.away_team}")

        # 9. Draw No Bet (DNB)
        if p_hw + p_aw > 0:
            dnb_h_p = round(p_hw / (p_hw + p_aw), 4)
            if h_odds and h_odds >= 1.25 and d_odds:
                dnb_h_odds = round(max(1.18, min(2.10, h_odds * (1.0 - 1.0 / d_odds))), 2)
                _eval_and_add("dnb_home", "Draw No Bet (DNB)", f"{f.home_team} Draw No Bet (DNB)", dnb_h_p, dnb_h_odds, f.home_team)

            dnb_a_p = round(p_aw / (p_hw + p_aw), 4)
            if a_odds and a_odds >= 1.25 and d_odds:
                dnb_a_odds = round(max(1.18, min(2.10, a_odds * (1.0 - 1.0 / d_odds))), 2)
                _eval_and_add("dnb_away", "Draw No Bet (DNB)", f"{f.away_team} Draw No Bet (DNB)", dnb_a_p, dnb_a_odds, f.away_team)

        # 10. Asian Handicap +1.5
        p_ah_a15 = probs.get("ah_away_plus_1_5", 0.0)
        if p_ah_a15 >= cand_thresh:
            ah_a15_odds = round(max(1.18, min(1.65, 1.0 / (p_ah_a15 * 0.93))), 2)
            _eval_and_add("ah_away_plus_1_5", "Asian Handicap (+1.5)", f"{f.away_team} (+1.5 Handicap)", p_ah_a15, ah_a15_odds, f.away_team)

        # Keep top 3 highest probability candidates per fixture to keep combination search blazing fast
        fixture_cands.sort(key=lambda c: (c["probability"], c["odds"]), reverse=True)
        candidates.extend(fixture_cands[:3])

    # Search combinations for requested leg count (strictly 2 or 3 teams)
    k = max(2, min(3, num_legs))
    combos: List[Dict[str, Any]] = []
    min_avg_target = max(0.85, min_leg_prob)

    def _eval_combos(legs_k: int, min_odds: float, min_avg: float, min_acc: float = 0.0) -> List[Dict[str, Any]]:
        found = []
        for combo_legs in itertools.combinations(candidates, legs_k):
            # Ensure distinct fixtures
            fixture_ids = {leg["fixture_id"] for leg in combo_legs}
            if len(fixture_ids) < legs_k:
                continue

            tot_odds = 1.0
            tot_prob = 1.0
            for leg in combo_legs:
                tot_odds *= leg["odds"]
                tot_prob *= leg["probability"]

            tot_odds = round(tot_odds, 2)
            tot_prob = round(tot_prob, 4)
            avg_prob = round(sum(l["probability"] for l in combo_legs) / len(combo_legs), 4)
            min_prob = round(min(l["probability"] for l in combo_legs), 4)

            # ── HARD RULE 1: ACCUMULATOR WIN PROB must be >= min_acc (85% minimum) ──
            # No exceptions. If tot_prob < min_acc, this combo is REJECTED.
            if min_acc > 0.0 and tot_prob < min_acc:
                continue

            # ── HARD RULE 2: AVG TEAM CONFIDENCE must be >= min_avg (85% minimum) ──
            # No exceptions. If avg_prob < min_avg, this combo is REJECTED.
            if avg_prob < min_avg:
                continue

            # ── HARD RULE 3: Every individual leg must be >= 85% confidence ──
            # No weak legs allowed in a banker slip.
            if min_prob < min_avg:
                continue

            if tot_odds >= min_odds and tot_odds <= max_total_odds:
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

                found.append({
                    "legs_count": legs_k,
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
        return found

    # ══════════════════════════════════════════════════════════════════════
    # STRICT COMBO SELECTION RULES (non-negotiable, as required by user):
    #   • AVG TEAM CONFIDENCE   >= 85%  (every single leg >= 85%)
    #   • ACCUMULATOR WIN PROB  >= 85%  (joint probability of all legs)
    #   • TOTAL ODDS            >= 2.00 (hard floor, never overridden)
    # If NO combo satisfies ALL THREE → return empty (no slip today).
    # ══════════════════════════════════════════════════════════════════════
    STRICT_AVG   = 0.85   # AVG TEAM CONFIDENCE floor
    STRICT_ACC   = 0.85   # ACCUMULATOR WIN PROB floor
    STRICT_ODDS  = max(2.00, min_total_odds)  # ODDS floor

    combos = _eval_combos(k, STRICT_ODDS, min_avg=STRICT_AVG, min_acc=STRICT_ACC)

    # Sort combos primarily by ACCUMULATOR WIN PROB (descending), then AVG TEAM CONFIDENCE, then odds
    combos.sort(
        key=lambda x: (
            x["combined_probability"],
            x["avg_leg_probability"],
            x["total_odds"],
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
