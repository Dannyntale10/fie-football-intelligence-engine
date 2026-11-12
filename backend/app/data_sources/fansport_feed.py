"""fansport_feed.py – Ingest real prices from the FANSPORT line feed.

FANSPORT (fsportug.icu) publishes its pre-match line through a public JSON
feed used by the website itself: ``games1x2`` for the upcoming slate and
``gameEvents`` for the full market list of one fixture. No key or account is
needed.

Market groups are identified by the platform's own ids: group 1 is 1X2,
group 8 double chance, group 19 both teams to score, group 17 totals and
group 2 carries draw-no-bet/handicap selections. The parser only maps groups
whose meaning can be verified against the 1X2 prices; everything else is left
alone rather than guessed.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests
from sqlalchemy.orm import Session

from app.data_sources.bookmaker_common import (
    BookmakerEvent,
    apply_bookmaker_events,
    competition_label,
    decimal,
    half_line,
    line_key,
)

logger = logging.getLogger(__name__)

FANSPORT_BASE = "https://fsportug.icu"
LINE_URL = f"{FANSPORT_BASE}/service-api/main-line-feed/v3/games1x2"
EVENT_URL = f"{FANSPORT_BASE}/service-api/main-line-feed/v3/gameEvents"

MAX_EVENTS = 40
MAX_ENRICHED = 20
ENRICH_DELAY_SECONDS = 0.15

_HEADERS = {
    "accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

# The feed is order-sensitive and rejects both percent-encoded commas and
# ``count`` placed anywhere but second, so the query is written literally.
_LINE_QUERY = (
    "cfView=3&count={count}&fcountry=191&gr=1099&grMode=4"
    "&lng=en&ref=110&selectedMs=1.1,2.1,10.1"
)


def _groups(groups: Iterable[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {
        int(group.get("groupId")): group
        for group in groups or []
        if group.get("groupId") is not None
    }


def _entries(group: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not group:
        return []
    return [entry for row in group.get("events") or [] for entry in row or []]


def _typed(group: Optional[Dict[str, Any]], type_id: int) -> List[Dict[str, Any]]:
    return [entry for entry in _entries(group) if int(entry.get("type") or -1) == type_id]


def _price(entries: List[Dict[str, Any]], *, parameter: Optional[float] = None) -> Optional[float]:
    for entry in entries:
        if parameter is not None:
            try:
                entry_parameter = float(entry.get("parameter"))
            except (TypeError, ValueError):
                continue
            if abs(entry_parameter - parameter) > 1e-9:
                continue
        price = decimal(entry.get("cf", entry.get("cfView")))
        if price:
            return price
    return None


def _parameter(entry: Dict[str, Any]) -> Optional[float]:
    raw = entry.get("parameter")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_market_odds(groups: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    by_id = _groups(groups)
    odds: Dict[str, float] = {}

    match_winner = by_id.get(1)
    for type_id, key in ((1, "home_win"), (2, "draw"), (3, "away_win")):
        price = _price(_typed(match_winner, type_id))
        if price:
            odds[key] = price

    double_chance = by_id.get(8)
    for type_id, key in (
        (4, "double_chance_1x"),
        (5, "double_chance_12"),
        (6, "double_chance_x2"),
    ):
        price = _price(_typed(double_chance, type_id))
        if price:
            odds[key] = price

    btts = by_id.get(19)
    for type_id, key in ((180, "btts_yes"), (181, "btts_no")):
        price = _price(_typed(btts, type_id))
        if price:
            odds[key] = price

    totals = by_id.get(17)
    for type_id, prefix in ((9, "over"), (10, "under")):
        for entry in _typed(totals, type_id):
            line = half_line(_parameter(entry))
            if line is None:
                continue
            price = decimal(entry.get("cf", entry.get("cfView")))
            if price:
                odds[f"{prefix}_{line_key(line)}"] = price

    # Group 2: type 7 is home with a signed handicap, type 8 is away. A null
    # parameter is draw-no-bet. Only half lines matching the model are kept.
    handicap = by_id.get(2)
    for entry in _typed(handicap, 7):
        parameter = _parameter(entry)
        if parameter is None:
            price = decimal(entry.get("cf", entry.get("cfView")))
            if price:
                odds["dnb_home"] = price
            continue
        if parameter >= 0:
            continue
        line = half_line(parameter)
        if line is None:
            continue
        price = decimal(entry.get("cf", entry.get("cfView")))
        if price:
            odds[f"ah_home_minus_{line_key(line)}"] = price

    for entry in _typed(handicap, 8):
        parameter = _parameter(entry)
        if parameter is None:
            price = decimal(entry.get("cf", entry.get("cfView")))
            if price:
                odds["dnb_away"] = price
            continue
        if parameter <= 0:
            continue
        line = half_line(parameter)
        if line is None:
            continue
        price = decimal(entry.get("cf", entry.get("cfView")))
        if price:
            odds[f"ah_away_plus_{line_key(line)}"] = price

    return odds


def parse_game(
    game: Dict[str, Any],
    full: Optional[Dict[str, Any]] = None,
    captured_at: Optional[datetime] = None,
) -> Optional[BookmakerEvent]:
    home = str((game.get("opponent1") or {}).get("fullName") or "").strip()
    away = str((game.get("opponent2") or {}).get("fullName") or "").strip()
    if not home or not away:
        return None

    start = game.get("startTs")
    try:
        kickoff = datetime.fromtimestamp(float(start), tz=timezone.utc)
    except (TypeError, ValueError):
        return None

    groups = (full or {}).get("eventGroups") or game.get("eventGroups") or []
    odds = _extract_market_odds(groups)
    if not all(key in odds for key in ("home_win", "draw", "away_win")):
        return None

    league = game.get("liga") or {}
    competition = str(league.get("name") or "").strip()
    country = str(league.get("countryName") or "").strip()
    numeric_id = str(game.get("id") or "").strip()
    const_id = str(game.get("constId") or numeric_id).strip()

    return BookmakerEvent(
        source="FANSPORT",
        bookmaker="FANSPORT",
        source_url=f"{EVENT_URL}?gameId={numeric_id}&marketType=1",
        event_id=const_id or None,
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition=competition or "Unknown",
        country=country or "Unknown",
        known_competition=competition_label(country, competition),
        market_odds=odds,
        captured_at=captured_at or datetime.now(timezone.utc),
    )


def fetch_upcoming_games(count: int = MAX_EVENTS, timeout: float = 20.0) -> List[Dict[str, Any]]:
    limit = max(1, min(count, MAX_EVENTS))
    response = requests.get(
        f"{LINE_URL}?{_LINE_QUERY.format(count=limit)}", headers=_HEADERS, timeout=timeout
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def fetch_event_markets(game_id: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    params = {
        "cfView": 3,
        "countEvents": 250,
        "fcountry": 191,
        "gameId": game_id,
        "gr": 1099,
        "grMode": 4,
        "lng": "en",
        "marketType": 1,
        "ref": 110,
    }
    response = requests.get(EVENT_URL, params=params, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def ingest_fansport_odds(
    db: Session,
    max_events: int = MAX_EVENTS,
    max_enriched: int = MAX_ENRICHED,
) -> Dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    games = fetch_upcoming_games()[:max_events]

    events: List[BookmakerEvent] = []
    skipped_no_odds = 0
    enrichable = [game for game in games if game.get("id") is not None][:max_enriched]
    enriched_ids = {str(game.get("id")) for game in enrichable}

    for game in games:
        full = None
        numeric_id = str(game.get("id") or "")
        if numeric_id in enriched_ids:
            try:
                full = fetch_event_markets(numeric_id)
            except requests.RequestException as error:
                logger.warning("FANSPORT event %s markets failed: %s", numeric_id, error)
            finally:
                time.sleep(ENRICH_DELAY_SECONDS)
        event = parse_game(game, full, captured_at)
        if event is None:
            skipped_no_odds += 1
            continue
        events.append(event)

    stats = apply_bookmaker_events(db, events)
    stats["fetched_events"] = len(games)
    stats["skipped_no_odds"] += skipped_no_odds
    stats["with_extra_markets"] = sum(1 for event in events if len(event.market_odds) > 3)
    stats["requests"] = 1 + len(enriched_ids)
    logger.info(
        "FANSPORT ingest: %d events, %d matched, %d new, %d skipped (competition)",
        stats["fetched_events"],
        stats["matched_existing"],
        stats["inserted_new"],
        stats["skipped_unknown_competition"],
    )
    return stats


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Import FANSPORT football prices into FIE.")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS)
    parser.add_argument("--max-enriched", type=int, default=MAX_ENRICHED)
    args = parser.parse_args(list(argv) if argv is not None else None)

    db = SessionLocal()
    try:
        print(
            json.dumps(
                ingest_fansport_odds(
                    db, max_events=args.max_events, max_enriched=args.max_enriched
                ),
                indent=2,
            )
        )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
