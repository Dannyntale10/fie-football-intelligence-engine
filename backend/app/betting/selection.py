"""Top market-structure selection under the 85% probability gate.

For every fixture the engine prices each market structure that has a real
bookmaker price and a registered model. A structure is eligible only when its
probability reaches the published 85% floor and its data confidence reaches
the published 0.60 floor. Eligible structures are weighted with probability
carrying the largest budget, and the single highest-scoring structure per
fixture is the pick. The runner-ups are kept so the choice can be inspected.

A structure is never promoted because the model likes it: a price must exist.
Model-derived prices are only used when the caller explicitly opts in, and are
labelled ``synthetic``.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.ai.ai_squad_checker import check_squad_and_injuries
from app.analytics.probabilities import analyze_fixture
from app.betting.labels import selection_label, team_label
from app.betting.pricing import devigged_probabilities, fixture_market_odds
from app.betting.ranking import top_pick_score
from app.market_engine.qualification import (
    MIN_DATA_CONFIDENCE,
    NO_QUALIFIED_SELECTIONS,
    QUALIFICATION_THRESHOLD,
    data_confidence,
)

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

SELECTION_VERSION = "structure-1"
MIN_THRESHOLD = QUALIFICATION_THRESHOLD  # the 85% floor cannot be lowered
MAX_RUNNER_UPS = 2
MAX_ESTIMATED_PRICE = 50.0

# Mainstream structures the model can price. Only these are considered when a
# caller opts into estimated prices; obscure keys are never invented.
_ESTIMATED_KEYS = (
    "home_win",
    "draw",
    "away_win",
    "double_chance_1x",
    "double_chance_x2",
    "double_chance_12",
    "dnb_home",
    "dnb_away",
    "over_0_5",
    "over_1_5",
    "over_2_5",
    "over_3_5",
    "under_1_5",
    "under_2_5",
    "under_3_5",
    "btts_yes",
    "btts_no",
    "home_over_0_5",
    "away_over_0_5",
    "home_over_1_5",
    "away_over_1_5",
    "ah_home_minus_0_5",
    "ah_home_minus_1_5",
    "ah_home_minus_2_5",
    "ah_away_plus_0_5",
    "ah_away_plus_1_5",
    "ah_away_plus_2_5",
)

_BLENDED_PREFIXES = (
    "home_win",
    "draw",
    "away_win",
    "double_chance_",
    "dnb_",
)


def _format_eat(value: Optional[datetime]) -> str:
    if not value:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")


def _estimated_price(probability: float) -> Optional[float]:
    if probability <= 0:
        return None
    return round(max(1.05, min(MAX_ESTIMATED_PRICE, 1.0 / (probability * 0.94))), 2)


def _confidence(fixture: Any, analysis: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    lambdas = analysis.get("estimated_lambdas") or {}
    context = {
        "home_lambda": lambdas.get("home_lambda"),
        "away_lambda": lambdas.get("away_lambda"),
        "lambda_source": analysis.get("lambda_source"),
        "competition": getattr(fixture, "competition", None),
    }
    stamp = getattr(fixture, "odds_updated_at", None)
    return data_confidence(SimpleNamespace(captured_at=stamp), context, now=now)


def _blend_flag(analysis: Dict[str, Any], key: str) -> tuple[bool, Optional[float], Optional[float]]:
    blend = analysis.get("probability_blend") or {}
    if not blend.get("applied"):
        return False, None, None
    if not key.startswith(_BLENDED_PREFIXES):
        return False, None, None
    model_probability = (blend.get("model") or {}).get(key)
    return True, blend.get("weight"), model_probability


def _evaluate_structure(
    fixture: Any,
    key: str,
    price: float,
    odds_source: str,
    probs: Dict[str, float],
    fair: Dict[str, float],
    analysis: Dict[str, Any],
    confidence: Dict[str, Any],
    threshold: float,
) -> Optional[Dict[str, Any]]:
    probability = probs.get(key)
    if probability is None:
        return None
    if probability < threshold or confidence["confidence"] < MIN_DATA_CONFIDENCE:
        return None

    home = getattr(fixture, "home_team", "")
    away = getattr(fixture, "away_team", "")
    market_type, selection = selection_label(key, home, away)
    fair_probability = fair.get(key, 1.0 / price)
    edge_percent = (probability - fair_probability) * 100.0
    ev_percent = (probability * price - 1.0) * 100.0
    blended, blend_weight, model_probability = _blend_flag(analysis, key)
    kickoff = getattr(fixture, "kickoff_utc", None)

    row: Dict[str, Any] = {
        "fixture_id": getattr(fixture, "id", None),
        "match": f"{home} vs {away}",
        "competition": getattr(fixture, "competition", ""),
        "team": team_label(key, home, away),
        "market_key": key,
        "market_type": market_type,
        "selection": selection,
        "odds": round(price, 2),
        "odds_source": odds_source,
        "probability": round(probability, 4),
        "probability_model": round(float(model_probability), 4)
        if model_probability is not None
        else round(probability, 4),
        "blended": blended,
        "blend_weight": blend_weight if blended else None,
        "implied_probability": round(1.0 / price, 4),
        "devigged_probability": round(fair_probability, 4),
        "edge_percent": round(edge_percent, 2),
        "expected_value_percent": round(ev_percent, 2),
        "confidence": confidence["confidence"],
        "confidence_reasons": confidence["reasons"],
        "model_source": analysis.get("lambda_source"),
        "kickoff_utc": kickoff.isoformat() if kickoff else None,
        "kickoff_eat": _format_eat(kickoff),
    }
    row.update(top_pick_score(row))
    return row


def _qualified_structures(
    fixture: Any,
    analysis: Dict[str, Any],
    market_odds: Dict[str, float],
    threshold: float,
    require_market_odds: bool,
    now: datetime,
) -> Dict[str, Any]:
    probs = analysis.get("all_markets") or {}
    _, fair = devigged_probabilities(market_odds)
    confidence = _confidence(fixture, analysis, now)
    rejected: Counter = Counter()
    estimated_skipped = 0
    considered = 0
    eligible: List[Dict[str, Any]] = []

    def consider(key: str, price: float, source: str) -> None:
        nonlocal considered
        probability = probs.get(key)
        if probability is None:
            return
        considered += 1
        if probability < threshold:
            rejected["below_probability_floor"] += 1
            return
        if confidence["confidence"] < MIN_DATA_CONFIDENCE:
            rejected["low_data_confidence"] += 1
            return
        row = _evaluate_structure(
            fixture, key, price, source, probs, fair, analysis, confidence, threshold
        )
        if row is not None:
            eligible.append(row)

    for key, price in market_odds.items():
        consider(key, price, "market")

    if require_market_odds:
        for key in _ESTIMATED_KEYS:
            if key in market_odds:
                continue
            probability = probs.get(key)
            if probability is not None and probability >= threshold:
                estimated_skipped += 1
    else:
        for key in _ESTIMATED_KEYS:
            if key in market_odds:
                continue
            probability = probs.get(key)
            if probability is None:
                continue
            price = _estimated_price(probability)
            if price is not None:
                consider(key, price, "synthetic")

    eligible.sort(
        key=lambda row: (row["score"], row["probability"], -row["odds"]),
        reverse=True,
    )
    return {
        "eligible": eligible,
        "rejected": rejected,
        "estimated_skipped": estimated_skipped,
        "considered": considered,
        "confidence": confidence,
    }


def _choose_structure(
    fixture: Any,
    eligible: List[Dict[str, Any]],
    db: Any,
) -> Optional[Dict[str, Any]]:
    home = getattr(fixture, "home_team", "")
    away = getattr(fixture, "away_team", "")
    competition = getattr(fixture, "competition", "")

    for index, row in enumerate(eligible):
        squad = check_squad_and_injuries(
            home_team=home,
            away_team=away,
            competition=competition,
            selection=row["selection"],
            db=db,
        )
        # Only a verdict backed by a verified feed may remove a pick. An
        # advisory reading must never silently veto a priced structure.
        if squad.get("verdict") == "VETO" and squad.get("source") == "verified":
            continue
        picked = dict(row)
        picked["ai_squad"] = squad
        picked["structures_considered"] = len(eligible)
        picked["runner_ups"] = [
            {
                "market_key": other["market_key"],
                "market_type": other["market_type"],
                "selection": other["selection"],
                "odds": other["odds"],
                "probability": other["probability"],
                "score": other["score"],
                "blended": other["blended"],
            }
            for other in eligible[index + 1:index + 1 + MAX_RUNNER_UPS]
        ]
        return picked
    return None


def select_top_picks(
    fixtures: List[Any],
    db: Any = None,
    threshold: float = MIN_THRESHOLD,
    require_market_odds: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return one weighted top structure per fixture that clears both floors."""
    now = now or datetime.now(timezone.utc)
    threshold = max(MIN_THRESHOLD, min(0.99, float(threshold)))

    picks: List[Dict[str, Any]] = []
    rejected: Counter = Counter()
    estimated_skipped = 0
    priced_fixtures = 0
    evaluated_fixtures = 0
    vetoed_fixtures = 0

    for fixture in fixtures:
        evaluated_fixtures += 1
        market_odds = fixture_market_odds(fixture)
        if market_odds:
            priced_fixtures += 1
        try:
            analysis = analyze_fixture(fixture, db=db)
        except Exception:
            rejected["analysis_failed"] += 1
            continue

        result = _qualified_structures(
            fixture,
            analysis,
            market_odds,
            threshold=threshold,
            require_market_odds=require_market_odds,
            now=now,
        )
        rejected.update(result["rejected"])
        estimated_skipped += result["estimated_skipped"]
        chosen = _choose_structure(fixture, result["eligible"], db)
        if chosen is None:
            if result["eligible"]:
                vetoed_fixtures += 1
                rejected["squad_veto"] += 1
            elif not result["considered"]:
                rejected["no_modelled_structure"] += 1
            continue
        picks.append(chosen)

    picks.sort(
        key=lambda row: (row["score"], row["probability"], -row["odds"]),
        reverse=True,
    )
    for position, row in enumerate(picks, start=1):
        row["rank"] = position

    return {
        "version": SELECTION_VERSION,
        "method": (
            "probability-first weighted choice: probability 60, price edge 15, "
            "expected value 10, data evidence 15; one structure per fixture"
        ),
        "scope": "one top structure per fixture, highest weighted probability first",
        "threshold": threshold,
        "min_confidence": MIN_DATA_CONFIDENCE,
        "evaluated_fixtures": evaluated_fixtures,
        "priced_fixtures": priced_fixtures,
        "estimates_skipped": estimated_skipped,
        "rejected": dict(rejected),
        "vetoed_fixtures": vetoed_fixtures,
        "picks": picks,
        "qualified": len(picks),
        "message": NO_QUALIFIED_SELECTIONS if not picks else f"{len(picks)} qualified top picks",
        "note": (
            "A top pick clears the published 85% probability floor and the 0.60 data-confidence "
            "floor using a real bookmaker price. The weighted score chooses the structure; it "
            "does not turn a high probability into a guarantee, and EV is reported as measured."
        ),
    }
