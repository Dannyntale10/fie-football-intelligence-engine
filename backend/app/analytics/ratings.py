from __future__ import annotations
"""Team strength ratings with time decay and Bayesian shrinkage.

Attack and defence strengths are solved jointly per competition with a
Dixon-Coles style iterative fit, then shrunk toward the league mean so a team
with three matches is not trusted like a team with thirty. Each match is
weighted by how recently it was played *within that competition's data*, and
the absolute age of the data is reported separately so callers can see when a
rating describes a squad that no longer exists.
"""
import difflib
import hashlib
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.models.team_rating import TeamRating
from app.data_sources.fixtures import clean_competition_name, clean_team_name

DEFAULT_LEAGUE_AVG_HOME = 1.50
DEFAULT_LEAGUE_AVG_AWAY = 1.15

# How fast a match loses influence within a competition's own history.
RATING_HALF_LIFE_DAYS = 150.0
MIN_MATCH_WEIGHT = 0.03
# Pseudo-matches pulling a team toward the league average (empirical Bayes).
PRIOR_MATCHES = 4.0
# A rating built from less than this much decayed evidence is not usable.
MIN_WEIGHTED_MATCHES = 1.5
# Beyond this the rating is flagged stale; it still exists but is not a
# description of the current squad.
STALE_AFTER_DAYS = 400.0

IGNORED_COMPETITIONS = {
    "test league",
    "example national league",
    "example premier division",
}

# Names that differ between feeds in ways generic rules cannot bridge.
RATING_ALIASES: Dict[str, str] = {
    "nott m forest": "nottingham forest",
    "notts forest": "nottingham forest",
    "sheffield weds": "sheffield wednesday",
    "west brom": "west bromwich albion",
    "west bromwich": "west bromwich albion",
    "brighton hove albion": "brighton",
    "brighton and hove albion": "brighton",
    "manchester city": "manchester city",
    "manchester united": "manchester united",
    "inter": "internazionale",
    "inter milan": "internazionale",
    "ac milan": "milan",
    "bayern munchen": "bayern munich",
    "borussia monchengladbach": "monchengladbach",
    "paris st germain": "paris saint germain",
    "psg": "paris saint germain",
    "sporting cp": "sporting lisbon",
    "sporting": "sporting lisbon",
    "benfica": "benfica",
    "porto": "fc porto",
    "braga": "sc braga",
    "vitesse": "vitesse arnhem",
    "sparta rotterdam": "sparta rotterdam",
}

_CLUB_STOPWORDS = {"afc", "fc", "cf", "sc", "ac", "as", "ssc", "cs", "cd", "club", "the"}


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def data_age_days(last_match_at: Optional[datetime], reference: Optional[datetime] = None) -> Optional[float]:
    """Age in days of the newest data behind a rating, or ``None`` when unknown."""
    if last_match_at is None:
        return None
    ref = reference or datetime.now(timezone.utc)
    return round((_utc(ref) - _utc(last_match_at)).total_seconds() / 86400.0, 1)


def rating_key(name: str) -> str:
    """Canonical comparison key for a team name used by the rating model.

    Kept separate from ``clean_team_name`` because the latter also feeds the
    fixture dedupe key; changing it would re-key existing rows.
    """
    cleaned = clean_team_name(name)
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split() if t not in _CLUB_STOPWORDS]
    if not tokens:
        tokens = cleaned.split()
    key = " ".join(tokens)
    return RATING_ALIASES.get(key, key)


def decay_weight(match_dt: datetime, reference_dt: datetime, half_life_days: float = RATING_HALF_LIFE_DAYS) -> float:
    """Exponential weight for a match played ``match_dt`` before ``reference_dt``."""
    age_days = max(0.0, (_utc(reference_dt) - _utc(match_dt)).total_seconds() / 86400.0)
    return max(MIN_MATCH_WEIGHT, 0.5 ** (age_days / half_life_days))


