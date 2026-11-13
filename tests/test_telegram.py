"""Telegram allowlist, commands and notification delivery."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models.fixture import Fixture
from app.models.predictions import Notification
from app.notifications import telegram


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def allowlist(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42,99")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")


def test_bot_is_closed_without_allowlist(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    assert telegram.is_authorized(42) is False
    assert telegram.is_authorized("42") is False


def test_allowlist_accepts_configured_ids(allowlist):
    assert telegram.is_authorized(42) is True
    assert telegram.is_authorized("99") is True
    assert telegram.is_authorized(7) is False


def test_send_message_reports_missing_configuration(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert telegram.send_message("hello", chat_id=42)["status"] == "not_configured"


def test_send_message_uses_transport(allowlist):
    calls = []

    def transport(url, payload):
        calls.append((url, payload))
        return {"ok": True}

    result = telegram.send_message("hello", chat_id=42, transport=transport)
    assert result["status"] == "sent"
    assert calls[0][1]["chat_id"] == "42"


def test_help_and_unknown_commands(db):
    assert "FIE commands" in telegram.handle_command("/help", db)
    assert "Unknown command" in telegram.handle_command("/nonsense", db)


def test_today_lists_fixture(db):
    fixture = Fixture(
        dedupe_key="telegram-test-fixture",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(hours=1),
        home_team="KCCA",
        away_team="Vipers",
        competition="UPL",
        country="Uganda",
        source="test",
    )
    db.add(fixture)
    db.commit()
    try:
        text = telegram.handle_command("/today", db)
        assert "KCCA vs Vipers" in text
    finally:
        db.delete(fixture)
        db.commit()


def test_performance_and_sources_commands(db):
    assert "Real slips only" in telegram.handle_command("/performance", db)
    sources = telegram.handle_command("/sources", db)
    assert "api_football" in sources
    settings = telegram.handle_command("/settings", db)
    assert "0.85" in settings


def _leg(selection: str, match: str, odds: float, bookmaker: str) -> dict:
    return {
        "selection": selection,
        "match": match,
        "odds": odds,
        "bookmaker": bookmaker,
        "captured_at": None,
    }


def _report(**overrides) -> dict:
    two = {
        "size": 2,
        "total_odds": 1.82,
        "combined_probability": 0.612,
        "expected_value_percent": -3.4,
        "legs": [
            _leg("KCCA to win", "KCCA vs Vipers", 1.35, "Betika"),
            _leg("Draw", "SC Villa vs Express", 1.35, "BetPawa"),
        ],
    }
    three = {
        "size": 3,
        "total_odds": 2.46,
        "combined_probability": 0.407,
        "expected_value_percent": -7.1,
        "legs": [
            _leg("KCCA to win", "KCCA vs Vipers", 1.35, "Betika"),
            _leg("Draw", "SC Villa vs Express", 1.35, "BetPawa"),
            _leg("Vipers to win", "Onduparaka vs Vipers", 1.35, "Betway"),
        ],
    }
    report = {
        "matchday_label": "Friday, 13 Nov 2026",
        "target_date": "2026-11-13",
        "params": {"min_leg_probability": 0.75},
        "tier": {"below_published_gate": True, "published_gate": 0.85},
        "scope": {"qualified_legs": 5},
        "combos": {"2_leg": [two], "3_leg": [three]},
        "best": two,
        "message": "5 legs at or above 75%; best combo 61.2%",
    }
    report.update(overrides)
    return report


def test_format_daily_report_quotes_tickets_and_gate():
    text = telegram.format_daily_report(_report())
    assert "Friday, 13 Nov 2026" in text
    assert "75% market tier" in text
    assert "below the published 85% gate" in text
    assert "2-leg: 1.82 odds" in text
    assert "3-leg: 2.46 odds" in text
    assert "KCCA to win | KCCA vs Vipers @ 1.35 (Betika)" in text
    assert "EV -3.4%" in text
    assert "Paper analysis only" in text


def test_format_daily_report_single_size_and_empty():
    report = _report()
    only_two = telegram.format_daily_report(report, size=2)
    assert "2-leg" in only_two
    assert "3-leg" not in only_two

    empty = telegram.format_daily_report(
        _report(combos={"2_leg": [], "3_leg": []}, best=None, message=telegram.NO_QUALIFIED)
    )
    assert telegram.NO_QUALIFIED in empty
    assert "Paper analysis only" not in empty


def _pending(db, **fields) -> Notification:
    db.query(Notification).filter(Notification.status == "pending").delete()
    row = Notification(channel="telegram", status="pending", **fields)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_deliver_pending_marks_sent(allowlist, db):
    row = _pending(db, subject="Daily shortlist", body="Leg one")
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return {"ok": True}

    try:
        result = telegram.deliver_pending(db, transport=transport)
        db.refresh(row)
        assert result["status"] == "ok"
        assert result["sent"] == 1
        assert row.status == "sent"
        assert row.sent_at is not None
        assert calls[0]["chat_id"] == "42"
        assert calls[0]["text"].startswith("Daily shortlist")
    finally:
        db.delete(row)
        db.commit()


def test_deliver_pending_marks_failed(allowlist, db):
    row = _pending(db, body="Leg one")

    try:
        result = telegram.deliver_pending(db, transport=lambda url, payload: {"ok": False})
        db.refresh(row)
        assert result["failed"] == 1
        assert row.status == "failed"
    finally:
        db.delete(row)
        db.commit()


def test_deliver_pending_leaves_rows_alone_without_token(monkeypatch, db):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    row = _pending(db, body="Leg one")

    try:
        result = telegram.deliver_pending(db, transport=lambda url, payload: {"ok": True})
        db.refresh(row)
        assert result["status"] == "not_configured"
        assert row.status == "pending"
    finally:
        db.delete(row)
        db.commit()


def test_deliver_pending_ignores_unlisted_recipient(allowlist, db):
    row = _pending(db, recipient="666", body="Leg one")
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return {"ok": True}

    try:
        telegram.deliver_pending(db, transport=transport)
        db.refresh(row)
        assert row.status == "sent"
        assert calls[0]["chat_id"] == "42"
    finally:
        db.delete(row)
        db.commit()


def test_combo_command_uses_daily_engine(db, monkeypatch):
    monkeypatch.setattr(
        "app.betting.daily_combos.build_daily_combos", lambda db, **kwargs: _report()
    )
    text = telegram.handle_command("/combo 2", db)
    assert "KCCA to win" in text
    assert "2-leg" in text
    assert "3-leg" not in text
    assert "/combo [2|3]" in telegram.handle_command("/combo 4", db)
    shortlist = telegram.handle_command("/accumulators", db)
    assert "2-leg" in shortlist and "3-leg" in shortlist


def test_combo_command_reports_empty_size(db, monkeypatch):
    empty = _report(
        combos={"2_leg": [], "3_leg": []}, best=None, message=telegram.NO_QUALIFIED
    )
    monkeypatch.setattr("app.betting.daily_combos.build_daily_combos", lambda db, **kwargs: empty)
    assert telegram.NO_QUALIFIED in telegram.handle_command("/combo 3", db)
    assert telegram.NO_QUALIFIED in telegram.handle_command("/combo", db)


def test_learning_and_backtest_commands(monkeypatch):
    monkeypatch.setattr(
        "app.analytics.learning.summary",
        lambda: {
            "trained": True,
            "version": "learn-v2",
            "trained_at": "2026-11-12T00:00:00+00:00",
            "sources": {"settled_legs": 0, "walkforward_matches": 500},
            "applied_markets": ["home_win"],
            "edge_premiums": {"home_win": {"premium": 1.03, "n": 120}},
            "notes": [],
        },
    )
    learning = telegram.handle_command("/learning", None)
    assert "learn-v2" in learning
    assert "home_win +3.0%" in learning
    assert "500 walk-forward matches" in learning

    monkeypatch.setattr(
        "app.analytics.combo_backtest.load_report",
        lambda: {
            "status": "ok",
            "params": {"min_leg_probability": 0.75},
            "window": {
                "first_matchday": "2026-10-01",
                "last_matchday": "2026-11-10",
                "matchdays": 41,
                "settled_matches": 300,
            },
            "combos": {
                "holdout": {
                    "2_leg": {
                        "count": 12,
                        "hit_rate": 0.583,
                        "average_predicted_probability": 0.56,
                        "roi_percent": -4.2,
                    }
                }
            },
            "learning": {"applied": False},
        },
    )
    monkeypatch.setattr(
        "app.analytics.combo_backtest.load_sweep",
        lambda: {
            "status": "ok",
            "recommendation": {
                "tier": 0.80,
                "action": "keep_default",
                "holdout_confirmed": False,
                "holdout": {"count": 17, "hit_rate": 0.765, "roi_percent": -1.56},
                "guidance": (
                    "The live engine keeps 75%: a tier that wins only on training "
                    "is not enough to change it."
                ),
            },
        },
    )
    replay = telegram.handle_command("/backtest", None)
    assert "41 matchdays" in replay
    assert "hit 58.3%" in replay
    assert "ROI -4.20%" in replay
    assert "not applied" in replay
    assert "Tier sweep: 80% leads on training" in replay
    assert "not confirmed; live default unchanged" in replay
    assert "keeps 75%" in replay


def test_value_command_quotes_only_attributed_prices(db):
    fixture = Fixture(
        dedupe_key="telegram-value-fixture",
        kickoff_utc=datetime.now(timezone.utc) + timedelta(hours=2),
        home_team="KCCA",
        away_team="Vipers",
        competition="UPL",
        country="Uganda",
        source="test",
        market_odds_json=json.dumps(
            {
                "home_win": {"price": 1.85, "bookmaker": "Betika", "captured_at": None},
                "draw": {"price": 3.4, "bookmaker": "Betika", "captured_at": None},
            }
        ),
    )
    db.add(fixture)
    db.commit()
    try:
        text = telegram.handle_command(f"/value {fixture.id}", db)
        assert "1.85 (Betika)" in text
        assert "Vipers to win" not in text  # no attributed away price, so not invented
    finally:
        db.delete(fixture)
        db.commit()


def test_poll_once_refuses_strangers_and_answers_allowed(allowlist):
    replies = []

    def transport(url, payload):
        if "getUpdates" in url:
            return {
                "result": [
                    {"update_id": 10, "message": {"chat": {"id": 7}, "text": "/help"}},
                    {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/help"}},
                ]
            }
        replies.append(payload)
        return {"ok": True}

    session = SessionLocal()
    try:
        next_offset = telegram.poll_once(session, offset=None, transport=transport)
    finally:
        session.close()
    assert next_offset == 12
    assert [reply["chat_id"] for reply in replies] == ["42"]
    assert "Not authorized" not in replies[0]["text"]


def test_queue_message_dedupes_by_key(db):
    first = telegram.queue_message(db, "Daily shortlist", "Leg one", dedupe_key="shortlist:t1")
    second = telegram.queue_message(db, "Daily shortlist", "Leg one", dedupe_key="shortlist:t1")
    try:
        assert first is not None
        assert second is None
        rows = db.query(Notification).filter(Notification.dedupe_key == "shortlist:t1").all()
        assert len(rows) == 1
    finally:
        db.query(Notification).filter(Notification.dedupe_key == "shortlist:t1").delete()
        db.commit()
