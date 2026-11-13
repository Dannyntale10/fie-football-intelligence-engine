"""One forecast per fixture per five-minute bucket, never rewritten.

The row freezes the pre-match prior, the score it was conditioned on, and the
numbers the model published. Settlement only fills the result columns; the
forecast itself is immutable evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LivePrediction(Base):
    __tablename__ = "live_predictions"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "minute_bucket", name="uq_live_prediction_bucket"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    minute: Mapped[int] = mapped_column(Integer)
    minute_bucket: Mapped[int] = mapped_column(Integer, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lambda_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lambda_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lambda_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    remaining_home_lambda: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    remaining_away_lambda: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    prob_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_draw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)
    model_version: Mapped[str] = mapped_column(String(40), default="live-dc-1")
    result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    extra_home_goals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra_away_goals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brier: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    top_pick_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
