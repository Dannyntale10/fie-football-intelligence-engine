from __future__ import annotations
import io
from pathlib import Path
from typing import List, Optional
import pandas as pd
from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func, delete
from app.core.config import settings
from app.database import Base, engine, get_db
from app.models.fixture import Fixture
from app.models.team_rating import TeamRating
from app.schemas import (
    FixtureImportRequest,
    FixtureOut,
    ImportSummaryOut,
    FixtureQualityRequest,
    FixtureAnalysisOut,
    TeamRatingOut,
    TeamRatingCreate,
    BacktestRequest,
    BacktestSummaryOut,
    PortfolioRequest,
    BestOddsOut,
)
from app.connectors.bookmakers import BOOKMAKERS, get_connectors
from app.services.fixtures import import_fixtures_from_dataframe
from app.services.quality import grade_data_quality, baseline_signals, evaluate_fixture_signals
from app.services.probabilities import poisson_market_probabilities, analyze_fixture
from app.services.ratings import upsert_team_rating, calculate_ratings_from_fixtures
from app.services.seeder import (
    seed_sample_historical_data,
    fetch_live_upcoming_fixtures,
    fetch_historical_league_season,
)
from app.services.live_feed import fetch_live_upcoming_fixtures_espn
from app.services.backtest import run_backtest
from app.services.portfolio import build_daily_portfolio
from app.services.combo import generate_high_confidence_combos

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Local-first football research and prediction platform foundation."
)

def _resolve_csv_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute() and p.exists():
        return p
    # Check cwd
    cand1 = Path.cwd() / p
    if cand1.exists():
        return cand1
    # Check project root directory (directory containing app/)
    cand2 = Path(__file__).resolve().parent.parent / p
    if cand2.exists():
        return cand2
    return cand1

from datetime import datetime, timezone, timedelta

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

def format_eat(dt_val: Optional[datetime]) -> Optional[str]:
    """Format UTC datetime into East Africa Time (EAT, UTC+3) for Uganda."""
    if not dt_val:
        return None
    if dt_val.tzinfo is None:
        dt_val = dt_val.replace(tzinfo=timezone.utc)
    return dt_val.astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")

def _build_fixture_out(f: Fixture, custom_signals: Optional[dict] = None) -> dict:
    signals = evaluate_fixture_signals(f, custom_signals=custom_signals)
    quality = grade_data_quality(signals)
    return {
        "id": f.id,
        "kickoff_utc": f.kickoff_utc,
        "kickoff_eat": format_eat(f.kickoff_utc),
        "home_team": f.home_team,
        "away_team": f.away_team,
        "competition": f.competition,
        "country": f.country,
        "source": f.source,
        "external_fixture_id": f.external_fixture_id,
        "home_odds": f.home_odds,
        "draw_odds": f.draw_odds,
        "away_odds": f.away_odds,
        "status": f.status,
        "data_quality_score": quality["score"],
        "data_quality_grade": quality["grade"],
        "eligible_for_analysis": quality["eligible_for_analysis"],
    }

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")

@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        fixture_count = db.scalar(select(func.count(Fixture.id))) or 0
        db_status = "connected"
    except Exception as exc:
        fixture_count = 0
        db_status = f"unhealthy: {exc}"
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": db_status,
        "total_fixtures": fixture_count,
        "min_prediction_probability": settings.min_prediction_probability,
    }

@app.get("/bookmakers")
def bookmakers():
    return [{"name": name, "connector_status": "not_configured"} for name in BOOKMAKERS]

@app.get("/connectors/status")
def connector_status():
    return [
        {
            "bookmaker": c.bookmaker_name,
            "configured": False,
            "fixtures_available": len(c.fetch_fixtures())
        }
        for c in get_connectors()
    ]

