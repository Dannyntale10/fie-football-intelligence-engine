from __future__ import annotations
"""
betpawa_feed.py – Ingest live prices from betPawa Uganda.

Pulls upcoming football events from betPawa's public sportsbook API, extracts
the 1X2 winner, double chance, both-teams-to-score, total goals, Asian handicap
and draw-no-bet markets, then either refreshes the prices on a fixture we
already track or inserts a new fixture for a competition the engine already
covers. Other competitions are skipped so the slate stays aligned with the
leagues FIE has ratings for.
"""
import argparse
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.fixtures import clean_team_name, make_dedupe_key

logger = logging.getLogger(__name__)

BETPAWA_BASE = "https://www.betpawa.ug"
BETPAWA_BRAND = "betpawa-uganda"
PAGE_SIZE = 100
MAX_EVENTS_CAP = 2000
MATCH_WINNER_MARKET_ID = "3743"
DOUBLE_CHANCE_MARKET_ID = "4693"
BTTS_MARKET_ID = "3795"
TOTAL_GOALS_MARKET_ID = "5000"
ASIAN_HANDICAP_MARKET_ID = "3774"
DRAW_NO_BET_MARKET_ID = "4703"

# Requested together so one page covers every market the combo engine can price.
MARKET_TYPE_IDS = [
    MATCH_WINNER_MARKET_ID,
    DOUBLE_CHANCE_MARKET_ID,
    BTTS_MARKET_ID,
    TOTAL_GOALS_MARKET_ID,
    ASIAN_HANDICAP_MARKET_ID,
    DRAW_NO_BET_MARKET_ID,
]

# Half-goal lines only: whole-number lines can push, so they are not comparable
# with the model's win/lose probabilities.
_MODEL_TOTAL_LINES = {"0.5", "1.5", "2.5", "3.5"}

# A full refresh costs 11 API calls (1,098 events). Polling that every 5s is
# 130+ requests/minute against a third-party feed and will get the IP rate
# limited. 30s is the floor, 60s the default; override with
# BETPAWA_REFRESH_SECONDS.
AUTO_REFRESH_DEFAULT = 60
AUTO_REFRESH_FLOOR = 30

