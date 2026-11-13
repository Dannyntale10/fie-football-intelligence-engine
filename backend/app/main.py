from __future__ import annotations
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func, delete, desc
from app.core.config import settings
from app.database import Base, engine, get_db
import app.models  # noqa: F401  (registers every platform table)
from app.models.fixture import Fixture
from app.models.team_rating import TeamRating
from app.models.price_snapshot import PriceSnapshot  # noqa: F401  (registers the table)
from app.schemas import (
    FixtureImportRequest,
    FixtureOut,
    LiveFixturesOut,
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
from app.data_sources.connectors.bookmakers import BOOKMAKERS, get_connectors
from app.data_sources.fixtures import import_fixtures_from_dataframe
from app.analytics.quality import grade_data_quality, baseline_signals, evaluate_fixture_signals
from app.analytics.probabilities import poisson_market_probabilities, analyze_fixture
from app.analytics.ratings import upsert_team_rating, calculate_ratings_from_fixtures
from app.data_sources.seeder import (
    seed_sample_historical_data,
    fetch_live_upcoming_fixtures,
    fetch_historical_league_season,
    fetch_all_major_leagues,
)
from app.data_sources.live_feed import fetch_live_upcoming_fixtures_espn
from app.data_sources.inplay import refresh_live_scores
from app.live.ledger import record_live_snapshots, settle_live_predictions
from app.models.live import LivePrediction
from app.analytics.backtest import run_backtest
from app.betting.portfolio import build_daily_portfolio
from app.betting.combo import generate_high_confidence_combos, top_picks_for_matchday

Base.metadata.create_all(bind=engine)

try:
    from app.tasks.scheduler import start_scheduler
    start_scheduler()
except Exception:
    pass

@asynccontextmanager
async def _lifespan(app: FastAPI):
    from app.notifications.telegram import start_polling_thread, stop_polling_thread

    start_polling_thread()
    try:
        yield
    finally:
        stop_polling_thread()


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Local-first football research and prediction platform foundation.",
    lifespan=_lifespan,
)

