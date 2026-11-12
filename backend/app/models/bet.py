from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class TrackedBet(Base):
    __tablename__ = "tracked_bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slip_type: Mapped[str] = mapped_column(String(60), index=True)  # "2-Team Combo", "3-Team Combo"
    matchday_label: Mapped[str] = mapped_column(String(120), index=True)
    total_odds: Mapped[float] = mapped_column(Float)
    avg_confidence: Mapped[float] = mapped_column(Float)
    accum_probability: Mapped[float] = mapped_column(Float)
    stake_amount: Mapped[float] = mapped_column(Float, default=0.0)
    stake_currency: Mapped[str] = mapped_column(String(10), default="UGX")
    stake_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(40), default="PENDING", index=True)  # PENDING, WON, LOST, VOID
    profit_loss: Mapped[float] = mapped_column(Float, default=0.0)
    profit_loss_usd: Mapped[float] = mapped_column(Float, default=0.0)
    legs_json: Mapped[str] = mapped_column(Text)  # JSON-encoded list of legs with scores and statuses
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    whatsapp_alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_settlement_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Provenance: what produced this slip, so results can be attributed later.
    model_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    ratings_hash: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    selection_mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
