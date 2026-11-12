"""Universal market catalogue, discovery and analysis endpoints."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.market_engine import analyse_fixture
from app.market_engine.catalogue import catalogue_payload, families_by_category
from app.market_engine.discovery import discover_from_market_odds
from app.market_engine.qualification import QUALIFICATION_THRESHOLD, NO_QUALIFIED_SELECTIONS
from app.models.fixture import Fixture

router = APIRouter(prefix="/markets", tags=["markets"])


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
