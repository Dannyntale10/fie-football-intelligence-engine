"""Paper-only bankroll arithmetic: what a target really requires."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.betting.bankroll import (
    DEFAULT_MAX_DAILY_EXPOSURE_PERCENT,
    DEFAULT_MAX_STAKE_PERCENT,
    daily_exposure,
    plan as build_plan,
    preset_plans,
)

router = APIRouter(prefix="/bankroll", tags=["bankroll"])


@router.get("/plan")
def bankroll_plan(
    stake: float = Query(..., gt=0, description="Paper stake"),
    odds: float = Query(..., gt=1.0, description="Total decimal odds"),
    bankroll: Optional[float] = Query(None, gt=0),
    target_profit: Optional[float] = Query(None, ge=0),
    slips_per_day: int = Query(1, ge=1, le=50),
    max_stake_percent: float = Query(DEFAULT_MAX_STAKE_PERCENT, gt=0, le=100),
    max_daily_exposure_percent: float = Query(
        DEFAULT_MAX_DAILY_EXPOSURE_PERCENT, gt=0, le=100
    ),
    currency: str = Query("UGX", max_length=10),
) -> Dict[str, Any]:
    result = build_plan(
        stake,
        odds,
        bankroll=bankroll,
        target_profit=target_profit,
        currency=currency,
        max_stake_percent=max_stake_percent,
    )
    result["daily_exposure"] = (
        daily_exposure(
            slips_per_day,
            stake,
            bankroll=bankroll,
            max_daily_exposure_percent=max_daily_exposure_percent,
            currency=currency,
        )
        if bankroll
        else None
    )
    return result


@router.get("/presets")
def bankroll_presets(
    bankroll: float = Query(1000.0, gt=0),
) -> Dict[str, Any]:
    return preset_plans(bankroll=bankroll)
