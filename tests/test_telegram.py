"""Telegram allowlist, commands and notification delivery."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import SessionLocal
from app.models.fixture import Fixture
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
