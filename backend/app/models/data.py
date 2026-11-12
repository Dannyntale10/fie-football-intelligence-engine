"""Historical form, match data, providers, bookmakers and odds storage."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TeamForm(Base):
    __tablename__ = "team_form"
    __table_args__ = (UniqueConstraint("team_id", "fixture_id", name="uq_team_form_fixture"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    competition: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    goals_for: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    goals_against: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String(2), nullable=True)
    xg_for: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    xg_against: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MatchStatistics(Base):
    __tablename__ = "match_statistics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), unique=True, index=True)
    home_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_possession: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    home_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_corners: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_fouls: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_offsides: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_offsides: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    home_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_xg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PlayerStatistics(Base):
    __tablename__ = "player_statistics"
    __table_args__ = (UniqueConstraint("player_id", "fixture_id", name="uq_player_fixture_stats"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    goals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    assists: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shots: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    saves: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80), index=True)


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Suspension(Base):
    __tablename__ = "suspensions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    matches: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Lineup(Base):
    __tablename__ = "lineups"
    __table_args__ = (UniqueConstraint("fixture_id", "player_id", name="uq_lineup_fixture_player"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), index=True)
    is_starting: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    expected_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DataProvider(Base):
    __tablename__ = "data_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), default="fixtures")
    base_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    free_plan: Mapped[bool] = mapped_column(Boolean, default=True)
    requests_per_minute: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    daily_quota: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quota_used: Mapped[int] = mapped_column(Integer, default=0)
    quota_reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="unconfigured")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class ProviderSyncLog(Base):
    __tablename__ = "provider_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("data_providers.id"), index=True)
    entity: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    records_written: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BookmakerCatalogue(Base):
    __tablename__ = "bookmaker_catalogue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    website: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    api_available: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Bookmaker(Base):
    __tablename__ = "bookmakers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    catalogue_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookmaker_catalogue.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    country: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MarketCatalogue(Base):
    __tablename__ = "market_catalogue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), index=True)
    settlement_group: Mapped[str] = mapped_column(String(80))
    model: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    required_stats: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AvailableMarket(Base):
    __tablename__ = "available_markets"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "bookmaker_id", "family", "selection", "line",
            name="uq_available_market_selection",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookmakers.id"), nullable=True, index=True)
    family: Mapped[str] = mapped_column(String(80), index=True)
    selection: Mapped[str] = mapped_column(String(80))
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    raw_selection: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    settlement_group: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bookmakers.id"), nullable=True, index=True)
    family: Mapped[str] = mapped_column(String(80), index=True)
    selection: Mapped[str] = mapped_column(String(80))
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[float] = mapped_column(Float)
    implied_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(80))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