@app.get("/fixtures", response_model=List[FixtureOut])
def list_fixtures(
    limit: int = 100,
    upcoming_only: bool = Query(False, description="Filter to only upcoming future matches"),
    status: Optional[str] = Query(None, description="Filter by match status"),
    competition: Optional[str] = Query(None, description="Filter by competition name"),
    country: Optional[str] = Query(None, description="Filter by country"),
    source: Optional[str] = Query(None, description="Filter by source"),
    db: Session = Depends(get_db)
):
    from datetime import datetime, timezone
    limit = max(1, min(limit, 500))
    query = select(Fixture)
    if upcoming_only:
        now = datetime.now(timezone.utc)
        query = query.where(Fixture.kickoff_utc >= now)
    if status:
        query = query.where(Fixture.status == status)
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))
    if country:
        query = query.where(Fixture.country.ilike(f"%{country}%"))
    if source:
        query = query.where(Fixture.source.ilike(f"%{source}%"))

    fixtures = db.scalars(query.order_by(Fixture.kickoff_utc).limit(limit)).all()
    return [_build_fixture_out(f) for f in fixtures]

@app.delete("/fixtures/past")
def delete_past_fixtures(db: Session = Depends(get_db)):
    """Delete all past fixtures that have already been played, keeping only future upcoming matches."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    deleted = db.execute(delete(Fixture).where(Fixture.kickoff_utc <= now)).rowcount
    db.commit()
    return {"message": f"Purged {deleted} past matches. Only genuine upcoming matches remain in the database.", "deleted_count": deleted}

@app.get("/fixtures/{fixture_id}", response_model=FixtureOut)
def get_fixture(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return _build_fixture_out(fixture)

@app.post("/fixtures/import-csv", response_model=ImportSummaryOut)
def import_csv(request: FixtureImportRequest, db: Session = Depends(get_db)):
    path = _resolve_csv_path(request.path)
    if not path.exists() or path.suffix.lower() != ".csv":
        raise HTTPException(
            status_code=400,
            detail=f"CSV file not found at '{request.path}'. Provide a valid relative or absolute .csv path."
        )
    try:
        frame = pd.read_csv(path)
        result = import_fixtures_from_dataframe(db, frame)
        return {
            "inserted": result["inserted"],
            "duplicates": result["duplicates"],
            "total_processed": result["total_processed"],
            "message": f"Successfully processed {result['total_processed']} rows ({result['inserted']} new, {result['duplicates']} deduplicated)."
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Could not import CSV: {exc}") from exc

@app.post("/fixtures/upload-csv", response_model=ImportSummaryOut)
async def upload_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are supported.")
    try:
        content = await file.read()
        frame = pd.read_csv(io.BytesIO(content))
        result = import_fixtures_from_dataframe(db, frame)
        return {
            "inserted": result["inserted"],
            "duplicates": result["duplicates"],
            "total_processed": result["total_processed"],
            "message": f"Successfully imported '{file.filename}': {result['inserted']} inserted, {result['duplicates']} duplicates."
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Upload parsing error: {exc}") from exc

@app.delete("/fixtures")
def clear_fixtures(db: Session = Depends(get_db)):
    count = db.scalar(select(func.count(Fixture.id))) or 0
    db.execute(delete(Fixture))
    db.commit()
    return {"message": f"Cleared {count} fixtures.", "deleted_count": count}

@app.get("/fixtures/{fixture_id}/quality")
def fixture_quality(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    signals = evaluate_fixture_signals(fixture)
    grading = grade_data_quality(signals)
    return {
        "fixture_id": fixture_id,
        "match": f"{fixture.home_team} vs {fixture.away_team}",
        "signals": signals,
        **grading
    }

@app.post("/fixtures/{fixture_id}/quality")
def update_fixture_quality(
    fixture_id: int,
    request: FixtureQualityRequest,
    db: Session = Depends(get_db)
):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    signals = evaluate_fixture_signals(fixture, custom_signals=request.signals)
    grading = grade_data_quality(signals)
    return {
        "fixture_id": fixture_id,
        "match": f"{fixture.home_team} vs {fixture.away_team}",
        "signals": signals,
        **grading
    }

@app.get("/fixtures/{fixture_id}/analysis", response_model=FixtureAnalysisOut)
def fixture_analysis(
    fixture_id: int,
    home_lambda: Optional[float] = Query(None, ge=0.0, le=10.0, description="Override home expected goals"),
    away_lambda: Optional[float] = Query(None, ge=0.0, le=10.0, description="Override away expected goals"),
    threshold: Optional[float] = Query(None, ge=0.0, le=1.0, description="Override qualification threshold"),
    rho: float = Query(-0.10, ge=-0.5, le=0.5, description="Dixon-Coles low-score adjustment factor"),
    db: Session = Depends(get_db)
):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    threshold_val = threshold if threshold is not None else settings.min_prediction_probability
    return analyze_fixture(
        fixture,
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        qualification_threshold=threshold_val,
        rho=rho,
        db=db
    )

@app.get("/ratings", response_model=List[TeamRatingOut])
def list_team_ratings(
    competition: Optional[str] = Query(None, description="Filter by competition"),
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = select(TeamRating)
    if competition:
        query = query.where(TeamRating.competition.ilike(f"%{competition}%"))
    return db.scalars(query.order_by(TeamRating.team_name).limit(limit)).all()

@app.post("/ratings", response_model=TeamRatingOut)
def create_or_update_rating(
    data: TeamRatingCreate,
    db: Session = Depends(get_db)
):
    return upsert_team_rating(
        db=db,
        team_name=data.team_name,
        competition=data.competition,
        attack_rating=data.attack_rating,
        defense_rating=data.defense_rating,
        matches_played=data.matches_played
    )

@app.post("/ratings/calculate-from-fixtures")
def calculate_ratings(
    competition: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    return calculate_ratings_from_fixtures(db, competition=competition)

@app.post("/fixtures/seed-historical")
def seed_historical(db: Session = Depends(get_db)):
    try:
        return seed_sample_historical_data(db)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to seed historical data: {exc}") from exc

@app.post("/fixtures/fetch-live-espn")
def api_fetch_live_espn(db: Session = Depends(get_db)):
    """Fetch real-time upcoming matches with live DraftKings odds across active competitions."""
    try:
        return fetch_live_upcoming_fixtures_espn(db)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to fetch live matches: {exc}") from exc

@app.post("/fixtures/fetch-upcoming")
def api_fetch_upcoming_fixtures(db: Session = Depends(get_db)):
    try:
        return fetch_live_upcoming_fixtures(db)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to fetch live upcoming fixtures: {exc}") from exc

@app.post("/fixtures/fetch-league")
def api_fetch_league_season(
    division: str = Query("E0", description="Division code (e.g. E0=Premier League, SP1=La Liga, I1=Serie A, D1=Bundesliga, F1=Ligue 1)"),
    season: str = Query("2324", description="Season code (e.g. 2324 for 2023-2024, 2425 for 2024-2025)"),
    db: Session = Depends(get_db)
):
    try:
        return fetch_historical_league_season(db, division_code=division, season=season)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to fetch league {division} season {season}: {exc}") from exc

@app.post("/backtest/run", response_model=BacktestSummaryOut)
def api_run_backtest(
    request: BacktestRequest,
    db: Session = Depends(get_db)
):
    try:
        return run_backtest(
            db=db,
            strategy=request.strategy,
            min_ev_percent=request.min_ev_percent,
            min_quality_score=request.min_quality_score,
            staking=request.staking,
            initial_bankroll=request.initial_bankroll,
            competition=request.competition
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Backtest error: {exc}") from exc

@app.get("/markets/baseline")
def markets_baseline(
    home_lambda: float = Query(1.6, ge=0.0, le=10.0),
    away_lambda: float = Query(1.1, ge=0.0, le=10.0),
    threshold: Optional[float] = Query(None, ge=0.0, le=1.0),
    rho: float = Query(-0.10, ge=-0.5, le=0.5, description="Dixon-Coles low-score adjustment factor")
):
    try:
        t = threshold if threshold is not None else settings.min_prediction_probability
        return poisson_market_probabilities(home_lambda, away_lambda, qualification_threshold=t, rho=rho)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.get("/portfolio/today")
def get_daily_portfolio(
    days_ahead: int = Query(7, ge=1, le=30, description="Days ahead window"),
    min_ev_percent: float = Query(0.0, ge=0.0, le=50.0, description="Minimum +EV %"),
    bankroll: float = Query(1000.0, ge=10.0, description="Bankroll for Kelly sizing"),
    kelly_fraction: float = Query(0.25, ge=0.05, le=1.0, description="Kelly fraction (0.25 = Quarter Kelly)"),
    rho: float = Query(-0.10, ge=-0.5, le=0.5, description="Dixon-Coles rho"),
    qualification_threshold: float = Query(0.85, ge=0.5, le=0.99, description="Confidence threshold"),
    competition: Optional[str] = Query(None, description="Filter by competition"),
    db: Session = Depends(get_db)
):
    """Generate the daily value portfolio — all +EV bets and qualifying markets for upcoming fixtures."""
    return build_daily_portfolio(
        db=db,
        days_ahead=days_ahead,
        min_ev_percent=min_ev_percent,
        bankroll=bankroll,
        kelly_fraction=kelly_fraction,
        rho=rho,
        qualification_threshold=qualification_threshold,
        competition=competition,
    )

@app.get("/portfolio/combos")
def get_portfolio_combos(
    num_legs: int = Query(3, ge=2, le=4, description="Number of teams in the multi-bet combo (2 or 3)"),
    min_total_odds: float = Query(2.00, ge=1.50, le=10.0, description="Minimum combined total odds"),
    min_leg_prob: float = Query(0.80, ge=0.50, le=0.99, description="Minimum individual leg win/draw probability"),
    bankroll: float = Query(1000.0, ge=10.0, description="Bankroll for Kelly sizing"),
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    db: Session = Depends(get_db)
):
    """Generate high-confidence 2-leg or 3-leg accumulators with combined odds >= 2.0+."""
    return generate_high_confidence_combos(
        db=db,
        num_legs=num_legs,
        min_leg_prob=min_leg_prob,
        min_total_odds=min_total_odds,
        bankroll=bankroll,
        competition=competition,
    )

@app.get("/fixtures/{fixture_id}/best-odds")
def fixture_best_odds(fixture_id: int, db: Session = Depends(get_db)):
    """Return best available odds across all stored bookmakers for a fixture, with arbitrage detection."""
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    # Collect all bookmaker odds
    books = {
        "Bet365": (fixture.home_odds, fixture.draw_odds, fixture.away_odds),
        "BetVictor": (fixture.bv_home_odds, fixture.bv_draw_odds, fixture.bv_away_odds),
        "Betway": (fixture.bw_home_odds, fixture.bw_draw_odds, fixture.bw_away_odds),
        "Pinnacle": (fixture.ps_home_odds, fixture.ps_draw_odds, fixture.ps_away_odds),
        "MaxOdds": (fixture.max_home_odds, fixture.max_draw_odds, fixture.max_away_odds),
    }

    best = {"home": (None, 0.0), "draw": (None, 0.0), "away": (None, 0.0)}
    for bname, (h, d, a) in books.items():
        if h and h > 1.0 and h > best["home"][1]:
            best["home"] = (bname, h)
        if d and d > 1.0 and d > best["draw"][1]:
            best["draw"] = (bname, d)
        if a and a > 1.0 and a > best["away"][1]:
            best["away"] = (bname, a)

    bh, bd, ba = best["home"][1], best["draw"][1], best["away"][1]
    min_overround = round((1/bh + 1/bd + 1/ba), 4) if (bh > 0 and bd > 0 and ba > 0) else 1.0
    has_arbitrage = min_overround < 1.0

    return {
        "fixture_id": fixture.id,
        "match": f"{fixture.home_team} vs {fixture.away_team}",
        "competition": fixture.competition,
        "kickoff_utc": fixture.kickoff_utc,
        "home": {"best_bookmaker": best["home"][0], "best_odds": best["home"][1], "all": {k: v[0] for k, v in books.items() if v[0] and v[0] > 1.0}},
        "draw": {"best_bookmaker": best["draw"][0], "best_odds": best["draw"][1], "all": {k: v[1] for k, v in books.items() if v[1] and v[1] > 1.0}},
        "away": {"best_bookmaker": best["away"][0], "best_odds": best["away"][1], "all": {k: v[2] for k, v in books.items() if v[2] and v[2] > 1.0}},
        "has_arbitrage": has_arbitrage,
        "min_overround": min_overround,
    }

@app.patch("/fixtures/mark-stale")
def mark_stale_fixtures(db: Session = Depends(get_db)):
    """Mark past scheduled fixtures with no result as 'stale' (pending_result)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    stale_query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc < now)
        .where(Fixture.home_score.is_(None))
    )
    stale = db.scalars(stale_query).all()
    count = 0
    for f in stale:
        f.status = "pending_result"
        count += 1
    db.commit()
    return {"message": f"Marked {count} past fixtures as 'pending_result'.", "updated_count": count}
