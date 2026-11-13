# Continuous Learning and In-Play Prediction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a conditional win probability for every in-play fixture, settle and score every snapshot, and refit ratings nightly behind a champion/challenger gate.

**Architecture:** Phase 1 adds a pure conditional-Poisson engine on top of the frozen pre-match prior, an append-only `live_predictions` ledger written by both the HTTP path and a 5-minute scheduler job, and read surfaces on web and iOS. Phase 2 adds a parameterised leak-free walk-forward, a configuration gate that compares half-lives on identical windows, committed model artifacts, and a nightly GitHub Actions workflow.

**Tech Stack:** Python 3.9 / FastAPI / SQLAlchemy / SQLite / NumPy / APScheduler, Next.js 14 (App Router) / TypeScript / Tailwind, SwiftUI, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-13-learning-and-live-prediction-design.md`

## Global Constraints

- The published **85% qualification gate is never lowered** and is **never applied to live forecasts**. Live numbers are not selections.
- **No paid APIs.** History comes from `football-data.co.uk`, scores from ESPN. Both keyless.
- **No fabricated data.** A missing score, clock, or goal expectation produces no probability. Never invent a fixture, price, score, injury, or lineup.
- **No automatic betting.** Every surface is paper-only.
- **Attribution preserved.** Every price keeps its bookmaker, source, and capture time.
- **Append-only history.** A settled `live_predictions` row is never rewritten. A corrected final score arrives through the normal results path.
- **Live probabilities never enter** `predictions`, `selections`, `combinations`, `tracked_bets`, the 85% gate, combos, slips, staking, or notifications.
- Exactly `NO QUALIFIED SELECTIONS FOUND.` when nothing qualifies.
- Never stage `data/combo_backtest.json` or `data/combo_sweep.json`.
- Never commit secrets. `sentinel_state.json` stays on disk and unreferenced. `RENDER_DEPLOY_HOOK` is a GitHub secret only.
- `data/historical_cache/` is gitignored and must not be committed.
- `FIE_API_KEY` never uses a `NEXT_PUBLIC_` prefix.
- Tests run against a throwaway DB via `tests/conftest.py`; every new test must clean up the rows it inserts.
- Local commands run from the repository root with `PYTHONPATH=backend`.

---

## File Structure

**Phase 1 — live probability**

| File | Responsibility |
| --- | --- |
| `backend/app/models/fixture.py` | Add `live_observed_at` |
| `backend/app/database.py` | Migrate `fixtures.live_observed_at` |
| `backend/app/data_sources/inplay.py` | Stamp observation time on every matched event |
| `backend/app/live/__init__.py` | Package marker |
| `backend/app/live/clock.py` | Elapsed-minute parsing and derivation |
| `backend/app/live/engine.py` | Conditional Poisson, intensity provider, artifact read |
| `backend/app/models/live.py` | `LivePrediction` table |
| `backend/app/models/__init__.py` | Register `LivePrediction` |
| `backend/app/live/ledger.py` | `record_live_snapshots`, `settle_live_predictions` |
| `backend/app/live/report.py` | Calibration aggregate and export rows |
| `backend/app/analytics/market_reference.py` | `before=` filter |
| `backend/app/analytics/probabilities.py` | `as_of=` pass-through |
| `backend/app/schemas.py` | `LiveFixtureOut`, live history shapes |
| `backend/app/main.py` | Routes, payload assembly, startup ratings load |
| `backend/app/tasks/scheduler.py` | `live_snapshot` job, settlement after results |
| `frontend/lib/types.ts` | Live types |
| `frontend/app/live/page.tsx` | Live board |
| `frontend/app/fixtures/[id]/page.tsx` | Fixture detail with live panel |
| `frontend/components/Nav.tsx` | Live link |
| `frontend/components/LiveCalibrationPanel.tsx` | Performance page panel |
| `frontend/app/performance/page.tsx` | Mount the panel |
| `frontend/lib/proxyAllowlist.ts` | Exact `/live/calibration` read |
| `ios/FIE/Core/Models.swift` | `LivePrediction`, live history |
| `ios/FIE/Features/LiveView.swift` | Live tab |
| `ios/FIE/Features/FixtureDetailView.swift` | Live panel + bucket history |
| `ios/FIE/FIEApp.swift` | Live tab |
| `ios/FIE/Core/EngineStore.swift` | Tab mapping |

**Phase 2 — nightly learning**

| File | Responsibility |
| --- | --- |
| `backend/app/analytics/calibration.py` | Half-life parameter, optional save path |
| `backend/app/analytics/ratings.py` | Half-life parameter on the refit |
| `backend/app/analytics/blending.py` | Half-life parameter, optional save path |
| `backend/app/analytics/learning.py` | Optional save path |
| `backend/app/analytics/model_gate.py` | Grid evaluation, champion/challenger decision, champion file |
| `backend/app/analytics/artifacts.py` | `ratings.json` writer and startup loader |
| `backend/app/live/fit.py` | Intensity fit and engine export fetch |
| `backend/app/data_sources/seeder.py` | `seasons_to_refresh` helper |
| `scripts/ingest_history.py` | `--refresh-seasons` |
| `scripts/train_nightly.py` | Nightly orchestration |
| `.github/workflows/nightly-learning.yml` | Nightly workflow |

---

## Phase 1 — Live win probability

### Task 1: Record when the live feed last saw a fixture

**Files:**
- Modify: `backend/app/models/fixture.py:47-49`
- Modify: `backend/app/database.py:24-32`
- Modify: `backend/app/data_sources/inplay.py:88-131`
- Test: `tests/test_live_observation.py` (create)
- Test: `tests/test_migrations.py` (extend)

**Interfaces:**
- Produces: `Fixture.live_observed_at: datetime | None`, stamped by `_apply` on every matched event.
- Produces: `fixtures.live_observed_at TIMESTAMP` on legacy databases.

- [ ] **Step 1: Write the failing observation test**

```python
"""A matched ESPN event stamps live_observed_at even when the score is unchanged."""
from datetime import datetime, timedelta, timezone

from app.data_sources.inplay import _apply
from app.models.fixture import Fixture


def _event(state="in", clock="67:34", home="1", away="0", name="STATUS_IN_PROGRESS"):
    return {
        "id": "9001",
        "competitions": [
            {
                "status": {"displayClock": clock, "type": {"state": state, "name": name}},
                "competitors": [
                    {"homeAway": "home", "score": home, "team": {"displayName": "Alpha FC"}},
                    {"homeAway": "away", "score": away, "team": {"displayName": "Beta FC"}},
                ],
            }
        ],
    }


def test_apply_stamps_observation_when_nothing_else_changes():
    fixture = Fixture(
        dedupe_key="observation-fixture",
        kickoff_utc=datetime.now(timezone.utc) - timedelta(minutes=70),
        home_team="Alpha FC",
        away_team="Beta FC",
        competition="Observation League",
        country="Test",
        source="Test",
        status="live",
        live_clock="67:34",
        home_score=1,
        away_score=0,
    )

    changed = _apply(fixture, _event())

    assert changed is True
    assert fixture.live_observed_at is not None
    assert fixture.live_clock == "67:34"
    assert fixture.home_score == 1
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_observation.py -v`

Expected: FAIL — `Fixture` has no `live_observed_at` attribute.

- [ ] **Step 3: Add the column and the migration entry**

In `backend/app/models/fixture.py`, after `live_clock`:

```python
    live_clock: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # When the feed last matched this fixture, even if the score did not move.
    live_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `backend/app/database.py`, add to `new_cols`:

```python
                    ("live_clock", "VARCHAR(20)"),
                    ("live_observed_at", "TIMESTAMP"),
                    ("espn_league_code", "VARCHAR(30)"),
```

- [ ] **Step 4: Stamp the observation time in `_apply`**

Replace the `changed = False` block in `backend/app/data_sources/inplay.py`:

```python
    # Hearing from the feed is itself an observation. A half-time match whose
    # clock is frozen at "HT" is still fresh, so this cannot be inferred from
    # the clock or the score.
    fixture.live_observed_at = datetime.now(timezone.utc)
    changed = True
```

- [ ] **Step 5: Extend the migration test**

Append to `tests/test_migrations.py`:

```python
def test_legacy_fixtures_gain_live_observed_at(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-fixtures.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE fixtures (id INTEGER PRIMARY KEY, dedupe_key VARCHAR(300), "
            "kickoff_utc TIMESTAMP, home_team VARCHAR(160), away_team VARCHAR(160), "
            "competition VARCHAR(160), country VARCHAR(100), source VARCHAR(80), "
            "status VARCHAR(40), created_at TIMESTAMP)"
        ))

    ensure_schema_compatibility(engine)

    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(fixtures)"))}

    assert "live_observed_at" in columns
```

- [ ] **Step 6: Run both test files**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_observation.py tests/test_migrations.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/fixture.py backend/app/database.py backend/app/data_sources/inplay.py tests/test_live_observation.py tests/test_migrations.py
git commit -m "feat(live): record when the feed last observed a fixture"
```

---

### Task 2: Parse and derive the elapsed minute

**Files:**
- Create: `backend/app/live/__init__.py`
- Create: `backend/app/live/clock.py`
- Test: `tests/test_live_clock.py` (create)

**Interfaces:**
- Produces: `elapsed_minutes(clock: str | None, kickoff, status: str, now) -> int | None`
- Produces: `REGULATION_MINUTES = 90`, `HALF_TIME_MINUTES = 45`, `HALF_TIME_BREAK = 15`

- [ ] **Step 1: Write the failing tests**

```python
"""Elapsed-minute parsing never guesses a clock it cannot read."""
from datetime import datetime, timedelta, timezone

import pytest

from app.live.clock import elapsed_minutes

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)


