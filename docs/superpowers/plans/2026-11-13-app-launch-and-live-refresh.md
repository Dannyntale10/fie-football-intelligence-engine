# App Launch and Live Refresh (Phase B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist live ESPN scores on fixtures, serve them from a throttled `GET /fixtures/live`, show a branded splash while eleven sections preload concurrently, and refresh live scores plus the active tab every 60 seconds.

**Architecture:** The existing `inplay.py` module becomes the single persisting score poller: it reads fixtures inside a four-hour window, polls only the ESPN league codes those fixtures need, matches events to stored rows by external id then exact team names, writes status/score/clock, and never creates a fixture. FastAPI exposes the stored live rows and the iOS client moves all network state into one `@MainActor EngineStore` injected at the app root; a single ticker owned by `RootView` refreshes live scores every minute.

**Tech Stack:** Python 3.9, FastAPI, SQLAlchemy 2.x, SQLite, pytest, SwiftUI (iOS 17), XCTest-free parse checks.

**Spec:** `docs/superpowers/specs/2026-11-13-bot-reliability-and-app-launch-design.md` (Section 5, B1–B4)

## Global Constraints

- The published 85% qualification gate is never lowered or bypassed.
- No bet is placed automatically; the bot and the app are read-only.
- Never fabricate a fixture, price, score, injury, or statistic.
- A fixture with no stored score renders no score; never show placeholder zeros.
- ESPN is the only live-score source; no paid API is introduced.
- Every stored price keeps its bookmaker and capture time; this phase does not touch prices.
- Do not delete synthetic ledger rows; that remains a separate approval-gated task.
- No secrets in commits; no `.env`, `sentinel_state.json`, or `data/*.json` runtime files staging.
- Run backend tests from the repo root with `.venv/bin/pytest -q`; `python` is not on PATH.
- Commit after each task; do not push until Task 10.
- Concurrent sessions share this working tree: run `git status --short` before staging and stage only the files named in the task.
- Do not restart or kill the long-running backend on port 8001; use port 8011 for smoke checks.

---

### Task 1: Store the ESPN league code on every ingested fixture

**Files:**
- Modify: `backend/app/models/fixture.py:44-46`
- Modify: `backend/app/database.py:24-30`
- Modify: `backend/app/data_sources/live_feed.py:109-225`
- Test: `tests/test_live_league_code.py` (create)

**Interfaces:**
- Produces: `Fixture.live_clock: str | None` and `Fixture.espn_league_code: str | None`.
- Produces: `fetch_live_upcoming_fixtures_espn` writes `espn_league_code` on insert and on existing rows, and counts a league-code-only change as one `updated` row.

- [ ] **Step 1: Write the failing test**

Create `tests/test_live_league_code.py`:

```python
"""ESPN ingestion keeps the league code needed for score polling."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.data_sources import live_feed
from app.data_sources.fixtures import make_dedupe_key
from app.database import SessionLocal
from app.models.fixture import Fixture

KICKOFF = datetime.now(timezone.utc) + timedelta(days=30)


def _event(home: str, away: str) -> dict:
    return {
        "id": "990001",
        "date": KICKOFF.isoformat().replace("+00:00", "Z"),
        "competitions": [{
            "status": {"type": {"state": "pre"}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ],
        }],
    }


class _Response:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events}


def _fake_get(events):
    def get(url, **_kwargs):
        if "/eng.1/" in url:
            return _Response(events)
        return _Response([])
    return get


def test_ingest_stores_the_league_code(monkeypatch):
    monkeypatch.setattr(
        live_feed.requests,
        "get",
        _fake_get([_event("Code Test FC", "Code Test Reserves")]),
    )
    db = SessionLocal()
    try:
        live_feed.fetch_live_upcoming_fixtures_espn(db, quick_sync=True)
        row = db.scalar(select(Fixture).where(Fixture.home_team == "Code Test FC"))
    finally:
        db.close()

    assert row is not None
    assert row.competition == "Premier League"
    assert row.espn_league_code == "eng.1"


def test_existing_fixture_gets_the_league_code(monkeypatch):
    dedupe = make_dedupe_key(
        KICKOFF,
        "Existing Code FC",
        "Existing Code Reserves",
        "Premier League",
    )
    db = SessionLocal()
    try:
        db.add(Fixture(
            dedupe_key=dedupe,
            kickoff_utc=KICKOFF,
            home_team="Existing Code FC",
            away_team="Existing Code Reserves",
            competition="Premier League",
            country="England",
            source="ESPN",
            status="scheduled",
        ))
        db.commit()

        monkeypatch.setattr(
            live_feed.requests,
            "get",
            _fake_get([_event("Existing Code FC", "Existing Code Reserves")]),
        )
        result = live_feed.fetch_live_upcoming_fixtures_espn(db, quick_sync=True)
        db.expire_all()
        row = db.scalar(select(Fixture).where(Fixture.home_team == "Existing Code FC"))
    finally:
        db.close()

    assert result["updated"] >= 1
    assert row is not None
    assert row.espn_league_code == "eng.1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_live_league_code.py -q`

Expected: FAIL with `AttributeError: 'Fixture' object has no attribute 'espn_league_code'`.

- [ ] **Step 3: Add the two columns to the model**

In `backend/app/models/fixture.py`, replace lines 44–46:

```python
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Live state written by the ESPN score poller; never inferred from odds.
    live_clock: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    espn_league_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="scheduled", index=True)
```

- [ ] **Step 4: Add the additive migration**

In `backend/app/database.py`, extend the fixtures `new_cols` list (currently lines 24–30) to:

```python
                new_cols = [
                    ("opening_home_odds", "REAL"),
                    ("opening_draw_odds", "REAL"),
                    ("opening_away_odds", "REAL"),
                    ("odds_updated_at", "TIMESTAMP"),
                    ("market_odds_json", "TEXT"),
                    ("live_clock", "VARCHAR(20)"),
                    ("espn_league_code", "VARCHAR(30)"),
                ]
```

- [ ] **Step 5: Carry the league code through ingestion**

In `backend/app/data_sources/live_feed.py`, replace the body from `tasks = []` through the end of the existing-row branch with:

```python
    # Build work items (URL tasks)
    tasks = []
    for comp_code, comp_name, country, date_params in COMPETITION_TARGETS:
        # For rapid 30s background sync, check today + next 2 days; full scan checks all 8 days
        active_params = date_params[:3] if quick_sync else date_params
        for d_param in active_params:
            url = f"http://site.api.espn.com/apis/site/v2/sports/soccer/{comp_code}/scoreboard{d_param}"
            tasks.append((url, comp_code, comp_name, country))

    def _fetch_url(item):
        target_url, c_code, c_name, c_country = item
        try:
            r = requests.get(target_url, headers=headers, timeout=3.5)
            if r.status_code == 200:
                return r.json(), c_code, c_name, c_country
        except Exception:
            pass
        return None, c_code, c_name, c_country

    seen_event_ids = set()
    raw_events = []

    # Concurrently fetch in parallel (12 workers)
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(_fetch_url, t) for t in tasks]
        for fut in as_completed(futures):
            data, c_code, c_name, c_country = fut.result()
            if not data:
                continue
            for event in data.get("events", []):
                e_id = event.get("id")
                if e_id and e_id in seen_event_ids:
                    continue
                if e_id:
                    seen_event_ids.add(e_id)
                raw_events.append((event, c_code, c_name, c_country))

    for event, comp_code, comp_name, country in raw_events:
        comp_data = event.get("competitions", [{}])[0]
        status_state = comp_data.get("status", {}).get("type", {}).get("state", "pre")

        raw_date = event.get("date")
        if not raw_date:
            continue
        try:
            kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except Exception:
            continue

        # STRICT RULE: Must be strictly in the future!
        if kickoff <= now:
            continue

        competitors = comp_data.get("competitors", [])
        if len(competitors) < 2:
            continue

        home_team = next(
            (c["team"].get("displayName") or c["team"].get("name")
             for c in competitors if c.get("homeAway") == "home"),
            None
        )
        away_team = next(
            (c["team"].get("displayName") or c["team"].get("name")
             for c in competitors if c.get("homeAway") == "away"),
            None
        )
        if not home_team or not away_team:
            continue

        # Extract DraftKings odds
        h_odds, d_odds, a_odds = None, None, None
        odds_list = comp_data.get("odds", [])
        if odds_list and isinstance(odds_list[0], dict):
            od = odds_list[0]
            ml = od.get("moneyline", {})
            h_odds = american_to_decimal(ml.get("home", {}).get("close", {}).get("odds"))
            d_odds = american_to_decimal(ml.get("draw", {}).get("close", {}).get("odds"))
            a_odds = american_to_decimal(ml.get("away", {}).get("close", {}).get("odds"))

            if not h_odds:
                h_odds = american_to_decimal(ml.get("home", {}).get("open", {}).get("odds"))
            if not d_odds:
                d_odds = american_to_decimal(ml.get("draw", {}).get("open", {}).get("odds"))
            if not a_odds:
                a_odds = american_to_decimal(ml.get("away", {}).get("open", {}).get("odds"))

        total_found += 1
        dedupe = make_dedupe_key(kickoff, home_team, away_team, comp_name)

        existing = db.scalar(select(Fixture).where(Fixture.dedupe_key == dedupe))
        if existing:
            changed = False
            if existing.espn_league_code != comp_code:
                existing.espn_league_code = comp_code
                changed = True
            if h_odds and existing.home_odds is None:
                existing.home_odds = h_odds
                existing.draw_odds = d_odds
                existing.away_odds = a_odds
                existing.source = "ESPN_DraftKings"
                changed = True
            if changed:
                db.commit()
                updated += 1
        else:
            event_id = event.get("id")
            new_fix = Fixture(
                dedupe_key=dedupe,
                kickoff_utc=kickoff,
                home_team=home_team,
                away_team=away_team,
                competition=comp_name,
                country=country,
                source="ESPN_DraftKings",
                external_fixture_id=str(event_id) if event_id else None,
                espn_league_code=comp_code,
                home_odds=h_odds,
                draw_odds=d_odds,
                away_odds=a_odds,
                status="scheduled",
            )
            db.add(new_fix)
            db.commit()
            inserted += 1
```

