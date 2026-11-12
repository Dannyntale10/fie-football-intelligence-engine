"""sportpesa_feed.py – Ingest real prices from SportPesa's public game feed.

Reads ``/api/upcoming/games`` for the upcoming football slate and
``/api/games/markets?markets=all`` for full 1X2, double chance, both-teams-to-
score, totals, draw-no-bet and Asian handicap depth. No key or account is
required; the endpoints are the same public JSON the website uses.

Only prices the feed actually returns are stored. Each stored price keeps the
SportPesa name, feed URL and capture timestamp for provenance.
"""
from __future__ import annotations

import argparse
import json
import logging
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

SPORTPESA_BASE = "https://www.sportpesa.com"
UPCOMING_URL = f"{SPORTPESA_BASE}/api/upcoming/games"
MARKETS_URL = f"{SPORTPESA_BASE}/api/games/markets"
MARKET_BATCH_SIZE = 10
MAX_EVENTS = 100

_HEADERS = {
    "accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
}

MATCH_WINNER_MARKET_ID = 10
DOUBLE_CHANCE_MARKET_ID = 46
BTTS_MARKET_ID = 43
TOTAL_GOALS_MARKET_ID = 52
DRAW_NO_BET_MARKET_ID = 47
ASIAN_HANDICAP_MARKET_ID = 51

_DOUBLE_CHANCE = {"1X": "double_chance_1x", "X2": "double_chance_x2", "12": "double_chance_12"}
_BTTS = {"YES": "btts_yes", "NO": "btts_no"}
_DNB = {"1": "dnb_home", "2": "dnb_away"}


def _market_id(market: Dict[str, Any]) -> int:
    try:
        return int(market.get("id"))
    except (TypeError, ValueError):
        return -1


def _selection(market: Dict[str, Any], short_name: str) -> Optional[Dict[str, Any]]:
    for row in market.get("selections") or []:
        if str(row.get("shortName", "")).strip().upper() == short_name:
            return row
    return None


