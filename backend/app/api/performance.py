"""Performance analytics from real (non-synthetic) tracked slips."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.betting.result_tracker import calculate_prediction_accuracy
from app.betting.settlement import _is_synthetic_bet
from app.database import get_db
from app.models.bet import TrackedBet

router = APIRouter(prefix="/performance", tags=["performance"])


def _real_bets(db: Session, days: Optional[int]) -> list[TrackedBet]:
    statement = select(TrackedBet).order_by(TrackedBet.placed_at.desc())
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(TrackedBet.placed_at >= cutoff)
    return [bet for bet in db.scalars(statement).all() if not _is_synthetic_bet(bet)]


@router.get("/summary")
def performance_summary(
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    bets = _real_bets(db, days)
    settled = [bet for bet in bets if bet.status in ("WON", "LOST", "VOID")]
    wins = sum(1 for bet in settled if bet.status == "WON")
    losses = sum(1 for bet in settled if bet.status == "LOST")
    voids = sum(1 for bet in settled if bet.status == "VOID")
    stake = round(sum(bet.stake_amount or 0.0 for bet in settled), 2)
    profit = round(sum(bet.profit_loss or 0.0 for bet in settled), 2)
    decided = wins + losses

    per_slip = {}
    for bet in settled:
        bucket = per_slip.setdefault(
            bet.slip_type,
            {"bets": 0, "wins": 0, "losses": 0, "voids": 0, "stake": 0.0, "profit": 0.0},
        )
        bucket["bets"] += 1
        bucket["stake"] += bet.stake_amount or 0.0
        bucket["profit"] += bet.profit_loss or 0.0
        bucket[{"WON": "wins", "LOST": "losses", "VOID": "voids"}[bet.status]] += 1
    for bucket in per_slip.values():
        bucket["roi"] = round(bucket["profit"] / bucket["stake"], 4) if bucket["stake"] else None
        bucket["accuracy"] = round(bucket["wins"] / (bucket["wins"] + bucket["losses"]), 4) if bucket["wins"] + bucket["losses"] else None

    return {
        "scope": "real slips only; synthetic test data excluded",
        "period_days": days,
        "bets": len(bets),
        "settled": len(settled),
        "pending": len(bets) - len(settled),
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "accuracy": round(wins / decided, 4) if decided else None,
        "stake": stake,
        "profit": profit,
        "roi": round(profit / stake, 4) if stake else None,
        "yield": round(profit / stake, 4) if stake else None,
        "by_slip_type": per_slip,
        "note": "Paper results only while the model is uncalibrated; no profit claim is made.",
    }


@router.get("/predictions")
def prediction_accuracy(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    return calculate_prediction_accuracy(db, days=days)