def _rating_index(db: Session, competition: str) -> Dict[str, TeamRating]:
    """Per-session index of a competition's ratings keyed by ``rating_key``."""
    cache: Dict[str, Dict[str, TeamRating]] = db.info.setdefault("_rating_index", {})
    comp = clean_competition_name(competition)
    if comp in cache:
        return cache[comp]
    rows = db.scalars(select(TeamRating).where(TeamRating.competition == comp)).all()
    index = {rating_key(r.team_name): r for r in rows}
    cache[comp] = index
    return index


def _match_rating(index: Dict[str, TeamRating], team_name: str) -> Optional[TeamRating]:
    key = rating_key(team_name)
    if not key:
        return None
    exact = index.get(key)
    if exact:
        return exact

    # Token containment: "bournemouth" vs "afc bournemouth", "brighton" vs
    # "brighton hove albion". Require a meaningful token to avoid matching
    # "City" to "Manchester City".
    key_tokens = set(key.split())
    best_subset: Optional[Tuple[int, TeamRating]] = None
    for candidate_key, rating in index.items():
        cand_tokens = set(candidate_key.split())
        smaller, larger = (key_tokens, cand_tokens) if len(key_tokens) <= len(cand_tokens) else (cand_tokens, key_tokens)
        # A single-token prefix ("hull" for "hull city") is safe; a bare
        # "city" is not, so the prefix rule is restricted to the leading token.
        prefix_hit = (
            len(smaller) == 1
            and len(next(iter(smaller))) >= 4
            and larger.split()[0] in smaller
        )
        if smaller and smaller.issubset(larger) and (max(len(t) for t in smaller) >= 5 or prefix_hit):
            score = len(smaller)
            if best_subset is None or score > best_subset[0]:
                best_subset = (score, rating)
    if best_subset:
        return best_subset[1]

    best_ratio = 0.0
    best_rating: Optional[TeamRating] = None
    for candidate_key, rating in index.items():
        ratio = difflib.SequenceMatcher(None, key, candidate_key).ratio()
        if ratio > best_ratio:
            best_ratio, best_rating = ratio, rating
    return best_rating if best_ratio >= 0.88 else None


def get_team_rating(db: Session, team_name: str, competition: str) -> Optional[TeamRating]:
    """Retrieve a team's rating within a competition, tolerating feed name variants."""
    return _match_rating(_rating_index(db, competition), team_name)


def get_rating_context(db: Session, home_team: str, away_team: str, competition: str) -> Dict[str, Any]:
    """Freshness metadata for the ratings behind a fixture."""
    home_r = get_team_rating(db, home_team, competition)
    away_r = get_team_rating(db, away_team, competition)
    now = datetime.now(timezone.utc)

    def age(r: Optional[TeamRating]) -> Optional[float]:
        if not r or not getattr(r, "last_match_at", None):
            return None
        return round((now - _utc(r.last_match_at)).total_seconds() / 86400.0, 1)

    home_age, away_age = age(home_r), age(away_r)
    ages = [a for a in (home_age, away_age) if a is not None]
    oldest = max(ages) if ages else None
    return {
        "home_rated": home_r is not None,
        "away_rated": away_r is not None,
        "home_weighted_matches": round(home_r.weighted_matches or 0.0, 2) if home_r else 0.0,
        "away_weighted_matches": round(away_r.weighted_matches or 0.0, 2) if away_r else 0.0,
        "home_data_age_days": home_age,
        "away_data_age_days": away_age,
        "data_age_days": oldest,
        "stale": bool(oldest is not None and oldest > STALE_AFTER_DAYS),
    }


