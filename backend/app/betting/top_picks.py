"""The five best analysed matches whose outright result clears the 85% gate.

The headline of each pick is a match-winner selection (home, draw or away)
ranked by the model's probability. It is only published when the price for
that exact outcome is a real bookmaker price with its bookmaker and capture
time, and when the data behind the probability clears the published
confidence floor. Markets that also clear 85% on the same fixture — double
chance, draw-no-bet, totals, both-teams-to-score — stay in the card as
supporting analysis; they do not displace the result being shown.

Five is a cap, not a quota: with fewer qualified matches the list is shorter,
and with none it is NO QUALIFIED SELECTIONS FOUND. The gate is never lowered
to fill the page.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.probabilities import analyze_fixture, calculate_expected_value
from app.betting.daily_combos import (
    EAT_TZ,
    _attribution,
    _format_eat,
    _price_age_hours,
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

TOP_PICKS = 5
DEFAULT_MAX_FIXTURES = 60
RESULT_MARKETS = ("home_win", "draw", "away_win")

_DOUBLE_CHANCE = {
    "double_chance_1x": "1X",
    "double_chance_x2": "X2",
    "double_chance_12": "12",
}

_RESULT_LABELS = {
    "home_win": lambda home, away: f"{home} to win",
    "draw": lambda home, away: "Draw",
    "away_win": lambda home, away: f"{away} to win",
    "dnb_home": lambda home, away: f"{home} draw no bet",
    "dnb_away": lambda home, away: f"{away} draw no bet",
}


def _line(key: str) -> Optional[str]:
    parts = key.split("_")
    if len(parts) >= 2 and parts[-1].isdigit() and parts[-2].isdigit():
        return f"{parts[-2]}.{parts[-1]}"
    return None


def _market_label(key: str, home: str, away: str) -> str:
    """Human label for a supporting market; never invents one for a price."""
    if key in _RESULT_LABELS:
        return _RESULT_LABELS[key](home, away)
    if key in _DOUBLE_CHANCE:
        return f"Double chance {_DOUBLE_CHANCE[key]}"
    if key == "btts_yes":
        return "Both teams to score"
    if key == "btts_no":
        return "Both teams not to score"
    if key == "either_team_over_1_5":
        return "Either team over 1.5 goals"
    line = _line(key)
    if line and key.startswith("over_"):
        return f"Over {line} goals"
    if line and key.startswith("under_"):
        return f"Under {line} goals"
    if line and key.startswith("home_over_"):
        return f"{home} over {line} goals"
    if line and key.startswith("away_over_"):
        return f"{away} over {line} goals"
    if line and key.startswith("ah_home_minus_"):
        return f"{home} handicap -{line}"
    if line and key.startswith("ah_away_plus_"):
        return f"{away} handicap +{line}"
    return key.replace("_", " ").capitalize()


def _pick_row(
    fixture: Fixture,
    analysis: Dict[str, Any],
    key: str,
    probability: float,
    entry: Dict[str, Any],
    confidence: Dict[str, Any],
    now: datetime,
) -> Dict[str, Any]:
    home, away = fixture.home_team, fixture.away_team
    price = float(entry["price"])
    captured = entry.get("captured_at")
    quality = analysis.get("data_quality") or {}
    blend = analysis.get("probability_blend") or {}

    model_vs_market = None
    if blend.get("applied"):
        model_vs_market = {
            "weight": blend.get("weight"),
            "model": (blend.get("model") or {}).get(key),
            "market": (blend.get("market") or {}).get(key),
            "competition": blend.get("competition"),
            "verdict": blend.get("verdict"),
        }

    supporting = [
        {
            "market_key": row["market"],
            "label": _market_label(row["market"], home, away),
            "probability": round(float(row["probability"]), 4),
        }
        for row in analysis.get("qualifying_markets") or []
        if row.get("market") != key
    ][:4]

    ev = calculate_expected_value(probability, price)

    return {
        "fixture_id": fixture.id,
        "match": f"{home} vs {away}",
        "competition": fixture.competition,
        "country": fixture.country,
        "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
        "kickoff_eat": _format_eat(fixture.kickoff_utc),
        "market_key": key,
        "market_type": "Match winner",
        "selection": _RESULT_LABELS[key](home, away),
        "probability": round(probability, 4),
        "market_probability": model_vs_market["market"] if model_vs_market else None,
        "model_probability": model_vs_market["model"] if model_vs_market else None,
        "odds": round(price, 2),
        "implied_probability": ev["implied_probability"],
        "expected_value_percent": ev["expected_value_percent"],
        "edge": ev["edge"],
        "bookmaker": entry.get("bookmaker"),
        "source": entry.get("source"),
        "source_url": entry.get("source_url"),
        "captured_at": captured,
        "price_age_hours": _price_age_hours(captured, now),
        "confidence": confidence["confidence"],
        "confidence_reasons": confidence["reasons"],
        "data_quality": {
            "grade": quality.get("grade"),
            "score": quality.get("score"),
            "eligible_for_analysis": quality.get("eligible_for_analysis"),
        },
        "expected_goals": analysis.get("estimated_total_goals"),
        "lambda_source": analysis.get("lambda_source"),
        "model_vs_market": model_vs_market,
        "also_qualifying": supporting,
    }


def build_top_picks(
    db: Session,
    limit: int = TOP_PICKS,
    target_date: Optional[str] = None,
    now: Optional[datetime] = None,
    max_fixtures: int = DEFAULT_MAX_FIXTURES,
) -> Dict[str, Any]:
    """Rank today's priced fixtures by modelled match-winner probability."""
    now = now or datetime.now(timezone.utc)
    target_date = target_date or now.astimezone(EAT_TZ).strftime("%Y-%m-%d")
    limit = max(1, min(TOP_PICKS, int(limit)))

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .where(Fixture.home_odds.isnot(None))
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
        "analysis_failed": 0,
        "below_gate": 0,
        "unattributed_price": 0,
        "low_data_confidence": 0,
        "duplicate_match": 0,
    }
    candidates: List[Tuple[Dict[str, Any], Fixture]] = []

    for fixture in fixtures:
        if is_placeholder_team(fixture.home_team) or is_placeholder_team(fixture.away_team):
            rejected["placeholder_fixture"] += 1
            continue
        attribution = _attribution(fixture)
        if not attribution:
            rejected["no_priced_market"] += 1
            continue
        try:
            analysis = analyze_fixture(fixture, db=db)
        except Exception:
            rejected["analysis_failed"] += 1
            continue

        markets = analysis.get("all_markets") or {}
        key, probability = max(
            ((name, float(markets.get(name) or 0.0)) for name in RESULT_MARKETS),
            key=lambda item: item[1],
        )
        if probability < QUALIFICATION_THRESHOLD:
            rejected["below_gate"] += 1
            continue

        entry = attribution.get(key)
        if not entry or float(entry.get("price") or 0.0) <= 1.0:
            rejected["unattributed_price"] += 1
            continue

        confidence = data_confidence(
            SimpleNamespace(captured_at=entry.get("captured_at")),
            {
                "home_lambda": (analysis.get("estimated_lambdas") or {}).get("home_lambda"),
                "away_lambda": (analysis.get("estimated_lambdas") or {}).get("away_lambda"),
                "lambda_source": analysis.get("lambda_source"),
            },
            now=now,
        )
        if confidence["confidence"] < MIN_DATA_CONFIDENCE:
            rejected["low_data_confidence"] += 1
            continue

        candidates.append(
            (_pick_row(fixture, analysis, key, probability, entry, confidence, now), fixture)
        )

    candidates.sort(
        key=lambda item: (item[0]["probability"], item[0]["confidence"]), reverse=True
    )

    picks: List[Dict[str, Any]] = []
    kept: List[Fixture] = []
    for pick, fixture in candidates:
        if any(same_match(fixture, other) for other in kept):
            rejected["duplicate_match"] += 1
            continue
        kept.append(fixture)
        pick["rank"] = len(picks) + 1
        picks.append(pick)
        if len(picks) >= limit:
            break

    return {
        "target_date": target_date,
        "threshold": QUALIFICATION_THRESHOLD,
        "minimum_confidence": MIN_DATA_CONFIDENCE,
        "params": {"limit": limit, "result_markets": list(RESULT_MARKETS)},
        "fixtures_scanned": len(fixtures),
        "count": len(picks),
        "picks": picks,
        "rejected": rejected,
        "message": (
            NO_QUALIFIED_SELECTIONS
            if not picks
            else f"{len(picks)} match{'es' if len(picks) != 1 else ''} clear the "
            f"{QUALIFICATION_THRESHOLD:.0%} gate with attributed prices."
        ),
        "note": (
            "Ranked by the model's win probability for the outright result. "
            "Supporting markets that also clear the gate are listed inside each card. "
            "Paper analysis only; the 85% gate is never lowered to fill the list."
        ),
    }
