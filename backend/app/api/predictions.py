"""Prediction history endpoints. Predictions are append-only."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictions import Prediction, Selection

router = APIRouter(prefix="/predictions", tags=["predictions"])


class PredictionIn(BaseModel):
    fixture_id: Optional[int] = None
    family: str
    selection: str
    line: Optional[float] = None
    probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fair_odds: Optional[float] = None
    ev: Optional[float] = None
    qualified: bool = False
    qualification_reason: Optional[str] = None
    data_quality: Optional[str] = None


def _row(prediction: Prediction) -> Dict[str, Any]:
    return {
        "id": prediction.id,
        "fixture_id": prediction.fixture_id,
        "family": prediction.family,
        "selection": prediction.selection,
        "line": prediction.line,
        "probability": prediction.probability,
        "confidence": prediction.confidence,
        "fair_odds": prediction.fair_odds,
        "ev": prediction.ev,
        "qualified": prediction.qualified,
        "qualification_reason": prediction.qualification_reason,
        "created_at": prediction.created_at.isoformat() if prediction.created_at else None,
    }


@router.get("")
def list_predictions(
    fixture_id: Optional[int] = None,
    qualified_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    statement = select(Prediction).order_by(Prediction.created_at.desc()).limit(limit)
    if fixture_id is not None:
        statement = statement.where(Prediction.fixture_id == fixture_id)
    if qualified_only:
        statement = statement.where(Prediction.qualified.is_(True))
    rows = db.scalars(statement).all()
    return {"count": len(rows), "predictions": [_row(row) for row in rows]}


@router.post("/record")
def record_prediction(payload: PredictionIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    prediction = Prediction(
        fixture_id=payload.fixture_id,
        family=payload.family,
        selection=payload.selection,
        line=payload.line,
        probability=payload.probability,
        confidence=payload.confidence,
        fair_odds=payload.fair_odds,
        ev=payload.ev,
        qualified=payload.qualified and (payload.probability or 0.0) >= 0.85,
        qualification_reason=payload.qualification_reason,
        data_quality=payload.data_quality,
        created_at=datetime.now(timezone.utc),
    )
    db.add(prediction)
    db.flush()
    if payload.fixture_id is not None:
        db.add(
            Selection(
                prediction_id=prediction.id,
                fixture_id=payload.fixture_id,
                family=payload.family,
                selection=payload.selection,
                line=payload.line,
                probability=payload.probability or 0.0,
                confidence=payload.confidence,
                qualified=prediction.qualified,
            )
        )
    db.commit()
    db.refresh(prediction)
    return _row(prediction)


@router.get("/fixtures/{fixture_id}/history")
def fixture_prediction_history(fixture_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.scalars(
        select(Prediction)
        .where(Prediction.fixture_id == fixture_id)
        .order_by(Prediction.created_at.asc())
    ).all()
    return {"fixture_id": fixture_id, "count": len(rows), "versions": [_row(row) for row in rows]}


@router.get("/selections")
def list_selections(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.scalars(select(Selection).order_by(Selection.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "prediction_id": row.prediction_id,
            "fixture_id": row.fixture_id,
            "family": row.family,
            "selection": row.selection,
            "line": row.line,
            "probability": row.probability,
            "confidence": row.confidence,
            "price": row.price,
            "bookmaker": row.bookmaker,
            "ev": row.ev,
            "qualified": row.qualified,
        }
        for row in rows
    ]