def ratings_snapshot_hash(db: Session) -> str:
    """Stable fingerprint of the rating table for provenance on generated slips."""
    rows = db.execute(
        select(TeamRating.competition, TeamRating.team_name, TeamRating.attack_rating, TeamRating.defense_rating)
        .order_by(TeamRating.competition, TeamRating.team_name)
    ).all()
    digest = hashlib.sha256()
    for comp, team, att, dfn in rows:
        digest.update(f"{comp}|{team}|{round(float(att), 4)}|{round(float(dfn), 4)};".encode())
    return digest.hexdigest()[:16]


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
    weighted_matches: Optional[float] = None,
    last_match_at: Optional[datetime] = None,
    commit: bool = True,
) -> TeamRating:
    """Create or update a team rating record.

    ``commit=False`` lets a caller that writes many ratings commit once.
    Each commit expires every object in the session, so committing per team
    while a full history is loaded is quadratic in the history size.
    """
    if weighted_matches is None:
        weighted_matches = float(matches_played)
    clean = rating_key(team_name)
    clean_comp = clean_competition_name(competition)
    rating = db.scalar(
        select(TeamRating).where(
            TeamRating.competition == clean_comp,
            TeamRating.team_name == clean,
        )
    )
    if rating:
        rating.attack_rating = round(attack_rating, 3)
        rating.defense_rating = round(defense_rating, 3)
        rating.matches_played = matches_played
        rating.home_attack_boost = round(home_attack_boost, 3)
        rating.home_goals_avg = round(home_goals_avg, 3)
        rating.away_goals_avg = round(away_goals_avg, 3)
        rating.weighted_matches = round(weighted_matches, 3)
        rating.last_match_at = last_match_at
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
            weighted_matches=round(weighted_matches, 3),
            last_match_at=last_match_at,
        )
        db.add(rating)
    if commit:
        db.commit()
        db.refresh(rating)
    return rating


def fit_strength_arrays(
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    weights: np.ndarray,
    home_goals: np.ndarray,
    away_goals: np.ndarray,
    n_teams: int,
    league_avg_home: float,
    league_avg_away: float,
    iterations: int = 10,
) -> Dict[str, np.ndarray]:
    """Vectorised attack/defence solve over per-team index arrays.

    Same algorithm as the loop formulation -- posterior means with
    ``PRIOR_MATCHES`` pseudo-matches, weighted by decay -- but expressed with
    ``bincount`` so a 26-year refit stays minutes instead of hours. The sums
    that do not depend on the current strengths (scored, conceded, weights)
    are hoisted out of the iteration.
    """
    home_idx = np.asarray(home_idx, dtype=np.int64)
    away_idx = np.asarray(away_idx, dtype=np.int64)
    weights = np.asarray(weights, dtype=np.float64)
    home_goals = np.asarray(home_goals, dtype=np.float64)
    away_goals = np.asarray(away_goals, dtype=np.float64)

    home_weight = np.bincount(home_idx, weights=weights, minlength=n_teams)
    away_weight = np.bincount(away_idx, weights=weights, minlength=n_teams)
    weight_sum = home_weight + away_weight
    scored = np.bincount(home_idx, weights=weights * home_goals, minlength=n_teams) + np.bincount(
        away_idx, weights=weights * away_goals, minlength=n_teams
    )
    conceded = np.bincount(home_idx, weights=weights * away_goals, minlength=n_teams) + np.bincount(
        away_idx, weights=weights * home_goals, minlength=n_teams
    )
    home_weighted_goals = np.bincount(home_idx, weights=weights * home_goals, minlength=n_teams)
    away_weighted_goals = np.bincount(away_idx, weights=weights * away_goals, minlength=n_teams)

    attack = np.ones(n_teams)
    defence = np.ones(n_teams)
    empty = weight_sum <= 0.0

    for _ in range(iterations):
        exp_home = league_avg_home * defence[away_idx]
        exp_away = league_avg_away * defence[home_idx]
        expected_scored = np.bincount(
            home_idx, weights=weights * exp_home, minlength=n_teams
        ) + np.bincount(away_idx, weights=weights * exp_away, minlength=n_teams)
        expected_conceded = np.bincount(
            home_idx, weights=weights * exp_away, minlength=n_teams
        ) + np.bincount(away_idx, weights=weights * exp_home, minlength=n_teams)

        attack = (scored + PRIOR_MATCHES) / (expected_scored + PRIOR_MATCHES)
        defence = (conceded + PRIOR_MATCHES) / (expected_conceded + PRIOR_MATCHES)
        attack[empty] = 1.0
        defence[empty] = 1.0

        total_w = float(weight_sum.sum()) or 1.0
        mean_attack = float((attack * weight_sum).sum()) / total_w
        mean_defence = float((defence * weight_sum).sum()) / total_w
        if mean_attack > 0.0:
            attack = attack / mean_attack
        if mean_defence > 0.0:
            defence = defence / mean_defence

    home_goals_avg = np.where(
        home_weight > 0.0,
        home_weighted_goals / np.where(home_weight > 0.0, home_weight, 1.0),
        league_avg_home,
    )
    away_goals_avg = np.where(
        away_weight > 0.0,
        away_weighted_goals / np.where(away_weight > 0.0, away_weight, 1.0),
        league_avg_away,
    )
    boost = np.maximum(0.0, (home_goals_avg / np.maximum(0.5, away_goals_avg)) - 1.0)

    return {
        "attack": attack,
        "defence": defence,
        "weighted_matches": weight_sum,
        "boost": boost,
        "home_goals_avg": home_goals_avg,
        "away_goals_avg": away_goals_avg,
        "matches_played": (
            np.bincount(home_idx, minlength=n_teams)
            + np.bincount(away_idx, minlength=n_teams)
        ).astype(np.int64),
    }


