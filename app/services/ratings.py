from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.team_rating import TeamRating
from app.models.fixture import Fixture
from app.services.fixtures import clean_team_name

DEFAULT_LEAGUE_AVG_HOME = 1.50
DEFAULT_LEAGUE_AVG_AWAY = 1.15

def get_team_rating(db: Session, team_name: str, competition: str) -> Optional[TeamRating]:
    """Retrieve rating for a team within a competition."""
    clean = clean_team_name(team_name)
    clean_comp = clean_team_name(competition)
    query = select(TeamRating).where(
        TeamRating.competition == clean_comp,
        TeamRating.team_name == clean
    )
    return db.scalar(query)

def upsert_team_rating(
    db: Session,
    team_name: str,
    competition: str,
    attack_rating: float,
    defense_rating: float,
    matches_played: int = 1,
    home_attack_boost: float = 0.0,
    home_goals_avg: float = 0.0,
    away_goals_avg: float = 0.0,
) -> TeamRating:
    """Create or update a team rating record."""
    clean = clean_team_name(team_name)
    clean_comp = clean_team_name(competition)
    rating = get_team_rating(db, clean, clean_comp)
    if rating:
        rating.attack_rating = round(attack_rating, 3)
        rating.defense_rating = round(defense_rating, 3)
        rating.matches_played = matches_played
        rating.home_attack_boost = round(home_attack_boost, 3)
        rating.home_goals_avg = round(home_goals_avg, 3)
        rating.away_goals_avg = round(away_goals_avg, 3)
        rating.updated_at = datetime.now(timezone.utc)
    else:
        rating = TeamRating(
            team_name=clean,
            competition=clean_comp,
            attack_rating=round(attack_rating, 3),
            defense_rating=round(defense_rating, 3),
            matches_played=matches_played,
            home_attack_boost=round(home_attack_boost, 3),
            home_goals_avg=round(home_goals_avg, 3),
            away_goals_avg=round(away_goals_avg, 3),
        )
        db.add(rating)
    db.commit()
    db.refresh(rating)
    return rating

def estimate_lambdas_from_ratings(
    db: Session,
    home_team: str,
    away_team: str,
    competition: str,
    league_avg_home: float = DEFAULT_LEAGUE_AVG_HOME,
    league_avg_away: float = DEFAULT_LEAGUE_AVG_AWAY
) -> Tuple[Optional[float], Optional[float], bool]:
    """Derive expected goals from attack and defense strength ratings.

    Returns (home_lambda, away_lambda, ratings_available).
    """
    home_r = get_team_rating(db, home_team, competition)
    away_r = get_team_rating(db, away_team, competition)

    if not home_r or not away_r:
        return None, None, False

    # Standard attack/defense strength formulation with per-team home advantage:
    home_boost = getattr(home_r, "home_attack_boost", 0.0) or 0.0
    home_boost_factor = 1.0 + min(0.35, max(0.0, home_boost * 0.25))

    home_lam = round(home_r.attack_rating * away_r.defense_rating * league_avg_home * home_boost_factor, 2)
    away_lam = round(away_r.attack_rating * home_r.defense_rating * league_avg_away, 2)

    # Bound to realistic range [0.1, 8.0]
    home_lam = max(0.1, min(8.0, home_lam))
    away_lam = max(0.1, min(8.0, away_lam))

    return home_lam, away_lam, True

def calculate_ratings_from_fixtures(db: Session, competition: Optional[str] = None) -> Dict[str, Any]:
    """Calculate and update team ratings from finished matches stored in the database."""
    query = select(Fixture).where(
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    matches = db.scalars(query).all()
    if not matches:
        return {"updated_teams": 0, "matches_analyzed": 0, "message": "No finished matches with scores found."}

    # Aggregate stats per competition and team
    comp_stats: Dict[str, Dict[str, Any]] = {}
    for m in matches:
        comp = clean_team_name(m.competition)
        if comp not in comp_stats:
            comp_stats[comp] = {
                "total_home_goals": 0,
                "total_away_goals": 0,
                "match_count": 0,
                "teams": {}
            }
        cs = comp_stats[comp]
        cs["match_count"] += 1
        cs["total_home_goals"] += m.home_score
        cs["total_away_goals"] += m.away_score

        # Team stats
        for side, team, scored, conceded in [
            ("home", clean_team_name(m.home_team), m.home_score, m.away_score),
            ("away", clean_team_name(m.away_team), m.away_score, m.home_score)
        ]:
            if team not in cs["teams"]:
                cs["teams"][team] = {
                    "home_scored": 0, "home_conceded": 0, "home_matches": 0,
                    "away_scored": 0, "away_conceded": 0, "away_matches": 0,
                }
            ts = cs["teams"][team]
            if side == "home":
                ts["home_scored"] += scored
                ts["home_conceded"] += conceded
                ts["home_matches"] += 1
            else:
                ts["away_scored"] += scored
                ts["away_conceded"] += conceded
                ts["away_matches"] += 1

    total_updated = 0
    for comp, cs in comp_stats.items():
        if cs["match_count"] == 0:
            continue
        avg_h = cs["total_home_goals"] / cs["match_count"] or DEFAULT_LEAGUE_AVG_HOME
        avg_a = cs["total_away_goals"] / cs["match_count"] or DEFAULT_LEAGUE_AVG_AWAY

        for team, ts in cs["teams"].items():
            total_m = ts["home_matches"] + ts["away_matches"]
            if total_m == 0:
                continue

            # Attack rating: relative scoring rate compared to league average
            home_att = (ts["home_scored"] / ts["home_matches"]) / avg_h if ts["home_matches"] > 0 else 1.0
            away_att = (ts["away_scored"] / ts["away_matches"]) / avg_a if ts["away_matches"] > 0 else 1.0
            overall_att = (home_att * ts["home_matches"] + away_att * ts["away_matches"]) / total_m

            # Defense rating: relative conceding rate compared to league average (lower is better defense)
            home_def = (ts["home_conceded"] / ts["home_matches"]) / avg_a if ts["home_matches"] > 0 else 1.0
            away_def = (ts["away_conceded"] / ts["away_matches"]) / avg_h if ts["away_matches"] > 0 else 1.0
            overall_def = (home_def * ts["home_matches"] + away_def * ts["away_matches"]) / total_m

            # Per-team Home Advantage metrics
            h_avg = (ts["home_scored"] / ts["home_matches"]) if ts["home_matches"] > 0 else avg_h
            a_avg = (ts["away_scored"] / ts["away_matches"]) if ts["away_matches"] > 0 else avg_a
            h_boost = max(0.0, (h_avg / max(0.5, a_avg)) - 1.0)

            upsert_team_rating(
                db=db,
                team_name=team,
                competition=comp,
                attack_rating=overall_att,
                defense_rating=overall_def,
                matches_played=total_m,
                home_attack_boost=h_boost,
                home_goals_avg=h_avg,
                away_goals_avg=a_avg,
            )
            total_updated += 1

    return {
        "updated_teams": total_updated,
        "matches_analyzed": len(matches),
        "message": f"Successfully updated ratings for {total_updated} team(s) from {len(matches)} match(es)."
    }
