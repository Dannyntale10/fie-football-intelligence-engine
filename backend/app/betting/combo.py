from __future__ import annotations
"""
combo.py – Daily Multi-Bet / Combo Engine.

Builds same-day accumulator tickets (2-leg or 3-leg) strictly from matches
playing on a specific matchday in Ugandan Time (East Africa Time, EAT).

Two selection strategies are available:

* ``ev`` (default in the app) – compare the model's probability with the
  bookmaker's de-vigged implied probability and keep legs where the model sees
  a positive edge. Slips are ordered by a composite rank score in which edge
  and expected value carry the most weight. This is the strategy
  that can actually find value, because a market price carries information the
  model does not.
* ``certainty`` (legacy) – require every leg and the joint ticket to clear an
  85% probability floor. Retained for backwards compatibility. Rank order here
  rewards average and joint probability instead of edge.

The rank score orders what the gates already allowed; it never admits a leg or
slip that a selection rule rejected.

Neither mode uses model-derived prices unless the caller explicitly opts in;
by default only prices ingested from a bookmaker feed are eligible.
"""
import itertools
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.analytics.probabilities import analyze_fixture, calculate_kelly_stake
from app.ai.ai_squad_checker import check_squad_and_injuries
from app.betting.labels import selection_label as _selection_label, team_label as _team_label
from app.betting.pricing import (
    devigged_probabilities as _devigged_probabilities,
    fixture_market_odds as _fixture_market_odds,
)
from app.betting.ranking import leg_score, payload as ranking_payload, rank_combos, rank_legs
from app.betting.selection import select_top_picks

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

# Bumped whenever the selection/scoring pipeline changes materially, so a
# recorded slip can be attributed to the model that produced it.
MODEL_VERSION = "fie-ev-6"


