"""Predictions, combinations, results, settlements and performance records."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_model_name_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(40))
    trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Prediction(Base):
    """Every prediction is stored; losing versions are never overwritten."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    model_version_id: Mapped[Optional[int]] = mapped_column(ForeignKey("model_versions.id"), nullable=True)
    family: Mapped[str] = mapped_column(String(80), index=True)
    selection: Mapped[str] = mapped_column(String(80))
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fair_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ev: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    qualification_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    data_quality: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Selection(Base):
    __tablename__ = "selections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("predictions.id"), nullable=True, index=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    family: Mapped[str] = mapped_column(String(80), index=True)
    selection: Mapped[str] = mapped_column(String(80))
    line: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    probability: Mapped[float] = mapped_column(Float)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bookmaker: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ev: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class Combination(Base):
    __tablename__ = "combinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    combined_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    combined_odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ev: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="paper", index=True)
    version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class CombinationSelection(Base):
    __tablename__ = "combination_selections"
    __table_args__ = (UniqueConstraint("combination_id", "selection_id", name="uq_combination_selection"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    combination_id: Mapped[int] = mapped_column(ForeignKey("combinations.id"), index=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"), index=True)
    probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    correlation_adjustment: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), unique=True, index=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ht_home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ht_away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="final")
    source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PredictionSettlement(Base):
    __tablename__ = "prediction_settlements"
    __table_args__ = (UniqueConstraint("prediction_id", name="uq_settlement_prediction"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[Optional[int]] = mapped_column(ForeignKey("predictions.id"), nullable=True, index=True)
    selection_id: Mapped[Optional[int]] = mapped_column(ForeignKey("selections.id"), nullable=True, index=True)
    fixture_id: Mapped[Optional[int]] = mapped_column(ForeignKey("fixtures.id"), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    stake: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    odds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_units: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)


class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scope: Mapped[str] = mapped_column(String(40), index=True)
    scope_value: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    bets: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    voids: Mapped[int] = mapped_column(Integer, default=0)
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    roi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yield_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clv: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    brier: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    recipient: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    related_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    related_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
