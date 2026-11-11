from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class TeamRating(Base):
    __tablename__ = "team_ratings"
    __table_args__ = (UniqueConstraint("team_name", "competition", name="uq_team_competition"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_name: Mapped[str] = mapped_column(String(160), index=True)
    competition: Mapped[str] = mapped_column(String(160), index=True)
    attack_rating: Mapped[float] = mapped_column(Float, default=1.0)
    defense_rating: Mapped[float] = mapped_column(Float, default=1.0)
    matches_played: Mapped[int] = mapped_column(Integer, default=0)
    home_attack_boost: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
    home_goals_avg: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
    away_goals_avg: Mapped[Optional[float]] = mapped_column(Float, default=0.0, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