def _collect_ev_candidates(
    fixtures: List[Fixture],
    db: Session,
    min_leg_prob: float,
    min_edge_percent: float,
    require_ratings: bool = True,
    max_edge_percent: float = 20.0,
    edge_multiplier: float = 1.0,
    learning_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Value path: keep legs where the model beats the de-vigged market price."""
    from app.analytics.learning import calibrate_markets, edge_premium

    candidates: List[Dict[str, Any]] = []
    fixtures_with_priced_markets = 0
    fixtures_with_market_odds = 0
    priced_legs = 0
    rated_fixtures = 0
    unrated_skipped = 0
    implausible_skipped = 0
    stale_rated_fixtures = 0
    calibrated_legs = 0
    base_required_edge = min_edge_percent * max(1.0, edge_multiplier)

    for f in fixtures:
        market_odds = _fixture_market_odds(f)
        if not market_odds:
            continue
        if all(key in market_odds for key in ("home_win", "away_win")):
            fixtures_with_market_odds += 1
        if len(market_odds) > 3:
            fixtures_with_priced_markets += 1

        try:
            analysis = analyze_fixture(f, db=db)
        except Exception:
            continue
        probs = analysis.get("all_markets", {})
        model_source = analysis.get("lambda_source")
        if model_source == "team_ratings":
            rated_fixtures += 1
            if (analysis.get("rating_meta") or {}).get("stale"):
                stale_rated_fixtures += 1
        elif require_ratings:
            # Without ratings the model is fitted to these very 1X2 prices, so
            # any "edge" would be circular rather than independent information.
            unrated_skipped += 1
            continue
        _, fair = _devigged_probabilities(market_odds)
        calibrated = calibrate_markets(
            {key: probs.get(key) for key in market_odds}, learning_report
        )

        fixture_cands: List[Dict[str, Any]] = []
        for key, price in market_odds.items():
            raw_prob = probs.get(key)
            if raw_prob is None:
                continue
            prob, was_calibrated = calibrated.get(key, (raw_prob, False))
            if was_calibrated:
                calibrated_legs += 1
            if prob < min_leg_prob:
                continue
            priced_legs += 1

            required_edge = base_required_edge * edge_premium(key, learning_report)
            fair_implied = fair.get(key, 1.0 / price)
            edge_pct = (prob - fair_implied) * 100.0
            ev_pct = (prob * price - 1.0) * 100.0
            if edge_pct < required_edge or ev_pct <= 0:
                continue
            if edge_pct > max_edge_percent:
                implausible_skipped += 1
                continue

            market_type, selection = _selection_label(key, f.home_team, f.away_team)
            squad_check = check_squad_and_injuries(
                home_team=f.home_team,
                away_team=f.away_team,
                competition=f.competition,
                selection=selection,
                db=db,
            )
            # Only a verdict backed by a verified feed may remove a leg. An
            # LLM reading from memory must never silently veto a priced edge.
            if squad_check.get("verdict") == "VETO" and squad_check.get("source") == "verified":
                continue

            fixture_cands.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "team": _team_label(key, f.home_team, f.away_team),
                "selection": selection,
                "market_key": key,
                "market_type": market_type,
                "odds": round(price, 2),
                "probability": round(prob, 4),
                "probability_raw": round(raw_prob, 4),
                "calibrated": was_calibrated,
                "required_edge_percent": round(required_edge, 2),
                "implied_probability": round(fair_implied, 4),
                "edge_percent": round(edge_pct, 2),
                "expected_value_percent": round(ev_pct, 2),
                "odds_source": "market",
                "model_source": model_source,
                "ai_squad": squad_check,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        fixture_cands.sort(
            key=lambda c: (leg_score(c)["score"], c["expected_value_percent"], c["probability"]),
            reverse=True,
        )
        candidates.extend(fixture_cands[:3])

    return {
        "candidates": candidates,
        "fixtures_with_priced_markets": fixtures_with_priced_markets,
        "fixtures_with_market_odds": fixtures_with_market_odds,
        "priced_legs": priced_legs,
        "rated_fixtures": rated_fixtures,
        "stale_rated_fixtures": stale_rated_fixtures,
        "unrated_skipped": unrated_skipped,
        "implausible_skipped": implausible_skipped,
        "calibrated_legs": calibrated_legs,
        "required_edge_percent": round(base_required_edge, 2),
    }


def _score_combo(
    combo_legs: tuple,
    legs_k: int,
    bankroll: float,
    kelly_fraction: float,
) -> Dict[str, Any]:
    tot_odds = 1.0
    tot_prob = 1.0
    for leg in combo_legs:
        tot_odds *= leg["odds"]
        tot_prob *= leg["probability"]

    tot_odds = round(tot_odds, 2)
    tot_prob = round(tot_prob, 4)
    avg_prob = round(sum(l["probability"] for l in combo_legs) / len(combo_legs), 4)
    min_prob = round(min(l["probability"] for l in combo_legs), 4)
    implied_prob = round(1.0 / tot_odds, 4)

    kelly = calculate_kelly_stake(
        model_probability=tot_prob,
        bookmaker_odds=tot_odds,
        bankroll=bankroll,
        fraction=kelly_fraction,
        max_stake_percent=5.0,
    )

    return {
        "legs_count": legs_k,
        "total_odds": tot_odds,
        "combined_probability": tot_prob,
        "avg_leg_probability": avg_prob,
        "min_leg_probability": min_prob,
        "implied_probability": implied_prob,
        "expected_value_percent": round((tot_prob * tot_odds - 1.0) * 100, 2),
        "edge_percent": round((tot_prob - implied_prob) * 100, 2),
        "kelly_stake_amount": kelly.get("stake_amount", 0.0),
        "kelly_stake_percent": kelly.get("stake_percent", 0.0),
        "legs": list(combo_legs),
    }


def _ev_combos(
    candidates: List[Dict[str, Any]],
    legs_k: int,
    min_odds: float,
    max_odds: float,
    bankroll: float,
    kelly_fraction: float,
) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for combo_legs in itertools.combinations(candidates, legs_k):
        if len({leg["fixture_id"] for leg in combo_legs}) < legs_k:
            continue
        row = _score_combo(combo_legs, legs_k, bankroll, kelly_fraction)
        if not (min_odds <= row["total_odds"] <= max_odds):
            continue
        if row["expected_value_percent"] <= 0:
            continue
        found.append(row)
    return found


def _format_eat(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")


def _pick_to_leg(pick: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a qualified top structure into a combo leg."""
    return {
        "fixture_id": pick["fixture_id"],
        "match": pick["match"],
        "competition": pick["competition"],
        "team": pick.get("team"),
        "selection": pick["selection"],
        "market_key": pick["market_key"],
        "market_type": pick["market_type"],
        "odds": pick["odds"],
        "probability": pick["probability"],
        "probability_raw": pick.get("probability_model", pick["probability"]),
        "calibrated": False,
        "implied_probability": pick.get("implied_probability"),
        "edge_percent": pick.get("edge_percent"),
        "expected_value_percent": pick.get("expected_value_percent"),
        "odds_source": pick["odds_source"],
        "model_source": pick.get("model_source"),
        "blended": pick.get("blended", False),
        "blend_weight": pick.get("blend_weight"),
        "confidence": pick.get("confidence"),
        "structures_considered": pick.get("structures_considered"),
        "runner_ups": pick.get("runner_ups", []),
        "ai_squad": pick.get("ai_squad"),
        "kickoff_utc": pick.get("kickoff_utc"),
        "kickoff_eat": pick.get("kickoff_eat"),
        "rank": pick.get("rank"),
        "score": pick.get("score"),
        "grade": pick.get("grade"),
    }


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


def top_picks_for_matchday(
    db: Session,
    target_date: Optional[str] = None,
    competition: Optional[str] = None,
    threshold: float = 0.85,
    require_market_odds: bool = True,
    limit: int = 25,
) -> Dict[str, Any]:
    """Highest weighted structure per fixture for the chosen matchday."""
    now = datetime.now(timezone.utc)
    matchdays = get_available_matchdays(db)
    resolved_date = target_date
    if not resolved_date and matchdays:
        resolved_date = matchdays[0]["date"]
    matchday_info = next((m for m in matchdays if m["date"] == resolved_date), None)
    if resolved_date != "all" and matchday_info is None and matchdays:
        resolved_date = matchdays[0]["date"]
        matchday_info = matchdays[0]

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))
    fixtures = db.scalars(query).all()
    if resolved_date and resolved_date != "all":
        fixtures = [
            fixture
            for fixture in fixtures
            if fixture.kickoff_utc.astimezone(EAT_TZ).strftime("%Y-%m-%d") == resolved_date
        ]

    result = select_top_picks(
        fixtures,
        db,
        threshold=threshold,
        require_market_odds=require_market_odds,
    )
    result.update(
        {
            "target_date": resolved_date,
            "matchday_label": matchday_info["label"]
            if matchday_info
            else ("All Upcoming Fixtures" if resolved_date == "all" else resolved_date),
            "matchday_matches_count": len(fixtures),
            "available_matchdays": matchdays,
            "returned": min(len(result["picks"]), limit),
        }
    )
    result["picks"] = result["picks"][:limit]
    return result