def fit_competition(
    matches: List[Dict[str, Any]],
    league_avg_home: float,
    league_avg_away: float,
    iterations: int = 10,
) -> Dict[str, Dict[str, float]]:
    """Iteratively solve attack/defence strengths with shrinkage and decay weights."""
    teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
    index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)

    fitted = fit_strength_arrays(
        home_idx=np.fromiter((index[m["home"]] for m in matches), dtype=np.int64, count=len(matches)),
        away_idx=np.fromiter((index[m["away"]] for m in matches), dtype=np.int64, count=len(matches)),
        weights=np.fromiter((m["weight"] for m in matches), dtype=np.float64, count=len(matches)),
        home_goals=np.fromiter((m["home_goals"] for m in matches), dtype=np.float64, count=len(matches)),
        away_goals=np.fromiter((m["away_goals"] for m in matches), dtype=np.float64, count=len(matches)),
        n_teams=n_teams,
        league_avg_home=league_avg_home,
        league_avg_away=league_avg_away,
        iterations=iterations,
    )

    last_match: Dict[str, datetime] = {}
    for match in matches:
        kickoff = match["kickoff"]
        for team in (match["home"], match["away"]):
            if team not in last_match or kickoff > last_match[team]:
                last_match[team] = kickoff

    result: Dict[str, Dict[str, Any]] = {}
    for i, team in enumerate(teams):
        result[team] = {
            "attack": float(fitted["attack"][i]),
            "defence": float(fitted["defence"][i]),
            "weighted_matches": float(fitted["weighted_matches"][i]),
            "matches_played": int(fitted["matches_played"][i]),
            "home_goals_avg": float(fitted["home_goals_avg"][i]),
            "away_goals_avg": float(fitted["away_goals_avg"][i]),
            "home_boost": float(fitted["boost"][i]),
            "last_match_at": last_match.get(team),
        }
    return result


