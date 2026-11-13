"""Resolve one coherent 1X2 market to de-vig against.

A de-vig is only meaningful when the three prices came from one bookmaker at
one moment. The fixture columns keep the best price per outcome, which is
right for what you could take but can mix three different books: their implied
probabilities may even sum to less than 1, and normalising that mixture
invents an edge. This module prefers a stored per-bookmaker quote and falls
back to the fixture prices only when they still look like a single market.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select

QUOTE_KEYS = ("home_win", "draw", "away_win")
# A single book's 1X2 margin sits in this band. Below the floor the prices
# cannot be one market (arbitrage); above the ceiling they are likely a stale
# or mismatched quote set rather than a real offer.
MIN_OVERROUND = 1.0
MAX_OVERROUND = 1.30


@dataclass(frozen=True)
class MarketReference:
    home: float
    draw: float
    away: float
    bookmaker: Optional[str]
    source: Optional[str]
    captured_at: Optional[datetime]
    overround: float
    origin: str  # "bookmaker" | "fixture"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "home": round(self.home, 4),
            "draw": round(self.draw, 4),
            "away": round(self.away, 4),
            "bookmaker": self.bookmaker,
            "source": self.source,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
            "overround": round(self.overround, 4),
            "margin_percent": round((self.overround - 1.0) * 100.0, 2),
            "origin": self.origin,
        }


def overround(home: float, draw: float, away: float) -> float:
    return 1.0 / home + 1.0 / draw + 1.0 / away


def is_coherent(value: float) -> bool:
    return MIN_OVERROUND < value <= MAX_OVERROUND


def _aware(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _from_prices(
    prices: Dict[str, float],
    *,
    bookmaker: Optional[str],
    source: Optional[str],
    captured_at: Optional[datetime],
    origin: str,
) -> Optional[MarketReference]:
    total = overround(prices["home_win"], prices["draw"], prices["away_win"])
    if not is_coherent(total):
        return None
    return MarketReference(
        home=float(prices["home_win"]),
        draw=float(prices["draw"]),
        away=float(prices["away_win"]),
        bookmaker=bookmaker,
        source=source,
        captured_at=captured_at,
        overround=total,
        origin=origin,
    )


def resolve(
    db: Any,
    fixture: Any,
    before: Optional[datetime] = None,
) -> Optional[MarketReference]:
    """Best coherent 1X2 reference for a fixture, or ``None``.

    Prefers a complete per-bookmaker quote (freshest, then sharpest). Falls
    back to the fixture's own three prices only when they form a market that
    still carries a margin, so a mixed best-price set is refused rather than
    normalised into a fake probability.

    ``before`` freezes the reference at a cutoff: only a quote provably
    captured strictly earlier is eligible. Passing ``None`` keeps the
    historical behaviour of using every stored price.
    """
    fixture_id = getattr(fixture, "id", None)
    if db is not None and fixture_id is not None:
        from app.models.market_quote import MarketQuote

        rows = db.scalars(
            select(MarketQuote).where(
                MarketQuote.fixture_id == fixture_id,
                MarketQuote.market_key.in_(QUOTE_KEYS),
            )
        ).all()
        cutoff = _aware(before) if before is not None else None
        books: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            # An unknown capture time cannot be proven pre-kickoff, so a live
            # prior refuses it instead of assuming the best.
            if cutoff is not None:
                if row.captured_at is None or _aware(row.captured_at) >= cutoff:
                    continue
            if row.price is None or row.price <= 1.0:
                continue
            key = (row.bookmaker or "").strip().upper()
            if not key:
                continue
            book = books.setdefault(
                key,
                {
                    "bookmaker": row.bookmaker,
                    "source": row.source,
                    "prices": {},
                    "captured_at": row.captured_at,
                },
            )
            book["prices"][row.market_key] = float(row.price)
            if _aware(row.captured_at) > _aware(book["captured_at"]):
                book["captured_at"] = row.captured_at
                book["source"] = row.source

        resolved = []
        for book in books.values():
            prices = book["prices"]
            if not all(key in prices for key in QUOTE_KEYS):
                continue
            reference = _from_prices(
                prices,
                bookmaker=book["bookmaker"],
                source=book["source"],
                captured_at=book["captured_at"],
                origin="bookmaker",
            )
            if reference is not None:
                resolved.append(reference)
        if resolved:
            resolved.sort(
                key=lambda ref: (_aware(ref.captured_at), -ref.overround), reverse=True
            )
            return resolved[0]

    prices = {
        "home_win": getattr(fixture, "home_odds", None),
        "draw": getattr(fixture, "draw_odds", None),
        "away_win": getattr(fixture, "away_odds", None),
    }
    if any(price is None or float(price) <= 1.0 for price in prices.values()):
        return None
    captured_at = getattr(fixture, "odds_updated_at", None)
    if before is not None and (
        captured_at is None or _aware(captured_at) >= _aware(before)
    ):
        return None
    return _from_prices(
        {key: float(value) for key, value in prices.items()},
        bookmaker=None,
        source=getattr(fixture, "source", None),
        captured_at=captured_at,
        origin="fixture",
    )