Also change the returned message (the last line of the function) to:

```python
        "message": f"Successfully ingested {inserted} genuine upcoming live matches ({updated} refreshed with league code or DraftKings odds) across UEFA Nations League & top leagues."
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_live_league_code.py tests/test_migrations.py -q`

Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/fixture.py backend/app/database.py backend/app/data_sources/live_feed.py tests/test_live_league_code.py
git commit -m "feat(live): store the ESPN league code on fixtures"
```

---

### Task 2: Rewrite inplay.py to persist live scores

**Files:**
- Rewrite: `backend/app/data_sources/inplay.py`
- Test: `tests/test_inplay.py` (create)

**Interfaces:**
- Produces: `refresh_live_scores(db: Session, *, force: bool = False) -> dict` returning `{"refreshed": bool, "updated": int, "skipped": int, "warning": str | None}`; it never raises and never creates a fixture.
- Produces: `fetch_live_scores(db: Session) -> List[Dict[str, Any]]` with the exact keys Streamlit and `dashboard.py` already read: `home_team`, `away_team`, `home_score`, `away_score`, `state`, `clock`, `status_desc`, `fixture_id`, `competition`, `is_live`, `is_finished`, `kickoff_eat`.
- Produces: module constant `REFRESH_INTERVAL_SECONDS = 60.0` and module variable `_last_refresh_monotonic` (tests reset it to `None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inplay.py`:

```python
"""The live poller persists ESPN state without creating fixtures."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.data_sources import inplay
from app.database import SessionLocal
from app.models.fixture import Fixture


class _Response:
    status_code = 200

    def __init__(self, events):
        self._events = events

    def json(self):
        return {"events": self._events}


def _event(ev_id, home, away, state, type_name, clock, home_score, away_score):
    return {
        "id": ev_id,
        "date": "2026-07-04T18:00Z",
        "competitions": [{
            "status": {
                "displayClock": clock,
                "period": 2,
                "type": {
                    "state": state,
                    "name": type_name,
                    "shortDetail": type_name,
                },
            },
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": home_score},
                {"homeAway": "away", "team": {"displayName": away}, "score": away_score},
            ],
        }],
    }


@pytest.fixture(autouse=True)
def _reset_throttle():
    inplay._last_refresh_monotonic = None
    yield
    inplay._last_refresh_monotonic = None


def _add_fixture(db, suffix: str, competition: str, external_id: str | None) -> Fixture:
    fixture = Fixture(
        dedupe_key=f"inplay-{suffix}",
        kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=30),
        home_team=f"{suffix} Home",
        away_team=f"{suffix} Away",
        competition=competition,
        country="Test",
        source="Test",
        external_fixture_id=external_id,
        status="scheduled",
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    return fixture


def test_refresh_persists_live_state_and_scores(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Live", "Premier League", "501")
        event = _event("501", fixture.home_team, fixture.away_team, "in", "STATUS_IN_PROGRESS", "67'", "2", "1")
        monkeypatch.setattr(inplay.requests, "get", lambda *_a, **_k: _Response([event]))

        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(fixture)
    finally:
        db.close()

    assert result["refreshed"] is True
    assert result["warning"] is None
    assert result["updated"] == 1
    assert fixture.status == "live"
    assert fixture.home_score == 2
    assert fixture.away_score == 1
    assert fixture.live_clock == "67'"


def test_refresh_maps_halftime_and_full_time(monkeypatch):
    db = SessionLocal()
    try:
        halftime = _add_fixture(db, "Halftime", "Premier League", "502")
        finished = _add_fixture(db, "Finished", "Premier League", "503")
        events = [
            _event("502", halftime.home_team, halftime.away_team, "in", "STATUS_HALFTIME", "", "0", "0"),
            _event("503", finished.home_team, finished.away_team, "post", "STATUS_FULL_TIME", "", "3", "2"),
        ]
        monkeypatch.setattr(inplay.requests, "get", lambda *_a, **_k: _Response(events))

        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(halftime)
        db.refresh(finished)
    finally:
        db.close()

    assert result["updated"] == 2
    assert halftime.status == "halftime"
    assert halftime.live_clock == "HT"
    assert finished.status == "finished"
    assert finished.live_clock == "FT"
    assert (finished.home_score, finished.away_score) == (3, 2)


def test_refresh_skips_fixtures_without_a_league_code(monkeypatch):
    db = SessionLocal()
    try:
        _add_fixture(db, "Unknown", "Unmapped Competition", "504")

        def _boom(*_a, **_k):
            raise AssertionError("no ESPN call expected")

        monkeypatch.setattr(inplay.requests, "get", _boom)
        result = inplay.refresh_live_scores(db, force=True)
    finally:
        db.close()

    assert result["skipped"] >= 1
    assert result["updated"] == 0


def test_refresh_is_throttled_to_one_minute(monkeypatch):
    db = SessionLocal()
    try:
        _add_fixture(db, "Throttled", "Premier League", "505")
        calls = []

        def _count(*_a, **_k):
            calls.append(1)
            return _Response([])

        monkeypatch.setattr(inplay.requests, "get", _count)
        first = inplay.refresh_live_scores(db, force=True)
        second = inplay.refresh_live_scores(db)
    finally:
        db.close()

    assert first["refreshed"] is True
    assert second["refreshed"] is False
    assert second["warning"] is None
    assert len(calls) == 1


def test_feed_failure_returns_a_warning_and_keeps_the_row(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Outage", "Premier League", "506")

        def _fail(*_a, **_k):
            raise RuntimeError("ESPN unreachable")

        monkeypatch.setattr(inplay.requests, "get", _fail)
        result = inplay.refresh_live_scores(db, force=True)
        db.refresh(fixture)
    finally:
        db.close()

    assert result["refreshed"] is False
    assert "ESPN unreachable" in (result["warning"] or "")
    assert fixture.status == "scheduled"
    assert fixture.home_score is None


def test_fetch_live_scores_returns_the_streamlit_shape(monkeypatch):
    db = SessionLocal()
    try:
        fixture = _add_fixture(db, "Shape", "Premier League", "507")
        fixture.status = "live"
        fixture.home_score = 1
        fixture.away_score = 0
        fixture.live_clock = "54'"
        db.commit()

        monkeypatch.setattr(inplay, "refresh_live_scores", lambda db, force=False: {
            "refreshed": False, "updated": 0, "skipped": 0, "warning": None,
        })
        rows = inplay.fetch_live_scores(db)
    finally:
        db.close()

    ids = {row["fixture_id"] for row in rows}
    assert fixture.id in ids
    row = next(item for item in rows if item["fixture_id"] == fixture.id)
    assert row["is_live"] is True
    assert row["home_score"] == "1"
    assert row["away_score"] == "0"
    assert row["clock"] == "54'"
    assert row["state"] == "in"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_inplay.py -q`

Expected: FAIL with `AttributeError: module 'app.data_sources.inplay' has no attribute '_last_refresh_monotonic'` or missing `refresh_live_scores`.

- [ ] **Step 3: Replace the module**

Write `backend/app/data_sources/inplay.py` in full:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_inplay.py -q`

Expected: PASS (6 tests).

- [ ] **Step 5: Confirm the Streamlit consumers still import**

Run: `.venv/bin/python -m compileall -q backend/app streamlit_app.py dashboard.py`

Expected: exit 0 with no output.

- [ ] **Step 6: Commit**

```bash
git add backend/app/data_sources/inplay.py tests/test_inplay.py
git commit -m "feat(live): persist ESPN scores onto stored fixtures"
```

---

### Task 3: Expose `GET /fixtures/live`

**Files:**
- Modify: `backend/app/schemas.py:15-38`
- Modify: `backend/app/main.py:23-30` and `backend/app/main.py:106-127`, `backend/app/main.py:207-215`
- Test: `tests/test_live_endpoint.py` (create)

**Interfaces:**
- Consumes: `refresh_live_scores(db, *, force=False)` from Task 2.
- Produces: `FixtureOut.home_score`, `FixtureOut.away_score`, `FixtureOut.live_clock`.
- Produces: `LiveFixturesOut = {as_of: datetime, refreshed: bool, warning: str | None, fixtures: [FixtureOut]}`.
- Produces: route `GET /fixtures/live` declared before `GET /fixtures/{fixture_id}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_endpoint.py`:

```python
"""GET /fixtures/live serves stored live rows and survives a feed outage."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.database import SessionLocal
from app.main import app
from app.models.fixture import Fixture