def generate_high_confidence_combos(
    db: Session,
    target_date: Optional[str] = None,
    num_legs: int = 3,
    min_leg_prob: float = 0.55,
    min_accumulator_prob: Optional[float] = None,
    min_total_odds: float = 2.00,
    max_total_odds: float = 6.00,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    competition: Optional[str] = None,
    require_market_odds: bool = True,
    selection_mode: str = "certainty",
    min_edge_percent: float = 3.0,
    require_ratings: bool = True,
    max_edge_percent: float = 20.0,
    allow_unvalidated_ev: bool = False,
) -> Dict[str, Any]:
    """Generate optimal same-day multi-bet slips.

    Args:
        db: SQLAlchemy session.
        target_date: Specific YYYY-MM-DD matchday (in EAT), or None for next active matchday.
        num_legs: Target number of legs (2 or 3).
        min_leg_prob: Minimum individual leg probability. In ``ev`` mode this is
            the value threshold; in ``certainty`` mode it is clamped to 0.85.
        min_accumulator_prob: Joint ticket probability floor (certainty mode only).
        min_total_odds: Minimum combined accumulator odds.
        max_total_odds: Cap for total accumulator odds.
        bankroll: Total bankroll for Kelly stake sizing.
        kelly_fraction: Kelly fraction (0.25 = quarter Kelly).
        competition: Optional competition name filter.
        require_market_odds: When True (default) certainty mode only uses prices
            ingested from a bookmaker feed. EV mode always requires them.
        selection_mode: ``"ev"`` ranks by edge over the de-vigged market price;
            ``"certainty"`` applies the legacy 85% probability floors.
        min_edge_percent: Minimum model-vs-market edge for a leg in EV mode.
        require_ratings: EV mode only rated teams with an independent
            (attack/defence) model. When a fixture has no ratings its
            probabilities are derived from the same 1X2 prices it is compared
            against, which would manufacture fake edges.
        max_edge_percent: Edges this large mean the model and the market
            disagree about team strength (usually stale ratings), not that the
            bookmaker is wrong. Such legs are rejected as model failures.
        allow_unvalidated_ev: EV mode only. When a walk-forward evaluation
            shows the model has not beaten the market out of sample, no slip is
            returned unless this is set. The measured deficit is always
            reported so the caller can show it.

    Returns:
        dict with target_matchday, best_combo, alternatives, candidate_legs,
        diagnostics and available_matchdays.
    """
    from app.analytics.calibration import load_report
    from app.analytics.learning import load_report as load_learning_report
    from app.analytics.ratings import ratings_snapshot_hash

    mode = "ev" if str(selection_mode).lower() == "ev" else "certainty"
    now = datetime.now(timezone.utc)
    matchdays = get_available_matchdays(db)

    # If no target date provided, default to next active matchday.
    # "all" is an explicit sentinel meaning every upcoming fixture.
    if not target_date and matchdays:
        target_date = matchdays[0]["date"]

    if target_date != "all":
        target_matchday_info = next((m for m in matchdays if m["date"] == target_date), None)
        if not target_matchday_info and matchdays:
            target_date = matchdays[0]["date"]
            target_matchday_info = matchdays[0]
    else:
        target_matchday_info = None

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

    mode = (selection_mode or "certainty").strip().lower()
    if mode not in ("ev", "certainty"):
        mode = "certainty"

    calibration_report = load_report() if mode == "ev" else None
    learning_report = load_learning_report() if mode == "ev" else None
    edge_multiplier = (
        float(calibration_report.get("edge_threshold_multiplier", 1.0))
        if calibration_report else 1.0
    )
    stale_rated_fixtures = 0
    required_edge_percent = round(min_edge_percent * max(1.0, edge_multiplier), 2)
    calibrated_legs = 0

    candidates: List[Dict[str, Any]] = []
    synthetic_legs_excluded = 0
    fixtures_with_market_odds = 0
    fixtures_with_priced_markets = 0
    priced_legs = 0
    rated_fixtures = 0
    unrated_skipped = 0
    implausible_skipped = 0

    # One weighted structure per fixture, already gated at >=85% probability
    # and >=0.60 data confidence. EV mode still lists these for inspection but
    # builds its slips from measured edges instead.
    selection = select_top_picks(
        fixtures,
        db,
        threshold=min_leg_prob,
        require_market_odds=require_market_odds,
    )

    if mode == "ev":
        ev_collected = _collect_ev_candidates(
            fixtures,
            db,
            min_leg_prob=min_leg_prob,
            min_edge_percent=min_edge_percent,
            require_ratings=require_ratings,
            max_edge_percent=max_edge_percent,
            edge_multiplier=edge_multiplier,
            learning_report=learning_report,
        )
        candidates = ev_collected["candidates"]
        calibrated_legs = ev_collected.get("calibrated_legs", 0)
        fixtures_with_market_odds = ev_collected["fixtures_with_market_odds"]
        fixtures_with_priced_markets = ev_collected["fixtures_with_priced_markets"]
        priced_legs = ev_collected["priced_legs"]
        rated_fixtures = ev_collected["rated_fixtures"]
        stale_rated_fixtures = ev_collected["stale_rated_fixtures"]
        unrated_skipped = ev_collected["unrated_skipped"]
        implausible_skipped = ev_collected["implausible_skipped"]
    else:
        candidates = [_pick_to_leg(pick) for pick in selection["picks"]]
        priced_legs = len(candidates)
        fixtures_with_market_odds = selection["priced_fixtures"]
        fixtures_with_priced_markets = selection["priced_fixtures"]
        rated_fixtures = sum(
            1 for pick in selection["picks"] if pick.get("model_source") == "team_ratings"
        )
        synthetic_legs_excluded = selection["estimates_skipped"]

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
    STRICT_AVG   = max(0.85, min_leg_prob)    # AVG TEAM CONFIDENCE floor
    STRICT_ACC   = max(0.85, min_accumulator_prob or 0.85)  # ACCUMULATOR WIN PROB floor
    STRICT_ODDS  = max(2.00, min_total_odds)  # ODDS floor

    if mode == "ev":
        combos = _ev_combos(
            candidates, k, min_total_odds, max_total_odds, bankroll, kelly_fraction
        )
    else:
        combos = _eval_combos(k, STRICT_ODDS, min_avg=STRICT_AVG, min_acc=STRICT_ACC)

    # One rank order for everything the user sees: highest first.
    ranked_combos = rank_combos(combos, mode)
    best_combo = ranked_combos[0] if ranked_combos else None
    alternatives = ranked_combos[1:7] if len(ranked_combos) > 1 else []

    # A model that has lost to the market out of sample cannot be trusted to
    # declare 3% edges. Block the slip unless the caller explicitly opts in,
    # and always report the measurement that caused it.
    calibration = None
    blocked_reason = None
    if mode == "ev" and calibration_report:
        skill = calibration_report.get("skill") or {}
        calibration = {
            "verdict": calibration_report.get("verdict"),
            "validated": bool(skill.get("validated")),
            "n_matches": calibration_report.get("n_matches"),
            "model_brier": (calibration_report.get("model") or {}).get("brier"),
            "market_brier": (calibration_report.get("market") or {}).get("brier"),
            "model_log_loss": (calibration_report.get("model") or {}).get("log_loss"),
            "market_log_loss": (calibration_report.get("market") or {}).get("log_loss"),
            "edge_threshold_multiplier": edge_multiplier,
            "evaluated_at": calibration_report.get("generated_at"),
        }
        if not calibration["validated"] and not allow_unvalidated_ev:
            if best_combo is not None:
                blocked_reason = (
                    "Walk-forward evaluation shows the model has not beaten the de-vigged "
                    "market, so its edges are not validated. Refresh results/ratings and "
                    "re-run the evaluation, or pass allow_unvalidated_ev=True to research anyway."
                )
            best_combo = None
            alternatives = []
            ranked_combos = []

    try:
        provenance = {
            "model_version": MODEL_VERSION,
            "ratings_hash": ratings_snapshot_hash(db),
            "selection_mode": mode,
            "edge_threshold_multiplier": edge_multiplier,
        }
    except Exception:
        provenance = {"model_version": MODEL_VERSION, "selection_mode": mode}

    return {
        "target_date": target_date,
        "matchday_label": target_matchday_info["label"] if target_matchday_info else ("All Upcoming Fixtures" if target_date == "all" else (target_date or "All Matchdays")),
        "matchday_matches_count": target_matchday_info["count"] if target_matchday_info else len(fixtures),
        "available_matchdays": matchdays,
        "params": {
            "num_legs": k,
            "min_leg_prob": min_leg_prob,
            "min_total_odds": min_total_odds,
            "bankroll": bankroll,
            "kelly_fraction": kelly_fraction,
            "require_market_odds": require_market_odds,
            "selection_mode": mode,
            "min_edge_percent": min_edge_percent,
            "required_edge_percent": required_edge_percent,
            "require_ratings": require_ratings,
            "max_edge_percent": max_edge_percent,
            "allow_unvalidated_ev": allow_unvalidated_ev,
            "selection_threshold": selection["threshold"],
            "min_data_confidence": selection["min_confidence"],
        },
        "calibration": calibration,
        "learning": {
            "active": bool(learning_report),
            "version": (learning_report or {}).get("version"),
            "trained_at": (learning_report or {}).get("trained_at"),
            "sources": (learning_report or {}).get("sources"),
            "applied_markets": (learning_report or {}).get("applied_markets", []),
            "edge_premiums": (learning_report or {}).get("edge_premiums", {}),
            "calibrated_legs": calibrated_legs,
        },
        "blocked_reason": blocked_reason,
        "provenance": provenance,
        "odds_policy": {
            "require_market_odds": require_market_odds,
            "synthetic_legs_excluded": synthetic_legs_excluded,
            "note": (
                "EV mode: legs must beat the de-vigged bookmaker price and can "
                "only use real market prices."
                if mode == "ev" else
                "Only prices ingested from a bookmaker feed are eligible. "
                "Model-derived prices are treated as estimates and excluded."
                if require_market_odds else
                "Model-derived prices are allowed and labelled odds_source='synthetic'."
            ),
        },
        "total_candidate_legs": len(candidates),
        "total_combos_generated": len(combos),
        "top_picks": selection,
        "ranking": ranking_payload(
            ranked_combos,
            []
            if blocked_reason
            else (candidates if mode == "certainty" else rank_legs(candidates)),
        ),
        "diagnostics": {
            "mode": mode,
            "fixtures_in_scope": len(fixtures),
            "fixtures_with_market_1x2": fixtures_with_market_odds,
            "fixtures_with_priced_markets": fixtures_with_priced_markets,
            "priced_legs": priced_legs,
            "rated_fixtures": rated_fixtures,
            "stale_rated_fixtures": stale_rated_fixtures,
            "unrated_fixtures_skipped": unrated_skipped,
            "implausible_edges_skipped": implausible_skipped,
            "required_edge_percent": required_edge_percent,
            "edge_threshold_multiplier": edge_multiplier,
            "calibrated_legs": calibrated_legs,
            "candidate_legs": len(candidates),
            "synthetic_legs_excluded": synthetic_legs_excluded,
            "top_picks_qualified": selection["qualified"],
            "top_pick_rejections": selection["rejected"],
            "combos_generated": len(combos),
            "best_edge_percent": round(
                max((c.get("edge_percent", 0.0) for c in candidates), default=0.0), 2
            ),
        },
        "best_combo": best_combo,
        "alternatives": alternatives,
    }
