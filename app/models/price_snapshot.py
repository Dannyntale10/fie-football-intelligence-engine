from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PriceSnapshot(Base):
    """A price we saw for a fixture market at a point in time.

    ``selection`` rows record what we actually took on a slip; ``close`` rows
    record the last price before kickoff. Comparing the two is closing-line
    value, the fastest low-variance measure of whether selections are good.
    """

    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(Integer, index=True)
    market_key: Mapped[str] = mapped_column(String(60), index=True)
    price: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # selection | close
    slip_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(60), default="betpawa")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