client = TestClient(app)


def _insert_live_fixture() -> int:
    db = SessionLocal()
    try:
        fixture = Fixture(
            dedupe_key=f"live-endpoint-{datetime.now(timezone.utc).timestamp()}",
            kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=20),
            home_team="Endpoint Live FC",
            away_team="Endpoint Live Reserves",
            competition="Fixture Payload League",
            country="Test",
            source="Test",
            external_fixture_id="770001",
            home_score=2,
            away_score=1,
            live_clock="67'",
            status="live",
        )
        db.add(fixture)
        db.commit()
        db.refresh(fixture)
        return fixture.id
    finally:
        db.close()


def test_live_endpoint_returns_scores_and_clock(monkeypatch):
    fixture_id = _insert_live_fixture()
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {"refreshed": True, "updated": 0, "skipped": 0, "warning": None},
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["refreshed"] is True
    assert payload["warning"] is None
    row = next(item for item in payload["fixtures"] if item["id"] == fixture_id)
    assert row["home_score"] == 2
    assert row["away_score"] == 1
    assert row["live_clock"] == "67'"
    assert row["status"] == "live"


def test_live_endpoint_survives_a_feed_outage(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {
            "refreshed": False,
            "updated": 0,
            "skipped": 0,
            "warning": "feed down",
        },
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    assert response.json()["warning"] == "feed down"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_live_endpoint.py -q`

Expected: FAIL with 422 on `/fixtures/live` (the path is parsed as `fixture_id`).

- [ ] **Step 3: Extend the fixture schema**

In `backend/app/schemas.py`, replace the `FixtureOut` class with:

```python
class FixtureOut(BaseModel):
    id: int
    kickoff_utc: datetime
    kickoff_eat: Optional[str] = None
    home_team: str
    away_team: str
    competition: str
    country: str
    source: str
    external_fixture_id: Optional[str] = None
    home_odds: Optional[float] = None
    draw_odds: Optional[float] = None
    away_odds: Optional[float] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    live_clock: Optional[str] = None
    status: Optional[str] = None
    data_quality_score: Optional[int] = None
    data_quality_grade: Optional[str] = None
    eligible_for_analysis: Optional[bool] = None

    model_config = {"from_attributes": True}

class LiveFixturesOut(BaseModel):
    as_of: datetime
    refreshed: bool
    warning: Optional[str] = None
    fixtures: List[FixtureOut]
```

- [ ] **Step 4: Include the fields in the fixture payload**

In `backend/app/main.py`, inside `_build_fixture_out`, replace the odds block with:

```python
        "home_odds": f.home_odds,
        "draw_odds": f.draw_odds,
        "away_odds": f.away_odds,
        "home_score": f.home_score,
        "away_score": f.away_score,
        "live_clock": f.live_clock,
        "status": f.status,
```

- [ ] **Step 5: Import the poller and the new schema**

In `backend/app/main.py`, add to the imports:

```python
from app.data_sources.inplay import refresh_live_scores
```

and add `LiveFixturesOut` to the existing `from app.schemas import (...)` block.

- [ ] **Step 6: Add the route before the id route**

In `backend/app/main.py`, insert this route after `delete_past_fixtures` and before `@app.get("/fixtures/{fixture_id}")`:

```python
@app.get("/fixtures/live", response_model=LiveFixturesOut)
def live_fixtures(db: Session = Depends(get_db)):
    """Refresh (throttled) and return stored in-play fixtures with scores and clock."""
    refresh = refresh_live_scores(db)
    rows = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(("live", "halftime")))
        .order_by(Fixture.kickoff_utc.asc())
    ).all()
    return {
        "as_of": datetime.now(timezone.utc),
        "refreshed": bool(refresh.get("refreshed")),
        "warning": refresh.get("warning"),
        "fixtures": [_build_fixture_out(f) for f in rows],
    }
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_live_endpoint.py tests/test_api.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_live_endpoint.py
git commit -m "feat(api): serve live fixtures with scores and clock"
```

---

### Task 4: Add the BrandLogo asset and launch image

**Files:**
- Create: `ios/FIE/Assets.xcassets/BrandLogo.imageset/Contents.json`
- Create: `ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo.png`
- Create: `ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo@2x.png`
- Create: `ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo@3x.png`
- Modify: `ios/FIE/Info.plist:38-42`

**Interfaces:**
- Produces: asset name `BrandLogo`, usable as `Image("BrandLogo")` and as the `UILaunchScreen` image.

- [ ] **Step 1: Create the imageset from the existing icon**

Run:

```bash
mkdir -p ios/FIE/Assets.xcassets/BrandLogo.imageset
sips -z 120 120 ios/FIE/Assets.xcassets/AppIcon.appiconset/AppIcon.png --out ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo.png
sips -z 240 240 ios/FIE/Assets.xcassets/AppIcon.appiconset/AppIcon.png --out ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo@2x.png
sips -z 360 360 ios/FIE/Assets.xcassets/AppIcon.appiconset/AppIcon.png --out ios/FIE/Assets.xcassets/BrandLogo.imageset/BrandLogo@3x.png
file ios/FIE/Assets.xcassets/BrandLogo.imageset/*.png
```

Expected: three PNG files at 120×120, 240×240, and 360×360 pixels.

- [ ] **Step 2: Write the imageset manifest**

Create `ios/FIE/Assets.xcassets/BrandLogo.imageset/Contents.json`:

```json
{
  "images" : [
    {
      "filename" : "BrandLogo.png",
      "idiom" : "universal",
      "scale" : "1x"
    },
    {
      "filename" : "BrandLogo@2x.png",
      "idiom" : "universal",
      "scale" : "2x"
    },
    {
      "filename" : "BrandLogo@3x.png",
      "idiom" : "universal",
      "scale" : "3x"
    }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
```

- [ ] **Step 3: Point the launch screen at the mark**

In `ios/FIE/Info.plist`, replace the `UILaunchScreen` dictionary with:

```xml
	<key>UILaunchScreen</key>
	<dict>
		<key>UIColorName</key>
		<string>LaunchBackground</string>
		<key>UIImageName</key>
		<string>BrandLogo</string>
	</dict>
```

- [ ] **Step 4: Verify the plist and JSON**

Run:

```bash
plutil -lint ios/FIE/Info.plist
.venv/bin/python -m json.tool ios/FIE/Assets.xcassets/BrandLogo.imageset/Contents.json
```

Expected: `OK` from `plutil` and pretty-printed JSON with no error.

- [ ] **Step 5: Commit**

```bash
git add ios/FIE/Assets.xcassets/BrandLogo.imageset ios/FIE/Info.plist
git commit -m "feat(ios): add the brand mark and launch image"
```

---

### Task 5: Add the shared EngineStore and live fixture models

**Files:**
- Modify: `ios/FIE/Core/Models.swift:19-40`
- Create: `ios/FIE/Core/EngineStore.swift`
- Modify: `ios/FIE.xcodeproj/project.pbxproj` (four entries for `EngineStore.swift`)

**Interfaces:**
- Produces: `Fixture.homeScore: Int?`, `Fixture.awayScore: Int?`, `Fixture.liveClock: String?`.
- Produces: `struct LiveFixturesResponse { asOf: String?, refreshed: Bool, warning: String?, fixtures: [Fixture] }`.
- Produces: `enum EngineSection` with 11 cases and `static func forTab(_:)`.
- Produces: `@MainActor final class EngineStore` with published `report`, `fixtures`, `gate`, `health`, `summary`, `clv`, `learning`, `sweep`, `sources`, `backtest`, `live`, `errors`, `ready`, `isBootstrapped`, `isRefreshing`, `lastUpdated`, `livePaused`, `cachedAt`; and methods `bootstrap()`, `refreshAll()`, `refresh(section:)`, `refreshLive()`, `refreshFeeds()`, `runBacktest()`, `startAutoRefresh(section:)`, `stopAutoRefresh()`, `setActiveSection(_:)`, `reset()`.
- Produces: `var readyCount: Int` and `var isAutoTicking: Bool`.

- [ ] **Step 1: Extend the Fixture model**

In `ios/FIE/Core/Models.swift`, replace the `Fixture` struct with:

```swift
struct Fixture: Decodable, Identifiable {
    let id: Int
    let kickoffUtc: String?
    let kickoffEat: String?
    let homeTeam: String
    let awayTeam: String
    let competition: String
    let country: String
    let source: String
    let externalFixtureId: String?
    let homeOdds: Double?
    let drawOdds: Double?
    let awayOdds: Double?
    let homeScore: Int?
    let awayScore: Int?
    let liveClock: String?
    let status: String?
    let dataQualityScore: Double?
    let dataQualityGrade: String?
    let eligibleForAnalysis: Bool?

    var matchLabel: String { "\(homeTeam) vs \(awayTeam)" }
}

struct LiveFixturesResponse: Decodable {
    let asOf: String?
    let refreshed: Bool
    let warning: String?
    let fixtures: [Fixture]
}
```

- [ ] **Step 2: Create the store**

Create `ios/FIE/Core/EngineStore.swift`:

```swift
import Foundation
import SwiftUI

enum EngineSection: String, CaseIterable, Identifiable {
    case health, report, fixtures, gate, summary, clv, learning, sweep, backtest, sources, live

    var id: String { rawValue }

    var title: String {
        switch self {
        case .health: return "Engine"
        case .report: return "Daily report"
        case .fixtures: return "Fixtures"
        case .gate: return "85% gate"
        case .summary: return "Ledger"
        case .clv: return "Closing line"
        case .learning: return "Learning"
        case .sweep: return "Tier sweep"
        case .backtest: return "Historical replay"
        case .sources: return "Sources"
        case .live: return "Live scores"
        }
    }

    static func forTab(_ index: Int) -> EngineSection {
        switch index {
        case 0: return .report
        case 1: return .fixtures
        case 2: return .summary
        case 3: return .sources
        default: return .health
        }
    }
}

@MainActor
final class EngineStore: ObservableObject {
    @Published private(set) var report: DailyComboReport?
    @Published private(set) var fixtures: [Fixture] = []
    @Published private(set) var gate: GateReport?
    @Published private(set) var health: Health?
    @Published private(set) var summary: PerformanceSummary?
    @Published private(set) var clv: ClvSummary?
    @Published private(set) var learning: LearningSummary?
    @Published private(set) var sweep: ComboSweep?
    @Published private(set) var sources: SourcesResponse?
    @Published private(set) var backtest: ComboBacktestReport?
    @Published private(set) var live: [Fixture] = []
    @Published private(set) var errors: [EngineSection: String] = [:]
    @Published private(set) var ready: Set<EngineSection> = []
    @Published private(set) var isBootstrapped = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var lastUpdated: Date?
    @Published private(set) var livePaused = false
    @Published private(set) var cachedAt: Date?

    private var autoTask: Task<Void, Never>?
    private var activeSection: EngineSection = .report

    var readyCount: Int { ready.count }
    var isAutoTicking: Bool { autoTask != nil }

    private var client: APIClient {
        let raw = UserDefaults.standard.string(forKey: AppSettings.serverURLKey)
            ?? AppSettings.defaultServerURL
        return APIClient(baseURL: URL(string: raw) ?? URL(string: AppSettings.defaultServerURL)!)
    }

    func bootstrap() async {
        guard !isBootstrapped, !isRefreshing else { return }

        if report == nil, let cached = ReportCache.load() {
            report = cached.report
            cachedAt = cached.savedAt
        }

        isRefreshing = true
        await withTaskGroup(of: Void.self) { group in
            for section in EngineSection.allCases {
                group.addTask { await self.load(section) }
            }
        }
        isRefreshing = false
        isBootstrapped = true
        lastUpdated = Date()
    }

    func refreshAll() async {
        isRefreshing = true
        await withTaskGroup(of: Void.self) { group in
            for section in EngineSection.allCases {
                group.addTask { await self.load(section) }
            }
        }
        isRefreshing = false
        lastUpdated = Date()
    }

    func refresh(section: EngineSection) async {
        await load(section)
        lastUpdated = Date()
    }

    func refreshLive() async {
        do {
            let response = try await client.get(
                "/fixtures/live",
                timeout: 20,
                as: LiveFixturesResponse.self
            )
            live = response.fixtures
            livePaused = false
            errors.removeValue(forKey: .live)
            lastUpdated = Date()
        } catch {
            livePaused = true
            errors[.live] = error.localizedDescription
        }
    }

    func refreshFeeds() async throws -> SourcesRefreshResult {
        let result = try await client.post(
            "/sources/refresh",
            timeout: 900,
            as: SourcesRefreshResult.self
        )
        await refresh(section: .report)
        await refresh(section: .fixtures)
        await refresh(section: .live)
        await refresh(section: .sources)
        return result
    }

    func runBacktest() async throws -> ComboBacktestReport {
        let result = try await client.post(
            "/combos/backtest",
            timeout: 900,
            as: ComboBacktestReport.self
        )
        backtest = result
        errors.removeValue(forKey: .backtest)
        return result
    }

    func startAutoRefresh(section: EngineSection) {
        setActiveSection(section)
        guard autoTask == nil else { return }
        autoTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 60_000_000_000)
                if Task.isCancelled { return }
                guard let self else { return }
                await self.refreshLive()
                await self.refresh(section: self.activeSection)
            }
        }
    }

    func stopAutoRefresh() {
        autoTask?.cancel()
        autoTask = nil
    }

    func setActiveSection(_ section: EngineSection) {
        activeSection = section
    }

    func reset() {
        stopAutoRefresh()
        report = nil
        fixtures = []
        gate = nil
        health = nil
        summary = nil
        clv = nil
        learning = nil
        sweep = nil
        sources = nil
        backtest = nil
        live = []
        errors = [:]
        ready = []
        isBootstrapped = false
        isRefreshing = false
        lastUpdated = nil
        livePaused = false
        cachedAt = nil
        ReportCache.clear()
    }

    private func load(_ section: EngineSection) async {
        do {
            switch section {
            case .health:
                health = try await client.get("/health", timeout: 15, as: Health.self)
            case .report:
                let fresh = try await client.get("/combos/daily", as: DailyComboReport.self)
                report = fresh
                cachedAt = nil
                ReportCache.save(fresh)
            case .fixtures:
                fixtures = try await client.get(
                    "/fixtures",
                    query: [
                        URLQueryItem(name: "upcoming_only", value: "true"),
                        URLQueryItem(name: "limit", value: "120"),
                    ],
                    as: [Fixture].self
                )
            case .gate:
                gate = try await client.get(
                    "/markets/qualifying",
                    timeout: 120,
                    as: GateReport.self
                )
            case .summary:
                summary = try await client.get(
                    "/performance/summary",
                    as: PerformanceSummary.self
                )
            case .clv:
                clv = try await client.get(
                    "/evaluation/clv",
                    query: [URLQueryItem(name: "days", value: "90")],
                    as: ClvSummary.self
                )
            case .learning:
                learning = try await client.get("/learning/summary", as: LearningSummary.self)
            case .sweep:
                sweep = try await client.get(
                    "/combos/backtest/sweep",
                    timeout: 120,
                    as: ComboSweep.self
                )
            case .backtest:
                backtest = try await client.get("/combos/backtest", as: ComboBacktestReport.self)
            case .sources:
                sources = try await client.get("/sources", as: SourcesResponse.self)
            case .live:
                let response = try await client.get(
                    "/fixtures/live",
                    timeout: 20,
                    as: LiveFixturesResponse.self
                )
                live = response.fixtures
                livePaused = false
            }
            errors.removeValue(forKey: section)
        } catch let error as APIError where
            section == .backtest && error.message.contains("No combo backtest yet") {
            errors.removeValue(forKey: .backtest)
        } catch {
            errors[section] = error.localizedDescription
            if section == .live { livePaused = true }
        }
        ready.insert(section)
    }
}
```

- [ ] **Step 3: Register the file in the Xcode project**

In `ios/FIE.xcodeproj/project.pbxproj`:

After the `ReportCache.swift in Sources` build-file line (line 15), add:

```text
		1C0000000000000000000013 /* EngineStore.swift in Sources */ = {isa = PBXBuildFile; fileRef = 1B0000000000000000000013 /* EngineStore.swift */; };
