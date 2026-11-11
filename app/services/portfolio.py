from __future__ import annotations
"""
portfolio.py – Daily value portfolio builder.

Scans upcoming fixtures, runs analyze_fixture() on each eligible match,
and returns all +EV markets and ≥85% qualifying markets as a shortlist.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.probabilities import analyze_fixture


def build_daily_portfolio(
    db: Session,
    days_ahead: int = 7,
    min_ev_percent: float = 0.0,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    rho: float = -0.10,
    qualification_threshold: float = 0.85,
    competition: Optional[str] = None,
) -> Dict[str, Any]:
    """Loop over scheduled upcoming fixtures and collect all +EV and qualifying markets.

    Args:
        db: SQLAlchemy session.
        days_ahead: Look-ahead window in days (default 7).
        min_ev_percent: Only include value bets with EV >= this % (default 0.0 = any +EV).
        bankroll: Bankroll used for Kelly sizing.
        kelly_fraction: Fraction of full Kelly (0.25 = quarter Kelly).
        rho: Dixon-Coles correction factor.
        qualification_threshold: 85% default.
        competition: Optional filter by competition name substring.

    Returns:
        dict with 'bets', 'qualifying_markets', 'summary' keys.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=days_ahead)

    query = (
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc >= now)
        .where(Fixture.kickoff_utc <= cutoff)
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    fixtures = db.scalars(query).all()

    value_slip: List[Dict[str, Any]] = []
    qualifying_slip: List[Dict[str, Any]] = []
    analysed = 0
    skipped = 0

    for fixture in fixtures:
        # Only analyse fixtures that have at least one valid odds column
        if not (fixture.home_odds and fixture.away_odds and
                fixture.home_odds > 1.0 and fixture.away_odds > 1.0):
            skipped += 1
            continue

        try:
            result = analyze_fixture(
                fixture,
                qualification_threshold=qualification_threshold,
                rho=rho,
                db=db,
                bankroll=bankroll,
                kelly_fraction=kelly_fraction,
            )
        except Exception:
            skipped += 1
            continue

        analysed += 1
        match_label = f"{fixture.home_team} vs {fixture.away_team}"
        kickoff = fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None

        # Collect +EV bets above min_ev_percent
        for vb in result.get("value_bets", []):
            if vb.get("is_value") and vb.get("expected_value_percent", 0) >= min_ev_percent:
                value_slip.append({
                    "fixture_id": fixture.id,
                    "kickoff_utc": kickoff,
                    "match": match_label,
                    "competition": fixture.competition,
                    "country": fixture.country,
                    "market": vb["market"],
                    "market_key": vb["market_key"],
                    "model_probability": vb["model_probability"],
                    "bookmaker_odds": vb["bookmaker_odds"],
                    "implied_probability": vb["implied_probability"],
                    "fair_odds": vb.get("fair_odds"),
                    "edge_percent": round(vb["edge"] * 100, 2),
                    "ev_percent": vb["expected_value_percent"],
                    "kelly_stake_amount": vb.get("kelly", {}).get("stake_amount", 0),
                    "kelly_stake_percent": vb.get("kelly", {}).get("stake_percent", 0),
                    "lambda_source": result.get("lambda_source", "odds_implied"),
                })

        # Collect qualifying markets (≥ threshold)
        for qm in result.get("qualifying_markets", []):
            qualifying_slip.append({
                "fixture_id": fixture.id,
                "kickoff_utc": kickoff,
                "match": match_label,
                "competition": fixture.competition,
                "country": fixture.country,
                "market": qm["market"],
                "probability": qm["probability"],
                "lambda_source": result.get("lambda_source", "odds_implied"),
            })

    # Sort by EV descending
    value_slip.sort(key=lambda x: x["ev_percent"], reverse=True)
    qualifying_slip.sort(key=lambda x: x["probability"], reverse=True)

    total_kelly_exposure = sum(b["kelly_stake_amount"] for b in value_slip)

    return {
        "generated_at": now.isoformat(),
        "params": {
            "days_ahead": days_ahead,
            "min_ev_percent": min_ev_percent,
            "bankroll": bankroll,
            "kelly_fraction": kelly_fraction,
            "rho": rho,
            "qualification_threshold": qualification_threshold,
        },
        "summary": {
            "fixtures_scanned": len(fixtures),
            "fixtures_analysed": analysed,
            "fixtures_skipped_no_odds": skipped,
            "value_bets_found": len(value_slip),
            "qualifying_markets_found": len(qualifying_slip),
            "total_kelly_exposure": round(total_kelly_exposure, 2),
        },
        "value_bets": value_slip,
        "qualifying_markets": qualifying_slip,
    }
