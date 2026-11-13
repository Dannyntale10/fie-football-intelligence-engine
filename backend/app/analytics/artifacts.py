"""Model artifacts that travel with the repository.

The hosted engine starts with an empty database. Loading `data/ratings.json`
into `TeamRating` at startup gives it the same prior the trainer measured,
without shipping the 50 MB history cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.ratings import (
    MIN_WEIGHTED_MATCHES,
    clean_competition_name,
    upsert_team_rating,
)
from app.database import SessionLocal
from app.models.predictions import SystemSetting
from app.models.team_rating import TeamRating

logger = logging.getLogger(__name__)

RATINGS_PATH = Path(__file__).resolve().parents[3] / "data" / "ratings.json"
RATINGS_SOURCE_KEY = "ratings_artifact_version"
LOCAL_RATINGS_SOURCE = "local"


def _artifact_hash(payload: Dict[str, Any]) -> str:
    """Hash the document with its own hash field blanked, so it can live inside."""
    body = dict(payload)
    body["content_hash"] = ""
    return "sha256:" + hashlib.sha256(
        json.dumps(body, indent=2, sort_keys=True).encode()
    ).hexdigest()


def _setting(db: Session, key: str) -> Optional[str]:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    return row.value if row else None


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row:
        row.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def write_ratings(
    path: Path,
    db: Session,
    *,
    half_life_days: float,
    trained_through: str,
    league_averages: Dict[str, Tuple[float, float]],
) -> Dict[str, Any]:
    rows = db.scalars(select(TeamRating)).all()
    competitions: Dict[str, Any] = {}
    teams = 0

    for row in rows:
        if (row.weighted_matches or 0.0) < MIN_WEIGHTED_MATCHES:
            continue
        block = competitions.setdefault(
            row.competition,
            {
                "league_avg_home": league_averages.get(row.competition, (1.50, 1.15))[0],
                "league_avg_away": league_averages.get(row.competition, (1.50, 1.15))[1],
                "teams": {},
            },
        )
        block["teams"][row.team_name] = {
            "attack": row.attack_rating,
            "defence": row.defense_rating,
            "weighted_matches": row.weighted_matches,
            "matches_played": row.matches_played,
            "home_attack_boost": row.home_attack_boost,
            "home_goals_avg": row.home_goals_avg,
            "away_goals_avg": row.away_goals_avg,
            "last_match_at": row.last_match_at.isoformat()
            if row.last_match_at
            else None,
        }
        teams += 1

    payload = {
        "artifact_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trained_through": trained_through,
        "half_life_days": half_life_days,
        "competitions": competitions,
    }
    payload["content_hash"] = _artifact_hash(payload)
    text = json.dumps(payload, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    return {
        "path": str(path),
        "teams": teams,
        "competitions": len(competitions),
        "content_hash": payload["content_hash"],
    }


def load_ratings(
    path: Path = RATINGS_PATH,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Seed or refresh the rating table from the committed artifact.

    The artifact is applied only when its ``content_hash`` differs from the
    stored artifact version. A fit computed from this database's own results
    outranks the artifact: it marks the stored version ``local`` and later
    startups leave those ratings alone.
    """
    if not path.exists():
        return 0

    db = session_factory()
    try:
        payload = json.loads(path.read_text())
        version = str(payload.get("content_hash") or _artifact_hash(payload))
        stored = _setting(db, RATINGS_SOURCE_KEY)

        if stored == LOCAL_RATINGS_SOURCE:
            logger.info("Ratings were fitted locally; keeping them over %s", path)
            return 0
        if stored and stored == version:
            return 0

        existing = db.scalar(select(func.count(TeamRating.id))) or 0
        if stored is None and existing:
            # Rows without provenance predate this loader; assume a local fit
            # outranks the artifact rather than overwriting measured ratings.
            _set_setting(db, RATINGS_SOURCE_KEY, LOCAL_RATINGS_SOURCE)
            db.commit()
            return 0

        loaded = 0
        for competition, block in (payload.get("competitions") or {}).items():
            comp = clean_competition_name(competition)
            db.query(TeamRating).filter(
                TeamRating.competition == comp
            ).delete(synchronize_session=False)
            for team, row in (block.get("teams") or {}).items():
                upsert_team_rating(
                    db,
                    team,
                    competition,
                    attack_rating=float(row["attack"]),
                    defense_rating=float(row["defence"]),
                    matches_played=int(row.get("matches_played") or 0),
                    home_attack_boost=float(row.get("home_attack_boost") or 0.0),
                    home_goals_avg=float(row.get("home_goals_avg") or 0.0),
                    away_goals_avg=float(row.get("away_goals_avg") or 0.0),
                    weighted_matches=float(row.get("weighted_matches") or 0.0),
                    last_match_at=_parse_dt(row.get("last_match_at")),
                    commit=False,
                )
                loaded += 1
        _set_setting(db, RATINGS_SOURCE_KEY, version)
        db.commit()
        logger.info("Loaded %s team ratings from %s", loaded, path)
        return loaded
    except Exception:
        db.rollback()
        logger.exception("Rating artifact load failed")
        return 0
    finally:
        db.close()