```

After the `ReportCache.swift` file-reference entry (line 36), add:

```text
		1B0000000000000000000013 /* EngineStore.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = EngineStore.swift; sourceTree = "<group>"; };
```

In the `Core` group children (after `Haptics.swift`), add:

```text
				1B0000000000000000000013 /* EngineStore.swift */,
```

In the `Sources` build phase (after the `Haptics.swift in Sources` line), add:

```text
				1C0000000000000000000013 /* EngineStore.swift in Sources */,
```

- [ ] **Step 4: Verify the project file and parse the new source**

Run:

```bash
plutil -lint ios/FIE.xcodeproj/project.pbxproj
xcrun swiftc -parse ios/FIE/Core/Models.swift ios/FIE/Core/EngineStore.swift ios/FIE/Core/APIClient.swift ios/FIE/Core/AppSettings.swift ios/FIE/Core/ReportCache.swift ios/FIE/Core/Formatting.swift
```

Expected: `OK` and no parse errors.

- [ ] **Step 5: Commit**

```bash
git add ios/FIE/Core/Models.swift ios/FIE/Core/EngineStore.swift ios/FIE.xcodeproj/project.pbxproj
git commit -m "feat(ios): add the shared engine store and live fixture models"
```

---

### Task 6: Show the splash while the store preloads

**Files:**
- Create: `ios/FIE/Design/SplashView.swift`
- Modify: `ios/FIE/FIEApp.swift`
- Modify: `ios/FIE.xcodeproj/project.pbxproj` (four entries for `SplashView.swift`)

**Interfaces:**
- Consumes: `EngineStore`, `EngineSection`, `store.readyCount`, `store.isBootstrapped`.
- Produces: `SplashView(readyCount:total:)`.
- Produces: `FIEApp` owns `@StateObject private var store = EngineStore()` and injects it with `.environmentObject`.
- Produces: `RootView` owns the splash gate, the 1-second minimum, the 6-second cap, the scene-phase loop control, and the tab-to-section mapping.

- [ ] **Step 1: Create the splash view**

Create `ios/FIE/Design/SplashView.swift`:

```swift
import SwiftUI