_HEADERS = {
    "accept": "application/json",
    "X-Pawa-Brand": BETPAWA_BRAND,
    "X-Pawa-Language": "en",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

# (betPawa region, betPawa competition) -> competition name already used in the
# FIE database. Keyed by region as well because names repeat across countries
# (e.g. "Premier League" exists in England, Russia, Kenya and Jamaica).
# Ambiguous names are omitted rather than guessed.
_KNOWN_COMPETITIONS = {
    ("England", "Premier League"): "Premier League",
    ("England", "Championship"): "Championship",
    ("Spain", "LaLiga"): "La Liga",
    ("Italy", "Serie A"): "Serie A",
    ("Germany", "Bundesliga"): "Bundesliga",
    ("France", "Ligue 1"): "Ligue 1",
    ("International", "UEFA Nations League"): "UEFA Nations League",
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

def _utc_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _competition_label(event: Dict[str, Any]) -> Optional[str]:
    raw = ((event.get("competition") or {}).get("name") or "").strip()
    region = ((event.get("region") or {}).get("name") or "").strip()
    if not raw or not region:
        return None
    return _KNOWN_COMPETITIONS.get((region, raw))


def _team_key(name: str) -> str:
    base = clean_team_name(name)
    tokens = [t for t in base.split() if t not in _TEAM_NOISE and not t.isdigit()]
    return " ".join(tokens) or base


def _same_team(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    # Guard against one-word keys like "united" matching half the league.
    return len(shorter) >= 7 and shorter in longer


def _extract_match_winner(event: Dict[str, Any]) -> Optional[Tuple[float, float, float]]:
    for market in event.get("markets") or []:
        market_type = market.get("marketType") or {}
        is_match_winner = (
            str(market_type.get("id")) == MATCH_WINNER_MARKET_ID
            or str(market_type.get("name", "")).upper().startswith("1X2")
        )
        if not is_match_winner:
            continue

        prices: Dict[str, float] = {}
        for row in market.get("row") or []:
            for price in row.get("prices") or []:
                label = str(price.get("name", "")).strip().upper()
                odds = price.get("odds")
                if price.get("suspended") or label not in ("1", "X", "2"):
                    continue
                if isinstance(odds, (int, float)) and odds > 1.0:
                    prices[label] = float(odds)

        if all(k in prices for k in ("1", "X", "2")):
            return prices["1"], prices["X"], prices["2"]
    return None


def _line_key(raw: Any) -> Optional[str]:
    """'2.5' -> '2_5' so the key matches the probability model's market names."""
    try:
        line = abs(float(str(raw)))
    except (TypeError, ValueError):
        return None
    if abs(line - round(line)) < 1e-9:
        return None
    return f"{line:g}".replace(".", "_")


def _price_market_key(market_id: str, label: str, handicap: Any) -> Optional[str]:
    label = label.strip()
    upper = label.upper()

    if market_id == MATCH_WINNER_MARKET_ID:
        return {"1": "home_win", "X": "draw", "2": "away_win"}.get(upper)

    if market_id == DOUBLE_CHANCE_MARKET_ID:
        return {
            "1X": "double_chance_1x",
            "X2": "double_chance_x2",
            "12": "double_chance_12",
        }.get(upper)

    if market_id == BTTS_MARKET_ID:
        return {"YES": "btts_yes", "NO": "btts_no"}.get(upper)

    if market_id == TOTAL_GOALS_MARKET_ID:
        line = _line_key(handicap)
        if not line or str(handicap).strip() not in _MODEL_TOTAL_LINES:
            return None
        if upper == "OVER":
            return f"over_{line}"
        if upper == "UNDER":
            return f"under_{line}"
        return None

    if market_id == ASIAN_HANDICAP_MARKET_ID:
        line = _line_key(handicap)
        if not line:
            return None
        signed = str(handicap).strip()
        if upper == "1" and signed.startswith("-"):
            return f"ah_home_minus_{line}"
        if upper == "2" and signed.startswith("+"):
            return f"ah_away_plus_{line}"
        return None

    if market_id == DRAW_NO_BET_MARKET_ID:
        return {"1": "dnb_home", "2": "dnb_away"}.get(upper)

    return None


def _extract_market_odds(event: Dict[str, Any]) -> Dict[str, float]:
    """Map every supported bookmaker price to the model's market name."""
    odds: Dict[str, float] = {}
    for market in event.get("markets") or []:
        market_id = str((market.get("marketType") or {}).get("id") or "")
        if market_id not in MARKET_TYPE_IDS:
            continue
        for row in market.get("row") or []:
            for price in row.get("prices") or []:
                if price.get("suspended"):
                    continue
                value = price.get("odds")
                if not isinstance(value, (int, float)) or value <= 1.0:
                    continue
                key = _price_market_key(
                    market_id,
                    str(price.get("name", "")),
                    price.get("handicap"),
                )
                if key:
                    odds[key] = float(value)
    return odds


def _parse_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prices = _extract_match_winner(event)
    if not prices:
        return None

    participants = event.get("participants") or []
    home = next((p for p in participants if p.get("position") == 1), None)
    away = next((p for p in participants if p.get("position") == 2), None)
    if not home or not away or not home.get("name") or not away.get("name"):
        return None

    start = event.get("startTime")
    if not start:
        return None
    try:
        kickoff = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return None

    market_odds = _extract_market_odds(event)
    market_odds.update({"home_win": prices[0], "draw": prices[1], "away_win": prices[2]})

    return {
        "event_id": str(event.get("id") or ""),
        "kickoff_utc": kickoff,
        "home_team": str(home["name"]).strip(),
        "away_team": str(away["name"]).strip(),
        "competition": ((event.get("competition") or {}).get("name") or "").strip() or "Unknown",
        "country": ((event.get("region") or {}).get("name") or "Unknown").strip(),
        "home_odds": prices[0],
        "draw_odds": prices[1],
        "away_odds": prices[2],
        "market_odds": market_odds,
        "known_competition": _competition_label(event),
    }


def fetch_upcoming_events(max_events: int = MAX_EVENTS_CAP, timeout: float = 15.0) -> List[Dict[str, Any]]:
    """Fetch upcoming football events with the full-time 1X2 market from betPawa."""
    url = f"{BETPAWA_BASE}/api/sportsbook/v4/events/lists/by-queries"
    events: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    skip = 0

    while len(events) < max_events:
        query = {
            "queries": [
                {
                    "query": {"eventType": "UPCOMING", "categories": ["2"]},
                    "sort": {"startTime": "ASC"},
                    "skip": skip,
                    "take": PAGE_SIZE,
                    "view": {"marketTypes": MARKET_TYPE_IDS},
                }
            ]
        }
        response = requests.get(
            url,
            params={"q": json.dumps(query, separators=(",", ":"))},
            headers=_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        page = ((payload.get("responses") or [{}])[0].get("responses")) or []

        fresh = 0
        for event in page:
            event_id = str(event.get("id") or "")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
            fresh += 1

        if len(page) < PAGE_SIZE or fresh == 0:
            break
        skip += PAGE_SIZE

    return events[:max_events]


def _find_existing(
    record: Dict[str, Any],
    fixtures_by_date: Dict[str, List[Fixture]],
) -> Optional[Fixture]:
    home_key = _team_key(record["home_team"])
    away_key = _team_key(record["away_team"])
    kickoff = record["kickoff_utc"]
    for delta in (0, -1, 1):
        day = _utc_date(kickoff + timedelta(days=delta))
        for fixture in fixtures_by_date.get(day, []):
            if _same_team(home_key, _team_key(fixture.home_team)) and _same_team(
                away_key, _team_key(fixture.away_team)
            ):
                return fixture
    return None


def ingest_betpawa_odds(db: Session, max_events: int = MAX_EVENTS_CAP) -> Dict[str, Any]:
    """Refresh tracked fixtures with betPawa prices and add new fixtures for covered leagues."""
    now = datetime.now(timezone.utc)
    events = fetch_upcoming_events(max_events=max_events)

    stats: Dict[str, Any] = {
        "fetched_events": len(events),
        "parsed_with_1x2": 0,
        "matched_existing": 0,
        "odds_updated": 0,
        "inserted_new": 0,
        "skipped_past": 0,
        "skipped_no_odds": 0,
        "skipped_unknown_competition": 0,
        "inserted_competitions": {},
    }

    records: List[Dict[str, Any]] = []
    for event in events:
        record = _parse_event(event)
        if not record:
            stats["skipped_no_odds"] += 1
            continue
        if record["kickoff_utc"] <= now:
            stats["skipped_past"] += 1
            continue
        records.append(record)
    stats["parsed_with_1x2"] = len(records)
    stats["with_extra_markets"] = sum(1 for r in records if len(r["market_odds"]) > 3)

    upcoming = db.scalars(select(Fixture).where(Fixture.kickoff_utc >= now)).all()
    fixtures_by_date: Dict[str, List[Fixture]] = {}
    for fixture in upcoming:
        fixtures_by_date.setdefault(_utc_date(fixture.kickoff_utc), []).append(fixture)

    tracked_competitions = {
        str(name) for name in db.scalars(select(Fixture.competition).distinct()).all() if name
    }
    inserted_keys: Set[str] = set()

    for record in records:
        odds_json = (
            json.dumps(record["market_odds"], sort_keys=True)
            if record["market_odds"] else None
        )
        match = _find_existing(record, fixtures_by_date)
        if match:
            stats["matched_existing"] += 1
            changed = False
            for attr in ("home_odds", "draw_odds", "away_odds"):
                value = record[attr]
                if getattr(match, attr) != value:
                    setattr(match, attr, value)
                    changed = True
            if getattr(match, "market_odds_json", None) != odds_json:
                match.market_odds_json = odds_json
                changed = True
            if changed:
                match.odds_updated_at = now
                stats["odds_updated"] += 1
            continue

        competition = record["known_competition"]
        if not competition or competition not in tracked_competitions:
            stats["skipped_unknown_competition"] += 1
            continue

        dedupe_key = make_dedupe_key(
            record["kickoff_utc"], record["home_team"], record["away_team"], competition
        )
        if dedupe_key in inserted_keys or db.scalar(
            select(Fixture.id).where(Fixture.dedupe_key == dedupe_key)
        ):
            continue

        fixture = Fixture(
            dedupe_key=dedupe_key,
            kickoff_utc=record["kickoff_utc"],
            home_team=record["home_team"],
            away_team=record["away_team"],
            competition=competition,
            country=record["country"],
            source="BETPAWA",
            external_fixture_id=record["event_id"] or None,
            home_odds=record["home_odds"],
            draw_odds=record["draw_odds"],
            away_odds=record["away_odds"],
            odds_updated_at=now,
            market_odds_json=odds_json,
            status="scheduled",
        )
        db.add(fixture)
        inserted_keys.add(dedupe_key)
        stats["inserted_new"] += 1
        stats["inserted_competitions"][competition] = (
            stats["inserted_competitions"].get(competition, 0) + 1
        )

    db.commit()
    logger.info(
        "betPawa ingest: %d events, %d matched, %d new, %d skipped (competition)",
        stats["fetched_events"],
        stats["matched_existing"],
        stats["inserted_new"],
        stats["skipped_unknown_competition"],
    )
    return stats


def resolve_refresh_seconds(explicit: Optional[int] = None) -> int:
    raw: Any = explicit if explicit is not None else os.getenv("BETPAWA_REFRESH_SECONDS")
    try:
        value = int(raw) if raw not in (None, "") else AUTO_REFRESH_DEFAULT
    except (TypeError, ValueError):
        value = AUTO_REFRESH_DEFAULT
    return max(AUTO_REFRESH_FLOOR, value)


_auto_thread: Optional[threading.Thread] = None
_auto_stop = threading.Event()
_auto_state: Dict[str, Any] = {
    "running": False,
    "interval_seconds": None,
    "started_at": None,
    "last_run_at": None,
    "last_error": None,
    "in_progress": False,
    "runs": 0,
    "last_result": None,
}


def _run_ingest_once() -> Dict[str, Any]:
    from app.database import SessionLocal

    _auto_state["in_progress"] = True
    db = SessionLocal()
    try:
        result = ingest_betpawa_odds(db)
    finally:
        db.close()
        _auto_state["in_progress"] = False

    _auto_state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    _auto_state["last_result"] = result
    _auto_state["last_error"] = None
    _auto_state["runs"] = _auto_state.get("runs", 0) + 1
    return result


def _auto_loop(interval_seconds: int) -> None:
    while not _auto_stop.is_set():
        try:
            _run_ingest_once()
        except Exception as exc:  # network/API failures must not kill the loop
            _auto_state["last_error"] = str(exc)
            logger.warning("betPawa auto-refresh failed: %s", exc)
        _auto_stop.wait(interval_seconds)

    _auto_state["running"] = False


def start_auto_ingest(interval_seconds: Optional[int] = None) -> bool:
    """Start the background odds refresh (idempotent). Returns True when running."""
    global _auto_thread

    if _auto_thread is not None and _auto_thread.is_alive():
        return True

    interval = resolve_refresh_seconds(interval_seconds)
    _auto_stop.clear()
    _auto_state.update(
        running=True,
        interval_seconds=interval,
        started_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        last_error=None,
    )
    _auto_thread = threading.Thread(
        target=_auto_loop,
        args=(interval,),
        daemon=True,
        name="FIE_BetPawaFeed",
    )
    _auto_thread.start()
    return True


def stop_auto_ingest() -> None:
    _auto_stop.set()
    _auto_state["running"] = False


def get_auto_ingest_status() -> Dict[str, Any]:
    return {
        **_auto_state,
        "running": bool(_auto_thread is not None and _auto_thread.is_alive() and not _auto_stop.is_set()),
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Import live betPawa Uganda 1X2 odds into FIE.")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS_CAP)
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep refreshing forever instead of running once.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help=f"Seconds between refreshes in loop mode (default {AUTO_REFRESH_DEFAULT}, floor {AUTO_REFRESH_FLOOR}).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.loop:
        db = SessionLocal()
        try:
            print(json.dumps(ingest_betpawa_odds(db, max_events=args.max_events), indent=2))
        finally:
            db.close()
        return 0

    interval = resolve_refresh_seconds(args.interval)
    print(f"betPawa auto-refresh every {interval}s — press Ctrl+C to stop.")
    try:
        while True:
            db = SessionLocal()
            try:
                stats = ingest_betpawa_odds(db, max_events=args.max_events)
                print(
                    f"[{datetime.now(timezone.utc):%H:%M:%S}Z] refreshed={stats['odds_updated']} "
                    f"new={stats['inserted_new']} matched={stats['matched_existing']}",
                    flush=True,
                )
            except Exception as exc:
                print(f"[{datetime.now(timezone.utc):%H:%M:%S}Z] error: {exc}", flush=True)
            finally:
                db.close()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