def _kickoff(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


@pytest.mark.parametrize(
    ("clock", "status", "expected"),
    [
        ("67:34", "live", 67),
        ("45+2", "live", 45),
        ("90+4", "live", 90),
        ("HT", "live", 45),
        (None, "halftime", 45),
        ("12'", "live", 12),
    ],
)
def test_clock_strings(clock, status, expected):
    assert elapsed_minutes(clock, _kickoff(70), status, NOW) == expected


def test_missing_clock_derives_from_kickoff_and_removes_the_break():
    assert elapsed_minutes(None, _kickoff(30), "live", NOW) == 30
    assert elapsed_minutes(None, _kickoff(70), "live", NOW) == 55


def test_derived_minute_is_clamped():
    assert elapsed_minutes(None, _kickoff(-5), "live", NOW) == 1
    assert elapsed_minutes(None, _kickoff(600), "live", NOW) == 90


def test_non_live_status_and_underivable_inputs_return_none():
    assert elapsed_minutes("67:34", _kickoff(70), "finished", NOW) is None
    assert elapsed_minutes("67:34", _kickoff(70), "scheduled", NOW) is None
    assert elapsed_minutes("garbage", None, "live", NOW) is None
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_clock.py -v`

Expected: FAIL — `ModuleNotFoundError: app.live`.

- [ ] **Step 3: Implement the module**

`backend/app/live/__init__.py`:

```python
"""In-play forecasting: clock parsing, conditional model, ledger and reports."""
```

`backend/app/live/clock.py`:

```python
"""Turn a feed clock into an elapsed regulation minute.

ESPN supplies a display clock only while the ball is in play. When it is
missing, the wall clock since kickoff is the only evidence available, and the
15-minute interval is removed once the raw elapsed time passes 45 minutes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

REGULATION_MINUTES = 90
HALF_TIME_MINUTES = 45
HALF_TIME_BREAK = 15


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _from_clock(clock: str) -> Optional[int]:
    text = clock.strip()
    if not text:
        return None
    if text.upper() in {"HT", "HALF TIME"}:
        return HALF_TIME_MINUTES
    head = text.split("+", 1)[0].strip().rstrip("'").strip()
    try:
        minute = int(head)
    except ValueError:
        return None
    return max(1, min(REGULATION_MINUTES, minute))


def elapsed_minutes(
    clock: Optional[str],
    kickoff: Optional[datetime],
    status: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Elapsed regulation minute, or ``None`` when it cannot be established."""
    status = (status or "").strip().lower()
    if status not in {"live", "halftime"}:
        return None
    if status == "halftime":
        return HALF_TIME_MINUTES
    if clock:
        parsed = _from_clock(clock)
        if parsed is not None:
            return parsed
    if kickoff is None or now is None:
        return None
    raw = int((_aware(now) - _aware(kickoff)).total_seconds() // 60)
    if raw > HALF_TIME_MINUTES:
        raw -= HALF_TIME_BREAK
    return max(1, min(REGULATION_MINUTES, raw))
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_clock.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/live/__init__.py backend/app/live/clock.py tests/test_live_clock.py
git commit -m "feat(live): parse and derive the elapsed minute"
```

---

### Task 3: Freeze the pre-match prior at kickoff

**Files:**
- Modify: `backend/app/analytics/market_reference.py:90-162`
- Modify: `backend/app/analytics/probabilities.py:505-527`
- Test: `tests/test_live_prior.py` (create)

**Interfaces:**
- Produces: `resolve(db, fixture, before: datetime | None = None)`.
- Produces: `analyze_fixture(..., as_of: datetime | None = None)`.
- Consumes: `MarketQuote.captured_at`, `Fixture.odds_updated_at`.

- [ ] **Step 1: Write the failing test**

```python
"""The live prior may only use prices provably captured before kickoff."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.analytics.market_reference import resolve
from app.analytics.probabilities import analyze_fixture
from app.database import SessionLocal
from app.models.market_quote import MarketQuote

KICKOFF = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)


def _fixture(fixture_id=990001):
    return SimpleNamespace(
        id=fixture_id,
        kickoff_utc=KICKOFF,
        home_team="Prior Home",
        away_team="Prior Away",
        competition="Prior League",
        country="Test",
        source="Test",
        home_odds=1.90,
        draw_odds=3.40,
        away_odds=4.20,
        odds_updated_at=KICKOFF - timedelta(hours=2),
    )


def _add_quote(db, fixture_id, bookmaker, home, draw, away, captured):
    for key, price in (("home_win", home), ("draw", draw), ("away_win", away)):
        db.add(
            MarketQuote(
                fixture_id=fixture_id,
                bookmaker=bookmaker,
                market_key=key,
                price=price,
                source="Test",
                captured_at=captured,
            )
        )


def _cleanup(db):
    db.query(MarketQuote).filter(MarketQuote.fixture_id == 990001).delete(synchronize_session=False)
    db.commit()


def test_before_ignores_a_quote_captured_after_kickoff():
    db = SessionLocal()
    try:
        _cleanup(db)
        _add_quote(db, 990001, "EarlyBook", 1.80, 3.60, 4.50, KICKOFF - timedelta(hours=3))
        _add_quote(db, 990001, "LateBook", 1.50, 4.00, 6.00, KICKOFF + timedelta(minutes=30))
        db.commit()
        fixture = _fixture()

        live_prior = resolve(db, fixture, before=KICKOFF)
        unrestrained = resolve(db, fixture)

        assert live_prior is not None
        assert live_prior.bookmaker == "EarlyBook"
        assert unrestrained is not None
        assert unrestrained.bookmaker == "LateBook"
    finally:
        _cleanup(db)
        db.close()


def test_fixture_fallback_needs_a_provable_capture_time():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.commit()
        fixture = _fixture()
        fixture.odds_updated_at = KICKOFF + timedelta(minutes=5)

        assert resolve(db, fixture, before=KICKOFF) is None
        assert resolve(db, fixture) is not None
    finally:
        _cleanup(db)
        db.close()


def test_analysis_passes_as_of_to_the_prior():
    db = SessionLocal()
    try:
        _cleanup(db)
        _add_quote(db, 990001, "EarlyBook", 1.80, 3.60, 4.50, KICKOFF - timedelta(hours=3))
        _add_quote(db, 990001, "LateBook", 1.50, 4.00, 6.00, KICKOFF + timedelta(minutes=30))
        db.commit()

        frozen = analyze_fixture(_fixture(), db=db, as_of=KICKOFF)
        current = analyze_fixture(_fixture(), db=db)

        assert frozen["market_reference"]["bookmaker"] == "EarlyBook"
        assert current["market_reference"]["bookmaker"] == "LateBook"
    finally:
        _cleanup(db)
        db.close()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_prior.py -v`

Expected: FAIL — `resolve() got an unexpected keyword argument 'before'`.

- [ ] **Step 3: Implement the `before` filter**

In `backend/app/analytics/market_reference.py`, change the signature and add the row filter:

```python
def resolve(
    db: Any,
    fixture: Any,
    before: Optional[datetime] = None,
) -> Optional[MarketReference]:
```

After `rows = db.scalars(...).all()`, add:

```python
        cutoff = _aware(before) if before is not None else None
```

Inside `for row in rows:`, before the price checks:

```python
            # An unknown capture time cannot be proven pre-kickoff, so a live
            # prior refuses it instead of assuming the best.
            if cutoff is not None:
                if row.captured_at is None or _aware(row.captured_at) >= cutoff:
                    continue
```

At the fixture fallback, replace the final `return _from_prices(...)` block with:

```python
    captured_at = getattr(fixture, "odds_updated_at", None)
    if before is not None and (
        captured_at is None or _aware(captured_at) >= _aware(before)
    ):
        return None
    return _from_prices(
        {key: float(value) for key, value in prices.items()},
        bookmaker=None,
        source=getattr(fixture, "source", None),
        captured_at=captured_at,
        origin="fixture",
    )
```

- [ ] **Step 4: Thread `as_of` through `analyze_fixture`**

In `backend/app/analytics/probabilities.py`:

```python
def analyze_fixture(
    fixture: Any,
    home_lambda: Optional[float] = None,
    away_lambda: Optional[float] = None,
    qualification_threshold: float = 0.85,
    rho: float = -0.10,
    db: Optional[Any] = None,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    as_of: Optional[datetime] = None,
) -> Dict[str, Any]:
```

Add `from datetime import datetime` to the imports at the top, and change the resolver call:

```python
        market_reference = resolve_market_reference(db, fixture, before=as_of)
```

- [ ] **Step 5: Run the tests and the existing market-reference suite**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_prior.py tests/test_market_reference.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/analytics/market_reference.py backend/app/analytics/probabilities.py tests/test_live_prior.py
git commit -m "feat(live): freeze the pre-match prior at kickoff"
```

---

### Task 4: Conditional Poisson engine and intensity seam

**Files:**
- Create: `backend/app/live/engine.py`
- Test: `tests/test_live_engine.py` (create)

**Interfaces:**
- Produces: `LiveState(home_goals, away_goals, home_lambda, away_lambda, minute)`.
- Produces: `outcome_probabilities(home_goals, away_goals, home_lambda, away_lambda, remaining_fraction, intensity=1.0) -> dict[str, float]`.
- Produces: `default_intensity(state) -> tuple[float, str]`.
- Produces: `fitted_intensity(state) -> tuple[float, str]` reading `data/live_calibration.json`.
- Produces: `load_intensity() -> dict` with `k` and `source`.
- Produces: `REGULATION_MINUTES`, `MAX_REMAINING_GOALS`, `INTENSITY_BOUNDS`.
- Produces: `IntensityProvider` type alias.

- [ ] **Step 1: Write the failing tests**

```python
"""The conditional model only knows the score and the time left."""
import json

import pytest

from app.live import engine
from app.live.engine import (
    INTENSITY_BOUNDS,
    LiveState,
    default_intensity,
    fitted_intensity,
    outcome_probabilities,
)


def test_probabilities_sum_to_one_and_favour_the_leader():
    probs = outcome_probabilities(1, 0, 1.5, 1.0, remaining_fraction=0.5)

    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert probs["home_win"] > probs["draw"] > probs["away_win"]


def test_a_finished_match_is_certain():
    probs = outcome_probabilities(2, 1, 1.4, 1.1, remaining_fraction=0.0)

    assert probs == {"home_win": 1.0, "draw": 0.0, "away_win": 0.0}


def test_conditioning_beats_the_static_pre_match_view():
    """At 2-1 with 89 minutes gone, the leader is far more likely than at 0-0."""
    late = outcome_probabilities(2, 1, 1.4, 1.1, remaining_fraction=1 / 90)
    early = outcome_probabilities(0, 0, 1.4, 1.1, remaining_fraction=1.0)

    assert late["home_win"] > early["home_win"] + 0.30


def test_intensity_scales_the_remaining_goals_and_is_clamped():
    base = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=1.0)
    excited = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=2.0)
    clamped = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=9.0)

    assert excited["home_win"] > base["home_win"]
    assert clamped == excited
    assert INTENSITY_BOUNDS == (0.5, 2.0)


def test_default_provider_is_neutral():
    state = LiveState(0, 0, 1.4, 1.1, 30)
    assert default_intensity(state) == (1.0, "default")
    assert fitted_intensity(state) == (1.0, "default")


def test_fitted_provider_reads_and_clamps_the_artifact(tmp_path, monkeypatch):
    artifact = tmp_path / "live_calibration.json"
    artifact.write_text(json.dumps({"status": "fitted", "k": 1.18}))
    monkeypatch.setattr(engine, "CALIBRATION_PATH", artifact)

    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.18, "fitted")

    artifact.write_text(json.dumps({"status": "fitted", "k": 5.0}))
    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) != (5.0, "fitted")


def test_rejected_artifact_falls_back_to_neutral(tmp_path, monkeypatch):
    artifact = tmp_path / "live_calibration.json"
    artifact.write_text(json.dumps({"status": "rejected", "k": 1.2}))
    monkeypatch.setattr(engine, "CALIBRATION_PATH", artifact)

    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.0, "default")
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_engine.py -v`

Expected: FAIL — `ModuleNotFoundError: app.live.engine`.

- [ ] **Step 3: Implement the engine**

`backend/app/live/engine.py`:

```python
"""Conditional win probabilities for a match already in play.

The model takes the pre-match goal expectation as fixed evidence and asks only
what the remaining goals can do to the current score. It deliberately knows
nothing about red cards, lineups or shots: those are not in the feed, and
inventing them would be worse than conditioning on less.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Tuple

REGULATION_MINUTES = 90
MAX_REMAINING_GOALS = 10
INTENSITY_BOUNDS = (0.5, 2.0)

CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "data" / "live_calibration.json"


@dataclass(frozen=True)
class LiveState:
    home_goals: int
    away_goals: int
    home_lambda: float
    away_lambda: float
    minute: int


IntensityProvider = Callable[[LiveState], Tuple[float, str]]


def _pmf(goals: int, mean: float) -> float:
    if mean <= 0.0:
        return 1.0 if goals == 0 else 0.0
    return math.exp(-mean) * (mean ** goals) / math.factorial(goals)


def outcome_probabilities(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_lambda: float,
    remaining_fraction: float,
    intensity: float = 1.0,
) -> Dict[str, float]:
    """Home/draw/away probabilities after conditioning on the current score."""
    fraction = max(0.0, min(1.0, float(remaining_fraction)))
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], float(intensity)))

    home_mean = max(0.0, float(home_lambda) * fraction * k)
    away_mean = max(0.0, float(away_lambda) * fraction * k)

    home_pmf = [_pmf(extra, home_mean) for extra in range(MAX_REMAINING_GOALS + 1)]
    away_pmf = [_pmf(extra, away_mean) for extra in range(MAX_REMAINING_GOALS + 1)]

    home_win = draw = away_win = total = 0.0
    for extra_home, p_home in enumerate(home_pmf):
        for extra_away, p_away in enumerate(away_pmf):
            probability = p_home * p_away
            total += probability
            final_home = home_goals + extra_home
            final_away = away_goals + extra_away
            if final_home > final_away:
                home_win += probability
            elif final_home == final_away:
                draw += probability
            else:
                away_win += probability

    if total <= 0.0:
        return {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    return {
        "home_win": round(home_win / total, 4),
        "draw": round(draw / total, 4),
        "away_win": round(away_win / total, 4),
    }


def default_intensity(state: LiveState) -> Tuple[float, str]:
    return 1.0, "default"


def load_intensity() -> Dict[str, object]:
    """Read the fitted multiplier, or report the neutral default."""
    try:
        payload = json.loads(CALIBRATION_PATH.read_text())
    except (OSError, ValueError):
        return {"k": 1.0, "source": "default"}
    if payload.get("status") != "fitted":
        return {"k": 1.0, "source": "default"}
    try:
        k = float(payload["k"])
    except (KeyError, TypeError, ValueError):
        return {"k": 1.0, "source": "default"}
    if not INTENSITY_BOUNDS[0] <= k <= INTENSITY_BOUNDS[1]:
        return {"k": 1.0, "source": "default"}
    return {"k": k, "source": "fitted"}


def fitted_intensity(state: LiveState) -> Tuple[float, str]:
    artifact = load_intensity()
    return float(artifact["k"]), str(artifact["source"])
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_engine.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/live/engine.py tests/test_live_engine.py
git commit -m "feat(live): add the conditional Poisson engine and intensity seam"
```

---

### Task 5: Append-only snapshot ledger and settlement

**Files:**
- Create: `backend/app/models/live.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/live/ledger.py`
- Test: `tests/test_live_ledger.py` (create)

**Interfaces:**
- Produces: `LivePrediction` mapped to `live_predictions`, unique `(fixture_id, minute_bucket)`.
- Produces: `record_live_snapshots(db, now=None, intensity_provider=None) -> dict[str, int]`.
- Produces: `settle_live_predictions(db, now=None) -> dict[str, int]`.
- Consumes: `elapsed_minutes`, `analyze_fixture`, `outcome_probabilities`.

- [ ] **Step 1: Write the failing tests**

```python
"""Snapshots are written once per bucket, suppress honestly, and settle once."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.live import ledger
from app.models.fixture import Fixture
from app.models.live import LivePrediction

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
PREFIX = "live-ledger-"


def _fixture(**overrides) -> Fixture:
    values = dict(
        dedupe_key=f"{PREFIX}{datetime.now(timezone.utc).timestamp()}",
        kickoff_utc=NOW - timedelta(minutes=70),
        home_team="Ledger Home",
        away_team="Ledger Away",
        competition="Ledger League",
        country="Test",
        source="Test",
        status="live",
        live_clock="67:34",
        home_score=1,
        away_score=0,
        live_observed_at=NOW,
    )
    values.update(overrides)
    return Fixture(**values)


def _cleanup(db):
    ids = [
        row[0]
        for row in db.query(Fixture.id).filter(Fixture.dedupe_key.like(f"{PREFIX}%")).all()
    ]
    if ids:
        db.query(LivePrediction).filter(LivePrediction.fixture_id.in_(ids)).delete(
            synchronize_session=False
        )
        db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def _fake_analysis(fixture, db=None, as_of=None):
    return {
        "estimated_lambdas": {"home_lambda": 1.5, "away_lambda": 1.0},
        "lambda_source": "market_odds",
        "all_markets": {"home_win": 0.52, "draw": 0.26, "away_win": 0.22},
    }


@pytest.fixture(autouse=True)
def _clean_ledger():
    db = SessionLocal()
    try:
        _cleanup(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        _cleanup(db)
    finally:
        db.close()


def test_one_snapshot_per_bucket(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    db = SessionLocal()
    try:
        fixture = _fixture()
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        first = ledger.record_live_snapshots(
            db, now=NOW, intensity_provider=lambda state: (1.0, "default")
        )
        second = ledger.record_live_snapshots(
            db, now=NOW + timedelta(minutes=1), intensity_provider=lambda state: (1.0, "default")
        )

        rows = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).all()

        assert first["written"] == 1
        assert second["written"] == 0
        assert second["skipped"] == 1
        assert len(rows) == 1
        row = rows[0]
        assert row.minute == 67
        assert row.minute_bucket == 65
        assert row.status == "ok"
        assert abs(row.prob_home + row.prob_draw + row.prob_away - 1.0) < 1e-3
        assert row.intensity_source == "default"
        assert row.prematch_home == 0.52
    finally:
        db.close()


def test_missing_score_suppresses_probabilities(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    db = SessionLocal()
    try:
        fixture = _fixture(home_score=None, away_score=None)
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        ledger.record_live_snapshots(db, now=NOW)

        row = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).one()
        assert row.status == "no_score"
        assert row.prob_home is None
        assert row.minute_bucket == 65
    finally:
        db.close()


def test_missing_clock_uses_the_negative_bucket(monkeypatch):
    monkeypatch.setattr(ledger, "analyze_fixture", _fake_analysis)
    monkeypatch.setattr(ledger, "elapsed_minutes", lambda *args, **kwargs: None)
    db = SessionLocal()
    try:
        fixture = _fixture()
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        ledger.record_live_snapshots(db, now=NOW)

        row = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).one()
        assert row.status == "no_clock"
        assert row.minute == 0
        assert row.minute_bucket == -1
        assert row.prob_home is None
    finally:
        db.close()


def test_settlement_scores_each_open_snapshot_once():
    db = SessionLocal()
    try:
        fixture = _fixture(status="finished", home_score=2, away_score=1)
        db.add(fixture)
        db.commit()
        db.refresh(fixture)

        for bucket, probs in ((0, (0.80, 0.15, 0.05)), (5, (0.55, 0.30, 0.15))):
            db.add(
                LivePrediction(
                    fixture_id=fixture.id,
                    minute=bucket,
                    minute_bucket=bucket,
                    observed_at=NOW - timedelta(minutes=60 - bucket),
                    home_score=1,
                    away_score=0,
                    prob_home=probs[0],
                    prob_draw=probs[1],
                    prob_away=probs[2],
                    status="ok",
                    model_version="live-dc-1",
                )
            )
        db.commit()

        first = ledger.settle_live_predictions(db, now=NOW)
        second = ledger.settle_live_predictions(db, now=NOW)

        rows = db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).all()
        assert first["settled"] == 2
        assert second["settled"] == 0
        for row in rows:
            assert row.result == "home_win"
            assert row.extra_home_goals == 1
            assert row.extra_away_goals == 1
            assert row.top_pick_correct is True
            assert 0.0 <= row.brier <= 2.0
            assert row.log_loss > 0.0
            assert row.settled_at is not None
    finally:
        db.close()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_ledger.py -v`

Expected: FAIL — `ModuleNotFoundError: app.models.live`.

- [ ] **Step 3: Create the model**

`backend/app/models/live.py`:

```python
"""One forecast per fixture per five-minute bucket, never rewritten.

The row freezes the pre-match prior, the score it was conditioned on, and the
numbers the model published. Settlement only fills the result columns; the
forecast itself is immutable evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LivePrediction(Base):
    __tablename__ = "live_predictions"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "minute_bucket", name="uq_live_prediction_bucket"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    minute: Mapped[int] = mapped_column(Integer)
    minute_bucket: Mapped[int] = mapped_column(Integer, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lambda_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lambda_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lambda_source: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    remaining_home_lambda: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    remaining_away_lambda: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    intensity_source: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    prob_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_draw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prob_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_home: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_draw: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prematch_away: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok", index=True)
    model_version: Mapped[str] = mapped_column(String(40), default="live-dc-1")
    result: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    extra_home_goals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    extra_away_goals: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brier: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    log_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    top_pick_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    settled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

Register it in `backend/app/models/__init__.py`:

```python
from app.models.live import LivePrediction
```

and add `"LivePrediction",` to `__all__` (alphabetically after `"Lineup"`).

- [ ] **Step 4: Implement the ledger**

`backend/app/live/ledger.py`:

```python
"""Write and settle the append-only live forecast ledger."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analytics.probabilities import analyze_fixture
from app.live.clock import elapsed_minutes
from app.live.engine import (
    INTENSITY_BOUNDS,
    LiveState,
    REGULATION_MINUTES,
    fitted_intensity,
    outcome_probabilities,
)
from app.models.fixture import Fixture
from app.models.live import LivePrediction

LIVE_WINDOW_HOURS = 3
BUCKET_MINUTES = 5
MODEL_VERSION = "live-dc-1"
OUTCOMES = ("home_win", "draw", "away_win")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _number(value) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _populate(
    db: Session,
    row: LivePrediction,
    fixture: Fixture,
    minute: Optional[int],
    provider: Callable[[LiveState], tuple],
) -> str:
    if fixture.home_score is None or fixture.away_score is None:
        return "no_score"
    if minute is None:
        return "no_clock"

    analysis = analyze_fixture(fixture, db=db, as_of=fixture.kickoff_utc)
    lambdas = analysis.get("estimated_lambdas") or {}
    home_lambda = _number(lambdas.get("home_lambda"))
    away_lambda = _number(lambdas.get("away_lambda"))
    source = analysis.get("lambda_source")
    row.lambda_source = source

    if (
        source in (None, "fallback")
        or home_lambda is None
        or away_lambda is None
        or home_lambda <= 0.0
        or away_lambda <= 0.0
    ):
        return "no_lambda"

    row.lambda_home = home_lambda
    row.lambda_away = away_lambda

    state = LiveState(
        home_goals=int(fixture.home_score),
        away_goals=int(fixture.away_score),
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        minute=int(minute),
    )
    k, intensity_source = provider(state)
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], float(k)))

    fraction = max(0.0, (REGULATION_MINUTES - int(minute)) / REGULATION_MINUTES)
    row.remaining_home_lambda = home_lambda * fraction * k
    row.remaining_away_lambda = away_lambda * fraction * k
    row.intensity = k
    row.intensity_source = intensity_source

    probabilities = outcome_probabilities(
        home_goals=state.home_goals,
        away_goals=state.away_goals,
        home_lambda=home_lambda,
        away_lambda=away_lambda,
        remaining_fraction=fraction,
        intensity=k,
    )
    row.prob_home = probabilities["home_win"]
    row.prob_draw = probabilities["draw"]
    row.prob_away = probabilities["away_win"]

    markets = analysis.get("all_markets") or {}
    row.prematch_home = _number(markets.get("home_win"))
    row.prematch_draw = _number(markets.get("draw"))
    row.prematch_away = _number(markets.get("away_win"))
    return "ok"


def record_live_snapshots(
    db: Session,
    now: Optional[datetime] = None,
    intensity_provider: Optional[Callable[[LiveState], tuple]] = None,
) -> Dict[str, int]:
    """Write at most one forecast per fixture per five-minute bucket."""
    now = now or datetime.now(timezone.utc)
    provider = intensity_provider or fitted_intensity

    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(("live", "halftime")))
        .where(Fixture.kickoff_utc >= now - timedelta(hours=LIVE_WINDOW_HOURS))
        .order_by(Fixture.kickoff_utc)
    ).all()

    written = skipped = 0
    for fixture in fixtures:
        minute = elapsed_minutes(
            fixture.live_clock, fixture.kickoff_utc, fixture.status, now
        )
        bucket = -1 if minute is None else (minute // BUCKET_MINUTES) * BUCKET_MINUTES
        exists = db.scalar(
            select(LivePrediction.id).where(
                LivePrediction.fixture_id == fixture.id,
                LivePrediction.minute_bucket == bucket,
            )
        )
        if exists is not None:
            skipped += 1
            continue

        row = LivePrediction(
            fixture_id=fixture.id,
            minute=minute if minute is not None else 0,
            minute_bucket=bucket,
            observed_at=fixture.live_observed_at or now,
            home_score=fixture.home_score,
            away_score=fixture.away_score,
            status="ok",
            model_version=MODEL_VERSION,
        )
        row.status = _populate(db, row, fixture, minute, provider)
        try:
            with db.begin_nested():
                db.add(row)
        except IntegrityError:  # scheduler and HTTP racing on the same bucket
            skipped += 1
            continue
        written += 1

    db.commit()
    return {"fixtures": len(fixtures), "written": written, "skipped": skipped}


def _label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home_win"
    if home_goals == away_goals:
        return "draw"
    return "away_win"


def settle_live_predictions(
    db: Session, now: Optional[datetime] = None
) -> Dict[str, int]:
    """Score open forecasts against the final result exactly once."""
    now = now or datetime.now(timezone.utc)

    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.status == "finished")
        .where(Fixture.home_score.isnot(None))
        .where(Fixture.away_score.isnot(None))
    ).all()

    settled = 0
    for fixture in fixtures:
        open_rows = db.scalars(
            select(LivePrediction).where(
                LivePrediction.fixture_id == fixture.id,
                LivePrediction.result.is_(None),
                LivePrediction.prob_home.isnot(None),
            )
        ).all()
        if not open_rows:
            continue

        home_goals = int(fixture.home_score)
        away_goals = int(fixture.away_score)
        label = _label(home_goals, away_goals)
        index = OUTCOMES.index(label)

        for row in open_rows:
            probabilities = (row.prob_home, row.prob_draw, row.prob_away)
            row.result = label
            row.extra_home_goals = home_goals - int(row.home_score or 0)
            row.extra_away_goals = away_goals - int(row.away_score or 0)
            row.brier = round(
                sum(
                    (probability - (1.0 if position == index else 0.0)) ** 2
                    for position, probability in enumerate(probabilities)
                ),
                6,
            )
            row.log_loss = round(-math.log(max(1e-9, probabilities[index])), 6)
            row.top_pick_correct = max(range(3), key=lambda i: probabilities[i]) == index
            row.settled_at = now
            settled += 1

    db.commit()
    return {"settled": settled}
```

- [ ] **Step 5: Run the ledger tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_ledger.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/live.py backend/app/models/__init__.py backend/app/live/ledger.py tests/test_live_ledger.py
git commit -m "feat(live): persist and settle an append-only snapshot ledger"
```

---

### Task 6: Serve the forecast, add the scheduler job, settle after results

**Files:**
- Modify: `backend/app/schemas.py:38-42`
- Modify: `backend/app/main.py:261-282`
- Modify: `backend/app/tasks/scheduler.py:19-30`, `:267-277`, `:336-369`
- Test: `tests/test_live_endpoint.py` (extend)
- Test: `tests/test_live_scheduler.py` (create)

**Interfaces:**
- Produces: `GET /fixtures/live` returns `live_prediction` on every fixture.
- Produces: `GET /fixtures/{fixture_id}/live` returns `{fixture_id, latest, history}`.
- Produces: `live_snapshot()` scheduler job at `*/5`.
- Consumes: `record_live_snapshots`, `settle_live_predictions`.

- [ ] **Step 1: Write the failing endpoint tests**

Append to `tests/test_live_endpoint.py`, replacing the cleanup fixture first:

```python
@pytest.fixture(autouse=True)
def _cleanup_fixtures():
    yield
    db = SessionLocal()
    try:
        ids = [
            row[0]
            for row in db.query(Fixture.id)
            .filter(Fixture.dedupe_key.like("live-endpoint-%"))
            .all()
        ]
        if ids:
            from app.models.live import LivePrediction

            db.query(LivePrediction).filter(
                LivePrediction.fixture_id.in_(ids)
            ).delete(synchronize_session=False)
            db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
```

Then add:

```python
def test_live_endpoint_includes_the_prediction_block(monkeypatch):
    from app.live import ledger

    fixture_id = _insert_live_fixture()
    monkeypatch.setattr(
        main_module,
        "refresh_live_scores",
        lambda db, force=False: {"refreshed": True, "updated": 0, "skipped": 0, "warning": None},
    )
    monkeypatch.setattr(
        ledger,
        "analyze_fixture",
        lambda fixture, db=None, as_of=None: {
            "estimated_lambdas": {"home_lambda": 1.6, "away_lambda": 0.9},
            "lambda_source": "market_odds",
            "all_markets": {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
        },
    )

    response = client.get("/fixtures/live")

    assert response.status_code == 200
    row = next(
        item for item in response.json()["fixtures"] if item["id"] == fixture_id
    )
    block = row["live_prediction"]
    assert block["status"] == "ok"
    assert block["minute"] == 67
    assert block["freshness"] == "fresh"
    assert set(block["probabilities"]) == {"home_win", "draw", "away_win"}
    assert abs(sum(block["probabilities"].values()) - 1.0) < 1e-3
    assert block["model_version"] == "live-dc-1"


def test_fixture_live_history_is_404_for_unknown_fixture():
    assert client.get("/fixtures/999999/live").status_code == 404


def test_fixture_live_history_is_empty_for_a_scheduled_match():
    db = SessionLocal()
    try:
        fixture = Fixture(
            dedupe_key=f"live-endpoint-history-{datetime.now(timezone.utc).timestamp()}",
            kickoff_utc=datetime.now(timezone.utc) + timedelta(hours=3),
            home_team="History Home",
            away_team="History Away",
            competition="Fixture Payload League",
            country="Test",
            source="Test",
            status="scheduled",
        )
        db.add(fixture)
        db.commit()
        db.refresh(fixture)
        fixture_id = fixture.id
    finally:
        db.close()

    response = client.get(f"/fixtures/{fixture_id}/live")

    assert response.status_code == 200
    assert response.json()["latest"] is None
    assert response.json()["history"] == []
```

- [ ] **Step 2: Write the failing scheduler test**

`tests/test_live_scheduler.py`:

```python
"""The live job refreshes, records and settles, and is scheduled every five minutes."""
from app.tasks import scheduler


class _FakeSession:
    def close(self):
        return None


def test_live_snapshot_runs_all_three_steps(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler, "_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.data_sources.inplay.refresh_live_scores",
        lambda db, force=False: calls.append(("refresh", force))
        or {"refreshed": True, "updated": 1},
    )
    monkeypatch.setattr(
        "app.live.ledger.record_live_snapshots",
        lambda db: calls.append(("record", None)) or {"written": 1},
    )
    monkeypatch.setattr(
        "app.live.ledger.settle_live_predictions",
        lambda db: calls.append(("settle", None)) or {"settled": 1},
    )

    result = scheduler.live_snapshot()

    assert [name for name, _ in calls] == ["refresh", "record", "settle"]
    assert calls[0][1] is True
    assert result["snapshots"] == {"written": 1}


def test_schedule_declares_five_minute_live_job():
    assert scheduler.SCHEDULE["live_minutes"] == 5
```

- [ ] **Step 3: Run both and confirm they fail**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_endpoint.py tests/test_live_scheduler.py -v`

Expected: FAIL — the payload has no `live_prediction`, `/fixtures/{id}/live` is 404 from the generic fixture route, and `scheduler.live_snapshot` does not exist.

- [ ] **Step 4: Extend the schemas**

In `backend/app/schemas.py`:

```python
class LiveFixtureOut(FixtureOut):
    live_prediction: Optional[Dict[str, Any]] = None


class LiveFixturesOut(BaseModel):
    as_of: datetime
    refreshed: bool
    warning: Optional[str] = None
    fixtures: List[LiveFixtureOut]
```

`Any` is already imported.

- [ ] **Step 5: Add the payload helpers and routes**

In `backend/app/main.py`, add imports:

```python
from app.live.ledger import record_live_snapshots, settle_live_predictions
from app.models.live import LivePrediction
```

Add before `_build_fixture_out`:

```python
LIVE_FRESH_SECONDS = 180
LIVE_STALE_SECONDS = 480


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _freshness(observed_at: Optional[datetime], now: datetime) -> str:
    if observed_at is None:
        return "expired"
    age = (now - _as_utc(observed_at)).total_seconds()
    if age < LIVE_FRESH_SECONDS:
        return "fresh"
    if age <= LIVE_STALE_SECONDS:
        return "stale"
    return "expired"


def _latest_live_prediction(db: Session, fixture_id: int) -> Optional[LivePrediction]:
    return db.scalar(
        select(LivePrediction)
        .where(LivePrediction.fixture_id == fixture_id)
        .order_by(LivePrediction.minute_bucket.desc(), LivePrediction.id.desc())
    )


def _live_prediction_payload(
    row: Optional[LivePrediction], now: datetime
) -> Optional[dict]:
    if row is None:
        return None

    freshness = _freshness(row.observed_at, now)
    status = row.status
    probabilities = None
    if row.prob_home is not None and freshness != "expired":
        probabilities = {
            "home_win": row.prob_home,
            "draw": row.prob_draw,
            "away_win": row.prob_away,
        }
    if freshness == "expired":
        # An old score is worse than no number; the stored row keeps its status.
        status = "stale"

    prematch = None
    if row.prematch_home is not None:
        prematch = {
            "home_win": row.prematch_home,
            "draw": row.prematch_draw,
            "away_win": row.prematch_away,
        }

    return {
        "status": status,
        "observed_at": row.observed_at,
        "minute": row.minute,
        "freshness": freshness,
        "score": {"home": row.home_score, "away": row.away_score},
        "remaining_minutes": max(0, 90 - row.minute),
        "probabilities": probabilities,
        "prematch_probabilities": prematch,
        "lambda": {"home": row.lambda_home, "away": row.lambda_away},
        "remaining_lambda": {
            "home": row.remaining_home_lambda,
            "away": row.remaining_away_lambda,
        },
        "intensity": row.intensity,
        "intensity_source": row.intensity_source,
        "lambda_source": row.lambda_source,
        "model_version": row.model_version,
    }


def _live_history_row(row: LivePrediction) -> dict:
    return {
        "minute": row.minute,
        "minute_bucket": row.minute_bucket,
        "observed_at": row.observed_at,
        "score": {"home": row.home_score, "away": row.away_score},
        "probabilities": {
            "home_win": row.prob_home,
            "draw": row.prob_draw,
            "away_win": row.prob_away,
        },
        "result": row.result,
        "extra_home_goals": row.extra_home_goals,
        "extra_away_goals": row.extra_away_goals,
        "brier": row.brier,
        "log_loss": row.log_loss,
        "top_pick_correct": row.top_pick_correct,
    }
```

Replace `live_fixtures` with:

```python
@app.get("/fixtures/live", response_model=LiveFixturesOut)
def live_fixtures(db: Session = Depends(get_db)):
    """Refresh, snapshot, settle, then serve the stored rows."""
    refresh = refresh_live_scores(db)
    record_live_snapshots(db)
    settle_live_predictions(db)

    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(("live", "halftime")))
        .order_by(Fixture.kickoff_utc.asc())
    ).all()

    fixtures = []
    for fixture in rows:
        item = _build_fixture_out(fixture)
        item["live_prediction"] = _live_prediction_payload(
            _latest_live_prediction(db, fixture.id), now
        )
        fixtures.append(item)

    return {
        "as_of": now,
        "refreshed": bool(refresh.get("refreshed")),
        "warning": refresh.get("warning"),
        "fixtures": fixtures,
    }
```

After `get_fixture`, add:

```python
@app.get("/fixtures/{fixture_id}/live")
def fixture_live_history(fixture_id: int, db: Session = Depends(get_db)):
    fixture = db.get(Fixture, fixture_id)
    if not fixture:
        raise HTTPException(status_code=404, detail="Fixture not found")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    history = db.scalars(
        select(LivePrediction)
        .where(
            LivePrediction.fixture_id == fixture_id,
            LivePrediction.result.isnot(None),
            LivePrediction.settled_at >= cutoff,
        )
        .order_by(LivePrediction.minute_bucket.asc())
    ).all()

    return {
        "fixture_id": fixture_id,
        "latest": _live_prediction_payload(
            _latest_live_prediction(db, fixture_id), now
        ),
        "history": [_live_history_row(row) for row in history],
    }
```

- [ ] **Step 6: Add the scheduler job**

In `backend/app/tasks/scheduler.py`:

```python
SCHEDULE = {
    "fixtures": "06:00",
    "statistics": "06:30",
    "models": "07:00",
    "shortlist": "07:30",
    "odds_minutes": 30,
    "odds_window": "06-23",
    "live_minutes": 5,
    "lineup_lead_minutes": "60-90",
    "results": "02:30",
    "analytics": "23:00",
    "delivery_minutes": 2,
}
```

Add before `update_results`:

```python
def live_snapshot() -> Dict[str, Any]:
    """Refresh live scores and keep the forecast ledger current."""
    try:
        from app.data_sources.inplay import refresh_live_scores
        from app.live.ledger import record_live_snapshots, settle_live_predictions

        db = _session()
        try:
            refresh = refresh_live_scores(db, force=True)
            written = record_live_snapshots(db)
            settled = settle_live_predictions(db)
        finally:
            db.close()
        return {"refresh": refresh, "snapshots": written, "settlement": settled}
    except Exception as error:
        logger.error("[Scheduler] live snapshot failed: %s", error)
        return {"error": str(error)}
```

Replace `update_results` with:

```python
def update_results() -> Dict[str, Any]:
    try:
        from app.betting.result_tracker import fetch_and_update_results
        from app.live.ledger import settle_live_predictions

        db = _session()
        try:
            result = fetch_and_update_results(db)
            result["live_settled"] = settle_live_predictions(db)
        finally:
            db.close()
        return result
    except Exception as error:
        logger.error("[Scheduler] results update failed: %s", error)
        return {"error": str(error)}
```

Register the job:

```python
        scheduler.add_job(
            live_snapshot,
            CronTrigger(minute=f"*/{SCHEDULE['live_minutes']}"),
            id="live_snapshot",
        )
```

- [ ] **Step 7: Run the tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_endpoint.py tests/test_live_scheduler.py tests/test_schema.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py backend/app/tasks/scheduler.py tests/test_live_endpoint.py tests/test_live_scheduler.py
git commit -m "feat(live): serve live probabilities and schedule the snapshot job"
```

---

### Task 7: Calibration and export endpoints

**Files:**
- Create: `backend/app/live/report.py`
- Modify: `backend/app/main.py` (imports and routes)
- Modify: `frontend/lib/proxyAllowlist.ts:9-28`
- Modify: `frontend/lib/proxyAllowlist.test.ts`
- Test: `tests/test_live_calibration.py` (create)

**Interfaces:**
- Produces: `calibration_report(db, days=90, now=None) -> dict`.
- Produces: `export_rows(db, days=90, now=None) -> list[dict]`.
- Produces: `verdict_for(n, brier_live, brier_prematch) -> str`.
- Produces: `GET /live/calibration`, `GET /live/export`.

- [ ] **Step 1: Write the failing tests**

`tests/test_live_calibration.py`:

```python
"""The calibration report states a verdict only when the sample supports one."""
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.live.report import calibration_report, export_rows, verdict_for
from app.models.fixture import Fixture
from app.models.live import LivePrediction

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)
PREFIX = "live-calibration-"


def test_verdict_thresholds():
    assert verdict_for(199, 0.40, 0.60) == "insufficient_sample"
    assert verdict_for(200, 0.60, 0.40) == "prematch_better"
    assert verdict_for(200, 0.4005, 0.4000) == "tie"
    assert verdict_for(200, 0.390, 0.400) == "conditional_beats_prematch"
    assert verdict_for(200, 0.410, 0.400) == "prematch_better"


@pytest.fixture
def seeded():
    db = SessionLocal()
    baseline = calibration_report(db, days=90, now=NOW)
    assert baseline["sample"]["snapshots"] == 0, (
        "another test leaked settled live_predictions rows into the shared database"
    )
    fixture = Fixture(
        dedupe_key=f"{PREFIX}{datetime.now(timezone.utc).timestamp()}",
        kickoff_utc=NOW - timedelta(hours=2),
        home_team="Calibration Home",
        away_team="Calibration Away",
        competition="Calibration League",
        country="Test",
        source="Test",
        status="finished",
        home_score=2,
        away_score=1,
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)

    for bucket in range(0, 60, 5):
        db.add(
            LivePrediction(
                fixture_id=fixture.id,
                minute=bucket,
                minute_bucket=bucket,
                observed_at=NOW - timedelta(minutes=45 - bucket),
                home_score=1,
                away_score=0,
                lambda_home=1.5,
                lambda_away=1.0,
                remaining_home_lambda=0.5,
                remaining_away_lambda=0.3,
                intensity=1.0,
                intensity_source="default",
                prob_home=0.70,
                prob_draw=0.20,
                prob_away=0.10,
                prematch_home=0.45,
                prematch_draw=0.28,
                prematch_away=0.27,
                status="ok",
                model_version="live-dc-1",
                result="home_win",
                extra_home_goals=1,
                extra_away_goals=1,
                brier=0.14,
                log_loss=0.357,
                top_pick_correct=True,
                settled_at=NOW - timedelta(minutes=10),
            )
        )
    db.commit()
    try:
        yield fixture.id, db
    finally:
        db.query(LivePrediction).filter(
            LivePrediction.fixture_id == fixture.id
        ).delete(synchronize_session=False)
        db.query(Fixture).filter(Fixture.id == fixture.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_report_shape_and_buckets(seeded):
    fixture_id, db = seeded
    report = calibration_report(db, days=90, now=NOW)

    assert report["sample"]["snapshots"] == 12
    assert report["sample"]["matches"] == 1
    assert report["verdict"] == "insufficient_sample"
    assert report["overall"]["n"] == 12
    assert [row["bucket"] for row in report["buckets"]] == [
        "0-14", "15-29", "30-44", "45-59", "60-74", "75-90"
    ]
    assert all(row["n"] == 0 for row in report["buckets"][4:])
    assert report["buckets"][0]["n"] == 3
    assert report["intensity"]["source"] in {"default", "fitted"}


def test_export_carries_the_fit_inputs(seeded):
    fixture_id, db = seeded
    rows = export_rows(db, days=90, now=NOW)

    assert len(rows) == 12
    row = rows[0]
    assert row["fixture_id"] == fixture_id
    assert row["competition"] == "Calibration League"
    assert row["remaining_home_lambda"] == 0.5
    assert row["remaining_away_lambda"] == 0.3
    assert row["extra_home_goals"] == 1
    assert row["extra_away_goals"] == 1
    assert row["result"] == "home_win"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_calibration.py -v`

Expected: FAIL — `ModuleNotFoundError: app.live.report`.

- [ ] **Step 3: Implement the report module**

`backend/app/live/report.py`:

```python
"""Aggregate settled snapshots and expose the export the fit consumes."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.live.engine import load_intensity
from app.models.fixture import Fixture
from app.models.live import LivePrediction

OUTCOMES = ("home_win", "draw", "away_win")
BUCKETS = (
    ("0-14", 0, 14),
    ("15-29", 15, 29),
    ("30-44", 30, 44),
    ("45-59", 45, 59),
    ("60-74", 60, 74),
    ("75-90", 75, 90),
)
MIN_SAMPLE = 200
MARGIN = 0.002


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _probabilities(row: LivePrediction) -> Dict[str, float]:
    return {
        "home_win": float(row.prob_home),
        "draw": float(row.prob_draw),
        "away_win": float(row.prob_away),
    }


def _prematch(row: LivePrediction) -> Dict[str, float]:
    return {
        "home_win": float(row.prematch_home),
        "draw": float(row.prematch_draw),
        "away_win": float(row.prematch_away),
    }


def _metrics(rows: List[LivePrediction]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {
            "n": 0,
            "brier_live": None,
            "brier_prematch": None,
            "log_loss_live": None,
            "log_loss_prematch": None,
            "top_pick_accuracy": None,
        }

    brier_live = brier_prematch = log_live = log_prematch = 0.0
    correct = 0
    for row in rows:
        index = OUTCOMES.index(row.result)
        probs = _probabilities(row)
        prematch = _prematch(row)
        actual = {name: 1.0 if i == index else 0.0 for i, name in enumerate(OUTCOMES)}
        brier_live += sum((probs[name] - actual[name]) ** 2 for name in OUTCOMES)
        brier_prematch += sum((prematch[name] - actual[name]) ** 2 for name in OUTCOMES)
        log_live -= math.log(max(1e-9, probs[OUTCOMES[index]]))
        log_prematch -= math.log(max(1e-9, prematch[OUTCOMES[index]]))
        if max(OUTCOMES, key=lambda name: probs[name]) == OUTCOMES[index]:
            correct += 1

    return {
        "n": n,
        "brier_live": round(brier_live / n, 6),
        "brier_prematch": round(brier_prematch / n, 6),
        "log_loss_live": round(log_live / n, 6),
        "log_loss_prematch": round(log_prematch / n, 6),
        "top_pick_accuracy": round(correct / n, 4),
    }


def verdict_for(n: int, brier_live: float, brier_prematch: float) -> str:
    if n < MIN_SAMPLE:
        return "insufficient_sample"
    improvement = brier_prematch - brier_live
    if improvement >= MARGIN:
        return "conditional_beats_prematch"
    if improvement <= -MARGIN:
        return "prematch_better"
    return "tie"


def _settled_rows(
    db: Session, *, days: int, now: datetime, with_fixture: bool = False
):
    cutoff = now - timedelta(days=days)
    if with_fixture:
        statement = (
            select(LivePrediction, Fixture)
            .join(Fixture, Fixture.id == LivePrediction.fixture_id)
            .where(LivePrediction.result.isnot(None))
            .order_by(LivePrediction.observed_at.asc())
        )
        return [
            (row, fixture)
            for row, fixture in db.execute(statement).all()
            if _utc(row.observed_at) >= cutoff
        ]

    rows = db.scalars(
        select(LivePrediction)
        .where(LivePrediction.result.isnot(None))
        .order_by(LivePrediction.minute_bucket.asc())
    ).all()
    return [row for row in rows if _utc(row.observed_at) >= cutoff]


def calibration_report(
    db: Session, days: int = 90, now: Optional[datetime] = None
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    rows = _settled_rows(db, days=days, now=now)

    overall = _metrics(rows)
    buckets = []
    for label, low, high in BUCKETS:
        subset = [row for row in rows if low <= row.minute <= high]
        buckets.append({"bucket": label, **_metrics(subset)})

    if overall["n"]:
        verdict = verdict_for(
            overall["n"], overall["brier_live"], overall["brier_prematch"]
        )
    else:
        verdict = "insufficient_sample"

    return {
        "generated_at": now.isoformat(),
        "sample": {
            "snapshots": overall["n"],
            "matches": len({row.fixture_id for row in rows}),
        },
        "verdict": verdict,
        "overall": overall,
        "buckets": buckets,
        "intensity": load_intensity(),
    }


def export_rows(
    db: Session, days: int = 90, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [
        {
            "fixture_id": row.fixture_id,
            "competition": fixture.competition,
            "kickoff_utc": fixture.kickoff_utc.isoformat()
            if fixture.kickoff_utc
            else None,
            "minute": row.minute,
            "minute_bucket": row.minute_bucket,
            "home_score": row.home_score,
            "away_score": row.away_score,
            "lambda_home": row.lambda_home,
            "lambda_away": row.lambda_away,
            "remaining_home_lambda": row.remaining_home_lambda,
            "remaining_away_lambda": row.remaining_away_lambda,
            "intensity": row.intensity,
            "extra_home_goals": row.extra_home_goals,
            "extra_away_goals": row.extra_away_goals,
            "result": row.result,
            "prob_home": row.prob_home,
            "prob_draw": row.prob_draw,
            "prob_away": row.prob_away,
        }
        for row, fixture in _settled_rows(db, days=days, now=now, with_fixture=True)
    ]
```

- [ ] **Step 4: Add the routes**

In `backend/app/main.py`:

```python
from app.live.report import calibration_report, export_rows
```

Add after the fixture live history route:

```python
@app.get("/live/calibration")
def live_calibration(
    days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)
):
    return calibration_report(db, days=days)


@app.get("/live/export")
def live_export(
    days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)
):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": export_rows(db, days=days),
    }
```

`/live/export` is server-only: it is not added to the dashboard proxy allowlist.

- [ ] **Step 5: Allow only the calibration read through the proxy**

In `frontend/lib/proxyAllowlist.ts`, add to `READ_PREFIXES` after `"/learning"`:

```ts
  "/live/calibration",
```

Because `underPrefix` requires an exact match or a following `/`, this does not expose `/live/export`.

Append to `frontend/lib/proxyAllowlist.test.ts`:

```ts
  it("forwards live calibration but never the export", () => {
    expect(isProxied("GET", "/live/calibration")).toBe(true);
    expect(isProxied("GET", "/live/calibration?days=30")).toBe(true);
    expect(isProxied("GET", "/live/export")).toBe(false);
  });
```

- [ ] **Step 6: Run the backend and proxy tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_calibration.py -v`

Run: `nvm use 22 >/dev/null 2>&1; cd frontend && npm test -- proxyAllowlist`

Expected: PASS for both.

- [ ] **Step 7: Commit**

```bash
git add backend/app/live/report.py backend/app/main.py frontend/lib/proxyAllowlist.ts frontend/lib/proxyAllowlist.test.ts tests/test_live_calibration.py
git commit -m "feat(live): expose calibration and the settled export"
```

---

### Task 8: Prove live fixtures never reach a selection

**Files:**
- Test: `tests/test_live_exclusion.py` (create)

**Interfaces:**
- Consumes: `top_picks_for_matchday`, `build_daily_combos`, `build_high_odds`.
- Asserts: no selection path can emit an in-play fixture, with no production change.

> Executed as a control-paired test: each builder must return an identical
> *scheduled* twin before its silence about the in-play fixture proves anything.
> The snippet below lacked that control and passed vacuously for
> `build_daily_combos` and `build_high_odds`, which only see a fixture that
> carries attributed `market_odds_json` prices. The committed
> `tests/test_live_exclusion.py` is the authority.

- [ ] **Step 1: Write the guardrail test**

```python
"""An in-play match is never a selection, even with an absurd edge."""
from datetime import datetime, timedelta, timezone

import pytest

from app.betting.combo import top_picks_for_matchday
from app.betting.daily_combos import build_daily_combos
from app.betting.high_odds import build_high_odds
from app.database import SessionLocal
from app.models.fixture import Fixture

PREFIX = "live-exclusion-"
NOW = datetime.now(timezone.utc)


def _ids(node):
    found = set()
    if isinstance(node, dict):
        if "fixture_id" in node and isinstance(node["fixture_id"], int):
            found.add(node["fixture_id"])
        for value in node.values():
            found |= _ids(value)
    elif isinstance(node, list):
        for value in node:
            found |= _ids(value)
    return found


@pytest.fixture
def live_fixture():
    db = SessionLocal()
    fixture = Fixture(
        dedupe_key=f"{PREFIX}{NOW.timestamp()}",
        kickoff_utc=NOW - timedelta(minutes=30),
        home_team="Live Exclusion Home",
        away_team="Live Exclusion Away",
        competition="Exclusion League",
        country="Test",
        source="Test",
        status="live",
        home_score=3,
        away_score=0,
        home_odds=1.02,
        draw_odds=21.0,
        away_odds=51.0,
        odds_updated_at=NOW - timedelta(hours=1),
    )
    db.add(fixture)
    db.commit()
    db.refresh(fixture)
    fixture_id = fixture.id
    db.close()

    yield fixture_id

    db = SessionLocal()
    try:
        db.query(Fixture).filter(Fixture.id == fixture_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_live_fixture_is_absent_from_every_selection(live_fixture):
    today = NOW.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        reports = [
            top_picks_for_matchday(db, target_date=today),
            build_daily_combos(db, now=NOW),
            build_high_odds(db, now=NOW),
        ]
        for report in reports:
            assert live_fixture not in _ids(report)
    finally:
        db.close()
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_exclusion.py -v`

Expected: PASS — the three builders already filter `status == "scheduled"` and future kickoff. This task locks that behaviour in.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live_exclusion.py
git commit -m "test(live): in-play fixtures never enter a selection path"
```

---

### Task 9: Web live board, fixture detail, and calibration panel

**Files:**
- Modify: `frontend/lib/types.ts`
- Create: `frontend/app/live/page.tsx`
- Create: `frontend/app/fixtures/[id]/page.tsx`
- Create: `frontend/components/LiveCalibrationPanel.tsx`
- Modify: `frontend/app/performance/page.tsx`
- Modify: `frontend/components/Nav.tsx:6-19`

**Interfaces:**
- Consumes: `GET /fixtures/live`, `GET /fixtures/{id}`, `GET /fixtures/{id}/live`, `GET /live/calibration`.
- Produces: `/live`, `/fixtures/{id}`, and a Performance panel.

- [ ] **Step 1: Add the types**

Append to `frontend/lib/types.ts`:

```ts
export type LiveProbabilities = {
  home_win: number;
  draw: number;
  away_win: number;
};

export type LivePrediction = {
  status: string;
  observed_at: string | null;
  minute: number;
  freshness: "fresh" | "stale" | "expired";
  score: { home: number | null; away: number | null };
  remaining_minutes: number;
  probabilities: LiveProbabilities | null;
  prematch_probabilities: LiveProbabilities | null;
  lambda: { home: number | null; away: number | null };
  remaining_lambda: { home: number | null; away: number | null };
  intensity: number | null;
  intensity_source: string | null;
  lambda_source: string | null;
  model_version: string;
};

export type LiveFixture = Fixture & {
  live_prediction: LivePrediction | null;
};

export type LiveFixturesResponse = {
  as_of: string;
  refreshed: boolean;
  warning: string | null;
  fixtures: LiveFixture[];
};

export type LiveHistoryResponse = {
  fixture_id: number;
  latest: LivePrediction | null;
  history: Array<{
    minute: number;
    minute_bucket: number;
    observed_at: string | null;
    score: { home: number | null; away: number | null };
    probabilities: LiveProbabilities | null;
    result: string | null;
    extra_home_goals: number | null;
    extra_away_goals: number | null;
    brier: number | null;
    log_loss: number | null;
    top_pick_correct: boolean | null;
  }>;
};

export type LiveCalibrationReport = {
  generated_at: string;
  sample: { snapshots: number; matches: number };
  verdict: string;
  overall: {
    n: number;
    brier_live: number | null;
    brier_prematch: number | null;
    log_loss_live: number | null;
    log_loss_prematch: number | null;
    top_pick_accuracy: number | null;
  };
  buckets: Array<{
    bucket: string;
    n: number;
    brier_live: number | null;
    brier_prematch: number | null;
  }>;
  intensity: { k: number; source: string };
};
```

Also add `live_prediction?: LivePrediction | null;` to the existing `Fixture` type so a plain fixture decode stays valid.

- [ ] **Step 2: Build the live board**

`frontend/app/live/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useEffect } from "react";

import { Badge, Card, ErrorNote, Loading, Note, PageHeader } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { LiveFixture, LiveFixturesResponse, LivePrediction } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const TONE: Record<string, "good" | "warn" | "muted"> = {
  fresh: "good",
  stale: "warn",
  expired: "muted",
};

function Probabilities({ prediction }: { prediction: LivePrediction }) {
  const probabilities = prediction.probabilities;
  if (!probabilities) {
    return (
      <p className="text-xs text-mist/50">
        No forecast ({prediction.status.replaceAll("_", " ")})
      </p>
    );
  }
  const rows = [
    ["Home", probabilities.home_win],
    ["Draw", probabilities.draw],
    ["Away", probabilities.away_win],
  ] as const;
  return (
    <div className="space-y-1">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className="w-12 text-mist/50">{label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-pitch">
            <div className="h-full bg-lime" style={{ width: `${value * 100}%` }} />
          </div>
          <span className="w-12 text-right tabular-nums text-mist/80">
            {fmtPercent(value, 1)}
          </span>
        </div>
      ))}
    </div>
  );
}

function LiveCard({ fixture }: { fixture: LiveFixture }) {
  const prediction = fixture.live_prediction;
  return (
    <Card
      title={fixture.match ?? `${fixture.home_team} vs ${fixture.away_team}`}
      subtitle={`${fixture.competition} · ${fixture.kickoff_eat ?? ""}`}
      actions={
        prediction ? (
          <Badge tone={TONE[prediction.freshness] ?? "muted"}>
            {prediction.freshness}
          </Badge>
        ) : null
      }
    >
      <div className="space-y-3">
        <div className="flex items-baseline justify-between">
          <p className="text-2xl font-bold tabular-nums text-white">
            {fixture.home_score ?? "–"} : {fixture.away_score ?? "–"}
          </p>
          <p className="text-sm tabular-nums text-mist/60">
            {fixture.live_clock ?? (prediction ? `${prediction.minute}'` : "—")}
          </p>
        </div>
        {prediction ? <Probabilities prediction={prediction} /> : null}
        <div className="flex items-center justify-between text-[11px] text-mist/45">
          <span>
            {prediction
              ? `Model ${prediction.model_version} · as of ${prediction.observed_at ?? "unknown"}`
              : "Waiting for the engine's first snapshot"}
          </span>
          <Link href={`/fixtures/${fixture.id}`} className="text-lime hover:underline">
            Match detail
          </Link>
        </div>
      </div>
    </Card>
  );
}

export default function LivePage() {
  const { data, loading, error, reload } =
    useApi<LiveFixturesResponse>("/fixtures/live");

  useEffect(() => {
    const timer = setInterval(reload, 30_000);
    return () => clearInterval(timer);
  }, [reload]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Live"
        subtitle="Conditional win probabilities for matches in play."
      />
      <Note tone="warn">
        Model estimate, not a price. Informational only. Live numbers never enter the
        85% gate, a slip, or a staking recommendation.
      </Note>
      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && !data && <Loading label="Reading the live board…" />}
      {data && data.warning && <Note tone="warn">{data.warning}</Note>}
      {data && data.fixtures.length === 0 && (
        <Card title="No matches in play">
          <p className="text-sm text-mist/60">
            Nothing is being played right now. The board refreshes every 30 seconds.
          </p>
        </Card>
      )}
      <div className="grid gap-3 lg:grid-cols-2">
        {data?.fixtures.map((fixture) => (
          <LiveCard key={fixture.id} fixture={fixture} />
        ))}
      </div>
    </div>
  );
}
```

If `Fixture` has no `match` field, delete the `fixture.match ??` fallback and use the team labels directly.

- [ ] **Step 3: Build the fixture detail page**

`frontend/app/fixtures/[id]/page.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import { Badge, Card, ErrorNote, Loading, Note, PageHeader } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { Fixture, LiveHistoryResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const RESULT_LABEL: Record<string, string> = {
  home_win: "Home win",
  draw: "Draw",
  away_win: "Away win",
};

export default function FixtureDetailPage() {
  const params = useParams<{ id: string }>();
  const fixture = useApi<Fixture>(`/fixtures/${params.id}`);
  const live = useApi<LiveHistoryResponse>(`/fixtures/${params.id}/live`);

  return (
    <div className="space-y-4">
      <PageHeader
        title={
          fixture.data
            ? `${fixture.data.home_team} vs ${fixture.data.away_team}`
            : "Fixture"
        }
        subtitle={fixture.data ? `${fixture.data.competition} · ${fixture.data.kickoff_eat ?? ""}` : undefined}
      />
      {fixture.error && <ErrorNote>{fixture.error}</ErrorNote>}
      {fixture.loading && !fixture.data && <Loading label="Loading the fixture…" />}

      {fixture.data && (
        <Card title="Result and state">
          <div className="flex flex-wrap items-center gap-4">
            <p className="text-3xl font-bold tabular-nums text-white">
              {fixture.data.home_score ?? "–"} : {fixture.data.away_score ?? "–"}
            </p>
            <Badge tone="muted">{fixture.data.status ?? "unknown"}</Badge>
            <span className="text-sm text-mist/60">{fixture.data.live_clock ?? "—"}</span>
            <Link
              href={`/analysis?fixture=${fixture.data.id}`}
              className="text-sm text-lime hover:underline"
            >
              Open full analysis
            </Link>
          </div>
        </Card>
      )}

      {live.data?.latest && (
        <Card
          title="Live forecast"
          actions={<Badge tone="muted">{live.data.latest.freshness}</Badge>}
        >
          {live.data.latest.probabilities ? (
            <div className="grid grid-cols-3 gap-3 text-center">
              {(["home_win", "draw", "away_win"] as const).map((key) => (
                <div key={key}>
                  <p className="text-xs text-mist/45">
                    {key === "home_win" ? "Home" : key === "draw" ? "Draw" : "Away"}
                  </p>
                  <p className="text-xl font-semibold tabular-nums text-white">
                    {fmtPercent(live.data.latest!.probabilities![key], 1)}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-mist/55">
              No forecast: {live.data.latest.status.replaceAll("_", " ")}.
            </p>
          )}
          <Note tone="warn">
            Model estimate, not a price. Informational only.
          </Note>
        </Card>
      )}

      <Card title="Settled snapshots">
        {live.data && live.data.history.length === 0 && (
          <p className="text-sm text-mist/55">
            No settled snapshots in the last 24 hours.
          </p>
        )}
        {live.data && live.data.history.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase text-mist/40">
                <tr>
                  <th className="py-2">Minute</th>
                  <th className="py-2">Score at snapshot</th>
                  <th className="py-2">Home / Draw / Away</th>
                  <th className="py-2">Final</th>
                  <th className="py-2">Brier</th>
                </tr>
              </thead>
              <tbody>
                {live.data.history.map((row) => (
                  <tr key={row.minute_bucket} className="border-t border-edge">
                    <td className="py-2 tabular-nums">{row.minute}</td>
                    <td className="py-2 tabular-nums">
                      {row.score.home} : {row.score.away}
                    </td>
                    <td className="py-2 tabular-nums">
                      {row.probabilities
                        ? `${fmtPercent(row.probabilities.home_win, 0)} / ${fmtPercent(row.probabilities.draw, 0)} / ${fmtPercent(row.probabilities.away_win, 0)}`
                        : "—"}
                    </td>
                    <td className="py-2">
                      {row.result ? RESULT_LABEL[row.result] ?? row.result : "Open"}
                    </td>
                    <td className="py-2 tabular-nums">
                      {row.brier?.toFixed(3) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Add the calibration panel**

`frontend/components/LiveCalibrationPanel.tsx`:

```tsx
"use client";

import { Badge, Card, Note } from "@/components/ui";
import { fmtPercent } from "@/lib/format";
import type { LiveCalibrationReport } from "@/lib/types";
import { useApi } from "@/lib/useApi";

export function LiveCalibrationPanel() {
  const { data, loading, error } = useApi<LiveCalibrationReport>("/live/calibration");

  return (
    <Card
      title="Live forecast calibration"
      actions={
        data ? <Badge tone="muted">{data.verdict.replaceAll("_", " ")}</Badge> : null
      }
    >
      {loading && <p className="text-sm text-mist/55">Reading settled snapshots…</p>}
      {error && <Note tone="warn">{error}</Note>}
      {data && (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            <div>
              <p className="text-xs text-mist/45">Snapshots</p>
              <p className="tabular-nums text-white">{data.sample.snapshots}</p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Matches</p>
              <p className="tabular-nums text-white">{data.sample.matches}</p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Brier live</p>
              <p className="tabular-nums text-white">
                {data.overall.brier_live?.toFixed(4) ?? "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-mist/45">Brier pre-match</p>
              <p className="tabular-nums text-white">
                {data.overall.brier_prematch?.toFixed(4) ?? "—"}
              </p>
            </div>
          </div>
          {data.overall.n > 0 && (
            <table className="w-full text-left text-xs">
              <thead className="text-mist/40">
                <tr>
                  <th className="py-1">Window</th>
                  <th className="py-1">n</th>
                  <th className="py-1">Live</th>
                  <th className="py-1">Pre-match</th>
                </tr>
              </thead>
              <tbody>
                {data.buckets.map((row) => (
                  <tr key={row.bucket} className="border-t border-edge">
                    <td className="py-1">{row.bucket}</td>
                    <td className="py-1 tabular-nums">{row.n}</td>
                    <td className="py-1 tabular-nums">
                      {row.brier_live?.toFixed(4) ?? "—"}
                    </td>
                    <td className="py-1 tabular-nums">
                      {row.brier_prematch?.toFixed(4) ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="text-[11px] text-mist/45">
            Intensity k = {data.intensity.k.toFixed(2)} ({data.intensity.source}). Below
            200 settled snapshots the verdict stays insufficient.
          </p>
        </div>
      )}
    </Card>
  );
}
```

Mount it in `frontend/app/performance/page.tsx`: add the import

```tsx
import { LiveCalibrationPanel } from "@/components/LiveCalibrationPanel";
```

and render `<LiveCalibrationPanel />` as the last child before the closing `</div>` of the page's outer container, after the existing final `</Card>`.

- [ ] **Step 5: Add the nav link**

In `frontend/components/Nav.tsx`, after Overview:

```tsx
  { href: "/", label: "Overview", short: "OV" },
  { href: "/live", label: "Live", short: "LV" },
```

- [ ] **Step 6: Build the dashboard**

Run: `cd frontend && npm run build`

Expected: exit 0. If a `TypeScript` error names a missing `match` field, remove the `fixture.match ??` fallback in `LiveCard` and use the two team labels.

- [ ] **Step 7: Verify in the browser**

Start the backend on `:8001`, then `cd frontend && npm run dev`, open `http://localhost:3000/live` and a `/fixtures/{id}` link. Confirm the disclaimer, the freshness badge, and that the page loads with no in-play match showing fabricated numbers.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib/types.ts frontend/app/live/page.tsx "frontend/app/fixtures/[id]/page.tsx" frontend/components/LiveCalibrationPanel.tsx frontend/app/performance/page.tsx frontend/components/Nav.tsx
git commit -m "feat(web): add the live board, fixture detail and calibration panel"
```

---

### Task 10: iOS live tab and fixture panel

**Files:**
- Modify: `ios/FIE/Core/Models.swift:24-53`
- Create: `ios/FIE/Features/LiveView.swift`
- Modify: `ios/FIE/Features/FixtureDetailView.swift`
- Modify: `ios/FIE/FIEApp.swift:33-55`
- Modify: `ios/FIE/Core/EngineStore.swift:25-33`

**Interfaces:**
- Consumes: `live_prediction` in `GET /fixtures/live`, `GET /fixtures/{id}/live`.
- Produces: a Live tab refreshing every 30 seconds.

- [ ] **Step 1: Add the models**

In `ios/FIE/Core/Models.swift`, add `livePrediction` to `Fixture`:

```swift
    let eligibleForAnalysis: Bool?
    let livePrediction: LivePrediction?
```

Add below `LiveFixturesResponse`:

```swift
struct LivePrediction: Decodable {
    struct Probabilities: Decodable {
        let homeWin: Double
        let draw: Double
        let awayWin: Double
    }

    struct Score: Decodable {
        let home: Int?
        let away: Int?
    }

    struct LambdaPair: Decodable {
        let home: Double?
        let away: Double?
    }

    let status: String
    let observedAt: String?
    let minute: Int
    let freshness: String
    let score: Score
    let remainingMinutes: Int
    let probabilities: Probabilities?
    let prematchProbabilities: Probabilities?
    let lambda: LambdaPair
    let remainingLambda: LambdaPair
    let intensity: Double?
    let intensitySource: String?
    let lambdaSource: String?
    let modelVersion: String
}

struct LiveHistoryResponse: Decodable {
    struct Snapshot: Decodable, Identifiable {
        let minute: Int
        let minuteBucket: Int
        let observedAt: String?
        let score: LivePrediction.Score
        let probabilities: LivePrediction.Probabilities?
        let result: String?
        let extraHomeGoals: Int?
        let extraAwayGoals: Int?
        let brier: Double?
        let logLoss: Double?
        let topPickCorrect: Bool?

        var id: Int { minuteBucket }
    }

    let fixtureId: Int
    let latest: LivePrediction?
    let history: [Snapshot]
}
```

`APIClient.decoder` already converts snake case, so no `CodingKeys` are needed.

- [ ] **Step 2: Create the Live tab**

`ios/FIE/Features/LiveView.swift`:

```swift
import SwiftUI

struct LiveView: View {
    @EnvironmentObject private var store: EngineStore

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 12) {
                    if store.live.isEmpty {
                        emptyState
                    }
                    ForEach(store.live) { fixture in
                        NavigationLink(value: fixture.id) {
                            LiveFixtureCard(fixture: fixture)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
            }
            .background(Palette.background.ignoresSafeArea())
            .navigationTitle("Live")
            .refreshable { await store.refreshLive() }
            .navigationDestination(for: Int.self) { id in
                FixtureDetailView(fixtureId: id)
            }
        }
        .task {
            await store.refreshLive()
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                if Task.isCancelled { return }
                await store.refreshLive()
            }
        }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No matches in play")
                .font(.headline)
            Text("Nothing is being played right now. This tab refreshes every 30 seconds.")
                .font(.footnote)
                .foregroundStyle(Palette.muted)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(Palette.card)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
}

private struct LiveFixtureCard: View {
    let fixture: Fixture

    private var prediction: LivePrediction? { fixture.livePrediction }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(fixture.matchLabel)
                    .font(.subheadline.weight(.semibold))
                Spacer()
                if let freshness = prediction?.freshness {
                    Text(freshness.uppercased())
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(freshness == "fresh" ? Palette.accent : Palette.warn)
                }
            }

            HStack(alignment: .firstTextBaseline) {
                Text("\(fixture.homeScore ?? 0) : \(fixture.awayScore ?? 0)")
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                Spacer()
                Text(fixture.liveClock ?? prediction.map { "\($0.minute)'" } ?? "—")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(Palette.muted)
            }

            if let probabilities = prediction?.probabilities {
                HStack(spacing: 12) {
                    probability("Home", probabilities.homeWin)
                    probability("Draw", probabilities.draw)
                    probability("Away", probabilities.awayWin)
                }
            } else if let status = prediction?.status {
                Text("No forecast: \(status.replacingOccurrences(of: "_", with: " "))")
                    .font(.caption)
                    .foregroundStyle(Palette.muted)
            }

            Text("Model estimate, not a price. Informational only.")
                .font(.caption2)
                .foregroundStyle(Palette.muted)
        }
        .padding(16)
        .background(Palette.card)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func probability(_ label: String, _ value: Double) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(Palette.muted)
            Text(value, format: .percent.precision(.fractionLength(1)))
                .font(.footnote.monospacedDigit().weight(.semibold))
        }
    }
}
```

If `Palette.warn` does not exist, use `Palette.muted`; check `ios/FIE/Design` for the exact token before saving.

- [ ] **Step 3: Add the tab**

In `ios/FIE/FIEApp.swift`, append after the Settings tab:

```swift
                LiveView()
                    .tag(5)
                    .tabItem { Label("Live", systemImage: "dot.radiowaves.left.and.right") }
```

In `EngineStore.forTab` add:

```swift
        case 5: return .live
```

- [ ] **Step 4: Add the live panel and history to the fixture detail**

In `FixtureDetailView`, add state:

```swift
    @State private var liveHistory: LiveHistoryResponse?
```

Add a loader next to the existing dossier load:

```swift
    private func loadLiveHistory() async {
        do {
            liveHistory = try await client.get(
                "/fixtures/\(fixtureId)/live",
                timeout: 20,
                as: LiveHistoryResponse.self
            )
        } catch {
            liveHistory = nil
        }
    }
```

Call `await loadLiveHistory()` from the existing `.task` that loads the dossier.

Render after the header/prices block:

```swift
                if let latest = liveHistory?.latest {
                    liveCard(latest)
                }
                if let history = liveHistory, !history.history.isEmpty {
                    liveHistoryCard(history)
                }
```

Add the two views:

```swift
    @ViewBuilder
    private func liveCard(_ prediction: LivePrediction) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Live forecast")
                    .font(.headline)
                Spacer()
                Text(prediction.freshness.uppercased())
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(prediction.freshness == "fresh" ? Palette.accent : Palette.muted)
            }
            if let probabilities = prediction.probabilities {
                HStack(spacing: 16) {
                    Text("Home \(probabilities.homeWin, format: .percent.precision(.fractionLength(1)))")
                    Text("Draw \(probabilities.draw, format: .percent.precision(.fractionLength(1)))")
                    Text("Away \(probabilities.awayWin, format: .percent.precision(.fractionLength(1)))")
                }
                .font(.footnote.monospacedDigit())
            } else {
                Text("No forecast: \(prediction.status.replacingOccurrences(of: "_", with: " "))")
                    .font(.footnote)
                    .foregroundStyle(Palette.muted)
            }
            Text("Model estimate, not a price. Informational only.")
                .font(.caption2)
                .foregroundStyle(Palette.muted)
        }
        .padding(16)
        .background(Palette.card)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    @ViewBuilder
    private func liveHistoryCard(_ history: LiveHistoryResponse) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Settled snapshots")
                .font(.headline)
            ForEach(history.history) { row in
                HStack {
                    Text("\(row.minute)'")
                        .font(.caption.monospacedDigit())
                        .frame(width: 44, alignment: .leading)
                    Text("\(row.score.home ?? 0) : \(row.score.away ?? 0)")
                        .font(.caption.monospacedDigit())
                    Spacer()
                    Text(row.result?.replacingOccurrences(of: "_", with: " ") ?? "open")
                        .font(.caption)
                        .foregroundStyle(Palette.muted)
                    if let brier = row.brier {
                        Text(brier, format: .number.precision(.fractionLength(3)))
                            .font(.caption.monospacedDigit())
                    }
                }
            }
        }
        .padding(16)
        .background(Palette.card)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }
```

- [ ] **Step 5: Build the app**

Run: `xcodebuild -project ios/FIE.xcodeproj -scheme FIE -destination 'platform=iOS Simulator,name=iPhone 16' build`

Expected: `BUILD SUCCEEDED`. If the scheme or simulator name differs, list them first with `xcodebuild -list -project ios/FIE.xcodeproj`.

- [ ] **Step 6: Commit**

```bash
git add ios/FIE/Core/Models.swift ios/FIE/Features/LiveView.swift ios/FIE/Features/FixtureDetailView.swift ios/FIE/FIEApp.swift ios/FIE/Core/EngineStore.swift
git commit -m "feat(ios): add the live tab and fixture live panel"
```

---

## Phase 2 — Nightly learning pipeline

### Task 11: Parameterise the walk-forward and add the champion gate

**Files:**
- Modify: `backend/app/analytics/calibration.py:110-164`, `:232-274`
- Modify: `backend/app/analytics/blending.py:409-417`
- Create: `backend/app/analytics/model_gate.py`
- Test: `tests/test_model_gate.py` (create)

**Interfaces:**
- Produces: `iter_walkforward_predictions(..., half_life_days=RATING_HALF_LIFE_DAYS)`.
- Produces: `evaluate_config(db, *, half_life_days, window_days=730, refit_days=14, now=None) -> dict`.
- Produces: `gate_decision(results, champion_config) -> dict`.
- Produces: `load_champion()`, `save_champion(payload)`, `CHAMPION_PATH`, `HALF_LIFE_GRID`.

- [ ] **Step 1: Write the failing gate tests**

```python
"""The gate compares configurations on the same windows, never a stale number."""
from app.analytics.model_gate import gate_decision


def _result(half_life, brier, log_loss=0.70, comps=None):
    return {
        "half_life_days": half_life,
        "n_matches": 1000,
        "brier": brier,
        "log_loss": log_loss,
        "per_competition": comps or {},
    }


def test_bootstrap_promotes_the_best_configuration():
    results = [_result(90, 0.610), _result(120, 0.600), _result(150, 0.605)]

    decision = gate_decision(results, None)

    assert decision["promote"] is True
    assert decision["reason"] == "bootstrap"
    assert decision["challenger"]["half_life_days"] == 120


def test_a_material_brier_gain_promotes():
    results = [_result(90, 0.598), _result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is True
    assert decision["reason"] == "brier_improved"


def test_a_small_gain_keeps_the_champion():
    results = [_result(90, 0.600), _result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "brier_gain_too_small"


def test_a_worse_log_loss_keeps_the_champion():
    results = [_result(90, 0.590, log_loss=0.75), _result(150, 0.601, log_loss=0.70)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "log_loss_worse"


def test_a_competition_regression_is_rejected():
    results = [
        _result(90, 0.590, comps={"Premier League": {"n": 500, "brier": 0.620}}),
        _result(150, 0.601, comps={"Premier League": {"n": 500, "brier": 0.600}}),
    ]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"].startswith("competition_regression")


def test_the_champion_config_alone_is_not_a_challenger():
    results = [_result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "config_unchanged"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_model_gate.py -v`

Expected: FAIL — `ModuleNotFoundError: app.analytics.model_gate`.

- [ ] **Step 3: Parameterise the half-life**

In `backend/app/analytics/calibration.py`:

```python
def _walkforward_for_competition(
    competition: str,
    matches: List[Fixture],
    refit_days: int,
    min_weighted_matches: float,
    rho: float,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
):
```

and in the weights:

```python
        weights = np.maximum(
            MIN_MATCH_WEIGHT, 0.5 ** (age_days / half_life_days)
        )
```

```python
def iter_walkforward_predictions(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    min_weighted_matches: float = 3.0,
    rho: float = -0.10,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
):
```

Pass it through:

```python
        yield from _walkforward_for_competition(
            comp,
            comp_matches,
            refit_days=refit_days,
            min_weighted_matches=min_weighted_matches,
            rho=rho,
            half_life_days=half_life_days,
        )
```

Add the same keyword to `run_walk_forward` and pass it to `iter_walkforward_predictions`.

In `backend/app/analytics/blending.py`, add `half_life_days: float = RATING_HALF_LIFE_DAYS` to `fit_report` and pass it to `iter_walkforward_predictions`. Import the constant:

```python
from app.analytics.ratings import RATING_HALF_LIFE_DAYS
```

- [ ] **Step 4: Implement the gate**

`backend/app/analytics/model_gate.py`:

```python
"""Choose the rating half-life by comparing configurations, not snapshots.

A stored champion score was measured on older data. Comparing it to a fresh
challenger would either freeze the model or reward a data refresh rather than
a better configuration. Every candidate is therefore replayed on the same
leak-free windows in the same run.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.analytics.calibration import brier_score, iter_walkforward_predictions, log_loss

HALF_LIFE_GRID = (90.0, 120.0, 150.0, 200.0)
DEFAULT_HALF_LIFE = 150.0
PROMOTION_MARGIN = 0.002
COMPETITION_FLOOR = 0.005
MIN_COMPETITION_SAMPLE = 150

CHAMPION_PATH = Path(__file__).resolve().parents[3] / "data" / "model_champion.json"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def evaluate_config(
    db: Session,
    *,
    half_life_days: float,
    window_days: int = 730,
    refit_days: int = 14,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    probabilities: List[Dict[str, float]] = []
    labels: List[str] = []
    per_competition: Dict[str, Dict[str, List]] = defaultdict(
        lambda: {"probs": [], "labels": []}
    )

    for prediction in iter_walkforward_predictions(
        db, refit_days=refit_days, half_life_days=half_life_days
    ):
        kickoff = prediction["fixture"].kickoff_utc
        if kickoff is None or _utc(kickoff) < cutoff:
            continue
        probabilities.append(prediction["model"])
        labels.append(prediction["label"])
        bucket = per_competition[prediction["competition"]]
        bucket["probs"].append(prediction["model"])
        bucket["labels"].append(prediction["label"])

    if not labels:
        return {
            "half_life_days": half_life_days,
            "n_matches": 0,
            "brier": None,
            "log_loss": None,
            "per_competition": {},
        }

    by_competition = {}
    for competition, bucket in per_competition.items():
        by_competition[competition] = {
            "n": len(bucket["labels"]),
            "brier": brier_score(bucket["probs"], bucket["labels"]),
            "log_loss": log_loss(bucket["probs"], bucket["labels"]),
        }

    return {
        "half_life_days": half_life_days,
        "n_matches": len(labels),
        "brier": brier_score(probabilities, labels),
        "log_loss": log_loss(probabilities, labels),
        "per_competition": by_competition,
    }


def gate_decision(
    results: List[Dict[str, Any]],
    champion_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    usable = [row for row in results if row["n_matches"]]
    if not usable:
        return {"promote": False, "reason": "no_evaluation_data", "challenger": None, "champion": None}

    if champion_config is None:
        challenger = min(usable, key=lambda row: row["brier"])
        return {
            "promote": True,
            "reason": "bootstrap",
            "challenger": challenger,
            "champion": challenger,
        }

    champion_half_life = float(
        champion_config.get("half_life_days", DEFAULT_HALF_LIFE)
    )
    champion = next(
        (row for row in usable if float(row["half_life_days"]) == champion_half_life),
        None,
    )
    if champion is None:
        return {
            "promote": False,
            "reason": "champion_config_not_evaluated",
            "challenger": None,
            "champion": None,
        }

    challenger = min(usable, key=lambda row: row["brier"])
    if float(challenger["half_life_days"]) == champion_half_life:
        return {
            "promote": False,
            "reason": "config_unchanged",
            "challenger": challenger,
            "champion": champion,
        }
    if challenger["brier"] > champion["brier"] - PROMOTION_MARGIN:
        reason = "brier_gain_too_small"
    elif challenger["log_loss"] > champion["log_loss"]:
        reason = "log_loss_worse"
    else:
        regressed = [
            name
            for name, metrics in challenger["per_competition"].items()
            if metrics["n"] >= MIN_COMPETITION_SAMPLE
            and metrics["brier"]
            > champion["per_competition"].get(name, {}).get("brier", 1.0)
            + COMPETITION_FLOOR
        ]
        reason = (
            f"competition_regression:{regressed[0]}"
            if regressed
            else "brier_improved"
        )

    return {
        "promote": reason == "brier_improved",
        "reason": reason,
        "challenger": challenger,
        "champion": champion,
    }


def load_champion() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(CHAMPION_PATH.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def save_champion(payload: Dict[str, Any]) -> None:
    CHAMPION_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHAMPION_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True))
```

- [ ] **Step 5: Run the gate tests and the calibration suite**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_model_gate.py tests/test_evaluation.py tests/test_blending.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/analytics/calibration.py backend/app/analytics/blending.py backend/app/analytics/model_gate.py tests/test_model_gate.py
git commit -m "feat(learning): compare half-life configurations on identical windows"
```

---

### Task 12: Committed ratings artifact and startup loader

**Files:**
- Create: `backend/app/analytics/artifacts.py`
- Modify: `backend/app/main.py` (startup load)
- Modify: `backend/app/analytics/ratings.py:398-410` (half-life parameter)
- Test: `tests/test_ratings_artifact.py` (create)

**Interfaces:**
- Produces: `write_ratings(path, db, *, half_life_days, trained_through, league_averages) -> dict`.
- Produces: `load_ratings(path=RATINGS_PATH) -> int`.
- Produces: `RATINGS_PATH`.
- Produces: `calculate_ratings_from_fixtures(..., half_life_days=RATING_HALF_LIFE_DAYS)`.

- [ ] **Step 1: Write the failing artifact test**

```python
"""A committed ratings artifact can seed an empty hosted database."""
import json

from app.analytics.artifacts import RATINGS_PATH, load_ratings, write_ratings
from app.database import SessionLocal
from app.models.team_rating import TeamRating


def _cleanup(db):
    db.query(TeamRating).filter(
        TeamRating.competition.in_(["Artifact League A", "Artifact League B"])
    ).delete(synchronize_session=False)
    db.commit()


def test_artifact_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "ratings.json"
    db = SessionLocal()
    try:
        _cleanup(db)
        db.add(TeamRating(
            team_name="artifact home",
            competition="Artifact League A",
            attack_rating=1.25,
            defense_rating=0.85,
            matches_played=20,
            weighted_matches=12.5,
            home_attack_boost=0.04,
            home_goals_avg=1.9,
            away_goals_avg=1.2,
        ))
        db.add(TeamRating(
            team_name="thin sample",
            competition="Artifact League B",
            attack_rating=1.10,
            defense_rating=1.00,
            matches_played=1,
            weighted_matches=0.4,
        ))
        db.commit()

        meta = write_ratings(
            path,
            db,
            half_life_days=150.0,
            trained_through="2026-11-12T23:00:00+00:00",
            league_averages={"Artifact League A": (1.51, 1.14)},
        )

        payload = json.loads(path.read_text())
        assert meta["content_hash"].startswith("sha256:")
        assert payload["half_life_days"] == 150.0
        assert "artifact home" in payload["competitions"]["Artifact League A"]["teams"]
        assert "thin sample" not in payload["competitions"].get("Artifact League B", {}).get("teams", {})

        _cleanup(db)
        loaded = load_ratings(path)
        assert loaded >= 1
        row = db.query(TeamRating).filter(
            TeamRating.competition == "Artifact League A",
            TeamRating.team_name == "artifact home",
        ).one()
        assert row.attack_rating == 1.25
        assert row.weighted_matches == 12.5
    finally:
        _cleanup(db)
        db.close()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_ratings_artifact.py -v`

Expected: FAIL — `ModuleNotFoundError: app.analytics.artifacts`.

- [ ] **Step 3: Add the half-life parameter to the refit**

In `backend/app/analytics/ratings.py`:

```python
def calculate_ratings_from_fixtures(
    db: Session,
    competition: Optional[str] = None,
    replace: bool = True,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
) -> Dict[str, Any]:
```

Find the `decay_weight(...)` call inside and pass `half_life_days=half_life_days`. If it currently builds weights inline with `RATING_HALF_LIFE_DAYS`, replace the constant with the parameter.

- [ ] **Step 4: Implement the artifact module**

`backend/app/analytics/artifacts.py`:

```python
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
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.ratings import MIN_WEIGHTED_MATCHES, upsert_team_rating
from app.database import SessionLocal
from app.models.team_rating import TeamRating

logger = logging.getLogger(__name__)

RATINGS_PATH = Path(__file__).resolve().parents[3] / "data" / "ratings.json"


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
    text = json.dumps(payload, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

    return {
        "path": str(path),
        "teams": teams,
        "competitions": len(competitions),
        "content_hash": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
    }


def load_ratings(path: Path = RATINGS_PATH) -> int:
    """Seed an empty rating table from the committed artifact."""
    if not path.exists():
        return 0

    db = SessionLocal()
    try:
        existing = db.scalar(select(func.count(TeamRating.id))) or 0
        if existing:
            return 0

        payload = json.loads(path.read_text())
        loaded = 0
        for competition, block in (payload.get("competitions") or {}).items():
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
        db.commit()
        logger.info("Loaded %s team ratings from %s", loaded, path)
        return loaded
    except Exception:
        db.rollback()
        logger.exception("Rating artifact load failed")
        return 0
    finally:
        db.close()
```

- [ ] **Step 5: Load at startup**

In `backend/app/main.py`, after `Base.metadata.create_all(bind=engine)`:

```python
try:
    from app.analytics.artifacts import load_ratings

    load_ratings()
except Exception:
    pass
```

- [ ] **Step 6: Run the artifact test and the ratings suite**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_ratings_artifact.py tests/test_evaluation.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/analytics/artifacts.py backend/app/analytics/ratings.py backend/app/main.py tests/test_ratings_artifact.py
git commit -m "feat(learning): ship a ratings artifact and seed an empty engine"
```

---

### Task 13: Fit the live intensity multiplier

**Files:**
- Create: `backend/app/live/fit.py`
- Test: `tests/test_live_fit.py` (create)

**Interfaces:**
- Produces: `fit_intensity(rows) -> dict`.
- Produces: `fetch_export_rows(url, key, days=90) -> list[dict]`.
- Consumes: `/live/export` rows with `remaining_*_lambda` and `extra_*_goals`.

- [ ] **Step 1: Write the failing tests**

```python
"""The intensity fit promotes only a material, validated shift."""
import math

from app.live.fit import fit_intensity


def _row(home_extra, away_extra, home_lambda=1.0, away_lambda=1.0):
    return {
        "remaining_home_lambda": home_lambda,
        "remaining_away_lambda": away_lambda,
        "extra_home_goals": home_extra,
        "extra_away_goals": away_extra,
    }


def test_too_few_snapshots_stays_insufficient():
    result = fit_intensity([_row(2, 1) for _ in range(499)])
    assert result["status"] == "insufficient_sample"


def test_a_neutral_multiplier_is_rejected():
    rows = [_row(1, 1) for _ in range(600)]
    result = fit_intensity(rows)
    assert result["status"] != "fitted"
    assert abs(result["k"] - 1.0) < 0.01


def test_a_material_shift_promotes():
    rows = [_row(2, 1) for _ in range(360)] + [_row(1, 1) for _ in range(240)]
    result = fit_intensity(rows)
    assert result["status"] == "fitted"
    assert 1.25 <= result["k"] <= 1.35
    assert result["validation"]["improvement"] > 0


def test_k_is_clamped_to_the_supported_band():
    rows = [_row(4, 3) for _ in range(600)]
    result = fit_intensity(rows)
    assert result["k"] <= 2.0
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_fit.py -v`

Expected: FAIL — `ModuleNotFoundError: app.live.fit`.

- [ ] **Step 3: Implement the fit**

`backend/app/live/fit.py`:

```python
"""Fit the one live knob from settled snapshots.

The multiplier is the ratio of goals actually scored after a snapshot to the
goals the frozen prior expected. It is promoted only when there is enough
evidence, the held-out Poisson likelihood improves, and the shift is large
enough to matter.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List

from app.live.engine import INTENSITY_BOUNDS

CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "data" / "live_calibration.json"
MIN_SNAPSHOTS = 500
MIN_IMPROVEMENT = 0.002
MIN_SHIFT = 0.05
VALIDATION_FRACTION = 0.30


def _poisson_logpmf(goals: int, mean: float) -> float:
    mean = max(mean, 1e-9)
    return goals * math.log(mean) - mean - math.lgamma(goals + 1)


def _mean_log_likelihood(rows: List[Dict[str, Any]], intensity: float) -> float:
    total = 0.0
    count = 0
    for row in rows:
        for side in ("home", "away"):
            mean = float(row.get(f"remaining_{side}_lambda") or 0.0) * intensity
            goals = int(row.get(f"extra_{side}_goals") or 0)
            total += _poisson_logpmf(goals, mean)
            count += 1
    return total / count if count else 0.0


def fit_intensity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [
        row
        for row in rows
        if (row.get("remaining_home_lambda") or 0) > 0
        or (row.get("remaining_away_lambda") or 0) > 0
    ]
    if not usable:
        return {"status": "no_data", "k": 1.0, "sample": 0}

    total_extra = sum(
        (row.get("extra_home_goals") or 0) + (row.get("extra_away_goals") or 0)
        for row in usable
    )
    total_remaining = sum(
        (row.get("remaining_home_lambda") or 0.0)
        + (row.get("remaining_away_lambda") or 0.0)
        for row in usable
    )
    if total_remaining <= 0:
        return {"status": "no_data", "k": 1.0, "sample": len(usable)}

    k = total_extra / total_remaining
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], k))

    split = max(1, int(len(usable) * (1.0 - VALIDATION_FRACTION)))
    validation = usable[split:]
    fitted_ll = _mean_log_likelihood(validation, k)
    base_ll = _mean_log_likelihood(validation, 1.0)
    improvement = fitted_ll - base_ll

    if len(usable) < MIN_SNAPSHOTS:
        status = "insufficient_sample"
    elif improvement < MIN_IMPROVEMENT:
        status = "rejected"
    elif abs(k - 1.0) < MIN_SHIFT:
        status = "rejected"
    else:
        status = "fitted"

    return {
        "status": status,
        "k": round(k, 4),
        "sample": len(usable),
        "validation": {
            "n": len(validation),
            "improvement": round(improvement, 6),
        },
        "generated_at": None,
    }


def fetch_export_rows(url: str, api_key: str, days: int = 90) -> List[Dict[str, Any]]:
    import requests

    headers = {"X-API-Key": api_key} if api_key else {}
    response = requests.get(
        f"{url.rstrip('/')}/live/export",
        params={"days": days},
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("rows") or []
    return rows if isinstance(rows, list) else []
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_live_fit.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/live/fit.py tests/test_live_fit.py
git commit -m "feat(live): fit and gate the intensity multiplier"
```

---

### Task 14: Nightly trainer and a refresh window on ingestion

**Files:**
- Modify: `backend/app/data_sources/seeder.py` (add `seasons_to_refresh`)
- Modify: `scripts/ingest_history.py:42-63`
- Modify: `backend/app/analytics/learning.py:282-287`
- Modify: `backend/app/analytics/blending.py:422-427`
- Create: `scripts/train_nightly.py`
- Test: `tests/test_nightly_helpers.py` (create)

**Interfaces:**
- Produces: `seasons_to_refresh(seasons, count) -> set[str]`.
- Produces: `save_report(report, path=None)` for blending and learning.
- Produces: `scripts/train_nightly.py --out-dir --window-days --refit-days --skip-ingest --dry-run`.

- [ ] **Step 1: Write the failing helper test**

```python
"""The nightly run refreshes only the newest seasons it needs."""
from app.data_sources.seeder import seasons_to_refresh


def test_refresh_window_takes_the_newest_seasons():
    seasons = ["2122", "2223", "2324", "2425", "2526"]
    assert seasons_to_refresh(seasons, 2) == {"2425", "2526"}
    assert seasons_to_refresh(seasons, 0) == set()
    assert seasons_to_refresh(seasons, 10) == set(seasons)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_nightly_helpers.py -v`

Expected: FAIL — `ImportError: cannot import name 'seasons_to_refresh'`.

- [ ] **Step 3: Add the helper and the CLI flag**

In `backend/app/data_sources/seeder.py`, after `history_season_codes`:

```python
def seasons_to_refresh(seasons: List[str], count: int) -> set:
    """The newest ``count`` season codes, which are always re-downloaded."""
    if count <= 0:
        return set()
    return set(seasons[-count:])
```

In `scripts/ingest_history.py`, add the argument:

```python
    parser.add_argument(
        "--refresh-seasons",
        type=int,
        default=0,
        help="Force re-download the newest N seasons (current plus N-1).",
    )
```

Import the helper in the `from app.data_sources.seeder import (...)` block, then after `seasons = ...`:

```python
    refresh = seasons_to_refresh(seasons, args.refresh_seasons)
```

and change the download call:

```python
                    fetched = _download(
                        BASE_URL.format(season=season, division=code),
                        cached,
                        args.force or season in refresh,
                    )
```

- [ ] **Step 4: Let the reports be written to a staging directory**

In `backend/app/analytics/blending.py`:

```python
def save_report(report: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2))
    _REPORT_CACHE["mtime"] = None
    _REPORT_CACHE["report"] = None
```

In `backend/app/analytics/learning.py`:

```python
def save_report(report: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else LEARNING_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True))
    _CACHE["mtime"] = target.stat().st_mtime
    _CACHE["report"] = report
```

Ensure `Path` and `Optional` are imported in both modules.

- [ ] **Step 5: Write the trainer**

`scripts/train_nightly.py`:

```python
#!/usr/bin/env python3
"""Nightly champion/challenger run over a rolling multi-season window.

Every configuration in the grid is replayed on the same leak-free windows.
The winning configuration is promoted only when it clears the gate; the
artifacts are written to ``--out-dir`` so tests run before anything is
committed by the workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.analytics.artifacts import write_ratings
from app.analytics.blending import fit_report
from app.analytics.learning import build_report as build_learning_report
from app.analytics.model_gate import (
    HALF_LIFE_GRID,
    evaluate_config,
    gate_decision,
    load_champion,
    save_champion,
)
from app.analytics.ratings import calculate_ratings_from_fixtures
from app.data_sources.seeder import history_season_codes, seasons_to_refresh
from app.database import SessionLocal
from app.live.fit import fetch_export_rows, fit_intensity
from app.models.fixture import Fixture


def _league_averages(db) -> dict:
    averages = defaultdict(lambda: [0, 0.0, 0.0])
    rows = db.query(
        Fixture.competition, Fixture.home_score, Fixture.away_score
    ).filter(
        Fixture.home_score.isnot(None), Fixture.away_score.isnot(None)
    ).all()
    for competition, home_score, away_score in rows:
        bucket = averages[competition]
        bucket[0] += 1
        bucket[1] += home_score
        bucket[2] += away_score
    return {
        competition: (values[1] / values[0], values[2] / values[0])
        for competition, values in averages.items()
        if values[0]
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _ingest(seasons: list, refresh: int) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ingest_history.py"),
            "--seasons",
            ",".join(seasons),
            "--refresh-seasons",
            str(refresh),
            "--no-ratings",
            "--quiet",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "data"))
    parser.add_argument("--window-days", type=int, default=730)
    parser.add_argument("--refit-days", type=int, default=14)
    parser.add_argument("--seasons", type=int, default=10)
    parser.add_argument("--refresh-seasons", type=int, default=2)
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    current_year = datetime.now(timezone.utc).year
    seasons = history_season_codes(current_year - args.seasons + 1, current_year)
    if not args.skip_ingest:
        _ingest(seasons, args.refresh_seasons)

    db = SessionLocal()
    try:
        results = [
            evaluate_config(
                db,
                half_life_days=half_life,
                window_days=args.window_days,
                refit_days=args.refit_days,
            )
            for half_life in HALF_LIFE_GRID
        ]
        champion = load_champion()
        decision = gate_decision(
            results, (champion or {}).get("config") if champion else None
        )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "training_seasons": len(seasons),
            "window_days": args.window_days,
            "results": results,
            "decision": {
                "promote": decision["promote"],
                "reason": decision["reason"],
            },
        }

        if decision["promote"]:
            winning = decision["challenger"]
            half_life = float(winning["half_life_days"])
            calculate_ratings_from_fixtures(db, replace=True, half_life_days=half_life)

            rating_meta = write_ratings(
                out_dir / "ratings.json",
                db,
                half_life_days=half_life,
                trained_through=datetime.now(timezone.utc).isoformat(),
                league_averages=_league_averages(db),
            )

            blend = fit_report(db, half_life_days=half_life)
            blend_path = out_dir / "blending.json"
            blend_path.write_text(json.dumps(blend, indent=2))

            learning = build_learning_report(db)
            if learning.get("status") == "fitted":
                (out_dir / "learning.json").write_text(
                    json.dumps(learning, indent=2, sort_keys=True)
                )

            if not args.dry_run:
                save_champion(
                    {
                        "artifact_version": 1,
                        "config": {"half_life_days": half_life},
                        "promoted_at": datetime.now(timezone.utc).isoformat(),
                        "trained_through": datetime.now(timezone.utc).isoformat(),
                        "training_seasons": len(seasons),
                        "gate": {
                            "champion_brier": decision["champion"]["brier"],
                            "challenger_brier": winning["brier"],
                            "holdout_matches": winning["n_matches"],
                            "window": f"{args.window_days}d",
                            "per_competition": winning["per_competition"],
                            "reason": decision["reason"],
                        },
                        "artifacts": {
                            "ratings.json": rating_meta["content_hash"],
                        },
                        "git_sha": _git_sha(),
                    }
                )

            report["promoted"] = {
                "half_life_days": half_life,
                "ratings": rating_meta,
                "blend_verdict": blend.get("verdict"),
            }

        engine_rows = []
        url = os.getenv("FIE_ENGINE_URL", "").strip()
        if url:
            engine_rows = fetch_export_rows(url, os.getenv("FIE_API_KEY", ""), days=90)
        live_fit = fit_intensity(engine_rows)
        report["live_intensity"] = live_fit
        if live_fit["status"] == "fitted" and not args.dry_run:
            (out_dir / "live_calibration.json").write_text(
                json.dumps(live_fit, indent=2, sort_keys=True)
            )

        (out_dir / "nightly_report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["decision"]))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

Add `--refresh-seasons` to the ingest call path. The `seasons_to_refresh` helper is used by the ingest script; the trainer passes the raw count.

- [ ] **Step 6: Run the helper test and the existing trainer-facing suites**

Run: `PYTHONPATH=backend .venv/bin/pytest tests/test_nightly_helpers.py tests/test_seeder_history.py tests/test_evaluation.py -v`

Expected: PASS.

- [ ] **Step 7: Smoke-run the trainer against the local database**

Run: `PYTHONPATH=backend .venv/bin/python scripts/train_nightly.py --skip-ingest --dry-run --window-days 730 --out-dir /tmp/fie-learning-smoke`

Expected: exit 0, and `/tmp/fie-learning-smoke/nightly_report.json` contains four `results` entries and a `decision.reason`. This can take several minutes on the full 26-season database; if it exceeds 10 minutes, stop it and record the timing rather than weakening the window.

- [ ] **Step 8: Commit**

```bash
git add backend/app/data_sources/seeder.py scripts/ingest_history.py backend/app/analytics/learning.py backend/app/analytics/blending.py scripts/train_nightly.py tests/test_nightly_helpers.py
git commit -m "feat(learning): add the nightly trainer and season refresh window"
```

---

### Task 15: Nightly GitHub Actions workflow

**Files:**
- Create: `.github/workflows/nightly-learning.yml`

**Interfaces:**
- Consumes: `scripts/train_nightly.py`, `backend/requirements.txt`, `tests/`.
- Secrets: `RENDER_DEPLOY_HOOK`; optional `FIE_ENGINE_URL`, `FIE_API_KEY`.

- [ ] **Step 1: Write the workflow**

```yaml
name: Nightly learning

on:
  schedule:
    - cron: "0 1 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: nightly-learning
  cancel-in-progress: false

jobs:
  train:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    env:
      PYTHONPATH: backend
      FIE_ENGINE_URL: ${{ secrets.FIE_ENGINE_URL }}
      FIE_API_KEY: ${{ secrets.FIE_API_KEY }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.txt

      - name: Install dependencies
        run: pip install -r backend/requirements.txt pytest

      - name: Train into staging
        run: python scripts/train_nightly.py --out-dir "$RUNNER_TEMP/learning-out"

      - name: Verify the repository
        run: pytest tests/ -q

      - name: Commit promoted artifacts
        id: commit
        run: |
          changed=false
          for name in ratings.json blending.json learning.json live_calibration.json model_champion.json; do
            if [ -f "$RUNNER_TEMP/learning-out/$name" ]; then
              cp "$RUNNER_TEMP/learning-out/$name" "data/$name"
              if ! git diff --quiet -- "data/$name"; then
                changed=true
              fi
            fi
          done
          echo "changed=$changed" >> "$GITHUB_OUTPUT"
          if [ "$changed" = "true" ]; then
            git config user.name "fie-bot"
            git config user.email "fie-bot@users.noreply.github.com"
            git add data/ratings.json data/blending.json data/learning.json data/model_champion.json
            [ -f data/live_calibration.json ] && git add data/live_calibration.json
            git commit -m "chore(learning): promote the nightly model $(date -u +%F)"
            git push
          fi

      - name: Deploy the engine
        if: steps.commit.outputs.changed == 'true'
        env:
          RENDER_DEPLOY_HOOK: ${{ secrets.RENDER_DEPLOY_HOOK }}
        run: |
          if [ -n "$RENDER_DEPLOY_HOOK" ]; then
            curl -fsS -X POST "$RENDER_DEPLOY_HOOK"
          else
            echo "RENDER_DEPLOY_HOOK is not configured; skipping deploy."
          fi

      - name: Upload the nightly report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: nightly-report
          path: ${{ runner.temp }}/learning-out/nightly_report.json
          if-no-files-found: ignore
```

- [ ] **Step 2: Validate the YAML locally**

Run: `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/nightly-learning.yml').read_text()); print('ok')"`

Expected: `ok`.

- [ ] **Step 3: Confirm the workflow never stages the cache or synthetic reports**

Run: `git status --short`

Expected: no changes under `data/historical_cache/`, and `data/combo_backtest.json` / `data/combo_sweep.json` remain unstaged.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/nightly-learning.yml
git commit -m "ci(learning): run and promote the nightly model"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
| --- | --- |
| §5.1 `live_observed_at` | 1 |
| §5.2 elapsed minute | 2 |
| §5.3 frozen prior | 3 |
| §5.4 conditional model, intensity seam | 4 |
| §5.5 ledger and settlement | 5 |
| §5.6 HTTP + 5-minute job + results settlement | 6 |
| §5.7 API payload, detail history, calibration, export | 6, 7 |
| §5.8 web and iOS surfaces | 9, 10 |
| §5.9 guardrails | 8 |
| §6.1 rolling 10 seasons, half-life grid | 11, 14 |
| §6.2 artifacts | 12, 13, 14 |
| §6.3 champion/challenger gate | 11, 14 |
| §6.4 live intensity fit | 13 |
| §6.5 nightly workflow | 15 |
| §3 no live price, no live bet | No task; the seam is `LivePriceProvider` in the spec only, and no route is shipped |

**Type consistency**

- `outcome_probabilities` returns `home_win` / `draw` / `away_win`; the ledger, report, API and UI all use those keys.
- `lambda_source` is `manual` / `team_ratings` / `market_odds` / `fallback` / `mixed`; only `fallback` (and missing lambdas) suppresses a live row.
- `record_live_snapshots` returns `{"fixtures", "written", "skipped"}`; `/fixtures/live` ignores the return and reads rows.
- `settle_live_predictions` returns `{"settled"}`; `update_results` stores it under `live_settled`.
- `gate_decision` consumes the exact dicts `evaluate_config` produces (`half_life_days`, `n_matches`, `brier`, `log_loss`, `per_competition`).
- `write_ratings` returns `content_hash`; the trainer writes it into `model_champion.json`.
- iOS `LivePrediction` matches the JSON keys after `convertFromSnakeCase`.
