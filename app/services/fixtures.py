from __future__ import annotations
import hashlib
import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional, Tuple
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture

COMMON_TEAM_ALIASES: dict[str, str] = {
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "wolves": "wolverhampton wanderers",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "psg": "paris saint germain",
}

COMPETITION_ALIASES: dict[str, str] = {
    "english premier league": "premier league",
    "epl": "premier league",
    "spanish la liga": "la liga",
    "primera division": "la liga",
    "la liga santander": "la liga",
    "la liga ea sports": "la liga",
    "italian serie a": "serie a",
    "german bundesliga": "bundesliga",
    "french ligue 1": "ligue 1",
}

def clean_competition_name(value: str) -> str:
    """Normalize competition names, resolving synonyms and regional naming variations."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("utf-8")
    without_dots = normalized.replace(".", "")
    cleaned = re.sub(r"[^a-z0-9 ]", " ", without_dots.lower())
    res = " ".join(cleaned.split())
    return COMPETITION_ALIASES.get(res, res)

def clean_team_name(value: str) -> str:
    """Normalize team names: strip diacritics/accents, lower-case, remove punctuation, map aliases."""
    if not value:
        return ""
    # Normalize unicode (e.g. Atlético -> Atletico, München -> Munchen)
    normalized = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("utf-8")
    # Remove periods directly so abbreviations like F.C. become FC instead of f c
    without_dots = normalized.replace(".", "")
    cleaned = re.sub(r"[^a-z0-9 ]", " ", without_dots.lower())
    res = " ".join(cleaned.split())
    return COMMON_TEAM_ALIASES.get(res, res)

def parse_kickoff_utc(val: Any) -> datetime:
    """Parse various timestamp representations into a timezone-aware UTC datetime."""
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, pd.Timestamp):
        dt = val.to_pydatetime()
    else:
        raw = str(val).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            # Fallback for pandas/general datetime formats
            dt = pd.to_datetime(raw, utc=True).to_pydatetime()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def make_dedupe_key(kickoff: datetime, home: str, away: str, competition: str) -> str:
    """Generate a stable SHA256 deduplication key across bookmakers and data providers."""
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    minute = kickoff.astimezone(timezone.utc).replace(second=0, microsecond=0).isoformat()
    raw = "|".join([minute, clean_team_name(home), clean_team_name(away), clean_competition_name(competition)])
    return hashlib.sha256(raw.encode()).hexdigest()

def _float_or_none(value: Any) -> Optional[float]:
    """Safely convert strings or numbers to float, treating NaN and empty strings as None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    val_str = str(value).strip().replace(",", ".")
    if val_str == "" or val_str.lower() in ("nan", "none", "null"):
        return None
    try:
        val = float(val_str)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except (ValueError, TypeError):
        return None

def import_fixture(db: Session, row: dict[str, Any]) -> Tuple[Fixture, bool]:
    """Import a single fixture row with deduplication and partial backfill of odds."""
    kickoff = parse_kickoff_utc(row["kickoff_utc"])
    key = make_dedupe_key(kickoff, str(row["home_team"]), str(row["away_team"]), str(row["competition"]))
    existing = db.scalar(select(Fixture).where(Fixture.dedupe_key == key))

    if existing:
        # If existing record is missing odds and the duplicate has them, backfill them
        updated = False
        new_home_odds = _float_or_none(row.get("home_odds"))
        new_draw_odds = _float_or_none(row.get("draw_odds"))
        new_away_odds = _float_or_none(row.get("away_odds"))

        if existing.home_odds is None and new_home_odds is not None:
            existing.home_odds = new_home_odds
            updated = True
        if existing.draw_odds is None and new_draw_odds is not None:
            existing.draw_odds = new_draw_odds
            updated = True
        if existing.away_odds is None and new_away_odds is not None:
            existing.away_odds = new_away_odds
            updated = True
        if not existing.external_fixture_id and row.get("external_fixture_id"):
            existing.external_fixture_id = str(row["external_fixture_id"])
            updated = True

        if updated:
            db.commit()
            db.refresh(existing)
        return existing, False

    fixture = Fixture(
        dedupe_key=key,
        kickoff_utc=kickoff,
        home_team=str(row["home_team"]).strip(),
        away_team=str(row["away_team"]).strip(),
        competition=str(row["competition"]).strip(),
        country=str(row.get("country") or "Unknown").strip(),
        source=str(row.get("source") or "unknown").strip(),
        external_fixture_id=str(row["external_fixture_id"]).strip() if row.get("external_fixture_id") else None,
        home_odds=_float_or_none(row.get("home_odds")),
        draw_odds=_float_or_none(row.get("draw_odds")),
        away_odds=_float_or_none(row.get("away_odds")),
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture, True

def import_fixtures_from_dataframe(db: Session, frame: pd.DataFrame) -> dict[str, int]:
    """Validate required columns and import fixtures from a pandas DataFrame."""
    required = {"kickoff_utc", "home_team", "away_team", "competition", "source"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required CSV columns: {sorted(missing)}")

    inserted = 0
    duplicates = 0
    records = frame.fillna("").to_dict(orient="records")
    for row in records:
        _, created = import_fixture(db, row)
        if created:
            inserted += 1
        else:
            duplicates += 1
    return {"inserted": inserted, "duplicates": duplicates, "total_processed": len(records)}
