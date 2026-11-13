from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_fixture_dedupe_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(300), index=True)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_team: Mapped[str] = mapped_column(String(160), index=True)
    away_team: Mapped[str] = mapped_column(String(160), index=True)
    competition: Mapped[str] = mapped_column(String(160), index=True)
    country: Mapped[str] = mapped_column(String(100), default="Unknown")
    source: Mapped[str] = mapped_column(String(80), index=True)
    external_fixture_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Multi-bookmaker odds columns (Bet365, BetVictor, Betway, Pinnacle)
    bv_home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bv_draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bv_away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bw_home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bw_draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bw_away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ps_home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ps_draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ps_away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opening_home_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opening_draw_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    opening_away_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # JSON map of real bookmaker prices keyed by the probability model's market
    # names (double_chance_1x, over_2_5, ah_away_plus_1_5, btts_no, ...).
    market_odds_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Live state written by the ESPN score poller; never inferred from odds.
    live_clock: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    espn_league_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="scheduled", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
