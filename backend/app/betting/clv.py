from __future__ import annotations
"""Closing-line value (CLV) tracking.

CLV asks a simpler question than profit and loss: did the price we took beat
the price the market settled on? It needs tens of selections, not hundreds, to
show whether a process is any good, so it is the first honest scoreboard for a
betting model.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.models.price_snapshot import PriceSnapshot


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _market_prices(fixture: Fixture) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    raw = getattr(fixture, "market_odds_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(value, dict):
                    value = value.get("price", value.get("odds"))
                if isinstance(value, (int, float)) and value > 1.0:
                    prices[str(key)] = float(value)
    for key, value in (
        ("home_win", fixture.home_odds),
        ("draw", fixture.draw_odds),
        ("away_win", fixture.away_odds),
    ):
        if value and value > 1.0:
            prices.setdefault(key, float(value))
    return prices


def record_selection_prices(db: Session, slip_id: int, legs: List[Dict[str, Any]]) -> int:
    """Snapshot the price actually taken for each leg of a recorded slip."""
    recorded = 0
    for leg in legs:
        fixture_id = leg.get("fixture_id")
        market_key = leg.get("market_key")
        price = leg.get("odds")
        if not fixture_id or not market_key or not price or float(price) <= 1.0:
            continue
        exists = db.scalar(
            select(PriceSnapshot).where(
                PriceSnapshot.slip_id == slip_id,
                PriceSnapshot.fixture_id == int(fixture_id),
                PriceSnapshot.market_key == str(market_key),
                PriceSnapshot.kind == "selection",
            )
        )
        if exists:
            continue
        db.add(PriceSnapshot(
            fixture_id=int(fixture_id),
            market_key=str(market_key),
            price=float(price),
            kind="selection",
            slip_id=slip_id,
        ))
        recorded += 1
    if recorded:
        db.commit()
    return recorded


def capture_closing_prices(db: Session, hours_after_kickoff: int = 6) -> int:
    """Store the last available price for fixtures that have just kicked off."""
    now = datetime.now(timezone.utc)
    upper = now + timedelta(minutes=15)
    lower = now - timedelta(hours=hours_after_kickoff)
    fixtures = db.scalars(
        select(Fixture).where(
            Fixture.kickoff_utc <= upper,
            Fixture.kickoff_utc >= lower,
        )
    ).all()

    captured = 0
    for fixture in fixtures:
        prices = _market_prices(fixture)
        if not prices:
            continue
        existing = {
            row[0] for row in db.execute(
                select(PriceSnapshot.market_key).where(
                    PriceSnapshot.fixture_id == fixture.id,
                    PriceSnapshot.kind == "close",
                )
            ).all()
        }
        for market_key, price in prices.items():
            if market_key in existing:
                continue
            db.add(PriceSnapshot(
                fixture_id=fixture.id,
                market_key=market_key,
                price=price,
                kind="close",
            ))
            captured += 1
    if captured:
        db.commit()
    return captured


def clv_summary(
    db: Session,
    days: Optional[int] = 90,
    exclude_slip_ids: Optional[set] = None,
) -> Dict[str, Any]:
    """Compare selection prices against closing prices across recorded slips."""
    query = select(PriceSnapshot).where(PriceSnapshot.kind.in_(["selection", "close"]))
    if days:
        query = query.where(PriceSnapshot.captured_at >= datetime.now(timezone.utc) - timedelta(days=days))
    snapshots = db.scalars(query).all()

    closes: Dict[tuple, PriceSnapshot] = {}
    selections: List[PriceSnapshot] = []
    for snapshot in snapshots:
        if snapshot.kind == "close":
            key = (snapshot.fixture_id, snapshot.market_key)
            current = closes.get(key)
            if current is None or _utc(snapshot.captured_at) > _utc(current.captured_at):
                closes[key] = snapshot
        elif not (exclude_slip_ids and snapshot.slip_id in exclude_slip_ids):
            selections.append(snapshot)

    rows = []
    for selection in selections:
        close = closes.get((selection.fixture_id, selection.market_key))
        if not close or close.price <= 1.0:
            continue
        clv_pct = (selection.price / close.price - 1.0) * 100.0
        rows.append({
            "fixture_id": selection.fixture_id,
            "market_key": selection.market_key,
            "selection_price": round(selection.price, 3),
            "closing_price": round(close.price, 3),
            "clv_percent": round(clv_pct, 2),
            "beat_close": clv_pct > 0,
        })

    if not rows:
        return {
            "n_selections": 0,
            "mean_clv_percent": None,
            "beat_close_percent": None,
            "by_market": {},
            "recent": [],
            "note": "No selection has a matching closing price yet.",
        }

    by_market: Dict[str, List[float]] = defaultdict(list)
    for row in rows:
        by_market[row["market_key"]].append(row["clv_percent"])

    beat = sum(1 for row in rows if row["beat_close"])
    return {
        "n_selections": len(rows),
        "mean_clv_percent": round(sum(r["clv_percent"] for r in rows) / len(rows), 2),
        "beat_close_percent": round(beat / len(rows) * 100.0, 1),
        "by_market": {
            market: {
                "n": len(values),
                "mean_clv_percent": round(sum(values) / len(values), 2),
            }
            for market, values in sorted(by_market.items(), key=lambda kv: -len(kv[1]))
        },
        "recent": sorted(rows, key=lambda r: r["fixture_id"], reverse=True)[:10],
    }
