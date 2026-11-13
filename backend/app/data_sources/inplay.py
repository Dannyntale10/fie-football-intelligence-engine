from __future__ import annotations
"""
inplay.py – Persist live ESPN scores onto stored fixtures.

The poller only updates fixtures that already exist. It never creates a match,
never invents a score, and returns a warning instead of raising when ESPN is
unreachable.
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.live_feed import COMPETITION_TARGETS
from app.models.fixture import Fixture

EAT_TZ = timezone(timedelta(hours=3), name="EAT")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"
REFRESH_INTERVAL_SECONDS = 60.0
HEADERS = {"User-Agent": "Mozilla/5.0"}

_POSTPONED_STATES = {"STATUS_POSTPONED", "STATUS_SUSPENDED"}
_CANCELLED_STATES = {"STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_ABANDONED", "STATUS_FORFEIT"}
_STATUS_LABELS = {
    "live": "In play",
    "halftime": "Half time",
    "finished": "Full time",
    "postponed": "Postponed",
    "cancelled": "Cancelled",
    "scheduled": "Scheduled",
}

_LEAGUE_CODE_BY_NAME = {
    display_name.strip().lower(): code
    for code, display_name, _country, _dates in COMPETITION_TARGETS
}

_last_refresh_monotonic: Optional[float] = None


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _league_code(fixture: Fixture) -> Optional[str]:
    if fixture.espn_league_code:
        return fixture.espn_league_code
    name = (fixture.competition or "").strip().lower()
    return _LEAGUE_CODE_BY_NAME.get(name)


def _score(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _teams(competition: dict) -> Dict[str, Any]:
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    return {
        "home_name": (home_team.get("displayName") or home_team.get("name") or "").strip(),
        "away_name": (away_team.get("displayName") or away_team.get("name") or "").strip(),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
    }


def _fetch_events(code: str) -> List[dict]:
    response = requests.get(ESPN_URL.format(code=code), headers=HEADERS, timeout=6)
    if response.status_code != 200:
        raise RuntimeError(f"ESPN returned {response.status_code} for {code}")
    return response.json().get("events", [])


def _apply(fixture: Fixture, event: dict) -> bool:
    competition = (event.get("competitions") or [{}])[0]
    status = competition.get("status") or {}
    type_info = status.get("type") or {}
    state = (type_info.get("state") or "pre").lower()
    type_name = (type_info.get("name") or "").upper()

    if state == "pre":
        new_status, clock = "scheduled", None
    elif state == "in":
        if "HALFTIME" in type_name:
            new_status, clock = "halftime", "HT"
        else:
            new_status, clock = "live", (status.get("displayClock") or None)
    elif state == "post":
        if type_name in _POSTPONED_STATES:
            new_status, clock = "postponed", None
        elif type_name in _CANCELLED_STATES:
            new_status, clock = "cancelled", None
        else:
            new_status, clock = "finished", "FT"
    else:
        return False

    changed = False
    if fixture.status != new_status:
        fixture.status = new_status
        changed = True
    if fixture.live_clock != clock:
        fixture.live_clock = clock
        changed = True

    if state == "in" or new_status == "finished":
        teams = _teams(competition)
        home_score = _score(teams["home_score"])
        away_score = _score(teams["away_score"])
        if home_score is not None and fixture.home_score != home_score:
            fixture.home_score = home_score
            changed = True
        if away_score is not None and fixture.away_score != away_score:
            fixture.away_score = away_score
            changed = True

    # Hearing from the feed is itself an observation. A half-time match whose
    # clock is frozen at "HT" is still fresh, so this cannot be inferred from
    # the clock or the score.
    fixture.live_observed_at = datetime.now(timezone.utc)
    changed = True

    return changed


def refresh_live_scores(db: Session, *, force: bool = False) -> Dict[str, Any]:
    """Refresh stored fixtures inside the live window. Never raises, never creates a fixture."""
    global _last_refresh_monotonic

    if (
        not force
        and _last_refresh_monotonic is not None
        and (time.monotonic() - _last_refresh_monotonic) < REFRESH_INTERVAL_SECONDS
    ):
        return {"refreshed": False, "updated": 0, "skipped": 0, "warning": None}

    _last_refresh_monotonic = time.monotonic()

    try:
        now = datetime.now(timezone.utc)
        candidates = db.scalars(
            select(Fixture)
            .where(Fixture.kickoff_utc >= now - timedelta(hours=4))
            .where(Fixture.kickoff_utc <= now + timedelta(minutes=15))
            .where(Fixture.status.in_(("scheduled", "live", "halftime")))
            .order_by(Fixture.kickoff_utc.asc())
        ).all()

        by_code: Dict[str, List[Fixture]] = {}
        skipped = 0
        for fixture in candidates:
            code = _league_code(fixture)
            if not code:
                skipped += 1
                continue
            by_code.setdefault(code, []).append(fixture)

        if not by_code:
            return {"refreshed": True, "updated": 0, "skipped": skipped, "warning": None}

        events_by_code: Dict[str, List[dict]] = {}
        failures: List[str] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_events, code): code for code in by_code}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    events_by_code[code] = future.result()
                except Exception as exc:
                    failures.append(f"{code}: {exc}")

        updated = 0
        for code, fixtures in by_code.items():
            events = events_by_code.get(code) or []
            by_external: Dict[str, dict] = {}
            by_names: Dict[tuple, dict] = {}
            for event in events:
                event_id = str(event.get("id") or "")
                if event_id:
                    by_external[event_id] = event
                competition = (event.get("competitions") or [{}])[0]
                teams = _teams(competition)
                if teams["home_name"] and teams["away_name"]:
                    key = (teams["home_name"].lower(), teams["away_name"].lower())
                    by_names[key] = event

            for fixture in fixtures:
                event = None
                if fixture.external_fixture_id:
                    event = by_external.get(str(fixture.external_fixture_id))
                if event is None:
                    key = (
                        (fixture.home_team or "").strip().lower(),
                        (fixture.away_team or "").strip().lower(),
                    )
                    event = by_names.get(key)
                if event is None:
                    continue
                if _apply(fixture, event):
                    updated += 1

        db.commit()

        warning = None
        if failures:
            warning = "Live feed unavailable for " + ", ".join(failures)
        return {
            "refreshed": not failures,
            "updated": updated,
            "skipped": skipped,
            "warning": warning,
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        return {
            "refreshed": False,
            "updated": 0,
            "skipped": 0,
            "warning": f"Live score refresh failed: {exc}",
        }


def fetch_live_scores(db: Session) -> List[Dict[str, Any]]:
    """Refresh (throttled) and return today's rows in the shape the Streamlit tables read."""
    refresh_live_scores(db)

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(
        select(Fixture)
        .where(Fixture.kickoff_utc >= day_start)
        .where(Fixture.kickoff_utc <= now + timedelta(hours=24))
        .order_by(Fixture.kickoff_utc.asc())
    ).all()

    matches: List[Dict[str, Any]] = []
    for fixture in rows:
        status = (fixture.status or "").lower()
        is_live = status in {"live", "halftime"}
        is_finished = status == "finished"
        kickoff = _as_utc(fixture.kickoff_utc)
        matches.append({
            "event_id": fixture.external_fixture_id,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "home_score": "" if fixture.home_score is None else str(fixture.home_score),
            "away_score": "" if fixture.away_score is None else str(fixture.away_score),
            "state": "in" if is_live else ("post" if is_finished else "pre"),
            "clock": fixture.live_clock or "",
            "period": 0,
            "status_desc": _STATUS_LABELS.get(status, status.title() or "Scheduled"),
            "fixture_id": fixture.id,
            "competition": fixture.competition,
            "is_live": is_live,
            "is_finished": is_finished,
            "kickoff_eat": kickoff.astimezone(EAT_TZ).strftime("%H:%M EAT") if kickoff else "",
        })

    state_order = {"in": 0, "pre": 1, "post": 2}
    matches.sort(key=lambda item: state_order.get(item["state"], 3))
    return matches
