"""Daily 2–3 team combos from real bookmaker prices.

The strategy is deliberately simple so it can be replayed on history:

* one selection per fixture, from the full-time 1X2 market;
* probability is the *de-vigged market probability* — the bookmaker's own
  view with its margin removed — not a model estimate;
* a leg qualifies at 75% by default. That tier is deliberately labelled as
  below the published 85% qualified gate; it never relaxes that gate;
* the combo price is the product of the real prices used, and every leg keeps
  its bookmaker, feed and capture time. A price without attribution is
  excluded rather than guessed.
"""
from __future__ import annotations

import itertools
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.learning import _logit, _sigmoid
from app.betting.pricing import devigged_probabilities, fixture_market_odds
from app.data_sources.fixtures import clean_team_name
from app.market_engine.qualification import (
    NO_QUALIFIED_SELECTIONS,
    QUALIFICATION_THRESHOLD,
)
from app.models.fixture import Fixture

EAT_TZ = timezone(timedelta(hours=3), name="EAT")
MARKET_KEYS = ("home_win", "draw", "away_win")

DEFAULT_TIER = 0.75
COMBO_SIZES = (2, 3)
MAX_CANDIDATE_LEGS = 15
MAX_COMBOS_PER_SIZE = 3


def learning_path():
    from pathlib import Path

    return Path(__file__).resolve().parents[3] / "data" / "combo_learning.json"


def load_learning() -> Optional[Dict[str, Any]]:
    path = learning_path()
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not report.get("applied"):
        return None
    if not isinstance(report.get("a"), (int, float)):
        return None
    return report


def calibrate(probability: float, learning: Optional[Dict[str, Any]]) -> float:
    """Apply the validated Platt correction, if one exists."""
    if not learning:
        return probability
    p = min(1.0 - 1e-6, max(1e-6, probability))
    corrected = _sigmoid(float(learning["a"]) * _logit(p) + float(learning.get("b", 0.0)))
    return min(1.0 - 1e-6, max(1e-6, corrected))


