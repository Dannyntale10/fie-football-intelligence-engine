"""betika_feed.py – Ingest real prices from Betika Uganda's public JSON feed.

The website reads its slate from ``api.betika.com/v1/uo/matches`` and its
per-match markets from ``/v1/uo/match``. Both are public, keyless and return
the prices the bookmaker actually offers. ``start_time`` is Kenya/Uganda
local time (EAT, UTC+3); the periods in the response metadata confirm a
04:00–04:00 EAT matchday window.

Only prices the feed returns are stored, and every stored price keeps the
Betika name, feed URL and capture timestamp.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

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

BETIKA_BASE = "https://api.betika.com"
BETIKA_SITE = "https://www.betika.com/en-ug/"
LIST_URL = f"{BETIKA_BASE}/v1/uo/matches"
DETAIL_URL = f"{BETIKA_BASE}/v1/uo/match"
PAGE_SIZE = 100
MAX_EVENTS = 200
MAX_ENRICHED = 12
EAT = timezone(timedelta(hours=3), name="EAT")

MATCH_WINNER = "1"
DOUBLE_CHANCE = "10"
BTTS = "29"
TOTAL = "18"
DRAW_NO_BET = "11"

_HEADERS = {
    "accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}


def _parse_kickoff(raw: Any) -> Optional[datetime]:
    """Betika returns EAT wall-clock time without an offset."""
    try:
        naive = datetime.strptime(str(raw).strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return naive.replace(tzinfo=EAT).astimezone(timezone.utc)


def _prices(odds_rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    for row in odds_rows or []:
        value = decimal(row.get("odd_value"))
        if value is None:
            continue
        prices[str(row.get("display", "")).strip().upper()] = value
    return prices


def _detail_markets(payload: Dict[str, Any]) -> Dict[str, float]:
    """Map Betika's match markets onto the probability model's market names."""
    odds: Dict[str, float] = {}
    for market in payload.get("data") or []:
        sub_type = str(market.get("sub_type_id") or "")
        rows = market.get("odds") or []

        if sub_type == MATCH_WINNER:
            prices = _prices(rows)
            mapping = {"1": "home_win", "X": "draw", "2": "away_win"}
            odds.update(
                {key: prices[label] for label, key in mapping.items() if label in prices}
            )
        elif sub_type == DOUBLE_CHANCE:
            prices = _prices(rows)
            mapping = {
                "1/X": "double_chance_1x",
                "X/2": "double_chance_x2",
                "1/2": "double_chance_12",
            }
            odds.update(
                {key: prices[label] for label, key in mapping.items() if label in prices}
            )
        elif sub_type == BTTS:
            prices = _prices(rows)
            if "YES" in prices:
                odds["btts_yes"] = prices["YES"]
            if "NO" in prices:
                odds["btts_no"] = prices["NO"]
        elif sub_type == DRAW_NO_BET:
            prices = _prices(rows)
            if "1" in prices:
                odds["dnb_home"] = prices["1"]
            if "2" in prices:
                odds["dnb_away"] = prices["2"]
        elif sub_type == TOTAL:
            for row in rows:
                raw_special = str(row.get("special_bet_value") or "").replace("total=", "")
                line = half_line(raw_special)
                label = str(row.get("display", "")).strip().upper()
                value = decimal(row.get("odd_value"))
                if line is None or value is None:
                    continue
                if label.startswith("OVER"):
                    odds[f"over_{line_key(line)}"] = value
                elif label.startswith("UNDER"):
                    odds[f"under_{line_key(line)}"] = value
    return odds


def parse_match(match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    home = str(match.get("home_team") or "").strip()
    away = str(match.get("away_team") or "").strip()
    kickoff = _parse_kickoff(match.get("start_time"))
    if not home or not away or kickoff is None:
        return None

    match_winner = next(
        (market for market in match.get("odds") or []
         if str(market.get("sub_type_id")) == MATCH_WINNER),
        None,
    )
    prices = _prices((match_winner or {}).get("odds") or [])
    if not all(label in prices for label in ("1", "X", "2")):
        return None

    market_odds = {
        "home_win": prices["1"],
        "draw": prices["X"],
        "away_win": prices["2"],
    }
    category = str(match.get("category") or "").strip()
    competition = str(match.get("competition_name") or "").strip() or "Unknown"
    return {
        "event_id": str(match.get("parent_match_id") or match.get("match_id") or ""),
        "kickoff_utc": kickoff,
        "home_team": home,
        "away_team": away,
        "competition": competition,
        "country": category or "Unknown",
        "market_odds": market_odds,
        "known_competition": competition_label(category, competition),
    }


def fetch_upcoming_matches(
    max_events: int = MAX_EVENTS, timeout: float = 15.0
) -> List[Dict[str, Any]]:
    """Fetch today's upcoming football matches with their full-time 1X2 prices."""
    matches: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    page = 1

    while len(matches) < max_events:
        response = requests.get(
            LIST_URL,
            params={
                "tab": "upcoming",
                "sub_type_id": "1",
                "sport_id": "14",
                "sort_id": "2",
                "period_id": "-1",
                "esports": "false",
                "page": page,
                "limit": PAGE_SIZE,
            },
            headers=_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data") or []
        fresh = 0
        for match in batch:
            match_id = str(match.get("parent_match_id") or match.get("match_id") or "")
            if not match_id or match_id in seen:
                continue
            seen.add(match_id)
            matches.append(match)
            fresh += 1
        total = int((payload.get("meta") or {}).get("total") or 0)
        if len(batch) < PAGE_SIZE or fresh == 0 or len(matches) >= total:
            break
        page += 1

    return matches[:max_events]


def fetch_match_markets(parent_match_id: str, timeout: float = 15.0) -> Dict[str, float]:
    response = requests.get(
        DETAIL_URL,
        params={"parent_match_id": parent_match_id},
        headers=_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return _detail_markets(response.json())


def ingest_betika_odds(db: Session, max_events: int = MAX_EVENTS) -> Dict[str, Any]:
    """Refresh tracked fixtures with Betika prices and add fixtures for covered leagues."""
    matches = fetch_upcoming_matches(max_events=max_events)
    captured_at = datetime.now(timezone.utc)
    requests_made = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE) if matches else 1

    records: List[Dict[str, Any]] = []
    enriched = 0
    for match in matches:
        record = parse_match(match)
        if record is None:
            continue
        if enriched < MAX_ENRICHED:
            enriched += 1
            try:
                extra = fetch_match_markets(record["event_id"])
                requests_made += 1
                for key, value in extra.items():
                    record["market_odds"].setdefault(key, value)
            except requests.RequestException as exc:
                logger.warning("Betika markets failed for %s: %s", record["event_id"], exc)
        records.append(record)

    feed_events = [
        BookmakerEvent(
            source="BETIKA",
            bookmaker="Betika",
            source_url=BETIKA_SITE,
            event_id=record["event_id"] or None,
            kickoff_utc=record["kickoff_utc"],
            home_team=record["home_team"],
            away_team=record["away_team"],
            competition=record["competition"],
            country=record["country"],
            known_competition=record["known_competition"],
            market_odds=record["market_odds"],
            captured_at=captured_at,
        )
        for record in records
    ]

    stats = apply_bookmaker_events(db, feed_events)
    stats["fetched_events"] = len(matches)
    stats["skipped_no_odds"] += len(matches) - len(records)
    stats["requests"] = requests_made
    stats["with_extra_markets"] = sum(1 for record in records if len(record["market_odds"]) > 3)

    logger.info(
        "Betika ingest: %d events, %d matched, %d new, %d skipped (competition)",
        stats["fetched_events"],
        stats["matched_existing"],
        stats["inserted_new"],
        stats["skipped_unknown_competition"],
    )
    return stats


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Import live Betika Uganda 1X2 odds into FIE.")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    db = SessionLocal()
    try:
        print(json.dumps(ingest_betika_odds(db, max_events=args.max_events), indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
