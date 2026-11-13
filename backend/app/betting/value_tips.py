"""Value tips on markets beyond 1X2.

The daily board leans on 1X2 because that is where the model can be checked
against a de-vigged market. The same test works for totals, draw-no-bet and
Asian handicap: one real price, a de-vigged market probability from the same
book's market, and the difference in probability points.

A tip is emitted only when a price exists and the model/market gap falls in
the band we trust. A gap beyond the ceiling is nearly always stale ratings,
not free money, so it is dropped rather than promoted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market_engine.catalogue import ENGINE_VERSION, get_definition
from app.market_engine.market_analysis import analyse_fixture_markets
from app.market_engine.qualification import NO_QUALIFIED_SELECTIONS
from app.models.fixture import Fixture

DEFAULT_FAMILIES = ("total_goals", "draw_no_bet", "asian_handicap")


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _wanted_families(families: Optional[List[str]]) -> List[str]:
    wanted = list(families) if families else list(DEFAULT_FAMILIES)
    for family in wanted:
        definition = get_definition(family)
        if definition is None or not definition.model:
            raise ValueError(f"Market family '{family}' cannot be priced, so it cannot carry a tip.")
    return wanted


def value_tips(
    db: Session,
    days_ahead: int = 3,
    min_edge_percent: float = 3.0,
    max_edge_percent: float = 20.0,
    families: Optional[List[str]] = None,
    per_fixture: int = 2,
    limit: int = 20,
    fixtures_limit: int = 150,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Rank +EV selections on the requested families across the upcoming slate."""
    moment = _aware(now) or datetime.now(timezone.utc)
    wanted = set(_wanted_families(families))

    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc >= moment)
        .where(Fixture.kickoff_utc <= moment + timedelta(days=days_ahead))
        .where(Fixture.market_odds_json.isnot(None))
        .order_by(Fixture.kickoff_utc.asc())
        .limit(fixtures_limit)
    ).all()

    tips: List[Dict[str, Any]] = []
    with_markets = 0
    failed = 0
    dropped = {
        "below_min_edge": 0,
        "above_max_edge": 0,
        "no_market_probability": 0,
        "non_positive_ev": 0,
    }

    for fixture in fixtures:
        try:
            analysis = analyse_fixture_markets(fixture, db=db)
        except Exception:
            failed += 1
            continue

        if analysis.get("discovered_count"):
            with_markets += 1

        for row in analysis.get("selections") or []:
            if row.get("family") not in wanted:
                continue
            if row.get("devigged_probability") is None:
                dropped["no_market_probability"] += 1
            elif row.get("ev") is None or float(row["ev"]) <= 0.0:
                dropped["non_positive_ev"] += 1

        candidates = (analysis.get("value_detection") or {}).get("value_selections") or []
        for entry in candidates:
            if entry.get("family") not in wanted:
                continue
            if entry.get("market_probability") is None:
                continue
            edge = float(entry.get("edge") or 0.0)
            if edge < min_edge_percent / 100.0:
                dropped["below_min_edge"] += 1
                continue
            if edge > max_edge_percent / 100.0:
                dropped["above_max_edge"] += 1
                continue
            tips.append({
                "fixture_id": fixture.id,
                "match": f"{fixture.home_team} vs {fixture.away_team}",
                "competition": fixture.competition,
                "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
                "family": entry.get("family"),
                "label": entry.get("label"),
                "selection": entry.get("selection"),
                "line": entry.get("line"),
                "bookmaker": entry.get("bookmaker"),
                "source": entry.get("source"),
                "captured_at": entry.get("captured_at"),
                "price": float(entry.get("price")),
                "probability": round(float(entry.get("probability")), 4),
                "market_probability": round(float(entry["market_probability"]), 4),
                "edge_percent": round(edge * 100.0, 2),
                "ev_percent": round(float(entry.get("ev") or 0.0) * 100.0, 2),
            })

    per_fixture_rows: Dict[int, List[Dict[str, Any]]] = {}
    for tip in tips:
        per_fixture_rows.setdefault(tip["fixture_id"], []).append(tip)

    kept: List[Dict[str, Any]] = []
    for rows in per_fixture_rows.values():
        rows.sort(key=lambda row: (row["edge_percent"], row["ev_percent"]), reverse=True)
        kept.extend(rows[:per_fixture])

    kept.sort(key=lambda row: (row["edge_percent"], row["ev_percent"]), reverse=True)
    kept = kept[:limit]

    return {
        "engine_version": ENGINE_VERSION,
        "status": "ok" if kept else "no_value",
        "message": None if kept else NO_QUALIFIED_SELECTIONS,
        "days_ahead": days_ahead,
        "families": sorted(wanted),
        "fixtures_scanned": len(fixtures),
        "fixtures_with_markets": with_markets,
        "failed_fixtures": failed,
        "thresholds": {
            "min_edge_percent": min_edge_percent,
            "max_edge_percent": max_edge_percent,
            "per_fixture": per_fixture,
        },
        "dropped": dropped,
        "tips": kept,
    }