def as_utc(value: datetime) -> datetime:
    """SQLite drops tzinfo; every kickoff column is stored in UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _format_eat(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return as_utc(value).astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")


SAME_MATCH_WINDOW = timedelta(hours=3)


def is_placeholder_team(name: str) -> bool:
    """Feed placeholder rows ("Home (Special bets)") are not real fixtures."""
    text = str(name or "").strip().lower()
    return text in {"home", "away"} or text.startswith("home (") or text.startswith("away (")


def _team_keys(fixture: Fixture) -> Tuple[str, str]:
    return clean_team_name(fixture.home_team), clean_team_name(fixture.away_team)


def same_match(left: Fixture, right: Fixture) -> bool:
    """Whether two rows describe the same fixture under different feed names.

    Feeds spell national teams differently (Czechia vs Czech Republic), so a
    shared normalized team plus a close kickoff is treated as one match: the
    same team cannot play twice within three hours, and pricing both rows as
    independent legs would multiply one event with itself.
    """
    if left.id is not None and left.id == right.id:
        return True
    if not left.kickoff_utc or not right.kickoff_utc:
        return False
    if abs(as_utc(left.kickoff_utc) - as_utc(right.kickoff_utc)) > SAME_MATCH_WINDOW:
        return False
    return bool(set(_team_keys(left)) & set(_team_keys(right)))


def _attribution(fixture: Fixture) -> Dict[str, Dict[str, Any]]:
    raw = getattr(fixture, "market_odds_json", None)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {
        key: value
        for key, value in (payload or {}).items()
        if isinstance(value, dict) and isinstance(value.get("price"), (int, float))
    }


def _selection_label(key: str, home: str, away: str) -> Tuple[str, str]:
    if key == "home_win":
        return "Match winner", f"{home} to win"
    if key == "away_win":
        return "Match winner", f"{away} to win"
    return "Match winner", "Draw"


def _price_age_hours(captured: Optional[str], now: datetime) -> Optional[float]:
    if not captured:
        return None
    try:
        stamp = datetime.fromisoformat(str(captured).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return round((now - stamp).total_seconds() / 3600.0, 2)


def _leg(
    fixture: Fixture,
    key: str,
    price: float,
    fair: float,
    entry: Dict[str, Any],
    learning: Optional[Dict[str, Any]],
    now: datetime,
) -> Dict[str, Any]:
    probability = calibrate(fair, learning)
    market_type, selection = _selection_label(key, fixture.home_team, fixture.away_team)
    captured = entry.get("captured_at")
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
        "market_probability": round(fair, 4),
        "calibrated": bool(learning),
        "bookmaker": entry.get("bookmaker"),
        "source": entry.get("source"),
        "source_url": entry.get("source_url"),
        "captured_at": captured,
        "price_age_hours": _price_age_hours(captured, now),
    }


def _combo(legs: List[Dict[str, Any]]) -> Dict[str, Any]:
    probability = 1.0
    odds = 1.0
    for leg in legs:
        probability *= leg["probability"]
        odds *= leg["odds"]
    odds = round(odds, 2)
    probability = round(probability, 4)
    return {
        "size": len(legs),
        "legs": legs,
        "combined_probability": probability,
        "total_odds": odds,
        "implied_probability": round(1.0 / odds, 4) if odds > 1.0 else None,
        "expected_value_percent": round((probability * odds - 1.0) * 100, 2),
        "min_leg_probability": min(leg["probability"] for leg in legs),
    }


def build_daily_combos(
    db: Session,
    target_date: Optional[str] = None,
    competition: Optional[str] = None,
    min_leg_probability: float = DEFAULT_TIER,
    max_price_age_hours: Optional[float] = None,
    limit_per_size: int = MAX_COMBOS_PER_SIZE,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Analyse today's priced fixtures into 2- and 3-leg high-probability combos."""
    now = now or datetime.now(timezone.utc)
    target_date = target_date or now.astimezone(EAT_TZ).strftime("%Y-%m-%d")
    tier = max(0.5, min(0.99, float(min_leg_probability)))

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc > now)
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))
    fixtures = [
        fixture
        for fixture in db.scalars(query).all()
        if fixture.kickoff_utc
        and as_utc(fixture.kickoff_utc).astimezone(EAT_TZ).strftime("%Y-%m-%d") == target_date
    ]

    learning = load_learning()
    legs: List[Dict[str, Any]] = []
    rejected = {
        "placeholder_fixture": 0,
        "no_priced_market": 0,
        "missing_1x2": 0,
        "unattributed_price": 0,
        "stale_price": 0,
        "below_probability_floor": 0,
        "duplicate_match": 0,
    }

    for fixture in fixtures:
        if is_placeholder_team(fixture.home_team) or is_placeholder_team(fixture.away_team):
            rejected["placeholder_fixture"] += 1
            continue
        market_odds = fixture_market_odds(fixture)
        if not market_odds:
            rejected["no_priced_market"] += 1
            continue
        if not all(key in market_odds for key in MARKET_KEYS):
            rejected["missing_1x2"] += 1
            continue

        attribution = _attribution(fixture)
        if not all(key in attribution for key in MARKET_KEYS):
            # Legacy flat prices have no bookmaker or capture time; they cannot
            # be presented as a bookmaker price for this slate.
            rejected["unattributed_price"] += 1
            continue

        entries = {key: attribution[key] for key in MARKET_KEYS}
        if max_price_age_hours is not None:
            ages = [_price_age_hours(entry.get("captured_at"), now) for entry in entries.values()]
            if any(age is None or age > max_price_age_hours for age in ages):
                rejected["stale_price"] += 1
                continue

        # De-vig and price the exact attributed trio, so the displayed price is
        # always the price whose bookmaker and capture time are shown.
        prices = {key: float(entries[key]["price"]) for key in MARKET_KEYS}
        _, fair = devigged_probabilities(prices)
        best_key = max(MARKET_KEYS, key=lambda key: fair.get(key, 0.0))
        probability = calibrate(fair.get(best_key, 0.0), learning)
        if probability < tier:
            rejected["below_probability_floor"] += 1
            continue
        leg = _leg(
            fixture,
            best_key,
            prices[best_key],
            fair.get(best_key, 0.0),
            entries[best_key],
            learning,
            now,
        )
        leg["_fixture"] = fixture
        legs.append(leg)

    qualified_count = len(legs)
    legs.sort(key=lambda leg: (leg["probability"], -leg["odds"]), reverse=True)
    candidates: List[Dict[str, Any]] = []
    for leg in legs:
        if any(same_match(leg["_fixture"], kept["_fixture"]) for kept in candidates):
            rejected["duplicate_match"] += 1
            continue
        candidates.append(leg)
        if len(candidates) >= MAX_CANDIDATE_LEGS:
            break
    for leg in candidates:
        leg.pop("_fixture", None)
    # The response lists the legs that can actually be combined: one per match.
    legs = candidates

    combos: Dict[str, List[Dict[str, Any]]] = {}
    for size in COMBO_SIZES:
        found: List[Dict[str, Any]] = []
        for group in itertools.combinations(candidates, size):
            if len({leg["fixture_id"] for leg in group}) < size:
                continue
            found.append(_combo(list(group)))
        found.sort(key=lambda row: (row["combined_probability"], row["total_odds"]), reverse=True)
        combos[f"{size}_leg"] = found[:limit_per_size]

    best = None
    for size in COMBO_SIZES:
        rows = combos.get(f"{size}_leg") or []
        if rows and (best is None or rows[0]["combined_probability"] > best["combined_probability"]):
            best = rows[0]

    stale_warnings = [
        leg
        for leg in legs
        if leg.get("price_age_hours") is not None and leg["price_age_hours"] > 12
    ]

    report = {
        "target_date": target_date,
        "matchday_label": datetime.strptime(target_date, "%Y-%m-%d")
        .replace(tzinfo=EAT_TZ)
        .strftime("%A, %d %b %Y"),
        "generated_at": now.isoformat(),
        "params": {
            "min_leg_probability": tier,
            "combo_sizes": list(COMBO_SIZES),
            "max_price_age_hours": max_price_age_hours,
            "competition": competition,
        },
        "tier": {
            "label": (
                f"{tier:.0%} market-probability tier"
                if tier < QUALIFICATION_THRESHOLD
                else "85% gate tier"
            ),
            "probability_source": "de-vigged 1X2 market price",
            "published_gate": QUALIFICATION_THRESHOLD,
            "below_published_gate": tier < QUALIFICATION_THRESHOLD,
            "note": (
                "Legs are priced from the bookmaker's own de-vigged 1X2 market. "
                "A leg below the published 85% qualified gate is labelled "
                "exploratory; the gate itself is unchanged."
            ),
        },
        "scope": {
            "fixtures_on_slate": len(fixtures),
            "fixtures_priced": sum(1 for f in fixtures if fixture_market_odds(f)),
            "qualified_legs": qualified_count,
            "candidate_legs": len(legs),
            "combos_considered": len(COMBO_SIZES),
            "rejected": rejected,
        },
        "learning": (
            {
                "applied": True,
                "a": learning.get("a"),
                "b": learning.get("b"),
                "n": learning.get("n"),
                "source": learning.get("generated_at"),
            }
            if learning
            else {"applied": False}
        ),
        "legs": legs,
        "combos": combos,
        "best": best,
        "stale_legs": [
            {"match": leg["match"], "price_age_hours": leg["price_age_hours"]}
            for leg in stale_warnings
        ],
        "message": NO_QUALIFIED_SELECTIONS if not best else
        f"{len(legs)} legs at or above {tier:.0%}; best combo {best['combined_probability']:.1%}",
        "note": (
            "Paper analysis only. The combo price is the product of the real "
            "prices shown; the combined probability is the product of the "
            "de-vigged market probabilities, so it is not a guarantee."
        ),
    }
    return report