def calculate_ratings_from_fixtures(
    db: Session,
    competition: Optional[str] = None,
    replace: bool = True,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
) -> Dict[str, Any]:
    """Rebuild team ratings from finished matches, with decay and shrinkage."""
    query = select(Fixture).where(
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None),
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    fixtures = db.scalars(query).all()
    if not fixtures:
        return {"updated_teams": 0, "matches_analyzed": 0, "message": "No finished matches with scores found."}

    by_competition: Dict[str, List[Fixture]] = defaultdict(list)
    for f in fixtures:
        comp = clean_competition_name(f.competition or "")
        if not comp or comp in IGNORED_COMPETITIONS:
            continue
        by_competition[comp].append(f)

    total_updated = 0
    total_matches = 0
    competitions: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for comp, comp_fixtures in by_competition.items():
        if replace:
            db.execute(delete(TeamRating).where(TeamRating.competition == comp))

        # Decay is relative to the newest match in this competition's history:
        # it expresses "how much of the recent past is this", not absolute age.
        reference = max(_utc(f.kickoff_utc) for f in comp_fixtures)
        rows: List[Dict[str, Any]] = []
        for f in comp_fixtures:
            kickoff = _utc(f.kickoff_utc)
            rows.append({
                "home": rating_key(f.home_team),
                "away": rating_key(f.away_team),
                "home_goals": int(f.home_score),
                "away_goals": int(f.away_score),
                "weight": decay_weight(kickoff, reference, half_life_days=half_life_days),
                "kickoff": kickoff,
            })

        total_goals = sum(r["home_goals"] + r["away_goals"] for r in rows)
        total_weight = sum(r["weight"] for r in rows) or 1.0
        league_avg_home = sum(r["home_goals"] for r in rows) / total_weight or DEFAULT_LEAGUE_AVG_HOME
        league_avg_away = sum(r["away_goals"] for r in rows) / total_weight or DEFAULT_LEAGUE_AVG_AWAY

        fitted = fit_competition(rows, league_avg_home, league_avg_away)

        data_age_days = round((now - reference).total_seconds() / 86400.0, 1)
        for team, values in fitted.items():
            if not team:
                continue
            upsert_team_rating(
                db=db,
                team_name=team,
                competition=comp,
                attack_rating=values["attack"],
                defense_rating=values["defence"],
                matches_played=values["matches_played"],
                home_attack_boost=values["home_boost"],
                home_goals_avg=values["home_goals_avg"],
                away_goals_avg=values["away_goals_avg"],
                weighted_matches=values["weighted_matches"],
                last_match_at=values["last_match_at"],
                commit=False,
            )
            total_updated += 1

        db.commit()

        total_matches += len(rows)
        competitions.append({
            "competition": comp,
            "matches": len(rows),
            "teams": len(fitted),
            "data_age_days": data_age_days,
            "stale": data_age_days > STALE_AFTER_DAYS,
            "avg_goals": round(total_goals / max(1, len(rows)), 2),
        })

    db.commit()
    db.info.pop("_rating_index", None)

    return {
        "updated_teams": total_updated,
        "matches_analyzed": total_matches,
        "competitions": sorted(competitions, key=lambda c: -c["matches"]),
        "stale_competitions": [c["competition"] for c in competitions if c["stale"]],
        "message": (
            f"Rebuilt ratings for {total_updated} team(s) from {total_matches} match(es) "
            f"across {len(competitions)} competition(s)."
        ),
    }


def estimate_lambdas_from_ratings(
    db: Session,
    home_team: str,
    away_team: str,
    competition: str,
    league_avg_home: float = DEFAULT_LEAGUE_AVG_HOME,
    league_avg_away: float = DEFAULT_LEAGUE_AVG_AWAY,
    min_weighted_matches: float = MIN_WEIGHTED_MATCHES,
) -> Tuple[Optional[float], Optional[float], bool]:
    """Derive expected goals from attack and defence strengths."""
    home_r = get_team_rating(db, home_team, competition)
    away_r = get_team_rating(db, away_team, competition)

    if not home_r or not away_r:
        return None, None, False
    if (home_r.weighted_matches or 0.0) < min_weighted_matches:
        return None, None, False
    if (away_r.weighted_matches or 0.0) < min_weighted_matches:
        return None, None, False

    home_boost = getattr(home_r, "home_attack_boost", 0.0) or 0.0
    home_boost_factor = 1.0 + min(0.35, max(0.0, home_boost * 0.25))

    # attack * opponent defence * league average for that side, where a
    # defence rating above 1.0 means the team concedes more than average.
    home_lam = home_r.attack_rating * away_r.defense_rating * league_avg_home * home_boost_factor
    away_lam = away_r.attack_rating * home_r.defense_rating * league_avg_away

    # The ratings are multiplicative around 1.0, so a neutral matchup reproduces
    # the historical league averages.
    home_lam = max(0.1, min(8.0, round(home_lam, 2)))
    away_lam = max(0.1, min(8.0, round(away_lam, 2)))
    return home_lam, away_lam, True
