"""Daily market-probability combos and their historical replay."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics.combo_backtest import (
    SWEEP_TIERS,
    load_report,
    load_sweep,
    run_combo_backtest,
    run_tier_sweep,
    save_learning,
    save_report,
    save_sweep,
)
from app.betting.daily_combos import (
    DEFAULT_TIER,
    MAX_COMBOS_PER_SIZE,
    build_daily_combos,
)
from app.database import get_db

router = APIRouter(prefix="/combos", tags=["combos"])


@router.get("/daily")
def daily_combos(
    target_date: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Matchday in EAT, default today"
    ),
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    min_leg_probability: float = Query(
        DEFAULT_TIER,
        ge=0.50,
        le=0.99,
        description="Leg ceiling. Below 0.85 the response is labelled exploratory; the published gate is unchanged.",
    ),
    max_price_age_hours: Optional[float] = Query(
        None, ge=0.1, le=168.0, description="Reject legs whose price is older than this"
    ),
    limit_per_size: int = Query(MAX_COMBOS_PER_SIZE, ge=1, le=10),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Two- and three-team combos from today's attributed bookmaker prices."""
    return build_daily_combos(
        db,
        target_date=target_date,
        competition=competition,
        min_leg_probability=min_leg_probability,
        max_price_age_hours=max_price_age_hours,
        limit_per_size=limit_per_size,
    )


@router.post("/backtest")
def run_backtest(
    competition: Optional[str] = Query(None),
    min_leg_probability: float = Query(DEFAULT_TIER, ge=0.50, le=0.99),
    stake: float = Query(10.0, ge=1.0, le=1000.0),
    limit_per_size: int = Query(
        1, ge=1, le=3, description="Tickets settled per size per matchday (1 = the top-ranked combo)"
    ),
    price_mode: str = Query("flat", pattern="^(flat|best)$"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Replay the combo rule on settled results and refit its calibration.

    Explicit action: it replays every settled fixture. The fitted calibration is
    stored only when the held-out slice shows a real improvement; otherwise an
    inactive record replaces any stale one.
    """
    try:
        report = run_combo_backtest(
            db,
            competition=competition,
            min_leg_probability=min_leg_probability,
            stake=stake,
            limit_per_size=limit_per_size,
            price_mode=price_mode,
        )
        if report.get("status") == "ok":
            save_report(report)
            report["learning_saved"] = save_learning(report)
        return report
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Combo backtest error: {exc}") from exc


@router.get("/backtest")
def saved_backtest() -> Dict[str, Any]:
    """Return the last saved historical replay (cheap, no recompute)."""
    report = load_report()
    if not report:
        raise HTTPException(
            status_code=404,
            detail="No combo backtest yet. POST /combos/backtest to run it on settled history.",
        )
    return report


@router.post("/backtest/sweep")
def run_backtest_sweep(
    competition: Optional[str] = Query(None),
    tiers: Optional[str] = Query(
        None,
        description=(
            "Comma-separated leg-probability tiers, e.g. 0.65,0.70,0.75,0.80,0.85. "
            "Defaults to the standard five-point sweep."
        ),
    ),
    stake: float = Query(10.0, ge=1.0, le=1000.0),
    limit_per_size: int = Query(
        1, ge=1, le=3, description="Tickets settled per size per matchday"
    ),
    price_mode: str = Query("flat", pattern="^(flat|best)$"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Replay several probability tiers and report which one training history supports.

    The recommended tier is chosen on training matchdays only; the holdout is
    reported afterwards as confirmation. Explicit action: it replays every
    settled fixture once per tier.
    """
    parsed: Optional[List[float]] = None
    if tiers:
        try:
            parsed = [float(part) for part in tiers.split(",") if part.strip()]
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="tiers must be comma-separated numbers"
            ) from exc
        if not parsed or len(parsed) > 8 or any(not 0.50 <= tier <= 0.99 for tier in parsed):
            raise HTTPException(
                status_code=400,
                detail="Provide between 1 and 8 tier probabilities in [0.50, 0.99]",
            )
    try:
        report = run_tier_sweep(
            db,
            competition=competition,
            tiers=parsed or SWEEP_TIERS,
            stake=stake,
            limit_per_size=limit_per_size,
            price_mode=price_mode,
        )
        if report.get("status") == "ok":
            save_sweep(report)
        return report
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Tier sweep error: {exc}") from exc


@router.get("/backtest/sweep")
def saved_sweep() -> Dict[str, Any]:
    """Return the last saved tier sweep (cheap, no recompute)."""
    report = load_sweep()
    if not report:
        raise HTTPException(
            status_code=404,
            detail=(
                "No tier sweep yet. POST /combos/backtest/sweep to compare tiers "
                "on settled history."
            ),
        )
    return report
