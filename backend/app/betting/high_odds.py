"""High-odds slips near 6.00 built only from genuinely +EV legs.

A 6.00 ticket needs one win in six just to break even. Publishing one is only
honest when the probability behind every leg beats the price actually offered,
using the fitted model-market blend rather than an uncalibrated model guess.
When no leg clears that bar, the slate is empty and the report says so; the
diagnostics show how far the best leg was, instead of relaxing the rule.

The published 85% qualification gate is untouched: this mode has its own
expected-value threshold, and a negative-EV leg is never presented as a pick.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.probabilities import analyze_fixture
from app.betting.daily_combos import (
    EAT_TZ,
    MARKET_KEYS,
    _attribution,
    _combo,
    _format_eat,
    _price_age_hours,
    _selection_label,
    as_utc,
    is_placeholder_team,
    same_match,
)
from app.market_engine.qualification import (
    MIN_DATA_CONFIDENCE,
    NO_QUALIFIED_SELECTIONS,
    QUALIFICATION_THRESHOLD,
    data_confidence,
)
from app.models.fixture import Fixture

TARGET_TOTAL_ODDS = 6.0
ODDS_BAND = (5.5, 6.5)
MIN_LEGS = 3
MAX_LEGS = 5
MIN_LEG_EV = 0.0
# A single bookmaker's 1X2 always carries a margin. Prices stitched together
# from different books can sum to less than 1.0, and de-vigging that mixture
# invents an edge that does not exist, so a leg only counts when the three
# prices still look like one market with a real margin.
MIN_MARKET_OVERROUND = 1.02
MAX_SLIPS = 3
MAX_CANDIDATE_LEGS = 12
DEFAULT_MAX_FIXTURES = 60
CLOSEST_LEGS = 3

Analysis = Callable[..., Dict[str, Any]]


def _confidence(
    analysis: Dict[str, Any], captured: Optional[str], now: datetime
) -> Dict[str, Any]:
    lambdas = analysis.get("estimated_lambdas") or {}
    return data_confidence(
        SimpleNamespace(captured_at=captured),
        {
            "home_lambda": lambdas.get("home_lambda"),
            "away_lambda": lambdas.get("away_lambda"),
            "lambda_source": analysis.get("lambda_source"),
        },
        now=now,
    )


def _leg_row(
    fixture: Fixture,
    key: str,
    probability: float,
    price: float,
    entry: Dict[str, Any],
    confidence: Dict[str, Any],
    now: datetime,
    overround: float,
) -> Dict[str, Any]:
    market_type, selection = _selection_label(key, fixture.home_team, fixture.away_team)
    return {
        "fixture_id": fixture.id,
        "match": f"{fixture.home_team} vs {fixture.away_team}",
        "competition": fixture.competition,
        "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
        "kickoff_eat": _format_eat(fixture.kickoff_utc),
        "market_key": key,
        "market_type": market_type,
        "selection": selection,
        "odds": round(price, 2),
        "probability": round(probability, 4),
        "implied_probability": round(1.0 / price, 4),
        "expected_value": round(probability * price - 1.0, 4),
        "edge_percent": round((probability - 1.0 / price) * 100.0, 2),
        "market_overround": round(overround, 4),
        "confidence": confidence["confidence"],
        "confidence_reasons": confidence["reasons"],
        "bookmaker": entry.get("bookmaker"),
        "source": entry.get("source"),
        "source_url": entry.get("source_url"),
        "captured_at": entry.get("captured_at"),
        "price_age_hours": _price_age_hours(entry.get("captured_at"), now),
    }


def build_high_odds(
    db: Session,
    limit: int = MAX_SLIPS,
    target_date: Optional[str] = None,
    now: Optional[datetime] = None,
    target_total: float = TARGET_TOTAL_ODDS,
    band: Tuple[float, float] = ODDS_BAND,
    min_leg_ev: float = MIN_LEG_EV,
    max_fixtures: int = DEFAULT_MAX_FIXTURES,
    analyse: Optional[Analysis] = None,
) -> Dict[str, Any]:
    """Assemble near-6.0 slips from legs whose blended probability beats the price."""
    now = now or datetime.now(timezone.utc)
    target_date = target_date or now.astimezone(EAT_TZ).strftime("%Y-%m-%d")
    limit = max(1, min(MAX_SLIPS, int(limit)))
    low, high = sorted((float(band[0]), float(band[1])))
    if low <= 1.0 or high <= low:
        raise ValueError("odds band must be a positive range above 1.0")
    analyse = analyse or analyze_fixture

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .order_by(Fixture.kickoff_utc)
    )
    fixtures = [
        fixture
        for fixture in db.scalars(query).all()
        if fixture.kickoff_utc
        and as_utc(fixture.kickoff_utc).astimezone(EAT_TZ).strftime("%Y-%m-%d") == target_date
    ][:max_fixtures]

    rejected = {
        "placeholder_fixture": 0,
        "no_priced_market": 0,
        "missing_1x2": 0,
        "inconsistent_market_prices": 0,
        "analysis_failed": 0,
        "no_fitted_blend": 0,
        "no_result_market": 0,
        "low_data_confidence": 0,
        "negative_ev_leg": 0,
        "duplicate_match": 0,
    }
    evaluated: List[Tuple[Dict[str, Any], Fixture]] = []

    for fixture in fixtures:
        if is_placeholder_team(fixture.home_team) or is_placeholder_team(fixture.away_team):
            rejected["placeholder_fixture"] += 1
            continue
        attribution = _attribution(fixture)
        if not attribution:
            rejected["no_priced_market"] += 1
            continue
        if not all(key in attribution for key in MARKET_KEYS):
            rejected["missing_1x2"] += 1
            continue
        try:
            analysis = analyse(fixture, db=db)
        except Exception:
            rejected["analysis_failed"] += 1
            continue

        reference = analysis.get("market_reference") or {}
        overround = reference.get("overround")
        if not overround:
            rejected["inconsistent_market_prices"] += 1
            continue
        overround = float(overround)
        if overround <= MIN_MARKET_OVERROUND:
            rejected["inconsistent_market_prices"] += 1
            continue

        blend = analysis.get("probability_blend") or {}
        if not blend.get("applied"):
            rejected["no_fitted_blend"] += 1
            continue
        markets = analysis.get("all_markets") or {}
        if not all(key in markets for key in MARKET_KEYS):
            rejected["no_result_market"] += 1
            continue

        options = []
        for key in MARKET_KEYS:
            price = float(attribution[key].get("price") or 0.0)
            probability = float(markets.get(key) or 0.0)
            if price <= 1.0 or probability <= 0.0:
                continue
            options.append((key, probability, price))
        if not options:
            rejected["missing_1x2"] += 1
            continue

        key, probability, price = max(
            options, key=lambda item: item[1] * item[2] - 1.0
        )
        entry = attribution[key]
        confidence = _confidence(analysis, entry.get("captured_at"), now)
        if confidence["confidence"] < MIN_DATA_CONFIDENCE:
            rejected["low_data_confidence"] += 1
            continue

        leg = _leg_row(fixture, key, probability, price, entry, confidence, now, overround)
        if leg["expected_value"] + 1e-9 < min_leg_ev:
            rejected["negative_ev_leg"] += 1
            evaluated.append((leg, fixture))
            continue
        leg["_fixture"] = fixture
        evaluated.append((leg, fixture))

    priced_legs = [leg for leg, _ in evaluated]
    best_leg = max(priced_legs, key=lambda row: row["expected_value"], default=None)
    qualified = [leg for leg in priced_legs if leg["expected_value"] + 1e-9 >= min_leg_ev]

    qualified.sort(key=lambda row: (row["expected_value"], row["probability"]), reverse=True)
    candidates: List[Dict[str, Any]] = []
    for leg in qualified:
        fixture = leg["_fixture"]
        if any(same_match(fixture, kept["_fixture"]) for kept in candidates):
            rejected["duplicate_match"] += 1
            continue
        candidates.append(leg)
        if len(candidates) >= MAX_CANDIDATE_LEGS:
            break

    found: List[Dict[str, Any]] = []
    for size in range(MIN_LEGS, MAX_LEGS + 1):
        for group in itertools.combinations(candidates, size):
            if len({leg["fixture_id"] for leg in group}) < size:
                continue
            row = _combo([{key: value for key, value in leg.items() if key != "_fixture"} for leg in group])
            if low <= row["total_odds"] <= high:
                found.append(row)
    found.sort(key=lambda row: (row["combined_probability"], row["total_odds"]), reverse=True)

    slips: List[Dict[str, Any]] = []
    used: set = set()
    for row in found:
        ids = {leg["fixture_id"] for leg in row["legs"]}
        if ids & used:
            continue
        slips.append(row)
        used |= ids
        if len(slips) >= limit:
            break

    for leg in candidates:
        leg.pop("_fixture", None)

    closest = sorted(
        (leg for leg in priced_legs if leg["expected_value"] + 1e-9 < min_leg_ev),
        key=lambda row: row["expected_value"],
        reverse=True,
    )[:CLOSEST_LEGS]

    strike = 1.0 / target_total
    return {
        "target_date": target_date,
        "matchday_label": datetime.strptime(target_date, "%Y-%m-%d")
        .replace(tzinfo=EAT_TZ)
        .strftime("%A, %d %b %Y"),
        "generated_at": now.isoformat(),
        "params": {
            "target_total_odds": round(target_total, 2),
            "odds_band": [round(low, 2), round(high, 2)],
            "min_legs": MIN_LEGS,
            "max_legs": MAX_LEGS,
            "min_leg_ev": min_leg_ev,
            "min_data_confidence": MIN_DATA_CONFIDENCE,
            "min_market_overround": MIN_MARKET_OVERROUND,
            "result_markets": list(MARKET_KEYS),
        },
        "scope": {
            "fixtures_on_slate": len(fixtures),
            "fixtures_analysed": sum(
                1 for leg in priced_legs if leg["market_key"] in MARKET_KEYS
            ),
            "legs_evaluated": len(priced_legs),
            "positive_ev_legs": len(qualified),
            "candidate_legs": len(candidates),
            "slips_found": len(slips),
        },
        "diagnostics": {
            "required_strike_rate_percent": round(strike * 100.0, 2),
            "best_leg_ev": best_leg["expected_value"] if best_leg else None,
            "best_leg_match": best_leg["match"] if best_leg else None,
            "best_leg_selection": best_leg["selection"] if best_leg else None,
            "best_leg_odds": best_leg["odds"] if best_leg else None,
            "combos_in_band": len(found),
            "published_gate": QUALIFICATION_THRESHOLD,
        },
        "rejected": rejected,
        "slips": slips,
        "closest_legs": closest,
        "legs_evaluated": len(priced_legs),
        "positive_ev_legs": len(qualified),
        "count": len(slips),
        "message": (
            f"{len(slips)} slip{'s' if len(slips) != 1 else ''} inside "
            f"{low:.2f}–{high:.2f}; every leg beats its price after the margin."
            if slips
            else NO_QUALIFIED_SELECTIONS
        ),
        "note": (
            "Paper only. Each leg uses the blended model-market probability, so a "
            "pick appears only when that probability beats the attributed price. "
            "Prices stitched from different bookmakers that no longer carry a "
            "margin are excluded, because de-vigging them would invent an edge. "
            "The 85% gate is untouched and no bet is placed."
        ),
    }