struct SplashView: View {
    let readyCount: Int
    let total: Int

    @State private var appeared = false

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            RadialGradient(
                colors: [Palette.accent.opacity(0.22), .clear],
                center: .center,
                startRadius: 10,
                endRadius: 420
            )
            .ignoresSafeArea()

            VStack(spacing: 18) {
                Spacer()

                Image("BrandLogo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 120, height: 120)
                    .clipShape(RoundedRectangle(cornerRadius: 28, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 28, style: .continuous)
                            .stroke(Palette.accent.opacity(0.35), lineWidth: 1)
                    )
                    .shadow(color: Palette.accent.opacity(0.25), radius: 24, y: 8)

                Text("FIE")
                    .font(.system(size: 40, weight: .bold, design: .rounded))
                    .tracking(4)
                    .foregroundStyle(Palette.textPrimary)

                Text("Football Intelligence Engine")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)

                Spacer()

                VStack(spacing: 8) {
                    ProgressView()
                        .tint(Palette.accent)
                    Text("Loading · \(readyCount) / \(total) ready")
                        .font(TypeScale.caption)
                        .foregroundStyle(Palette.textTertiary)
                        .monospacedDigit()
                }
                .padding(.bottom, 36)
            }
            .opacity(appeared ? 1 : 0)
            .scaleEffect(appeared ? 1 : 0.96)
        }
        .onAppear {
            withAnimation(.easeOut(duration: 0.45)) { appeared = true }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Football Intelligence Engine is loading, \(readyCount) of \(total) sections ready"
        )
    }
}
```

- [ ] **Step 2: Wire the app root**

Replace `ios/FIE/FIEApp.swift` in full:

```swift
import SwiftUI

@main
struct FIEApp: App {
    @StateObject private var store = EngineStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .tint(Palette.accent)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var store: EngineStore
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("fie.selectedTab") private var selectedTab = 0
    @State private var showSplash = true

    var body: some View {
        ZStack {
            TabView(selection: $selectedTab) {
                TodayView()
                    .tag(0)
                    .tabItem { Label("Today", systemImage: "sparkles") }

                FixturesView()
                    .tag(1)
                    .tabItem { Label("Fixtures", systemImage: "sportscourt") }

                PerformanceView()
                    .tag(2)
                    .tabItem { Label("Performance", systemImage: "chart.bar.xaxis") }

                SourcesView()
                    .tag(3)
                    .tabItem { Label("Sources", systemImage: "antenna.radiowaves.left.and.right") }

                SettingsView()
                    .tag(4)
                    .tabItem { Label("Settings", systemImage: "gearshape") }
            }

            if showSplash {
                SplashView(
                    readyCount: store.readyCount,
                    total: EngineSection.allCases.count
                )
                .transition(.opacity)
                .zIndex(1)
            }
        }
        .task { await loadUntilReady() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active:
                Task {
                    await store.refreshLive()
                    store.startAutoRefresh(section: EngineSection.forTab(selectedTab))
                }
            case .background, .inactive:
                store.stopAutoRefresh()
            @unknown default:
                break
            }
        }
        .onChange(of: selectedTab) { _, tab in
            store.setActiveSection(EngineSection.forTab(tab))
        }
    }

    @MainActor
    private func loadUntilReady() async {
        let started = Date()
        Task { await store.bootstrap() }

        while !store.isBootstrapped && Date().timeIntervalSince(started) < 6 {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }

        let remaining = 1.0 - Date().timeIntervalSince(started)
        if remaining > 0 {
            try? await Task.sleep(nanoseconds: UInt64(remaining * 1_000_000_000))
        }

        withAnimation(.easeOut(duration: 0.35)) { showSplash = false }
        store.startAutoRefresh(section: EngineSection.forTab(selectedTab))
    }
}
```

- [ ] **Step 3: Register the file in the Xcode project**

In `ios/FIE.xcodeproj/project.pbxproj`:

After the `EngineStore.swift in Sources` build-file line added in Task 5, add:

```text
		1C0000000000000000000014 /* SplashView.swift in Sources */ = {isa = PBXBuildFile; fileRef = 1B0000000000000000000014 /* SplashView.swift */; };
```

After the `EngineStore.swift` file-reference entry, add:

```text
		1B0000000000000000000014 /* SplashView.swift */ = {isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = SplashView.swift; sourceTree = "<group>"; };
```

In the `Design` group children (after `Components.swift`), add:

```text
				1B0000000000000000000014 /* SplashView.swift */,
```

In the `Sources` build phase (after the `EngineStore.swift in Sources` line), add:

```text
				1C0000000000000000000014 /* SplashView.swift in Sources */,
```

- [ ] **Step 4: Verify the project file and parse the app sources**

Run:

```bash
plutil -lint ios/FIE.xcodeproj/project.pbxproj
xcrun swiftc -parse ios/FIE/FIEApp.swift ios/FIE/Design/SplashView.swift ios/FIE/Core/EngineStore.swift
```

Expected: `OK` and no parse errors.

- [ ] **Step 5: Commit**

```bash
git add ios/FIE/Design/SplashView.swift ios/FIE/FIEApp.swift ios/FIE.xcodeproj/project.pbxproj
git commit -m "feat(ios): show a branded splash until preload completes"
```

---

### Task 7: Add live presentation components and the match-detail score line

**Files:**
- Modify: `ios/FIE/Design/Components.swift` (append)
- Modify: `ios/FIE/Features/FixtureDetailView.swift:3-11`, `ios/FIE/Features/FixtureDetailView.swift:104-132`

**Interfaces:**
- Consumes: `Fixture.homeScore`, `Fixture.awayScore`, `Fixture.liveClock`, `store.live`.
- Produces: `LiveDot`, `LiveScoreRow`, `LiveMatchesCard(fixtures:maxRows:glow:)`.
- Produces: `FixtureDetailView` reads `@EnvironmentObject var store: EngineStore` and renders a score line only when both scores exist.

- [ ] **Step 1: Append the shared live views**

Append to `ios/FIE/Design/Components.swift`:

```swift
struct LiveDot: View {
    @State private var pulsing = false

    var body: some View {
        Circle()
            .fill(Palette.danger)
            .frame(width: 8, height: 8)
            .scaleEffect(pulsing ? 1.35 : 0.9)
            .opacity(pulsing ? 0.55 : 1)
            .animation(
                .easeInOut(duration: 0.9).repeatForever(autoreverses: true),
                value: pulsing
            )
            .onAppear { pulsing = true }
            .accessibilityHidden(true)
    }
}

