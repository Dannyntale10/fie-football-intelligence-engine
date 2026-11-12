from __future__ import annotations
"""
combo.py – Daily Multi-Bet / Combo Engine.

Builds same-day accumulator tickets (2-leg or 3-leg) strictly from matches
playing on a specific matchday in Ugandan Time (East Africa Time, EAT).

Two selection strategies are available:

* ``ev`` (default in the app) – compare the model's probability with the
  bookmaker's de-vigged implied probability and keep legs where the model sees
  a positive edge. Combos are ranked by expected value. This is the strategy
  that can actually find value, because a market price carries information the
  model does not.
* ``certainty`` (legacy) – require every leg and the joint ticket to clear an
  85% probability floor. Retained for backwards compatibility.

Neither mode uses model-derived prices unless the caller explicitly opts in;
by default only prices ingested from a bookmaker feed are eligible.
"""
import itertools
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.probabilities import analyze_fixture, calculate_kelly_stake
from app.services.ai_squad_checker import check_squad_and_injuries

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

# Bumped whenever the selection/scoring pipeline changes materially, so a
# recorded slip can be attributed to the model that produced it.
MODEL_VERSION = "fie-ev-3"

# Markets whose prices sum to one outcome set and can therefore be de-vigged
# together. Asian handicap pairs are complementary for the listed half lines.
_DEVIG_GROUPS = (
    ("home_win", "draw", "away_win"),
    ("double_chance_1x", "double_chance_x2", "double_chance_12"),
    ("btts_yes", "btts_no"),
    ("over_0_5", "under_0_5"),
    ("over_1_5", "under_1_5"),
    ("over_2_5", "under_2_5"),
    ("over_3_5", "under_3_5"),
    ("dnb_home", "dnb_away"),
    ("ah_home_minus_0_5", "ah_away_plus_0_5"),
    ("ah_home_minus_1_5", "ah_away_plus_1_5"),
    ("ah_home_minus_2_5", "ah_away_plus_2_5"),
)


