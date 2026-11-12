"""Shared matching, best-price merging and provenance for bookmaker feeds.

Each feed module parses its own public JSON into ``BookmakerEvent`` records.
This module owns everything after parsing: team matching, competition
allow-listing, dedupe and the best-price merge. A price is stored only when a
feed actually returned it, and every stored price keeps the bookmaker, feed,
URL and capture timestamp that produced it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture

logger = logging.getLogger(__name__)

# (provider country/region, provider competition) -> competition name used by
# the FIE database. Keyed by country because names repeat across countries.
KNOWN_COMPETITIONS = {
    ("England", "Premier League"): "Premier League",
    ("England", "Championship"): "Championship",
    ("Spain", "LaLiga"): "La Liga",
    ("Spain", "La Liga"): "La Liga",
    ("Italy", "Serie A"): "Serie A",
    ("Germany", "Bundesliga"): "Bundesliga",
    ("France", "Ligue 1"): "Ligue 1",
    ("Turkey", "Super Lig"): "Super Lig",
    ("Netherlands", "Eredivisie"): "Eredivisie",
    ("Portugal", "Primeira Liga"): "Primeira Liga",
    ("Belgium", "Belgian Pro League"): "Belgian Pro League",
    ("Greece", "Super League Greece"): "Super League Greece",
    ("Scotland", "Scottish Premiership"): "Scottish Premiership",
    ("International", "UEFA Nations League"): "UEFA Nations League",
    ("Europe", "UEFA Nations League"): "UEFA Nations League",
    ("Brazil", "Brasileiro Serie A"): "Serie A Brazil",
    ("USA", "MLS"): "MLS",
    ("Mexico", "Liga MX"): "Liga MX",
    ("Africa", "CAF Champions League"): "CAF Champions League",
    ("International", "CAF Champions League"): "CAF Champions League",
}

# Tokens that vary between providers but do not identify the club itself.
_TEAM_NOISE = {
    "fc", "cf", "afc", "sc", "ac", "club", "de", "cd", "ca", "cp", "cr",
    "sv", "ss", "us", "as", "sp", "mg", "rj", "ce", "pr", "go", "ba",
    "and", "the", "of",
}

# Half-goal lines only: whole-number lines can push and are not comparable
# with the model's win/lose probabilities.
MODEL_TOTAL_LINES = {0.5, 1.5, 2.5, 3.5}


def utc_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def team_key(name: str) -> str:
    from app.data_sources.fixtures import clean_team_name

    base = clean_team_name(name)
    tokens = [t for t in base.split() if t not in _TEAM_NOISE and not t.isdigit()]
    return " ".join(tokens) or base


def same_team(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    # Guard against one-word keys like "united" matching half the league.
    return len(shorter) >= 7 and shorter in longer


def competition_label(country: Any, competition: Any) -> Optional[str]:
    region = str(country or "").strip()
    name = str(competition or "").strip()
    if not region or not name:
        return None
    return KNOWN_COMPETITIONS.get((region, name))


def half_line(value: Any) -> Optional[float]:
    """Return an absolute half-goal line (0.5..3.5) or ``None``."""
    try:
        line = abs(float(value))
    except (TypeError, ValueError):
        return None
    if line not in MODEL_TOTAL_LINES:
        return None
    return line


def line_key(line: float) -> str:
    return f"{line:g}".replace(".", "_")


def decimal(value: Any) -> Optional[float]:
    try:
        price = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None


@dataclass
class BookmakerEvent:
    source: str
    bookmaker: str
    source_url: str
    event_id: Optional[str]
    kickoff_utc: datetime
    home_team: str
    away_team: str
    competition: str
    country: str
    known_competition: Optional[str]
    market_odds: Dict[str, float]
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def home_odds(self) -> Optional[float]:
        return self.market_odds.get("home_win")

    @property
    def draw_odds(self) -> Optional[float]:
        return self.market_odds.get("draw")

    @property
    def away_odds(self) -> Optional[float]:
        return self.market_odds.get("away_win")


def find_existing(
    record: Any,
    fixtures_by_date: Dict[str, List[Fixture]],
) -> Optional[Fixture]:
    home_key = team_key(record.home_team)
    away_key = team_key(record.away_team)
    kickoff = record.kickoff_utc
    for delta in (0, -1, 1):
        day = utc_date(kickoff + timedelta(days=delta))
        for fixture in fixtures_by_date.get(day, []):
            if same_team(home_key, team_key(fixture.home_team)) and same_team(
                away_key, team_key(fixture.away_team)
            ):
                return fixture
    return None


def _parse_payload(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    else:
        parsed = raw
    return parsed if isinstance(parsed, dict) else {}


def entry_price(entry: Any) -> Optional[float]:
    if isinstance(entry, dict):
        entry = entry.get("price", entry.get("odds"))
    if isinstance(entry, (int, float)) and entry > 1.0:
        return float(entry)
    return None


def merge_market_prices(
    raw: Any,
    incoming: Dict[str, float],
    *,
    bookmaker: str,
    source: str,
    source_url: str,
    captured_at: datetime,
) -> tuple[Dict[str, Any], bool]:
    """Keep the best price per market and record where that price came from."""
    payload = _parse_payload(raw)
    changed = False
    stamp = (captured_at or datetime.now(timezone.utc)).isoformat()
    for key, value in incoming.items():
        price = decimal(value)
        if price is None:
            continue
        current_entry = payload.get(key)
        current = entry_price(current_entry)
        # A legacy flat number has no verified bookmaker, so it cannot defend
        # its price: the next observed, attributed price replaces it.
        if isinstance(current_entry, dict) and current is not None and current >= price - 1e-9:
            continue
        payload[str(key)] = {
            "price": round(price, 4),
            "bookmaker": bookmaker,
            "source": source,
            "source_url": source_url,
            "captured_at": stamp,
        }
        changed = True
    return payload, changed


def _apply_merged_fields(fixture: Fixture, payload: Dict[str, Any]) -> bool:
    changed = False
    for attr, key in (
        ("home_odds", "home_win"),
        ("draw_odds", "draw"),
        ("away_odds", "away_win"),
    ):
        price = entry_price(payload.get(key))
        if price is not None and getattr(fixture, attr) != price:
            setattr(fixture, attr, price)
            changed = True
    for attr, key in (
        ("max_home_odds", "home_win"),
        ("max_draw_odds", "draw"),
        ("max_away_odds", "away_win"),
    ):
        price = entry_price(payload.get(key))
        if price is not None and getattr(fixture, attr) != price:
            setattr(fixture, attr, price)
            changed = True
    return changed


def apply_bookmaker_events(
    db: Session,
    events: Iterable[BookmakerEvent],
) -> Dict[str, Any]:
    """Merge parsed events into tracked fixtures and insert covered fixtures."""
    now = datetime.now(timezone.utc)
    stats: Dict[str, Any] = {
        "parsed_with_1x2": 0,
        "matched_existing": 0,
        "odds_updated": 0,
        "inserted_new": 0,
        "skipped_past": 0,
        "skipped_no_odds": 0,
        "skipped_unknown_competition": 0,
        "inserted_competitions": {},
    }

    upcoming = db.scalars(select(Fixture).where(Fixture.kickoff_utc >= now)).all()
    fixtures_by_date: Dict[str, List[Fixture]] = {}
    for fixture in upcoming:
        fixtures_by_date.setdefault(utc_date(fixture.kickoff_utc), []).append(fixture)

    tracked_competitions = {
        str(name) for name in db.scalars(select(Fixture.competition).distinct()).all() if name
    }
    inserted_keys: Set[str] = set()
    seen: Set[tuple[str, str]] = set()

    from app.data_sources.fixtures import make_dedupe_key

    for event in events:
        if event.kickoff_utc <= now:
            stats["skipped_past"] += 1
            continue
        if not all(
            event.market_odds.get(key)
            for key in ("home_win", "draw", "away_win")
        ):
            stats["skipped_no_odds"] += 1
            continue
        if event.event_id:
            identity = (event.source, str(event.event_id))
            if identity in seen:
                continue
            seen.add(identity)
        stats["parsed_with_1x2"] += 1

        match = find_existing(event, fixtures_by_date)
        if match is not None:
            stats["matched_existing"] += 1
            payload, changed = merge_market_prices(
                match.market_odds_json,
                event.market_odds,
                bookmaker=event.bookmaker,
                source=event.source,
                source_url=event.source_url,
                captured_at=event.captured_at,
            )
            if changed:
                match.market_odds_json = json.dumps(payload, sort_keys=True)
                stats["odds_updated"] += 1
            _apply_merged_fields(match, payload)
            # Freshness reflects when the feed was last read, not when a price
            # last improved; a verified refresh is still fresh data.
            match.odds_updated_at = event.captured_at
            continue

        competition = event.known_competition
        if not competition or competition not in tracked_competitions:
            stats["skipped_unknown_competition"] += 1
            continue

        dedupe_key = make_dedupe_key(
            event.kickoff_utc, event.home_team, event.away_team, competition
        )
        if dedupe_key in inserted_keys or db.scalar(
            select(Fixture.id).where(Fixture.dedupe_key == dedupe_key)
        ):
            continue

        payload, _ = merge_market_prices(
            None,
            event.market_odds,
            bookmaker=event.bookmaker,
            source=event.source,
            source_url=event.source_url,
            captured_at=event.captured_at,
        )
        fixture = Fixture(
            dedupe_key=dedupe_key,
            kickoff_utc=event.kickoff_utc,
            home_team=event.home_team,
            away_team=event.away_team,
            competition=competition,
            country=event.country or "Unknown",
            source=event.source,
            external_fixture_id=event.event_id,
            home_odds=event.home_odds,
            draw_odds=event.draw_odds,
            away_odds=event.away_odds,
            max_home_odds=event.home_odds,
            max_draw_odds=event.draw_odds,
            max_away_odds=event.away_odds,
            odds_updated_at=event.captured_at,
            market_odds_json=json.dumps(payload, sort_keys=True),
            status="scheduled",
        )
        db.add(fixture)
        inserted_keys.add(dedupe_key)
        stats["inserted_new"] += 1
        stats["inserted_competitions"][competition] = (
            stats["inserted_competitions"].get(competition, 0) + 1
        )

    db.commit()
    return stats