# Open so the host can probe the app and a browser can reach the docs, where
# the Authorize button supplies the key for every data call.
_OPEN_PATHS = frozenset({"/", "/health", "/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


@app.middleware("http")
async def require_api_key(request: Request, call_next):
    """Reject unauthenticated calls once FIE_API_KEY is configured.

    /health stays open so the host's health check works without the secret, and
    the docs pages stay open so the key can be entered in a browser at all.
    """
    configured = settings.fie_api_key
    if not configured or request.method == "OPTIONS" or request.url.path in _OPEN_PATHS:
        return await call_next(request)
    supplied = request.headers.get("x-api-key", "")
    if not secrets.compare_digest(supplied, configured):
        return JSONResponse({"detail": "Missing or invalid API key"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.frontend_origins.split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _openapi_with_api_key():
    """Declare X-API-Key so the docs' Authorize button sends the header."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    scheme = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    schema.setdefault("components", {}).setdefault("securitySchemes", {})["FieApiKey"] = scheme
    schema["security"] = [{"FieApiKey": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_with_api_key

def _resolve_csv_path(raw_path: str) -> Path:
    p = Path(raw_path)
    if p.is_absolute() and p.exists():
        return p
    # Check cwd
    cand1 = Path.cwd() / p
    if cand1.exists():
        return cand1
    # Check repository root (two levels above backend/app)
    cand2 = Path(__file__).resolve().parent.parent.parent / p
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

LIVE_FRESH_SECONDS = 180
LIVE_STALE_SECONDS = 480


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _freshness(observed_at: Optional[datetime], now: datetime) -> str:
    if observed_at is None:
        return "expired"
    age = (now - _as_utc(observed_at)).total_seconds()
    if age < LIVE_FRESH_SECONDS:
        return "fresh"
    if age <= LIVE_STALE_SECONDS:
        return "stale"
    return "expired"


def _latest_live_prediction(db: Session, fixture_id: int) -> Optional[LivePrediction]:
    return db.scalar(
        select(LivePrediction)
        .where(LivePrediction.fixture_id == fixture_id)
        .order_by(LivePrediction.minute_bucket.desc(), LivePrediction.id.desc())
    )


def _live_prediction_payload(
    row: Optional[LivePrediction], now: datetime
) -> Optional[dict]:
    if row is None:
        return None

    freshness = _freshness(row.observed_at, now)
    status = row.status
    probabilities = None
    if row.prob_home is not None and freshness != "expired":
        probabilities = {
            "home_win": row.prob_home,
            "draw": row.prob_draw,
            "away_win": row.prob_away,
        }
    if freshness == "expired":
        # An old score is worse than no number; the stored row keeps its status.
        status = "stale"

    prematch = None
    if row.prematch_home is not None:
        prematch = {
            "home_win": row.prematch_home,
            "draw": row.prematch_draw,
            "away_win": row.prematch_away,
        }

    return {
        "status": status,
        "observed_at": row.observed_at,
        "minute": row.minute,
        "freshness": freshness,
        "score": {"home": row.home_score, "away": row.away_score},
        "remaining_minutes": max(0, 90 - row.minute),
        "probabilities": probabilities,
        "prematch_probabilities": prematch,
        "lambda": {"home": row.lambda_home, "away": row.lambda_away},
        "remaining_lambda": {
            "home": row.remaining_home_lambda,
            "away": row.remaining_away_lambda,
        },
        "intensity": row.intensity,
        "intensity_source": row.intensity_source,
        "lambda_source": row.lambda_source,
        "model_version": row.model_version,
    }


def _live_history_row(row: LivePrediction) -> dict:
    return {
        "minute": row.minute,
        "minute_bucket": row.minute_bucket,
        "observed_at": row.observed_at,
        "score": {"home": row.home_score, "away": row.away_score},
        "probabilities": {
            "home_win": row.prob_home,
            "draw": row.prob_draw,
            "away_win": row.prob_away,
        },
        "result": row.result,
        "extra_home_goals": row.extra_home_goals,
        "extra_away_goals": row.extra_away_goals,
        "brier": row.brier,
        "log_loss": row.log_loss,
        "top_pick_correct": row.top_pick_correct,
    }


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
        "home_score": f.home_score,
        "away_score": f.away_score,
        "live_clock": f.live_clock,
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
    return [
        {
            "name": c.bookmaker_name,
            "connector_status": "configured" if getattr(c, "configured", False) else "not_configured",
            "source": getattr(c, "source", None),
        }
        for c in get_connectors()
    ]

@app.get("/connectors/status")
def connector_status(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Fixture.source, func.count()).group_by(Fixture.source)).all())
    return [
        {
            "bookmaker": c.bookmaker_name,
            "configured": getattr(c, "configured", False),
            "fixtures_available": counts.get(getattr(c, "source", None), 0),
            "notes": getattr(c, "notes", None),
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
    descending: bool = Query(False, description="Newest kickoff first instead of oldest first"),
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

    fixtures = db.scalars(
        query.order_by(
            Fixture.kickoff_utc.desc() if descending else Fixture.kickoff_utc.asc()
        ).limit(limit)
    ).all()
    return [_build_fixture_out(f) for f in fixtures]

@app.delete("/fixtures/past")
def delete_past_fixtures(db: Session = Depends(get_db)):
    """Delete all past fixtures that have already been played, keeping only future upcoming matches."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    deleted = db.execute(delete(Fixture).where(Fixture.kickoff_utc <= now)).rowcount
    db.commit()
    return {"message": f"Purged {deleted} past matches. Only genuine upcoming matches remain in the database.", "deleted_count": deleted}

@app.get("/fixtures/live", response_model=LiveFixturesOut)
def live_fixtures(db: Session = Depends(get_db)):
    """Refresh, snapshot, settle, then serve the stored rows."""
    refresh = refresh_live_scores(db)
    record_live_snapshots(db)
    settle_live_predictions(db)

    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(("live", "halftime")))
        .order_by(Fixture.kickoff_utc.asc())
    ).all()

    fixtures = []
    for fixture in rows:
        item = _build_fixture_out(fixture)
        item["live_prediction"] = _live_prediction_payload(
            _latest_live_prediction(db, fixture.id), now
        )
        fixtures.append(item)

    return {
        "as_of": now,
        "refreshed": bool(refresh.get("refreshed")),
        "warning": refresh.get("warning"),
        "fixtures": fixtures,
    }

@app.get("/fixtures/{fixture_id}", response_model=FixtureOut)
def get_fixture(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return _build_fixture_out(fixture)

@app.get("/fixtures/{fixture_id}/live")
def fixture_live_history(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    history = db.scalars(
        select(LivePrediction)
        .where(
            LivePrediction.fixture_id == fixture_id,
            LivePrediction.result.isnot(None),
            LivePrediction.settled_at >= cutoff,
        )
        .order_by(LivePrediction.minute_bucket.asc())
    ).all()

    return {
        "fixture_id": fixture_id,
        "latest": _live_prediction_payload(
            _latest_live_prediction(db, fixture_id), now
        ),
        "history": [_live_history_row(row) for row in history],
    }

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

@app.post("/fixtures/fetch-all-major-leagues")
def api_fetch_all_major_leagues(
    season: str = Query("2324", description="Season code (e.g. 2324 for 2023-2024, 2425 for 2024-2025)"),
    db: Session = Depends(get_db)
):
    """Fetch complete historical seasons for Premier League, La Liga, Serie A, Bundesliga, and Ligue 1, calibrating all team ratings."""
    try:
        return fetch_all_major_leagues(db, season=season)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to fetch all major leagues: {exc}") from exc

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
    target_date: Optional[str] = Query(None, description="Specific YYYY-MM-DD matchday (in EAT), or None for next active matchday"),
    num_legs: int = Query(3, ge=2, le=3, description="Target number of teams in the combo (strictly 2 or 3)"),
    selection_mode: str = Query("ev", pattern="^(ev|certainty)$", description="'ev' ranks legs by edge over the de-vigged bookmaker price; 'certainty' ranks by joint win probability"),
    min_edge_percent: float = Query(3.0, ge=0.0, le=50.0, description="EV mode: minimum model-probability edge over the de-vigged market price (percentage points)"),
    min_total_odds: Optional[float] = Query(None, ge=1.10, le=10.0, description="Minimum combined total odds (default 1.50 in EV mode, 2.00 in certainty mode)"),
    min_leg_prob: Optional[float] = Query(None, ge=0.50, le=0.99, description="Minimum individual leg win probability (default 0.55 in EV mode, 0.85 in certainty mode)"),
    min_accumulator_prob: Optional[float] = Query(None, ge=0.50, le=1.00, description="Certainty mode: minimum joint accumulator ticket win probability (e.g. 0.85 for 85%-100%)"),
    bankroll: float = Query(1000.0, ge=10.0, description="Bankroll for Kelly sizing"),
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    allow_synthetic_odds: bool = Query(False, description="Allow model-derived (estimated) prices, labelled as such. Off by default: only ingested bookmaker prices are eligible."),
    require_ratings: bool = Query(True, description="EV mode: only compare the independent team-ratings model against the market. With no ratings the model is fitted to the same 1X2 prices, so any edge would be circular."),
    max_edge_percent: float = Query(20.0, ge=3.0, le=50.0, description="EV mode: reject legs where model and market disagree by more than this (usually stale ratings, not a real edge)."),
    allow_unvalidated_ev: bool = Query(False, description="EV mode: return slips even when walk-forward evaluation shows the model has not beaten the market. Research only."),
    db: Session = Depends(get_db),
):
    """Generate daily accumulators. EV mode (default) targets model edge over the bookmaker's de-vigged price; certainty mode targets joint win probability >= 85%."""
    ev_mode = selection_mode == "ev"
    return generate_high_confidence_combos(
        db=db,
        target_date=target_date,
        num_legs=num_legs,
        min_leg_prob=min_leg_prob if min_leg_prob is not None else (0.55 if ev_mode else 0.85),
        min_accumulator_prob=min_accumulator_prob,
        min_total_odds=min_total_odds if min_total_odds is not None else (1.50 if ev_mode else 2.00),
        bankroll=bankroll,
        competition=competition,
        require_market_odds=not allow_synthetic_odds,
        selection_mode=selection_mode,
        min_edge_percent=min_edge_percent,
        require_ratings=require_ratings,
        max_edge_percent=max_edge_percent,
        allow_unvalidated_ev=allow_unvalidated_ev,
    )

@app.get("/portfolio/top-picks")
def get_top_picks(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD matchday in EAT, 'all', or None for the next matchday"),
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    threshold: float = Query(0.85, ge=0.85, le=0.99, description="Probability floor; never below the published 85% gate"),
    allow_synthetic_odds: bool = Query(False, description="Allow model-derived prices, labelled as such. Off by default."),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Choose the highest weighted market structure per fixture at >=85% win probability.

    One structure per fixture is selected from everything the model can price
    and a real bookmaker price exists for. The 85% probability floor and the
    0.60 data-confidence floor cannot be lowered by this endpoint.
    """
    return top_picks_for_matchday(
        db=db,
        target_date=target_date,
        competition=competition,
        threshold=threshold,
        require_market_odds=not allow_synthetic_odds,
        limit=limit,
    )


@app.get("/evaluation/calibration")
def get_calibration_report():
    """Return the last saved walk-forward calibration report (cheap, no recompute)."""
    from app.analytics.calibration import load_report
    report = load_report()
    if not report:
        raise HTTPException(
            status_code=404,
            detail="No calibration report yet. POST /evaluation/calibration to run the walk-forward evaluation.",
        )
    return report

@app.post("/evaluation/calibration")
def run_calibration(
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    refit_days: int = Query(14, ge=1, le=60, description="Days between refits; shorter is stricter but slower"),
    db: Session = Depends(get_db),
):
    """Replay history out-of-sample and compare the model with the de-vigged market.

    This is deliberately an explicit action: it takes tens of seconds because it
    refits the rating model hundreds of times.
    """
    from app.analytics.calibration import run_walk_forward, save_report
    try:
        report = run_walk_forward(db, competition=competition, refit_days=refit_days)
        if report.get("n_matches"):
            save_report(report)
        return report
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Calibration error: {exc}") from exc

@app.get("/evaluation/blend")
def get_blend_report():
    """Return the last fitted model-market blend (cheap, no recompute)."""
    from app.analytics.blending import load_report
    report = load_report()
    if not report or report.get("status") != "fitted":
        raise HTTPException(
            status_code=404,
            detail="No fitted blend report yet. POST /evaluation/blend to fit it.",
        )
    return report

@app.post("/evaluation/blend")
def fit_blend(
    competition: Optional[str] = Query(None, description="Optional competition filter"),
    refit_days: int = Query(14, ge=1, le=60, description="Days between refits; shorter is stricter but slower"),
    db: Session = Depends(get_db),
):
    """Fit the 1X2 model-market blend on a chronological walk-forward holdout.

    The weight is chosen on the earliest 70% of predictions and scored on the
    latest 30%. It is deliberately an explicit action because it replays the
    rating model hundreds of times.
    """
    from app.analytics.blending import fit_report, save_report
    try:
        report = fit_report(db, competition=competition, refit_days=refit_days)
        if report.get("status") == "fitted":
            save_report(report)
        return report
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Blend fit error: {exc}") from exc

@app.post("/evaluation/backtest")
def run_walkforward_backtest_api(
    min_edge_percent: float = Query(3.0, ge=0.0, le=50.0),
    staking: str = Query("flat", pattern="^(flat|quarter_kelly)$"),
    flat_stake_percent: float = Query(2.0, ge=0.1, le=10.0),
    initial_bankroll: float = Query(1000.0, ge=10.0),
    competition: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Backtest the EV strategy with ratings that never see the future."""
    from app.analytics.backtest import run_walkforward_backtest
    try:
        result = run_walkforward_backtest(
            db,
            min_edge_percent=min_edge_percent,
            staking=staking,
            flat_stake_percent=flat_stake_percent,
            initial_bankroll=initial_bankroll,
            competition=competition,
        )
        result.pop("history", None)  # keep the response small
        return result
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Backtest error: {exc}") from exc

@app.get("/evaluation/clv")
def get_clv(
    days: int = Query(90, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """Closing-line value: did the prices we took beat the closing price?"""
    from app.betting.clv import clv_summary
    from app.betting.settlement import synthetic_slip_ids
    return clv_summary(db, days=days, exclude_slip_ids=synthetic_slip_ids(db))

@app.get("/ratings/freshness")
def ratings_freshness(db: Session = Depends(get_db)):
    """How old is the evidence behind each competition's ratings?"""
    from sqlalchemy import func
    from app.analytics.ratings import STALE_AFTER_DAYS, data_age_days, ratings_snapshot_hash

    rows = db.execute(
        select(TeamRating.competition, func.count(TeamRating.id), func.max(TeamRating.last_match_at))
        .group_by(TeamRating.competition)
    ).all()
    competitions = []
    for comp, teams, last_match in rows:
        age = data_age_days(last_match)
        competitions.append({
            "competition": comp,
            "teams": teams,
            "data_age_days": age,
            "stale": bool(age is not None and age > STALE_AFTER_DAYS),
        })
    return {
        "snapshot_hash": ratings_snapshot_hash(db),
        "stale_after_days": STALE_AFTER_DAYS,
        "competitions": sorted(competitions, key=lambda c: -(c["teams"] or 0)),
    }

@app.get("/fixtures/{fixture_id}/deep-analysis")
def fixture_deep_analysis(
    fixture_id: int,
    home_lambda: Optional[float] = Query(None, ge=0.0, le=10.0),
    away_lambda: Optional[float] = Query(None, ge=0.0, le=10.0),
    rho: float = Query(-0.10, ge=-0.5, le=0.5),
    db: Session = Depends(get_db),
):
    """One-fixture dossier: model vs market, form splits, H2H, advisory team news and risks."""
    from app.analytics.match_analysis import build_match_dossier

    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return build_match_dossier(
        db, fixture, home_lambda=home_lambda, away_lambda=away_lambda, rho=rho
    )

@app.get("/learning/summary")
def learning_summary():
    """Learned calibration parameters and edge premiums from settled results."""
    from app.analytics.learning import summary
    return summary()

@app.post("/learning/train")
def learning_train(db: Session = Depends(get_db)):
    """Refit the self-correction layer from out-of-sample history and settled legs.

    Explicit action: it reruns the walk-forward predictions, so it is slow.
    """
    from app.analytics.learning import build_report, save_report
    try:
        report = build_report(db)
        save_report(report)
        return report
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Learning error: {exc}") from exc

@app.get("/ai/status")
def ai_status():
    """Whether a language model is configured for the advisory analyst."""
    from app.ai.ai_analyst import status
    return status()

@app.post("/ai/review")
def ai_review(payload: Dict[str, Any] = Body(...)):
    """Advisory review of a generated slip.

    Accepts the combo-engine response under ``slip``. Every number the analyst
    cites is checked against the engine's own fact sheet; the result never
    changes a selection.
    """
    from app.ai import ai_analyst
    slip = payload.get("slip")
    if not isinstance(slip, dict) or not slip.get("best_combo"):
        raise HTTPException(status_code=400, detail="Provide the combo-engine result under 'slip'")
    try:
        return ai_analyst.review(ai_analyst.facts_from_slip(slip))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Analyst error: {exc}") from exc

@app.post("/ai/ask")
def ai_ask(payload: Dict[str, Any] = Body(...)):
    """Ask the advisory analyst a question about a generated slip."""
    from app.ai import ai_analyst
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Provide a 'question'")
    if len(question) > ai_analyst.MAX_QUESTION:
        raise HTTPException(
            status_code=400,
            detail=f"Question is longer than {ai_analyst.MAX_QUESTION} characters",
        )
    slip = payload.get("slip")
    facts = ai_analyst.facts_from_slip(slip if isinstance(slip, dict) else None)
    try:
        return ai_analyst.ask(question, facts)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Analyst error: {exc}") from exc

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


@app.get("/ledger/summary")
def get_ledger_summary(db: Session = Depends(get_db)):
    """Retrieve overall performance ledger summary (wins, losses, win rate %, PnL in UGX/USD, ROI)."""
    from app.betting.settlement import get_performance_ledger_summary
    stats = get_performance_ledger_summary(db)
    # Remove SQLAlchemy objects from dict for pure JSON serialization
    recent = stats.pop("recent_bets", [])
    return {
        **stats,
        "recent_count": len(recent),
    }


@app.get("/ledger/history")
def get_ledger_history(
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Retrieve full historical tracked bets with leg outcomes and final scores."""
    from app.models.bet import TrackedBet
    import json
    bets = db.scalars(
        select(TrackedBet).order_by(desc(TrackedBet.placed_at)).limit(limit)
    ).all()
    out = []
    for b in bets:
        out.append({
            "id": b.id,
            "slip_type": b.slip_type,
            "matchday_label": b.matchday_label,
            "total_odds": b.total_odds,
            "avg_confidence": b.avg_confidence,
            "accum_probability": b.accum_probability,
            "stake_amount": b.stake_amount,
            "stake_currency": b.stake_currency,
            "stake_usd": b.stake_usd,
            "status": b.status,
            "profit_loss": b.profit_loss,
            "profit_loss_usd": b.profit_loss_usd,
            "placed_at": b.placed_at.isoformat() if b.placed_at else None,
            "settled_at": b.settled_at.isoformat() if b.settled_at else None,
            "legs": json.loads(b.legs_json),
        })
    return out


@app.post("/ledger/settle")
def trigger_settle_now(db: Session = Depends(get_db)):
    """Manually trigger settlement for all finished pending bets."""
    from app.betting.settlement import settle_pending_bets, get_performance_ledger_summary
    settled = settle_pending_bets(db)
    stats = get_performance_ledger_summary(db)
    stats.pop("recent_bets", None)
    return {
        "settled_count": len(settled),
        "ledger_stats": stats,
    }



# ── Modular platform routers ─────────────────────────────────────────────────
from app.api import ALL_ROUTERS  # noqa: E402

for _router in ALL_ROUTERS:
    app.include_router(_router)
