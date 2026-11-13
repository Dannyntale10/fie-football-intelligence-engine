"""One bookmaker's own 1X2 quote for a fixture.

The fixture columns keep the best available price per outcome, which is right
for what a bettor could take but wrong as a probability reference: three
prices from three different books do not add up to one market. This table
keeps each book's complete trio so the de-vig has a coherent source.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketQuote(Base):
    __tablename__ = "market_quotes"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "bookmaker", "market_key", name="uq_market_quote_book_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    bookmaker: Mapped[str] = mapped_column(String(60), index=True)
    market_key: Mapped[str] = mapped_column(String(40), index=True)
    price: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(60), default="unknown")
    source_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
