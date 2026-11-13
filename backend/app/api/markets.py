"""Universal market catalogue, discovery and analysis endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.betting.high_odds import MAX_SLIPS as HIGH_ODDS_SLIPS
from app.betting.high_odds import build_high_odds
from app.betting.top_picks import TOP_PICKS, build_top_picks
from app.market_engine import analyse_fixture
from app.market_engine.catalogue import catalogue_payload, families_by_category
from app.market_engine.discovery import discover_from_market_odds
from app.market_engine.qualification import QUALIFICATION_THRESHOLD, NO_QUALIFIED_SELECTIONS
from app.models.fixture import Fixture

router = APIRouter(prefix="/markets", tags=["markets"])


@router.get("/top-picks")
def top_picks(
    target_date: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Matchday in EAT, default today"
    ),
    limit: int = Query(TOP_PICKS, ge=1, le=TOP_PICKS),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """The five best analysed matches whose outright result clears the 85% gate."""
    return build_top_picks(db, limit=limit, target_date=target_date)


@router.get("/high-odds")
def high_odds_slips(
    target_date: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Matchday in EAT, default today"
    ),
    limit: int = Query(HIGH_ODDS_SLIPS, ge=1, le=HIGH_ODDS_SLIPS),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Near-6.00 slips built only from legs whose blended probability beats the price."""
    return build_high_odds(db, limit=limit, target_date=target_date)


@router.get("/qualifying")
def qualifying_markets(
    target_date: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Matchday in EAT, default today"
    ),
    max_fixtures: int = Query(40, ge=1, le=200, description="Fixtures scanned, kickoff order"),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Markets clearing the published 85% gate, scanned from scheduled fixtures."""
    from app.analytics.probabilities import analyze_fixture
    from app.betting.daily_combos import EAT_TZ, as_utc, is_placeholder_team

    now = datetime.now(timezone.utc)
    day = target_date or now.astimezone(EAT_TZ).strftime("%Y-%m-%d")
    fixtures = [
        fixture
        for fixture in db.scalars(
            select(Fixture)
            .where(Fixture.status == "scheduled", Fixture.kickoff_utc > now)
            .where(Fixture.home_odds.isnot(None))
            .order_by(Fixture.kickoff_utc)
        ).all()
        if fixture.kickoff_utc
        and as_utc(fixture.kickoff_utc).astimezone(EAT_TZ).strftime("%Y-%m-%d") == day
        and not is_placeholder_team(fixture.home_team)
        and not is_placeholder_team(fixture.away_team)
    ][:max_fixtures]

    rows: List[Dict[str, Any]] = []
    for fixture in fixtures:
        try:
            analysis = analyze_fixture(fixture, db=db)
        except Exception:
            continue
        for market in analysis.get("qualifying_markets", []):
            rows.append(
                {
                    "fixture_id": fixture.id,
                    "match": f"{fixture.home_team} vs {fixture.away_team}",
                    "competition": fixture.competition,
                    "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
                    "kickoff_eat": as_utc(fixture.kickoff_utc).astimezone(EAT_TZ).strftime(
                        "%a %d %b, %H:%M EAT"
                    ),
                    "market": market["market"],
                    "probability": market["probability"],
                }
            )
        if len(rows) >= limit:
            break

    rows.sort(key=lambda row: row["probability"], reverse=True)
    rows = rows[:limit]
    return {
        "target_date": day,
        "threshold": QUALIFICATION_THRESHOLD,
        "fixtures_scanned": len(fixtures),
        "count": len(rows),
        "qualifying": rows,
        "message": (
            NO_QUALIFIED_SELECTIONS
            if not rows
            else f"{len(rows)} markets clear the {QUALIFICATION_THRESHOLD:.0%} gate."
        ),
    }


@router.get("/catalogue")
def market_catalogue() -> Dict[str, Any]:
    return {
        "engine": "market-engine-1",
        "qualification_threshold": QUALIFICATION_THRESHOLD,
        "families": catalogue_payload(),
        "by_category": families_by_category(),
        "note": "Only markets actually priced by a source are analysed; the catalogue is not a claim of coverage.",
    }


@router.get("/fixtures/{fixture_id}/discovery")
def fixture_discovery(fixture_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return discover_from_market_odds(fixture).as_dict()


@router.get("/fixtures/{fixture_id}/analysis")
def fixture_market_analysis(fixture_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return analyse_fixture(fixture, db=db)


@router.get("/qualification-policy")
def qualification_policy() -> Dict[str, Any]:
    return {
        "minimum_probability": QUALIFICATION_THRESHOLD,
        "minimum_confidence": 0.60,
        "message_when_empty": NO_QUALIFIED_SELECTIONS,
        "probabilities_and_confidence_are_separate": True,
        "never_lowered_to_fill_a_report": True,
    }
