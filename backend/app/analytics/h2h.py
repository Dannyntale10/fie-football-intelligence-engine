from __future__ import annotations
"""
h2h.py – Head-to-Head historical match analytics.
"""
from typing import Any, Dict, Tuple
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session
from app.models.fixture import Fixture

def get_h2h_stats(db: Session, home_team: str, away_team: str, max_matches: int = 10) -> Dict[str, Any]:
    """Get head-to-head statistics between two teams."""
    finished = db.scalars(
        select(Fixture)
        .where(Fixture.home_score.isnot(None))
        .where(or_(
            and_(Fixture.home_team == home_team, Fixture.away_team == away_team),
            and_(Fixture.home_team == away_team, Fixture.away_team == home_team),
        ))
        .order_by(Fixture.kickoff_utc.desc())
        .limit(max_matches)
    ).all()

    if not finished:
        return {
            "total": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
            "avg_total_goals": 0.0, "avg_home_goals": 0.0, "avg_away_goals": 0.0,
            "last_5_results": [], "form_string": ""
        }

    home_wins = draws = away_wins = 0
    total_goals = home_goals = away_goals = 0
    results = []

    for m in finished:
        h, a = m.home_score, m.away_score
        total_goals += h + a
        # Normalise from perspective of current fixture's `home_team`
        if m.home_team == home_team:
            hg, ag = h, a
        else:
            hg, ag = a, h
        home_goals += hg
        away_goals += ag
        if hg > ag:
            home_wins += 1
            results.append("W")
        elif hg == ag:
            draws += 1
            results.append("D")
        else:
            away_wins += 1
            results.append("L")

    n = len(finished)
    return {
        "total": n,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "avg_total_goals": round(total_goals / n, 2),
        "avg_home_goals": round(home_goals / n, 2),
        "avg_away_goals": round(away_goals / n, 2),
        "last_5_results": results[:5],
        "form_string": "".join(results[:5]),
    }

def h2h_lambda_adjustment(
    db: Session,
    home_team: str,
    away_team: str,
    home_lambda: float,
    away_lambda: float,
    weight: float = 0.20
) -> Tuple[float, float]:
    """Slightly adjust Poisson lambdas based on long-term H2H scoring trends."""
    stats = get_h2h_stats(db, home_team, away_team)
    if stats["total"] < 3:
        return home_lambda, away_lambda

    adj_h = home_lambda * (1.0 - weight) + stats["avg_home_goals"] * weight
    adj_a = away_lambda * (1.0 - weight) + stats["avg_away_goals"] * weight
    return round(adj_h, 2), round(adj_a, 2)
