"""Competition, team and player entities."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    confederation: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_league_country_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    tier: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_cup: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    external_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("league_id", "name", name="uq_season_league_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_team_country_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    short_name: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    aliases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stadium: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    position: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    nationality: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    external_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
