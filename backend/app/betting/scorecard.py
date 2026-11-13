"""Weekly honest scorecard for the tracked ledger.

Profit over one week is mostly noise. Closing-line value and Brier skill
move first, so they belong beside the win rate: a losing week that beat the
close is bad luck, a winning week that lost to it is not a process to grow.

Every block reports its own sample size and says when it has nothing. The
market side of Brier is only computed for a 1X2 leg whose book can be
de-vigged, because inventing a market probability for other markets would
fake the comparison.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bet import TrackedBet
from app.models.fixture import Fixture

EMPTY_MESSAGE = "NO SETTLED SELECTIONS IN THIS WINDOW."
ONE_X2 = ("home_win", "draw", "away_win")


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _probability(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 < number < 1.0 else None


def _brier(pairs: List[Tuple[float, float]]) -> Optional[float]:
    if not pairs:
        return None
    return round(sum((probability - outcome) ** 2 for probability, outcome in pairs) / len(pairs), 4)


def _market_probability(
    db: Session,
    fixture_id: Any,
    market_key: str,
    cache: Dict[int, Any],
) -> Optional[float]:
    """De-vigged probability for a 1X2 selection, or ``None``."""
    if market_key not in ONE_X2:
        return None
    try:
        key = int(fixture_id)
    except (TypeError, ValueError):
        return None

    if key not in cache:
        from app.analytics.market_reference import resolve

        fixture = db.get(Fixture, key)
        cache[key] = resolve(db, fixture) if fixture is not None else None
    reference = cache[key]
    if reference is None:
        return None

    price = {"home_win": reference.home, "draw": reference.draw, "away_win": reference.away}[market_key]
    if price <= 1.0:
        return None
    total = 1.0 / reference.home + 1.0 / reference.draw + 1.0 / reference.away
    return (1.0 / price) / total


def build_scorecard(
    db: Session,
    days: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate one window of settled real slips into a readable scorecard."""
    moment = _aware(now) or datetime.now(timezone.utc)
    since = moment - timedelta(days=days)

    from app.betting.settlement import _is_synthetic_bet

    recorded = db.scalars(
        select(TrackedBet).order_by(TrackedBet.settled_at.desc())
    ).all()
    synthetic_ids = {bet.id for bet in recorded if _is_synthetic_bet(bet)}
    real = [bet for bet in recorded if bet.id not in synthetic_ids]

    settled = []
    for bet in real:
        if bet.status not in ("WON", "LOST"):
            continue
        when = _aware(bet.settled_at) or _aware(bet.placed_at)
        if when is not None and when >= since:
            settled.append(bet)

    legs = 0
    won_legs = 0
    model_pairs: List[Tuple[float, float]] = []
    market_model: List[Tuple[float, float]] = []
    market_pairs: List[Tuple[float, float]] = []
    cache: Dict[int, Any] = {}

    for bet in settled:
        try:
            parsed = json.loads(bet.legs_json or "[]")
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, list):
            continue
        for leg in parsed:
            if not isinstance(leg, dict):
                continue
            status = str(leg.get("status") or "").upper()
            if status not in ("WON", "LOST"):
                continue
            legs += 1
            won = status == "WON"
            if won:
                won_legs += 1
            probability = _probability(leg.get("probability"))
            if probability is None:
                continue
            outcome = 1.0 if won else 0.0
            model_pairs.append((probability, outcome))
            market = _market_probability(db, leg.get("fixture_id"), str(leg.get("market_key")), cache)
            if market is not None:
                market_model.append((probability, outcome))
                market_pairs.append((market, outcome))

    won = [bet for bet in settled if bet.status == "WON"]
    lost = [bet for bet in settled if bet.status == "LOST"]
    staked = sum(bet.stake_usd or 0.0 for bet in settled)
    profit = sum(bet.profit_loss_usd or 0.0 for bet in settled)

    tiers: Dict[Optional[float], Dict[str, Any]] = {}
    for bet in settled:
        key = round(float(bet.tier), 2) if bet.tier is not None else None
        bucket = tiers.setdefault(
            key, {"tier": key, "slips": 0, "won": 0, "lost": 0, "win_rate": 0.0}
        )
        bucket["slips"] += 1
        bucket["won" if bet.status == "WON" else "lost"] += 1
    for bucket in tiers.values():
        bucket["win_rate"] = round(bucket["won"] / bucket["slips"] * 100.0, 1)
    hit_rate_by_tier = sorted(
        tiers.values(),
        key=lambda row: (row["tier"] is None, -(row["tier"] or 0.0)),
    )

    window_ids = {bet.id for bet in settled}
    excluded = synthetic_ids | {bet.id for bet in real if bet.id not in window_ids}
    from app.betting.clv import clv_summary

    clv = clv_summary(db, days=None, exclude_slip_ids=excluded)

    brier_model = _brier(model_pairs)
    brier_market = _brier(market_pairs)
    brier_market_model = _brier(market_model)
    skill = None
    if brier_market is not None and brier_market > 0 and brier_market_model is not None:
        skill = round((brier_market - brier_market_model) / brier_market, 4)

    return {
        "status": "ok" if settled else "insufficient_sample",
        "message": None if settled else EMPTY_MESSAGE,
        "window_days": days,
        "since": since.isoformat(),
        "results": {
            "slips": len(settled),
            "won": len(won),
            "lost": len(lost),
            "win_rate": round(len(won) / len(settled) * 100.0, 1) if settled else None,
            "staked_usd": round(staked, 2),
            "profit_usd": round(profit, 2),
            "roi_percent": round(profit / staked * 100.0, 1) if staked > 0 else None,
        },
        "selections": {
            "legs": legs,
            "scored_legs": len(model_pairs),
            "won_legs": won_legs,
            "hit_rate_percent": round(won_legs / legs * 100.0, 1) if legs else None,
        },
        "clv": clv,
        "brier": {
            "n": len(model_pairs),
            "model": brier_model,
            "note": None if model_pairs else "No settled leg carried a model probability.",
            "vs_market": {
                "n": len(market_pairs),
                "model": brier_market_model,
                "market": brier_market,
                "skill": skill,
                "note": None if market_pairs else "No settled 1X2 leg had a coherent de-vigged reference.",
            },
        },
        "hit_rate_by_tier": hit_rate_by_tier,
    }