def _fixture_market_odds(fixture: Fixture) -> Dict[str, float]:
    """Real bookmaker prices for a fixture, keyed by model market name."""
    odds: Dict[str, float] = {}
    raw = getattr(fixture, "market_odds_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(value, (int, float)) and value > 1.0:
                    odds[str(key)] = float(value)

    for key, value in (
        ("home_win", fixture.home_odds),
        ("draw", fixture.draw_odds),
        ("away_win", fixture.away_odds),
    ):
        if key not in odds and value and value > 1.0:
            odds[key] = float(value)

    # Use the best price available across ingested books when one exists.
    for key, best in (
        ("home_win", getattr(fixture, "max_home_odds", None)),
        ("draw", getattr(fixture, "max_draw_odds", None)),
        ("away_win", getattr(fixture, "max_away_odds", None)),
    ):
        if best and best > odds.get(key, 0.0):
            odds[key] = float(best)
    return odds


def _devigged_probabilities(
    market_odds: Dict[str, float],
) -> tuple[Dict[str, float], Dict[str, float]]:
    """Return (raw implied, vig-free implied) probabilities from decimal prices."""
    implied = {key: 1.0 / price for key, price in market_odds.items()}
    fair = dict(implied)
    for group in _DEVIG_GROUPS:
        if not all(key in implied for key in group):
            continue
        overround = sum(implied[key] for key in group)
        if overround <= 1.0:  # arbitrage or bad data; leave raw prices untouched
            continue
        for key in group:
            fair[key] = implied[key] / overround
    return implied, fair


def _selection_label(key: str, home: str, away: str) -> tuple[str, str]:
    """Human-readable (market type, selection) for a model market key."""
    fixed = {
        "home_win": ("Match Result (1)", f"{home} to Win"),
        "draw": ("Match Result (X)", "Draw"),
        "away_win": ("Match Result (2)", f"{away} to Win"),
        "double_chance_1x": ("Double Chance (1X)", f"{home} Win or Draw (1X)"),
        "double_chance_x2": ("Double Chance (X2)", f"{away} Win or Draw (X2)"),
        "double_chance_12": ("Double Chance (12)", f"{home} or {away} to Win (12)"),
        "btts_yes": ("Both Teams To Score", "Both Teams To Score - Yes"),
        "btts_no": ("Both Teams To Score", "Both Teams To Score - No"),
        "dnb_home": ("Draw No Bet (DNB)", f"{home} Draw No Bet (DNB)"),
        "dnb_away": ("Draw No Bet (DNB)", f"{away} Draw No Bet (DNB)"),
    }
    if key in fixed:
        return fixed[key]

    for prefix, under in (("over_", False), ("under_", True)):
        if key.startswith(prefix):
            line = key[len(prefix):].replace("_", ".")
            label = f"{'Under' if under else 'Over'} {line} Match Goals"
            return "Total Goals", label

    for prefix, side in (("ah_home_minus_", "home"), ("ah_away_plus_", "away")):
        if key.startswith(prefix):
            line = key[len(prefix):].replace("_", ".")
            if side == "home":
                return "Asian Handicap", f"{home} -{line} Handicap"
            return "Asian Handicap", f"{away} +{line} Handicap"

    return "Market", key.replace("_", " ").title()


def _team_label(key: str, home: str, away: str) -> str:
    if key.startswith("home") or key.startswith("ah_home") or key.startswith("dnb_home"):
        return home
    if key.startswith("double_chance_1x"):
        return home
    if key.startswith("away") or key.startswith("ah_away") or key.startswith("dnb_away"):
        return away
    if key.startswith("double_chance_x2"):
        return away
    return f"{home} / {away}"


def _collect_ev_candidates(
    fixtures: List[Fixture],
    db: Session,
    min_leg_prob: float,
    min_edge_percent: float,
    require_ratings: bool = True,
    max_edge_percent: float = 20.0,
    edge_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """Value path: keep legs where the model beats the de-vigged market price."""
    candidates: List[Dict[str, Any]] = []
    fixtures_with_priced_markets = 0
    fixtures_with_market_odds = 0
    priced_legs = 0
    rated_fixtures = 0
    unrated_skipped = 0
    implausible_skipped = 0
    stale_rated_fixtures = 0
    required_edge = min_edge_percent * max(1.0, edge_multiplier)

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

        fixture_cands: List[Dict[str, Any]] = []
        for key, price in market_odds.items():
            prob = probs.get(key)
            if prob is None or prob < min_leg_prob:
                continue
            priced_legs += 1

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
                "implied_probability": round(fair_implied, 4),
                "edge_percent": round(edge_pct, 2),
                "expected_value_percent": round(ev_pct, 2),
                "odds_source": "market",
                "model_source": model_source,
                "ai_squad": squad_check,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        fixture_cands.sort(key=lambda c: c["expected_value_percent"], reverse=True)
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
        "required_edge_percent": round(required_edge, 2),
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
    from app.services.calibration import load_report
    from app.services.ratings import ratings_snapshot_hash

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
    edge_multiplier = (
        float(calibration_report.get("edge_threshold_multiplier", 1.0))
        if calibration_report else 1.0
    )
    stale_rated_fixtures = 0
    required_edge_percent = round(min_edge_percent * max(1.0, edge_multiplier), 2)

    candidates: List[Dict[str, Any]] = []

    # Individual floor to allow complementary legs that combine to >= 85% average confidence
    cand_thresh = 0.70
    synthetic_legs_excluded = 0
    fixtures_with_market_odds = 0
    fixtures_with_priced_markets = 0
    priced_legs = 0
    rated_fixtures = 0
    unrated_skipped = 0
    implausible_skipped = 0

    if mode == "ev":
        ev_collected = _collect_ev_candidates(
            fixtures,
            db,
            min_leg_prob=min_leg_prob,
            min_edge_percent=min_edge_percent,
            require_ratings=require_ratings,
            max_edge_percent=max_edge_percent,
            edge_multiplier=edge_multiplier,
        )
        candidates = ev_collected["candidates"]
        fixtures_with_market_odds = ev_collected["fixtures_with_market_odds"]
        fixtures_with_priced_markets = ev_collected["fixtures_with_priced_markets"]
        priced_legs = ev_collected["priced_legs"]
        rated_fixtures = ev_collected["rated_fixtures"]
        stale_rated_fixtures = ev_collected["stale_rated_fixtures"]
        unrated_skipped = ev_collected["unrated_skipped"]
        implausible_skipped = ev_collected["implausible_skipped"]
        fixtures_for_certainty: List[Fixture] = []
    else:
        fixtures_for_certainty = fixtures

    for f in fixtures_for_certainty:
        try:
            analysis = analyze_fixture(f, db=db)
        except Exception:
            continue

        probs = analysis.get("all_markets", {})

        # A price is only a market price when it came from an ingested feed.
        # Anything computed from model probabilities is an estimate and is
        # labelled as such (and gated out by default).
        h_market = f.home_odds if (f.home_odds and f.home_odds > 1.0) else None
        d_market = f.draw_odds if (f.draw_odds and f.draw_odds > 1.0) else None
        a_market = f.away_odds if (f.away_odds and f.away_odds > 1.0) else None

        h_odds = h_market or (round(1.0 / max(0.05, probs.get("home_win", 0.33) * 0.92), 2) if probs.get("home_win", 0) > 0.05 else None)
        d_odds = d_market or (round(1.0 / max(0.05, probs.get("draw", 0.27) * 0.92), 2) if probs.get("draw", 0) > 0.05 else 3.20)
        a_odds = a_market or (round(1.0 / max(0.05, probs.get("away_win", 0.30) * 0.92), 2) if probs.get("away_win", 0) > 0.05 else None)

        if h_market and a_market:
            fixtures_with_market_odds += 1

        fixture_cands: List[Dict[str, Any]] = []

        def _eval_and_add(
            m_key: str,
            m_type: str,
            sel_label: str,
            prob_v: float,
            odds_v: Optional[float],
            team_lbl: str,
            odds_source: str,
        ):
            nonlocal synthetic_legs_excluded
            if prob_v < cand_thresh or not odds_v or odds_v < 1.08:
                return
            if require_market_odds and odds_source != "market":
                synthetic_legs_excluded += 1
                return

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
                "odds_source": odds_source,
                "ai_squad": squad_check,
                "kickoff_utc": f.kickoff_utc.isoformat(),
                "kickoff_eat": _format_eat(f.kickoff_utc),
            })

        # 1. Direct Home Win
        p_hw = probs.get("home_win", 0.0)
        if h_odds and h_odds >= 1.15:
            _eval_and_add("home_win", "Direct Win (1)", f"{f.home_team} to Win", p_hw, h_odds, f.home_team, "market" if h_market else "synthetic")

        # 2. Direct Away Win
        p_aw = probs.get("away_win", 0.0)
        if a_odds and a_odds >= 1.15:
            _eval_and_add("away_win", "Direct Win (2)", f"{f.away_team} to Win", p_aw, a_odds, f.away_team, "market" if a_market else "synthetic")

        # 3. Double Chance 1X
        p_1x = probs.get("double_chance_1x", 0.0)
        if h_odds and d_odds:
            dc_1x_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / d_odds)), 2)
            _eval_and_add("double_chance_1x", "Double Chance (1X)", f"{f.home_team} Win or Draw (1X)", p_1x, dc_1x_odds, f.home_team, "synthetic")

        # 4. Double Chance X2
        p_x2 = probs.get("double_chance_x2", 0.0)
        if a_odds and d_odds:
            dc_x2_odds = round(1.0 / ((1.0 / a_odds) + (1.0 / d_odds)), 2)
            _eval_and_add("double_chance_x2", "Double Chance (X2)", f"{f.away_team} Win or Draw (X2)", p_x2, dc_x2_odds, f.away_team, "synthetic")

        # 5. Double Chance 12
        p_12 = probs.get("double_chance_12", 0.0)
        if h_odds and a_odds:
            dc_12_odds = round(1.0 / ((1.0 / h_odds) + (1.0 / a_odds)), 2)
            _eval_and_add("double_chance_12", "Double Chance (12)", f"{f.home_team} or {f.away_team} to Win (12)", p_12, dc_12_odds, f"{f.home_team} / {f.away_team}", "synthetic")

        # 6. Team Over 0.5 Goals
        p_ho05 = probs.get("home_over_0_5", 0.0)
        if h_odds and h_odds <= 1.65:
            t_odds = round(max(1.10, min(1.35, 1.0 / (p_ho05 * 0.94))), 2)
            _eval_and_add("home_over_0_5", "Team Goals (Over 0.5)", f"{f.home_team} to Score (Over 0.5 Goals)", p_ho05, t_odds, f.home_team, "synthetic")

        p_ao05 = probs.get("away_over_0_5", 0.0)
        if a_odds and a_odds <= 1.65:
            t_odds = round(max(1.10, min(1.35, 1.0 / (p_ao05 * 0.94))), 2)
            _eval_and_add("away_over_0_5", "Team Goals (Over 0.5)", f"{f.away_team} to Score (Over 0.5 Goals)", p_ao05, t_odds, f.away_team, "synthetic")

        # 7. Match Goals Over 1.5
        p_o15 = probs.get("over_1_5", 0.0)
        o15_odds = round(max(1.22, min(1.65, 1.0 / (p_o15 * 0.93))), 2)
        _eval_and_add("over_1_5", "Total Goals (Over 1.5)", "Over 1.5 Match Goals", p_o15, o15_odds, f"{f.home_team} / {f.away_team}", "synthetic")

        # 8. Match Goals Under 3.5
        p_u35 = probs.get("under_3_5", 0.0)
        u35_odds = round(max(1.20, min(1.60, 1.0 / (p_u35 * 0.94))), 2)
        _eval_and_add("under_3_5", "Total Goals (Under 3.5)", "Under 3.5 Match Goals", p_u35, u35_odds, f"{f.home_team} / {f.away_team}", "synthetic")

        # 9. Draw No Bet (DNB)
        if p_hw + p_aw > 0:
            dnb_h_p = round(p_hw / (p_hw + p_aw), 4)
            if h_odds and h_odds >= 1.25 and d_odds:
                dnb_h_odds = round(max(1.18, min(2.10, h_odds * (1.0 - 1.0 / d_odds))), 2)
                _eval_and_add("dnb_home", "Draw No Bet (DNB)", f"{f.home_team} Draw No Bet (DNB)", dnb_h_p, dnb_h_odds, f.home_team, "synthetic")

            dnb_a_p = round(p_aw / (p_hw + p_aw), 4)
            if a_odds and a_odds >= 1.25 and d_odds:
                dnb_a_odds = round(max(1.18, min(2.10, a_odds * (1.0 - 1.0 / d_odds))), 2)
                _eval_and_add("dnb_away", "Draw No Bet (DNB)", f"{f.away_team} Draw No Bet (DNB)", dnb_a_p, dnb_a_odds, f.away_team, "synthetic")

        # 10. Asian Handicap +1.5
        p_ah_a15 = probs.get("ah_away_plus_1_5", 0.0)
        if p_ah_a15 >= cand_thresh:
            ah_a15_odds = round(max(1.18, min(1.65, 1.0 / (p_ah_a15 * 0.93))), 2)
            _eval_and_add("ah_away_plus_1_5", "Asian Handicap (+1.5)", f"{f.away_team} (+1.5 Handicap)", p_ah_a15, ah_a15_odds, f.away_team, "synthetic")

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
    STRICT_AVG   = max(0.85, min_leg_prob)    # AVG TEAM CONFIDENCE floor
    STRICT_ACC   = max(0.85, min_accumulator_prob or 0.85)  # ACCUMULATOR WIN PROB floor
    STRICT_ODDS  = max(2.00, min_total_odds)  # ODDS floor

    if mode == "ev":
        combos = _ev_combos(
            candidates, k, min_total_odds, max_total_odds, bankroll, kelly_fraction
        )
    else:
        combos = _eval_combos(k, STRICT_ODDS, min_avg=STRICT_AVG, min_acc=STRICT_ACC)

    # EV mode ranks by expected value; certainty mode by joint probability.
    if mode == "ev":
        combos.sort(
            key=lambda x: (x["expected_value_percent"], x["combined_probability"], x["total_odds"]),
            reverse=True,
        )
    else:
        combos.sort(
            key=lambda x: (
                x["combined_probability"],
                x["avg_leg_probability"],
                x["total_odds"],
                x["expected_value_percent"],
            ),
            reverse=True,
        )

    best_combo = combos[0] if combos else None
    alternatives = combos[1:7] if len(combos) > 1 else []

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
        },
        "calibration": calibration,
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
            "candidate_legs": len(candidates),
            "synthetic_legs_excluded": synthetic_legs_excluded,
            "combos_generated": len(combos),
            "best_edge_percent": round(
                max((c.get("edge_percent", 0.0) for c in candidates), default=0.0), 2
            ),
        },
        "best_combo": best_combo,
        "alternatives": alternatives,
    }
