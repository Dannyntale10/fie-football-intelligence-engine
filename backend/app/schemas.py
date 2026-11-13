from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class FixtureImportRequest(BaseModel):
    path: str = Field(default="data/sample_fixtures.csv", description="CSV path relative to project root")

class FixtureQualityRequest(BaseModel):
    signals: Optional[Dict[str, float]] = Field(
        default=None,
        description="Optional signals dict (e.g. historical_records: 0.8, team_news: 1.0) to evaluate or override"
    )

class FixtureOut(BaseModel):
    id: int
    kickoff_utc: datetime
    kickoff_eat: Optional[str] = None
    home_team: str
    away_team: str
    competition: str
    country: str
    source: str
    external_fixture_id: Optional[str] = None
    home_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    away_odds: Optional[float] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    live_clock: Optional[str] = None
    status: Optional[str] = None
    data_quality_score: Optional[int] = None
    data_quality_grade: Optional[str] = None
    eligible_for_analysis: Optional[bool] = None

    model_config = {"from_attributes": True}

class LiveFixturesOut(BaseModel):
    as_of: datetime
    refreshed: bool
    warning: Optional[str] = None
    fixtures: List[FixtureOut]

class ImportSummaryOut(BaseModel):
    inserted: int
    duplicates: int
    total_processed: int
    message: str

class ValueBetOut(BaseModel):
    market: str
    market_key: str
    model_probability: float
    bookmaker_odds: float
    implied_probability: float
    fair_odds: Optional[float] = None
    edge: float
    expected_value_percent: float
    is_value: bool
    kelly: Optional[Dict[str, Any]] = None

class FixtureAnalysisOut(BaseModel):
    fixture_id: Optional[int] = None
    match: str
    competition: str
    country: str
    kickoff_utc: Optional[datetime] = None
    source: str
    estimated_lambdas: Dict[str, float]
    lambda_source: Optional[str] = None
    dixon_coles_rho: Optional[float] = None
    qualification_threshold: float
    bookmaker_margin: Optional[Dict[str, Any]] = None
    value_bets: List[ValueBetOut] = []
    qualifying_markets: List[Dict[str, Any]] = []
    all_markets: Dict[str, float]
    data_quality: Dict[str, Any]

class TeamRatingOut(BaseModel):
    id: int
    team_name: str
    competition: str
    attack_rating: float
    defense_rating: float
    matches_played: int
    updated_at: datetime

    model_config = {"from_attributes": True}

class TeamRatingCreate(BaseModel):
    team_name: str
    competition: str
    attack_rating: float = Field(default=1.0, ge=0.1, le=5.0)
    defense_rating: float = Field(default=1.0, ge=0.1, le=5.0)
    matches_played: int = Field(default=5, ge=1)

class BacktestRequest(BaseModel):
    strategy: str = Field(default="value_bet", description="'value_bet' or 'high_confidence_85'")
    min_ev_percent: float = Field(default=3.0, ge=0.0, le=50.0)
    min_quality_score: int = Field(default=50, ge=0, le=100)
    staking: str = Field(default="quarter_kelly", description="'quarter_kelly', 'half_kelly', or 'flat'")
    initial_bankroll: float = Field(default=1000.0, ge=10.0)
    competition: Optional[str] = None

class BacktestSummaryOut(BaseModel):
    strategy: str
    staking: str
    total_bets: int
    wins: int
    losses: int
    win_rate_percent: float
    initial_bankroll: float
    final_bankroll: float
    net_profit: float
    roi_percent: float
    max_drawdown_percent: float
    history: List[Dict[str, Any]]

class PortfolioRequest(BaseModel):
    days_ahead: int = Field(default=7, ge=1, le=30)
    min_ev_percent: float = Field(default=0.0, ge=0.0, le=50.0)
    bankroll: float = Field(default=1000.0, ge=10.0)
    kelly_fraction: float = Field(default=0.25, ge=0.05, le=1.0)
    rho: float = Field(default=-0.10, ge=-0.5, le=0.5)
    qualification_threshold: float = Field(default=0.85, ge=0.5, le=0.99)
    competition: Optional[str] = None

class BestOddsOut(BaseModel):
    fixture_id: int
    match: str
    competition: str
    kickoff_utc: Optional[datetime] = None
    home: Dict[str, Any]
    draw: Dict[str, Any]
    away: Dict[str, Any]
    has_arbitrage: bool
    min_overround: float