def _market_line(market: Dict[str, Any], selection: Dict[str, Any]) -> Optional[float]:
    for candidate in (market.get("specValue"), selection.get("specValue")):
        try:
            return float(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _extract_market_odds(markets: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    odds: Dict[str, float] = {}
    for market in markets or []:
        market_id = _market_id(market)

        if market_id == MATCH_WINNER_MARKET_ID:
            for label, key in (("1", "home_win"), ("X", "draw"), ("2", "away_win")):
                row = _selection(market, label)
                price = decimal(row.get("odds")) if row else None
                if price:
                    odds[key] = price
            continue

        if market_id == DOUBLE_CHANCE_MARKET_ID:
            for label, key in _DOUBLE_CHANCE.items():
                row = _selection(market, label)
                price = decimal(row.get("odds")) if row else None
                if price:
                    odds[key] = price
            continue

        if market_id == BTTS_MARKET_ID:
            for label, key in _BTTS.items():
                row = _selection(market, label)
                price = decimal(row.get("odds")) if row else None
                if price:
                    odds[key] = price
            continue

        if market_id == TOTAL_GOALS_MARKET_ID:
            over = _selection(market, "OV")
            under = _selection(market, "UN")
            anchor = over or under
            if not anchor:
                continue
            line = half_line(_market_line(market, anchor))
            if line is None:
                continue
            token = line_key(line)
            over_price = decimal(over.get("odds")) if over else None
            under_price = decimal(under.get("odds")) if under else None
            if over_price:
                odds[f"over_{token}"] = over_price
            if under_price:
                odds[f"under_{token}"] = under_price
            continue

        if market_id == DRAW_NO_BET_MARKET_ID:
            for label, key in _DNB.items():
                row = _selection(market, label)
                price = decimal(row.get("odds")) if row else None
                if price:
                    odds[key] = price
            continue

        if market_id == ASIAN_HANDICAP_MARKET_ID:
            anchor = _selection(market, "1") or _selection(market, "2")
            if not anchor:
                continue
            line = _market_line(market, anchor)
            if line is None or line == 0:
                continue
            # Negative spec = home gives the handicap, which is the direction
            # the probability model prices. Positive spec is home receiving;
            # that is a different market the model does not list.
            if line >= 0:
                continue
            magnitude = half_line(line)
            if magnitude is None:
                continue
            token = line_key(magnitude)
            home = _selection(market, "1")
            away = _selection(market, "2")
            home_price = decimal(home.get("odds")) if home else None
            away_price = decimal(away.get("odds")) if away else None
            if home_price:
                odds[f"ah_home_minus_{token}"] = home_price
            if away_price:
                odds[f"ah_away_plus_{token}"] = away_price

    return odds


def _kickoff(game: Dict[str, Any]) -> Optional[datetime]:
    raw = game.get("date")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    stamp = game.get("dateTimestamp")
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(stamp / 1000.0, tz=timezone.utc)
    return None


def parse_game(
    game: Dict[str, Any],
    markets: Optional[Iterable[Dict[str, Any]]] = None,
    captured_at: Optional[datetime] = None,
) -> Optional[BookmakerEvent]:
    competitors = game.get("competitors") or []
    if len(competitors) < 2:
        return None
    home = str((competitors[0] or {}).get("name") or "").strip()
    away = str((competitors[1] or {}).get("name") or "").strip()
    if not home or not away:
        return None

    kickoff = _kickoff(game)
    if kickoff is None:
        return None

    rows = list(markets) if markets is not None else list(game.get("markets") or [])
    odds = _extract_market_odds(rows)
    if not all(key in odds for key in ("home_win", "draw", "away_win")):
        return None

    competition = str((game.get("competition") or {}).get("name") or "").strip()
    country = str((game.get("country") or {}).get("name") or "").strip()
    game_id = str(game.get("id") or "").strip()

    return BookmakerEvent(
        source="SPORTPESA",
        bookmaker="SportPesa",
        source_url=f"{MARKETS_URL}?games={game_id}&markets=all",
        event_id=game_id or None,
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition=competition or "Unknown",
        country=country or "Unknown",
        known_competition=competition_label(country, competition),
        market_odds=odds,
        captured_at=captured_at or datetime.now(timezone.utc),
    )


def fetch_upcoming_games(timeout: float = 20.0) -> List[Dict[str, Any]]:
    response = requests.get(UPCOMING_URL, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    games = payload if isinstance(payload, list) else []
    return [
        game for game in games
        if str((game.get("sport") or {}).get("id") or "") in ("", "1")
    ]


def fetch_markets(game_ids: Iterable[str], timeout: float = 20.0) -> Dict[str, List[Dict[str, Any]]]:
    merged: Dict[str, List[Dict[str, Any]]] = {}
    batch: List[str] = []
    for game_id in game_ids:
        batch.append(str(game_id))
        if len(batch) < MARKET_BATCH_SIZE:
            continue
        merged.update(_fetch_market_batch(batch, timeout))
        batch = []
    if batch:
        merged.update(_fetch_market_batch(batch, timeout))
    return merged


def _fetch_market_batch(game_ids: List[str], timeout: float) -> Dict[str, List[Dict[str, Any]]]:
    response = requests.get(
        MARKETS_URL,
        params={"games": ",".join(game_ids), "markets": "all"},
        headers=_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def ingest_sportpesa_odds(db: Session, max_events: int = MAX_EVENTS) -> Dict[str, Any]:
    captured_at = datetime.now(timezone.utc)
    games = fetch_upcoming_games()[:max_events]
    markets = fetch_markets(str(game["id"]) for game in games if game.get("id") is not None)

    events: List[BookmakerEvent] = []
    skipped_no_odds = 0
    for game in games:
        event = parse_game(game, markets.get(str(game.get("id"))), captured_at)
        if event is None:
            skipped_no_odds += 1
            continue
        events.append(event)

    stats = apply_bookmaker_events(db, events)
    stats["fetched_events"] = len(games)
    stats["skipped_no_odds"] += skipped_no_odds
    stats["with_extra_markets"] = sum(1 for event in events if len(event.market_odds) > 3)
    stats["requests"] = 1 + (len(games) + MARKET_BATCH_SIZE - 1) // MARKET_BATCH_SIZE
    logger.info(
        "SportPesa ingest: %d events, %d matched, %d new, %d skipped (competition)",
        stats["fetched_events"],
        stats["matched_existing"],
        stats["inserted_new"],
        stats["skipped_unknown_competition"],
    )
    return stats


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Import SportPesa football prices into FIE.")
    parser.add_argument("--max-events", type=int, default=MAX_EVENTS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    db = SessionLocal()
    try:
        print(json.dumps(ingest_sportpesa_odds(db, max_events=args.max_events), indent=2))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
