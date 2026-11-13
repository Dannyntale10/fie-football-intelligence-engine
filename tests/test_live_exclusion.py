"""An in-play match is never a selection, even with an absurd edge.

Every assertion is paired with a control: an identical scheduled fixture that
the same builder does return. Without the control the test would still pass if
the builder could not see the fixture at all.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.betting import daily_combos as dc
from app.betting.combo import top_picks_for_matchday
from app.betting.daily_combos import build_daily_combos
from app.betting.high_odds import build_high_odds
from app.database import SessionLocal
from app.models.fixture import Fixture
from app.notifications.telegram import format_daily_report

PREFIX = "live-exclusion-"
NOW = datetime.now(timezone.utc)
KICKOFF = NOW + timedelta(hours=5)


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


def _market():
    stamp = (NOW - timedelta(minutes=30)).isoformat()
    return json.dumps(
        {
            key: {
                "price": price,
                "bookmaker": "BetPawa",
                "source": "BETPAWA",
                "source_url": "https://www.betpawa.ug/",
                "captured_at": stamp,
            }
            for key, price in (("home_win", 1.02), ("draw", 21.0), ("away_win", 51.0))
        }
    )


def _fixture(key, status):
    return Fixture(
        dedupe_key=f"{PREFIX}{key}-{NOW.timestamp()}",
        kickoff_utc=KICKOFF,
        home_team=f"Exclusion {key} Home",
        away_team=f"Exclusion {key} Away",
        competition="Exclusion League",
        country="Test",
        source="BETPAWA",
        status=status,
        home_odds=1.02,
        draw_odds=21.0,
        away_odds=51.0,
        max_home_odds=1.02,
        max_draw_odds=21.0,
        max_away_odds=51.0,
        market_odds_json=_market(),
        odds_updated_at=NOW - timedelta(hours=1),
    )


@pytest.fixture
def fixtures():
    db = SessionLocal()
    control = _fixture("control", "scheduled")
    live = _fixture("live", "live")
    db.add_all([control, live])
    db.commit()
    db.refresh(control)
    db.refresh(live)
    # Derived with the same helper the builders use: SQLite hands the kickoff
    # back naive, and a bare astimezone would read it as local time.
    target_date = dc.as_utc(control.kickoff_utc).astimezone(dc.EAT_TZ).strftime("%Y-%m-%d")
    ids = control.id, live.id
    db.close()

    yield ids, target_date

    db = SessionLocal()
    try:
        db.query(Fixture).filter(Fixture.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _isolate_learning(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "learning_path", lambda: tmp_path / "combo_learning.json")


def test_live_fixture_is_absent_from_every_selection(fixtures):
    (control_id, live_id), target_date = fixtures
    db = SessionLocal()
    try:
        reports = [
            # "all" avoids the matchday grouping: the stored kickoff comes back
            # naive, and grouping it by EAT date depends on the machine's clock.
            top_picks_for_matchday(db, target_date="all", limit=500),
            build_daily_combos(db, target_date=target_date, now=NOW),
            build_high_odds(db, target_date=target_date, now=NOW),
        ]
        for report in reports:
            found = _ids(report)
            assert control_id in found, "the builder cannot see the scheduled twin"
            assert live_id not in found
    finally:
        db.close()


def test_the_digest_never_names_a_live_match():
    """The push quotes the combo, so an in-play match must not reach it either.

    Two scheduled controls are seeded so a 2-leg ticket exists; a single
    control plus the live match would produce no ticket at all.
    """
    db = SessionLocal()
    controls = [_fixture("digest-a", "scheduled"), _fixture("digest-b", "scheduled")]
    live = _fixture("digest-live", "live")
    db.add_all([*controls, live])
    db.commit()
    for row in [*controls, live]:
        db.refresh(row)
    competition = controls[0].competition
    target_date = dc.as_utc(controls[0].kickoff_utc).astimezone(dc.EAT_TZ).strftime(
        "%Y-%m-%d"
    )
    ids = [row.id for row in [*controls, live]]
    db.close()

    try:
        db = SessionLocal()
        try:
            report = build_daily_combos(
                db, target_date=target_date, competition=competition, now=NOW
            )
            digest = format_daily_report(report)
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(Fixture).filter(Fixture.id.in_(ids)).delete(
                synchronize_session=False
            )
            db.commit()
        finally:
            db.close()

    assert "2-leg" in digest, digest
    assert "Exclusion digest-a Home" in digest
    assert "Exclusion digest-b Home" in digest
    assert "Exclusion digest-live Home" not in digest