struct LiveScoreRow: View {
    let fixture: Fixture

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(fixture.matchLabel)
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Palette.textPrimary)
                    .lineLimit(2)
                Text(fixture.competition)
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if let home = fixture.homeScore, let away = fixture.awayScore {
                Text("\(home) – \(away)")
                    .font(.system(size: 20, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
            }

            if let clock = fixture.liveClock {
                Pill(text: clock, tint: Palette.danger)
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
    }
}

struct LiveMatchesCard: View {
    let fixtures: [Fixture]
    var maxRows: Int? = nil
    var glow: Bool = false

    private var shown: [Fixture] {
        guard let maxRows else { return fixtures }
        return Array(fixtures.prefix(maxRows))
    }

    var body: some View {
        Card(glow: glow) {
            HStack(spacing: 8) {
                LiveDot()
                Eyebrow(text: "Live now", tint: Palette.danger)
                Spacer()
                Pill(
                    text: fixtures.count > shown.count
                        ? "\(fixtures.count) in play"
                        : "\(fixtures.count) live",
                    tint: Palette.danger
                )
            }

            ForEach(shown) { fixture in
                NavigationLink {
                    FixtureDetailView(fixture: fixture)
                } label: {
                    LiveScoreRow(fixture: fixture)
                }
                .buttonStyle(.plain)
            }
        }
    }
}
```

- [ ] **Step 2: Read the store in the detail view**

In `ios/FIE/Features/FixtureDetailView.swift`, add this property directly under the `serverURL` declaration:

```swift
    @EnvironmentObject private var store: EngineStore
```

- [ ] **Step 3: Render the score line in the header**

In `ios/FIE/Features/FixtureDetailView.swift`, inside `header(_:)`, insert this call immediately after the `.fixedSize(horizontal: false, vertical: true)` line that follows `Text(info.match)`: 

```swift
            if let live = liveMatch {
                liveScoreLine(live)
            }
```

Then add these two members at the end of the `FixtureDetailView` struct (after `headerInfo`):

```swift
    private var liveMatch: Fixture? {
        store.live.first { $0.id == fixtureId }
    }

    @ViewBuilder
    private func liveScoreLine(_ live: Fixture) -> some View {
        if let home = live.homeScore, let away = live.awayScore {
            HStack(spacing: 8) {
                LiveDot()
                Text("\(home) – \(away)")
                    .font(.system(size: 22, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(Palette.textPrimary)
                if let clock = live.liveClock {
                    Pill(text: clock, tint: Palette.danger)
                }
                Spacer(minLength: 0)
            }
        }
    }
```

- [ ] **Step 4: Parse the changed sources**

Run:

```bash
xcrun swiftc -parse ios/FIE/Design/Components.swift ios/FIE/Features/FixtureDetailView.swift ios/FIE/Core/EngineStore.swift ios/FIE/Core/Models.swift
```

Expected: no parse errors.

- [ ] **Step 5: Commit**

```bash
git add ios/FIE/Design/Components.swift ios/FIE/Features/FixtureDetailView.swift
git commit -m "feat(ios): add live score components and header score"
```

---

### Task 8: Rewire Today and Fixtures to the store

**Files:**
- Modify: `ios/FIE/Features/TodayView.swift:3-275`
- Modify: `ios/FIE/Features/FixturesView.swift:3-197`

**Interfaces:**
- Consumes: `EngineStore` from Task 5, `LiveMatchesCard` from Task 7.
- Produces: both views read `@EnvironmentObject private var store: EngineStore` and keep only their local UI state.

- [ ] **Step 1: Replace the Today state block and body**

In `ios/FIE/Features/TodayView.swift`, replace everything from `struct TodayView: View {` through the end of `analyseSlate()` and `applyRefreshSummary` (line 3 through the closing brace before `// MARK: - Report`) with:

```swift
struct TodayView: View {
    @EnvironmentObject private var store: EngineStore

    @State private var analysing = false
    @State private var refreshingFeeds = false
    @State private var refreshError: String?
    @State private var refreshNote: String?

    private var report: DailyComboReport? { store.report }
    private var cachedAt: Date? { store.cachedAt }
    private var health: Health? { store.health }
    private var errorMessage: String? { store.errors[.report] }
    private var hasLoaded: Bool { store.isBootstrapped }

    private var busy: Bool { analysing || refreshingFeeds || store.isRefreshing }

    private var analyseLabel: String {
        if analysing { return "Analysing stored prices…" }
        if refreshingFeeds { return "Refreshing bookmaker feeds…" }
        return "Analyse today's slate"
    }

    private var gate: Double? {
        report?.tier.publishedGate ?? health?.minPredictionProbability
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    engineChip
                    actions
                    statusNotes
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: report == nil ? .danger : .warn)
                    }
                    if analysing && report == nil {
                        LoadingCard(label: "Reading today's slate from stored prices…")
                    }
                    if !store.live.isEmpty {
                        LiveMatchesCard(fixtures: store.live, maxRows: 3)
                    }
                    if let report {
                        DailyComboReportView(report: report, cachedAt: cachedAt)
                            .transition(.opacity)
                            .animation(
                                store.isAutoTicking ? nil : .easeOut(duration: 0.25),
                                value: report.generatedAt
                            )
                    } else if !busy && errorMessage == nil && hasLoaded {
                        Card {
                            Text("No report yet.")
                                .font(TypeScale.section)
                            Text("Tap Analyse today's slate to read the stored bookmaker prices for today's fixtures.")
                                .font(TypeScale.body)
                                .foregroundStyle(Palette.textSecondary)
                        }
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Today")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        HistoryView()
                    } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .accessibilityLabel("Test on history")
                }
            }
            .task {
                if !store.isBootstrapped { await store.bootstrap() }
            }
            .refreshable { await store.refreshAll() }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 6) {
            Eyebrow(text: "Control room")
            Text("Football Intelligence")
                .font(TypeScale.title)
                .foregroundStyle(Palette.textPrimary)
            Text("One 1X2 selection per fixture from today's real bookmaker prices, de-vigged. The 75% tier is exploratory and sits below the published 85% gate; the gate itself is unchanged.")
                .font(TypeScale.caption)
                .foregroundStyle(Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var engineChip: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(engineTint)
                .frame(width: 7, height: 7)
            Text(engineLabel)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Palette.textSecondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            if let gate {
                Pill(text: "\(Int((gate * 100).rounded()))% gate", tint: Palette.accent)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Palette.card)
        )
    }

    private var engineTint: Color {
        if health != nil { return Palette.accent }
        return store.isBootstrapped ? Palette.danger : Palette.textTertiary
    }

    private var engineLabel: String {
        if let health {
            return "Engine online · \(Fmt.count(health.totalFixtures)) fixtures stored"
        }
        if !store.isBootstrapped {
            return "Checking the engine…"
        }
        return report == nil
            ? "Engine unreachable · no saved report"
            : "Engine unreachable · showing the saved report"
    }

    private var actions: some View {
        Button {
            Task { await analyseSlate() }
        } label: {
            HStack(spacing: 8) {
                if busy {
                    ProgressView()
                        .tint(Palette.background)
                        .controlSize(.small)
                }
                Text(analyseLabel)
            }
        }
        .buttonStyle(PrimaryButtonStyle(busy: busy))
        .disabled(busy)
    }

    @ViewBuilder
    private var statusNotes: some View {
        if refreshingFeeds {
            LoadingCard(label: "Refreshing the public bookmaker feeds; the report below uses stored prices meanwhile.")
        }
        if let refreshError {
            NoteRow(
                text: "\(refreshError) The report below was rebuilt from whatever prices are stored.",
                tone: .warn
            )
        }
        if let refreshNote {
            NoteRow(text: refreshNote)
        }
    }

    @MainActor
    private func analyseSlate() async {
        guard !busy else { return }
        refreshError = nil
        refreshNote = nil

        analysing = true
        await store.refresh(section: .report)
        analysing = false

        refreshingFeeds = true
        do {
            let refreshed = try await store.refreshFeeds()
            applyRefreshSummary(refreshed)
            if store.errors[.report] == nil {
                Haptics.success()
            } else {
                Haptics.warning()
            }
        } catch {
            refreshError = error.localizedDescription
            Haptics.warning()
        }
        refreshingFeeds = false
    }

    @MainActor
    private func applyRefreshSummary(_ refreshed: SourcesRefreshResult) {
        let feeds = Array(refreshed.feeds.values)
        let failed = feeds.filter(\.isFailed).count
        let prices = refreshed.totals.oddsUpdated ?? 0
        let newFixtures = refreshed.totals.insertedNew ?? 0
        refreshNote = "Synced \(feeds.count - failed)/\(feeds.count) bookmaker feeds · \(prices) prices updated · \(newFixtures) new fixtures."

        if failed > 0 {
            let reasons = feeds.filter(\.isFailed).map { feed -> String in
                let name = feed.label ?? feed.provider ?? "feed"
                return "\(name): \(feed.error ?? "unreachable")"
            }
            refreshError = "\(failed) bookmaker feed(s) unreachable, so their prices were not refreshed"
                + (reasons.isEmpty ? "." : " (\(reasons.joined(separator: "; "))).")
        }
    }
}
```

- [ ] **Step 2: Replace the Fixtures state block and body**

In `ios/FIE/Features/FixturesView.swift`, replace everything from `struct FixturesView: View {` through the closing brace of the old `load()` method (line 3 through line 197, immediately before `struct FixtureCard`) with:

```swift
struct FixturesView: View {
    private enum Mode: String, CaseIterable {
        case upcoming = "Upcoming"
        case gate = "85% gate"
    }

    @EnvironmentObject private var store: EngineStore
    @AppStorage("fie.fixturesMode") private var storedMode = Mode.upcoming.rawValue

    @State private var mode: Mode = .upcoming
    @State private var searchText = ""

    private var fixtures: [Fixture] { store.fixtures }
    private var gate: GateReport? { store.gate }
    private var loading: Bool { !store.isBootstrapped && store.fixtures.isEmpty }
    private var loadingGate: Bool { !store.isBootstrapped && store.gate == nil }
    private var errorMessage: String? { store.errors[.fixtures] }
    private var gateError: String? { store.errors[.gate] }

    private var filtered: [Fixture] {
        let needle = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return fixtures }
        return fixtures.filter { matches($0, needle: needle) }
    }

    private var filteredLive: [Fixture] {
        let needle = searchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return store.live }
        return store.live.filter { matches($0, needle: needle) }
    }

    private func matches(_ fixture: Fixture, needle: String) -> Bool {
        fixture.homeTeam.lowercased().contains(needle)
            || fixture.awayTeam.lowercased().contains(needle)
            || fixture.competition.lowercased().contains(needle)
            || fixture.country.lowercased().contains(needle)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Picker("View", selection: $mode) {
                    ForEach(Mode.allCases, id: \.self) { item in
                        Text(item.rawValue).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 16)
                .padding(.vertical, 8)

                ScrollView {
                    LazyVStack(spacing: 10) {
                        if store.livePaused {
                            NoteRow(
                                text: "Live scores paused; showing the last stored score.",
                                tone: .warn
                            )
                        }
                        if mode == .upcoming && !filteredLive.isEmpty {
                            LiveMatchesCard(fixtures: filteredLive, glow: true)
                        }
                        switch mode {
                        case .upcoming:
                            upcomingContent
                        case .gate:
                            gateContent
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 4)
                }
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Fixtures")
            .searchable(text: $searchText, prompt: "Team or competition")
            .task(id: mode) {
                storedMode = mode.rawValue
                if !store.isBootstrapped {
                    await store.bootstrap()
                } else {
                    await store.refresh(section: mode == .upcoming ? .fixtures : .gate)
                }
            }
            .onAppear {
                mode = Mode(rawValue: storedMode) ?? .upcoming
            }
            .refreshable { await store.refreshAll() }
        }
    }

    @ViewBuilder
    private var upcomingContent: some View {
        if let errorMessage {
            NoteRow(text: errorMessage, tone: .danger)
        }
        if loading && fixtures.isEmpty {
            LoadingCard(label: "Reading the upcoming slate…")
        }
        ForEach(filtered) { fixture in
            NavigationLink {
                FixtureDetailView(fixture: fixture)
            } label: {
                FixtureCard(fixture: fixture)
            }
            .buttonStyle(.plain)
        }
        if !loading && filtered.isEmpty && errorMessage == nil {
            Card {
                Text("No fixtures on the slate.")
                    .font(TypeScale.section)
                Text("Refresh the bookmaker feeds from the Sources tab, or clear the search filter.")
                    .font(TypeScale.body)
                    .foregroundStyle(Palette.textSecondary)
            }
        }
    }

    @ViewBuilder
    private var gateContent: some View {
        if let gateError {
            NoteRow(text: gateError, tone: .danger)
        }
        if loadingGate && gate == nil {
            LoadingCard(label: "Scanning today's fixtures for the 85% gate…")
        }
        if let gate {
            Card {
                HStack {
                    Eyebrow(text: "Published gate")
                    Spacer()
                    Pill(text: "\(Int((gate.threshold * 100).rounded()))%", tint: Palette.accent)
                }
                Text(gate.message)
                    .font(TypeScale.section)
                    .foregroundStyle(Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                LabeledRow(label: "Fixtures scanned", value: Fmt.count(gate.fixturesScanned))
                LabeledRow(label: "Markets clearing the gate", value: Fmt.count(gate.count))
                Text("Only markets at or above the published threshold are listed. The gate is never lowered to fill this screen; when nothing clears it, the screen says so.")
                    .font(TypeScale.caption)
                    .foregroundStyle(Palette.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if gate.qualifying.isEmpty {
                Card {
                    Text("NO QUALIFIED SELECTIONS FOUND.")
                        .font(.system(size: 15, weight: .bold, design: .monospaced))
                        .foregroundStyle(Palette.warn)
                    Text("Scan more fixtures, or wait for fresher prices. Nothing below \(Fmt.percent(gate.threshold, digits: 0)) is shown.")
                        .font(TypeScale.body)
                        .foregroundStyle(Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ForEach(gate.qualifying) { row in
                NavigationLink {
                    FixtureDetailView(fixtureId: row.fixtureId)
                } label: {
                    GateRow(row: row)
                }
                .buttonStyle(.plain)
            }
        }
    }
}
```

- [ ] **Step 3: Parse the two views**

Run:

```bash
xcrun swiftc -parse ios/FIE/Features/TodayView.swift ios/FIE/Features/FixturesView.swift ios/FIE/Design/Components.swift ios/FIE/Core/EngineStore.swift
```

Expected: no parse errors.

- [ ] **Step 4: Commit**

```bash
git add ios/FIE/Features/TodayView.swift ios/FIE/Features/FixturesView.swift
git commit -m "refactor(ios): read Today and Fixtures from the engine store"
```

---

### Task 9: Rewire Performance, Sources, History, and Settings

**Files:**
- Modify: `ios/FIE/Features/PerformanceView.swift:3-68`, `ios/FIE/Features/PerformanceView.swift:335-361`
- Modify: `ios/FIE/Features/SourcesView.swift:3-70`, `ios/FIE/Features/SourcesView.swift:162-214`
- Modify: `ios/FIE/Features/HistoryView.swift:3-72`, `ios/FIE/Features/HistoryView.swift:213-257`
- Modify: `ios/FIE/Features/SettingsView.swift:3-8`, `ios/FIE/Features/SettingsView.swift:59-63`

**Interfaces:**
- Consumes: `store.health`, `store.summary`, `store.clv`, `store.learning`, `store.sweep`, `store.sources`, `store.backtest`, `store.errors`, `store.refresh(section:)`, `store.refreshAll()`, `store.refreshFeeds()`, `store.runBacktest()`, `store.reset()`.
- Produces: no view owns network state except Sources' sync-log list and Settings' connection test.

- [ ] **Step 1: Rewire Performance**

In `ios/FIE/Features/PerformanceView.swift`, replace the block from `struct PerformanceView: View {` through the end of `var body` (line 3 through line 68) with:

```swift
struct PerformanceView: View {
    @EnvironmentObject private var store: EngineStore

    private var health: Health? { store.health }
    private var summary: PerformanceSummary? { store.summary }
    private var clv: ClvSummary? { store.clv }
    private var learning: LearningSummary? { store.learning }
    private var sweep: ComboSweep? { store.sweep }
    private var loading: Bool { !store.isBootstrapped }
    private var errorMessage: String? { store.errors[.summary] ?? store.errors[.health] }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if loading && health == nil && summary == nil {
                        LoadingCard(label: "Reading the ledger…")
                    }
                    if let health {
                        engineCard(health)
                    }
                    if let summary {
                        ledgerCard(summary)
                        slipTypeCard(summary)
                        Text(summary.note)
                            .font(TypeScale.caption)
                            .foregroundStyle(Palette.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let clv {
                        clvCard(clv)
                    }
                    if let learning {
                        learningCard(learning)
                    }
                    if let sweep, (sweep.rows ?? []).isEmpty == false {
                        sweepCard(sweep)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Performance")
            .task {
                if !store.isBootstrapped { await store.bootstrap() }
            }
            .refreshable { await store.refreshAll() }
        }
    }
```

Then delete the existing `client` computed property and the entire `load()` method from this file (the block that starts with `@MainActor private func load() async {` and ends with its closing brace). The `intro` and every card builder below it stay unchanged.

- [ ] **Step 2: Rewire Sources**

In `ios/FIE/Features/SourcesView.swift`, replace the block from `struct SourcesView: View {` through the end of `var body` (line 3 through line 70) with:

```swift
struct SourcesView: View {
    @EnvironmentObject private var store: EngineStore
    @AppStorage(AppSettings.serverURLKey) private var serverURL = AppSettings.defaultServerURL

    @State private var logs: [SyncLog] = []
    @State private var loadingLogs = false
    @State private var refreshing = false
    @State private var refreshError: String?
    @State private var refreshNote: String?

    private var sources: SourcesResponse? { store.sources }
    private var loading: Bool { !store.isBootstrapped && store.sources == nil }
    private var errorMessage: String? { store.errors[.sources] }
    private var busy: Bool { loadingLogs || refreshing || store.isRefreshing }

    private var client: APIClient {
        APIClient(baseURL: URL(string: serverURL) ?? APIClient.fromStoredSettings().baseURL)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    intro

                    Button {
                        Task { await refreshFeeds() }
                    } label: {
                        HStack(spacing: 8) {
                            if refreshing {
                                ProgressView()
                                    .tint(Palette.background)
                                    .controlSize(.small)
                            }
                            Text(refreshing ? "Refreshing bookmaker feeds…" : "Refresh bookmaker feeds")
                        }
                    }
                    .buttonStyle(PrimaryButtonStyle(busy: busy))
                    .disabled(busy)

                    if let refreshNote {
                        NoteRow(text: refreshNote)
                    }
                    if let refreshError {
                        NoteRow(text: refreshError, tone: .warn)
                    }
                    if let errorMessage {
                        NoteRow(text: errorMessage, tone: .danger)
                    }
                    if loading && sources == nil {
                        LoadingCard(label: "Reading provider readiness…")
                    }

                    if let sources {
                        providersCard(sources)
                    }
                    if !logs.isEmpty {
                        logsCard
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Sources")
            .task {
                if !store.isBootstrapped { await store.bootstrap() }
                await loadLogs()
            }
            .refreshable {
                await store.refreshAll()
                await loadLogs()
            }
        }
    }
```

Then replace the existing `load()` and `refreshFeeds()` methods with:

```swift
    @MainActor
    private func loadLogs() async {
        guard !loadingLogs else { return }
        loadingLogs = true
        defer { loadingLogs = false }
        do {
            logs = try await client.get(
                "/sources/sync-logs",
                query: [URLQueryItem(name: "limit", value: "12")],
                as: [SyncLog].self
            )
        } catch {
            logs = []
        }
    }

    @MainActor
    private func refreshFeeds() async {
        guard !refreshing else { return }
        refreshing = true
        refreshError = nil
        refreshNote = nil
        do {
            let result = try await store.refreshFeeds()
            applyRefreshSummary(result)
            await loadLogs()
        } catch {
            refreshError = error.localizedDescription
        }
        refreshing = false
    }
```

The `intro`, `providersCard`, `logsCard`, and `applyRefreshSummary` stay unchanged.

- [ ] **Step 3: Rewire History**

In `ios/FIE/Features/HistoryView.swift`, replace the block from `struct HistoryView: View {` through the end of `var body` (line 3 through line 72) with:

```swift
struct HistoryView: View {
    @EnvironmentObject private var store: EngineStore

    @State private var running = false
    @State private var errorMessage: String?
    @State private var statusMessage: String?

    private var report: ComboBacktestReport? { store.backtest }
    private var loading: Bool { !store.isBootstrapped && store.backtest == nil }
    private var busy: Bool { running || store.isRefreshing }
    private var hasLoaded: Bool { store.isBootstrapped }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                intro

                Button {
                    Task { await runReplay() }
                } label: {
                    HStack(spacing: 8) {
                        if running {
                            ProgressView()
                                .tint(Palette.background)
                                .controlSize(.small)
                        }
                        Text(running ? "Replaying settled history…" : "Run historical replay")
                    }
                }
                .buttonStyle(PrimaryButtonStyle(busy: busy))
                .disabled(busy)

                if let statusMessage {
                    NoteRow(text: statusMessage)
                }
                if let errorMessage {
                    NoteRow(text: errorMessage, tone: .danger)
                }
                if loading && report == nil {
                    LoadingCard(label: "Loading the saved replay…")
                }

                if let report {
                    reportBody(report)
                } else if !busy && errorMessage == nil && hasLoaded {
                    Card {
                        Text("No replay saved yet.")
                            .font(TypeScale.section)
                        Text("Run the replay on settled history to measure hit rate and ROI, then keep the calibration only if it beats its own holdout bar.")
                            .font(TypeScale.body)
                            .foregroundStyle(Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(Palette.background.ignoresSafeArea())
        .navigationTitle("Historical test")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            if !store.isBootstrapped { await store.bootstrap() }
        }
    }
```

Then delete the existing `client` computed property, `loadSaved()`, and `runReplay()`, and add:

```swift
    @MainActor
    private func runReplay() async {
        guard !running else { return }
        running = true
        errorMessage = nil
        statusMessage = nil
        do {
            let result = try await store.runBacktest()
            if result.status == "ok" {
                if result.learning?.applied == true {
                    statusMessage = "Replay finished. The calibration cleared its held-out bar and is now stored for live use."
                } else {
                    statusMessage = "Replay finished. The calibration did not clear its held-out bar, so raw de-vigged prices stay in use."
                }
            } else {
                statusMessage = result.message ?? "The replay returned status \(result.status)."
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        running = false
    }
```

- [ ] **Step 4: Reset the store when the server address changes**

In `ios/FIE/Features/SettingsView.swift`, add this property directly under the `serverURL` declaration:

```swift
    @EnvironmentObject private var store: EngineStore
```

Then, in `body`, add this modifier directly after `.refreshable { await loadDelivery() }`:

```swift
            .onChange(of: serverURL) { _, _ in
                store.reset()
                Task {
                    await store.bootstrap()
                    store.startAutoRefresh(section: .health)
                }
            }
```

- [ ] **Step 5: Parse all touched views**

Run:

```bash
xcrun swiftc -parse ios/FIE/Features/PerformanceView.swift ios/FIE/Features/SourcesView.swift ios/FIE/Features/HistoryView.swift ios/FIE/Features/SettingsView.swift ios/FIE/Core/EngineStore.swift
```

Expected: no parse errors.

- [ ] **Step 6: Commit**

```bash
git add ios/FIE/Features/PerformanceView.swift ios/FIE/Features/SourcesView.swift ios/FIE/Features/HistoryView.swift ios/FIE/Features/SettingsView.swift
git commit -m "refactor(ios): read remaining tabs from the engine store"
```

---

### Task 10: Verify, document, and push

**Files:**
- Modify: `README.md` (API summary and iOS section)

**Interfaces:**
- Consumes: everything from Tasks 1–9.

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass, including the new `test_live_league_code.py`, `test_inplay.py`, and `test_live_endpoint.py`.

- [ ] **Step 2: Compile the Python entry points**

Run: `.venv/bin/python -m compileall -q backend/app streamlit_app.py dashboard.py`

Expected: exit 0 with no output.

- [ ] **Step 3: Parse every Swift source and lint the project files**

Run:

```bash
xcrun swiftc -parse $(find ios/FIE -name '*.swift')
plutil -lint ios/FIE/Info.plist ios/FIE.xcodeproj/project.pbxproj
.venv/bin/python -m json.tool ios/FIE/Assets.xcassets/BrandLogo.imageset/Contents.json > /dev/null
```

Expected: no parse errors, `OK` twice, and no JSON error.

Also verify both new files are registered exactly four times each:

```bash
grep -c "EngineStore.swift" ios/FIE.xcodeproj/project.pbxproj
grep -c "SplashView.swift" ios/FIE.xcodeproj/project.pbxproj
```

Expected: `4` and `4`.

- [ ] **Step 4: Attempt a real simulator build**

Run: `xcodebuild -version`

If Xcode is installed, run:

```bash
xcodebuild -project ios/FIE.xcodeproj -scheme FIE -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build CODE_SIGNING_ALLOWED=NO
```

Expected: `BUILD SUCCEEDED`. If `xcodebuild` is unavailable on this Mac, record that the build could not be run locally and that parse checks passed.

- [ ] **Step 5: Smoke-test the API on a free port**

Start a throwaway server on 8011 (never touch the 8001 process):

```bash
.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8011
```

In a second shell, run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8011/health
curl -s http://127.0.0.1:8011/fixtures/live | .venv/bin/python -c "import json,sys; p=json.load(sys.stdin); print(sorted(p.keys())); print(len(p['fixtures'])); print(p['refreshed'], p['warning'])"
```

Expected: `200`; keys `['as_of', 'fixtures', 'refreshed', 'warning']`; a fixture count; `refreshed` is `True` or `False` with a real warning — never a fabricated score. Stop only the 8011 process when done.

- [ ] **Step 6: Update the README**

In `README.md`, immediately after the line `The API is mounted without a prefix; interactive docs are at /docs.`, add:

```markdown
Live scores: `GET /fixtures/live` refreshes and returns today's in-play fixtures
with stored scores and clock. The endpoint is throttled to one upstream ESPN
refresh per 60 seconds and never creates a fixture.
```

In the `## iOS app` section, add this bullet after the five-tabs bullet:

```markdown
- On launch the app shows the FIE splash while eleven sections preload
  concurrently, then refreshes live scores every 60 seconds while it is in the
  foreground. Fixtures pins a Live now card, Today shows a compact live strip,
  and the match detail header shows the score and clock only when the engine
  has them.
```

- [ ] **Step 7: Review and commit the documentation**

Run:

```bash
git status --short
git diff README.md
```

Stage only `README.md` and commit:

```bash
git add README.md
git commit -m "docs: describe live fixtures and the app preload"
```

- [ ] **Step 8: Push**

Run:

```bash
git status --short
git log --oneline origin/main..HEAD
git push origin main
```

Expected: only `data/combo_backtest.json`, `data/combo_learning.json`, and `data/combo_sweep.json` remain modified and unstaged; every Phase B commit is listed; the push succeeds.
