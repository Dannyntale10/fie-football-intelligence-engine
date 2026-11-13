# Bot Reliability (Phase A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Telegram delivery stateful and retryable, run inbound commands inside the API process, retire the legacy WhatsApp sentinel without losing settlement or ledger recording, and make the allowlist honour settings.

**Architecture:** Delivery state lives on the `Notification` row (`attempts`, `next_attempt_at`, `last_error`, `dedupe_key`), so a 2-minute sweep can retry, dedupe and explain failures. A FastAPI lifespan starts one guarded long-poll thread that persists its offset in `SystemSetting`. The scheduler becomes the only automatic producer: it records published shortlist tickets, settles tracked bets, and queues Telegram notifications instead of sending WhatsApp.

**Tech Stack:** Python 3.9, FastAPI, SQLAlchemy 2.x, SQLite, APScheduler, pytest, Streamlit (legacy dashboard), Next.js settings page.

**Spec:** `docs/superpowers/specs/2026-11-13-bot-reliability-and-app-launch-design.md`

## Global Constraints

- The published 85% qualification gate is never lowered or bypassed.
- No bet is placed automatically; the bot is read-only.
- Never fabricate a fixture, price, score, injury, or statistic.
- Every stored price keeps its bookmaker and capture time.
- Do not delete synthetic ledger rows; that remains a separate approval-gated task.
- No secrets in commits; no `.env` or `sentinel_state.json` staging.
- Run tests from the repo root with `.venv/bin/pytest -q`.
- Commit after each task; do not push until the final verification task.
- Concurrent sessions share this working tree: run `git status --short` before staging and stage only the files named in the task.

---

### Task 1: Add notification and tracked-bet state columns

**Files:**
- Modify: `backend/app/models/predictions.py:148-160`
- Modify: `backend/app/models/bet.py:27-32`
- Modify: `backend/app/database.py:1-64`
- Test: `tests/test_migrations.py` (create)

**Interfaces:**
- Produces: `Notification.attempts: int`, `Notification.next_attempt_at: datetime | None`, `Notification.last_error: str | None`, `Notification.dedupe_key: str | None`.
- Produces: `TrackedBet.dedupe_key: str | None`, `TrackedBet.tier: float | None`, `TrackedBet.settlement_notified: bool`.
- Produces: partial unique indexes `ux_notifications_dedupe_key` and `ux_tracked_bets_dedupe_key`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migrations.py`:

```python
"""Legacy SQLite tables gain the delivery and settlement columns."""
from __future__ import annotations

from sqlalchemy import create_engine, text

from app.database import ensure_schema_compatibility


def test_legacy_tables_gain_columns_and_backfill(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE notifications (id INTEGER PRIMARY KEY, channel TEXT, "
            "recipient TEXT, subject TEXT, body TEXT, status TEXT, related_type TEXT, "
            "related_id INTEGER, sent_at TIMESTAMP, created_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE TABLE tracked_bets (id INTEGER PRIMARY KEY, slip_type TEXT, "
            "matchday_label TEXT, status TEXT, whatsapp_settlement_sent BOOLEAN DEFAULT 0)"
        ))
        conn.execute(text(
            "INSERT INTO tracked_bets (id, slip_type, matchday_label, status, whatsapp_settlement_sent) "
            "VALUES (1, '2-Team Combo', 'Friday', 'WON', 1)"
        ))

    ensure_schema_compatibility(engine)

    with engine.connect() as conn:
        notif_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(notifications)"))}
        bet_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(tracked_bets)"))}
        indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(notifications)"))}
        settled = conn.execute(text("SELECT settlement_notified FROM tracked_bets WHERE id = 1")).scalar()

    assert {"attempts", "next_attempt_at", "last_error", "dedupe_key"} <= notif_cols
    assert {"dedupe_key", "tier", "settlement_notified"} <= bet_cols
    assert "ux_notifications_dedupe_key" in indexes
    assert settled == 1
```

- [ ] **Step 2: Run it and confirm failure**

```bash
.venv/bin/pytest tests/test_migrations.py -q
```

Expected: FAIL because the columns and indexes do not exist.

- [ ] **Step 3: Add the model columns**

In `backend/app/models/predictions.py`, inside `Notification` after `related_id`:

```python
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
```

In `backend/app/models/bet.py`, after `whatsapp_settlement_sent`:

```python
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tier: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    settlement_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
```

- [ ] **Step 4: Extend the migration helper**

At the top of `backend/app/database.py`, add:

```python
import logging

logger = logging.getLogger(__name__)
```

Inside `ensure_schema_compatibility`, after the existing `tracked_bets` block, add the notification block, and extend the tracked-bets block. Replace the whole function body with:

```python
def ensure_schema_compatibility(eng):
    """Safely add newly introduced columns to existing SQLite tables if not present."""
    try:
        with eng.connect() as conn:
            res = conn.execute(text("PRAGMA table_info(fixtures)")).fetchall()
            if res:
                col_names = {row[1] for row in res}
                new_cols = [
                    ("opening_home_odds", "REAL"),
                    ("opening_draw_odds", "REAL"),
                    ("opening_away_odds", "REAL"),
                    ("odds_updated_at", "TIMESTAMP"),
                    ("market_odds_json", "TEXT"),
                ]
                for col, col_type in new_cols:
                    if col not in col_names:
                        conn.execute(text(f"ALTER TABLE fixtures ADD COLUMN {col} {col_type}"))
                conn.commit()

            res_tr = conn.execute(text("PRAGMA table_info(team_ratings)")).fetchall()
            if res_tr:
                tr_cols = {row[1] for row in res_tr}
                new_tr_cols = [
                    ("home_attack_boost", "REAL DEFAULT 0.0"),
                    ("home_goals_avg", "REAL DEFAULT 0.0"),
                    ("away_goals_avg", "REAL DEFAULT 0.0"),
                    ("weighted_matches", "REAL DEFAULT 0.0"),
                    ("last_match_at", "TIMESTAMP"),
                ]
                for col, col_type in new_tr_cols:
                    if col not in tr_cols:
                        conn.execute(text(f"ALTER TABLE team_ratings ADD COLUMN {col} {col_type}"))
                conn.commit()

            res_notif = conn.execute(text("PRAGMA table_info(notifications)")).fetchall()
            if res_notif:
                notif_cols = {row[1] for row in res_notif}
                new_notif_cols = [
                    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
                    ("next_attempt_at", "TIMESTAMP"),
                    ("last_error", "VARCHAR(400)"),
                    ("dedupe_key", "VARCHAR(200)"),
                ]
                for col, col_type in new_notif_cols:
                    if col not in notif_cols:
                        conn.execute(text(f"ALTER TABLE notifications ADD COLUMN {col} {col_type}"))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_dedupe_key "
                    "ON notifications (dedupe_key) WHERE dedupe_key IS NOT NULL"
                ))
                conn.commit()

            res_bets = conn.execute(text("PRAGMA table_info(tracked_bets)")).fetchall()
            if res_bets:
                bet_cols = {row[1] for row in res_bets}
                had_settlement = "settlement_notified" in bet_cols
                new_bet_cols = [
                    ("model_version", "TEXT"),
                    ("ratings_hash", "TEXT"),
                    ("selection_mode", "TEXT"),
                    ("dedupe_key", "VARCHAR(200)"),
                    ("tier", "REAL"),
                    ("settlement_notified", "INTEGER NOT NULL DEFAULT 0"),
                ]
                for col, col_type in new_bet_cols:
                    if col not in bet_cols:
                        conn.execute(text(f"ALTER TABLE tracked_bets ADD COLUMN {col} {col_type}"))
                if not had_settlement and "whatsapp_settlement_sent" in bet_cols:
                    conn.execute(text(
                        "UPDATE tracked_bets SET settlement_notified = "
                        "CASE WHEN whatsapp_settlement_sent = 1 THEN 1 ELSE 0 END"
                    ))
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_tracked_bets_dedupe_key "
                    "ON tracked_bets (dedupe_key) WHERE dedupe_key IS NOT NULL"
                ))
                conn.commit()
    except Exception:
        logger.exception("Schema compatibility migration failed")
```

- [ ] **Step 5: Run the migration test and the schema suite**

```bash
.venv/bin/pytest tests/test_migrations.py tests/test_schema.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/predictions.py backend/app/models/bet.py backend/app/database.py tests/test_migrations.py
git commit -m "feat(notifications): add delivery state and settlement columns"
```

---

### Task 2: Add a deduped queue helper

**Files:**
- Modify: `backend/app/notifications/telegram.py:1-25` and after `send_message`
- Modify: `backend/app/tasks/scheduler.py:35-60`, `build_shortlist`, `daily_analytics`, `refit_blend`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `telegram.queue_message(db, subject, body, *, dedupe_key, related_type=None, related_id=None, channel="telegram") -> int | None`.
- Produces: `scheduler._notify(subject, body, *, dedupe_key, related_type=None, related_id=None) -> None`.
- Produces: `scheduler._ticket_signature(ticket: Dict[str, Any]) -> str` and `scheduler._shortlist_dedupe(report: Dict[str, Any]) -> str` (both reused by Task 7).

- [ ] **Step 1: Write the failing dedupe test**

Append to `tests/test_telegram.py`:

```python
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
```

- [ ] **Step 2: Run it and confirm failure**

```bash
.venv/bin/pytest tests/test_telegram.py::test_queue_message_dedupes_by_key -q
```

Expected: FAIL with `AttributeError: module ... has no attribute 'queue_message'`.

- [ ] **Step 3: Implement `queue_message`**

In `backend/app/notifications/telegram.py`, add `from sqlalchemy.exc import IntegrityError` to the imports, then add this function immediately after `send_message`:

```python
def queue_message(
    db: Session,
    subject: str,
    body: str,
    *,
    dedupe_key: str,
    related_type: Optional[str] = None,
    related_id: Optional[int] = None,
    channel: str = "telegram",
) -> Optional[int]:
    """Insert a pending row unless one with the same dedupe key already exists."""
    from app.models.predictions import Notification

    existing = db.scalar(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return None
    row = Notification(
        channel=channel,
        subject=subject,
        body=body,
        status="pending",
        related_type=related_type,
        related_id=related_id,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(row)
    return row.id
```

- [ ] **Step 4: Route the scheduler through it**

In `backend/app/tasks/scheduler.py`, replace `_notify` with:

```python
def _notify(
    subject: str,
    body: str,
    *,
    dedupe_key: str,
    related_type: Optional[str] = None,
    related_id: Optional[int] = None,
) -> None:
    try:
        from app.notifications.telegram import queue_message

        db = _session()
        try:
            queue_message(
                db,
                subject,
                body,
                dedupe_key=dedupe_key,
                related_type=related_type,
                related_id=related_id,
            )
        finally:
            db.close()
    except Exception as error:  # pragma: no cover - notification is best effort
        logger.warning("[Scheduler] notification not queued: %s", error)
```

Update the four call sites. First add two module-level helpers directly above `_notify`:

```python
def _ticket_signature(ticket: Dict[str, Any]) -> str:
    import hashlib

    legs = [
        (leg.get("fixture_id"), leg.get("market_key"), leg.get("odds"))
        for leg in ticket.get("legs", [])
    ]
    raw = json.dumps([ticket.get("size"), legs], default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _shortlist_dedupe(report: Dict[str, Any]) -> str:
    combos = report.get("combos") or {}
    parts = [
        _ticket_signature(tickets[0])
        for key in ("2_leg", "3_leg")
        for tickets in (combos.get(key) or [])[:1]
    ]
    day = report.get("target_date") or report.get("matchday_label") or "today"
    return f"shortlist:{day}:{'_'.join(parts) or 'empty'}"
```

Add `import json` alongside the existing `import logging`/`import os` at the top of `scheduler.py` (`datetime` is already imported).

- In `build_shortlist`, compute `dedupe = _shortlist_dedupe(report)` once, then call `_notify(subject, text, dedupe_key=dedupe)` for the empty branch and `_notify(subject, text, dedupe_key=dedupe, related_type="combos")` for the non-empty branch.
- In `daily_analytics`, pass `dedupe_key=f"analytics:{datetime.now(EAT_TZ).strftime('%Y-%m-%d')}"` to the existing `_notify(...)` call.
- In `refit_blend`, pass `dedupe_key=f"blend:{datetime.now(EAT_TZ).strftime('%Y-%m-%d')}:{report['weights']['default']:.3f}"`.

Those are the only four `_notify(` call sites in the module; every one must pass `dedupe_key` because the parameter is keyword-only and required.

- In `daily_analytics`, use:
  `dedupe_key=f"analytics:{datetime.now(EAT_TZ).strftime('%Y-%m-%d')}"`.
- In `refit_blend`, use:
  `dedupe_key=f"blend:{datetime.now(EAT_TZ).strftime('%Y-%m-%d')}:{report['weights']['default']:.3f}"`.

Import `datetime, timezone, timedelta` are already present in scheduler; `EAT_TZ` is defined.

- [ ] **Step 5: Run the telegram and scheduler-related tests**

```bash
.venv/bin/pytest tests/test_telegram.py tests/test_new_features.py -q
```

Expected: PASS (the old tests still pass because `_notify` keeps its role).

- [ ] **Step 6: Commit**

```bash
git add backend/app/notifications/telegram.py backend/app/tasks/scheduler.py tests/test_telegram.py
git commit -m "feat(notifications): dedupe queued messages by content key"
```

---

### Task 3: Resolve the allowlist from settings with env override

**Files:**
- Modify: `backend/app/notifications/telegram.py:42-52`
- Modify: `backend/app/notifications/telegram.py::poll_once`, `deliver_pending`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `telegram.allowed_chat_ids(db=None) -> List[str]`.
- Produces: `telegram.is_authorized(chat_id, db=None) -> bool`.
- Produces: `telegram.allowlist_source(db=None) -> str` returning `"env"`, `"settings"`, or `"empty"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_allowlist_reads_settings_when_env_is_empty(monkeypatch, db):
    from app.models.predictions import SystemSetting

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    row = SystemSetting(key="telegram_allowlist", value="7, 8")
    db.add(row)
    db.commit()
    try:
        assert telegram.allowed_chat_ids(db) == ["7", "8"]
        assert telegram.is_authorized(7, db) is True
        assert telegram.allowlist_source(db) == "settings"
    finally:
        db.delete(row)
        db.commit()


def test_allowlist_env_overrides_settings(monkeypatch, db):
    from app.models.predictions import SystemSetting

    row = SystemSetting(key="telegram_allowlist", value="7")
    db.add(row)
    db.commit()
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    try:
        assert telegram.allowed_chat_ids(db) == ["42"]
        assert telegram.is_authorized(7, db) is False
        assert telegram.allowlist_source(db) == "env"
    finally:
        db.delete(row)
        db.commit()
```

- [ ] **Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_telegram.py::test_allowlist_reads_settings_when_env_is_empty -q
```

Expected: FAIL because the setting is ignored.

- [ ] **Step 3: Implement resolution**

Replace the existing `allowed_chat_ids` and `is_authorized` with:

```python
def _split_ids(raw: Optional[str]) -> List[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _setting_allowlist(db: Optional[Session]) -> List[str]:
    if db is None:
        return []
    from app.models.predictions import SystemSetting

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "telegram_allowlist"))
    return _split_ids(row.value if row else "")


def allowed_chat_ids(db: Optional[Session] = None) -> List[str]:
    env_ids = _split_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", ""))
    return env_ids or _setting_allowlist(db)


def is_authorized(chat_id: Any, db: Optional[Session] = None) -> bool:
    allowlist = allowed_chat_ids(db)
    return bool(allowlist) and str(chat_id) in allowlist


def allowlist_source(db: Optional[Session] = None) -> str:
    if _split_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")):
        return "env"
    if _setting_allowlist(db):
        return "settings"
    return "empty"
```

Update `poll_once` to call `is_authorized(chat_id, db)` and `deliver_pending` to resolve the allowlist once with `allowed_chat_ids(db)`.

- [ ] **Step 4: Run the allowlist tests**

```bash
.venv/bin/pytest tests/test_telegram.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/notifications/telegram.py tests/test_telegram.py
git commit -m "feat(telegram): read the allowlist from settings with env override"
```

---

### Task 4: Retry, back off, chunk, and fail closed

**Files:**
- Modify: `backend/app/notifications/telegram.py:1-25`, `send_message`, `_delivery_recipient`, `deliver_pending`
- Modify: `backend/app/notifications/telegram.py::format_daily_report` (remove the `[:4000]` truncation)
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `MAX_DELIVERY_ATTEMPTS = 5`, `BACKOFF_MINUTES = (1, 2, 4, 8, 16)`, `MAX_CHUNK = 3500`.
- Produces: `_chunk_text(text: str, limit: int = MAX_CHUNK) -> List[str]`.
- Produces: `_record_failure(row, message: str, now: datetime) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_unlisted_recipient_fails_closed_without_redirect(allowlist, db):
    row = _pending(db, recipient="666", body="Leg one")
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return {"ok": True}

    try:
        result = telegram.deliver_pending(db, transport=transport)
        db.refresh(row)
        assert calls == []
        assert row.status == "pending"
        assert row.attempts == 1
        assert "not allowlisted" in (row.last_error or "")
        assert result["sent"] == 0
    finally:
        db.delete(row)
        db.commit()


def test_failed_delivery_backs_off_then_caps(allowlist, db):
    row = _pending(db, body="Leg one")
    try:
        for expected_attempt in range(1, 6):
            telegram.deliver_pending(db, transport=lambda url, payload: {"ok": False, "description": "boom"})
            db.refresh(row)
            assert row.attempts == expected_attempt
            if expected_attempt < 5:
                assert row.status == "pending"
                assert row.next_attempt_at is not None
                row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
        assert row.status == "failed"
        assert row.last_error == "boom"
    finally:
        db.delete(row)
        db.commit()


def test_retry_after_429_keeps_row_pending(allowlist, db):
    row = _pending(db, body="Leg one")
    try:
        telegram.deliver_pending(
            db,
            transport=lambda url, payload: {
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 30},
            },
        )
        db.refresh(row)
        assert row.status == "pending"
        assert row.attempts == 0
        assert row.next_attempt_at is not None
    finally:
        db.delete(row)
        db.commit()


def test_long_message_is_chunked(allowlist, db):
    row = _pending(db, body=("x" * 2000 + "\n") * 3)
    calls = []

    def transport(url, payload):
        calls.append(payload["text"])
        return {"ok": True}

    try:
        result = telegram.deliver_pending(db, transport=transport)
        db.refresh(row)
        assert result["sent"] == 1
        assert len(calls) > 1
        assert all(len(chunk) <= telegram.MAX_CHUNK for chunk in calls)
    finally:
        db.delete(row)
        db.commit()
```

Replace `test_deliver_pending_ignores_unlisted_recipient` with the first test above, and replace `test_deliver_pending_marks_failed` with a network-failure version that expects `pending` after one attempt:

```python
def test_deliver_pending_marks_failed(allowlist, db):
    row = _pending(db, body="Leg one")
    try:
        result = telegram.deliver_pending(db, transport=lambda url, payload: {"ok": False})
        db.refresh(row)
        assert result["pending"] == 1
        assert row.status == "pending"
    finally:
        db.delete(row)
        db.commit()
```

- [ ] **Step 2: Confirm the delivery tests fail**

```bash
.venv/bin/pytest tests/test_telegram.py -q
```

Expected: FAIL on the new expectations.

- [ ] **Step 3: Implement constants, chunking, and failure recording**

Add after `NO_QUALIFIED`:

```python
MAX_DELIVERY_ATTEMPTS = 5
BACKOFF_MINUTES = (1, 2, 4, 8, 16)
MAX_CHUNK = 3500
```

Add before `_delivery_recipient`:

```python
def _chunk_text(text: str, limit: int = MAX_CHUNK) -> List[str]:
    """Split on line boundaries; hard-split a single oversized line."""
    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


def _record_failure(row: Any, message: Optional[str], now: datetime) -> None:
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = (message or "delivery failed")[:400]
    if row.attempts >= MAX_DELIVERY_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
        return
    row.status = "pending"
    minutes = BACKOFF_MINUTES[min(row.attempts - 1, len(BACKOFF_MINUTES) - 1)]
    row.next_attempt_at = now + timedelta(minutes=minutes)
```

Replace `_delivery_recipient` with:

```python
def _delivery_recipient(row: Any, allowlist: List[str]) -> Optional[str]:
    """Only deliver to an explicitly allowlisted chat; never redirect."""
    candidate = str(getattr(row, "recipient", "") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    return candidate if candidate in allowlist else None
```

Replace `send_message` with:

```python
def send_message(
    text: str,
    chat_id: Optional[Any] = None,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    recipient = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    token = _token()
    if not token:
        return {"status": "not_configured", "detail": "TELEGRAM_BOT_TOKEN is not set"}
    if not recipient:
        return {"status": "no_recipient", "detail": "No chat_id supplied"}
    transport = transport or _post
    try:
        response = transport(
            f"{API_BASE}/bot{token}/sendMessage",
            {"chat_id": recipient, "text": text, "disable_web_page_preview": True},
        )
    except Exception as error:
        return {"status": "failed", "error": str(error)}
    if response.get("ok"):
        return {"status": "sent", "response": response}
    parameters = response.get("parameters") or {}
    return {
        "status": "failed",
        "error": response.get("description") or "Telegram rejected the message",
        "retry_after": parameters.get("retry_after"),
        "response": response,
    }
```

Replace `deliver_pending` with:

```python
def deliver_pending(
    db: Session,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Drain due Telegram rows with bounded retries and content dedupe."""
    from app.models.predictions import Notification

    if not _token():
        return {"status": "not_configured", "checked": 0, "sent": 0, "failed": 0, "pending": 0, "retried": 0}

    allowlist = allowed_chat_ids(db)
    if not allowlist:
        return {"status": "no_recipient", "checked": 0, "sent": 0, "failed": 0, "pending": 0, "retried": 0}

    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(Notification)
        .where(Notification.channel == "telegram", Notification.status == "pending")
        .where(
            (Notification.next_attempt_at.is_(None))
            | (Notification.next_attempt_at <= now)
        )
        .order_by(Notification.created_at)
        .limit(limit)
    ).all()

    sent = failed = pending = retried = 0
    for row in rows:
        recipient = _delivery_recipient(row, allowlist)
        if recipient is None:
            _record_failure(row, "recipient not allowlisted", now)
            if row.status == "failed":
                failed += 1
            else:
                pending += 1
            continue

        body = f"{row.subject}\n\n{row.body}" if row.subject else row.body
        error: Optional[str] = None
        retry_after: Optional[int] = None
        delivered = True
        for chunk in _chunk_text(body):
            result = send_message(chunk, chat_id=recipient, transport=transport)
            if result.get("status") != "sent":
                delivered = False
                error = result.get("error") or result.get("detail")
                retry_after = result.get("retry_after")
                break
        if delivered:
            row.status = "sent"
            row.sent_at = now
            row.last_error = None
            row.next_attempt_at = None
            sent += 1
        elif retry_after:
            row.last_error = (error or "rate limited")[:400]
            row.next_attempt_at = now + timedelta(seconds=min(int(retry_after), 3600))
            retried += 1
        else:
            _record_failure(row, error, now)
            if row.status == "failed":
                failed += 1
            else:
                pending += 1

    if rows:
        db.commit()
    return {
        "status": "ok",
        "checked": len(rows),
        "sent": sent,
        "failed": failed,
        "pending": pending,
        "retried": retried,
    }
```

Remove the `text[:4000]` truncation from `format_daily_report` (return the full joined text).

- [ ] **Step 4: Run the delivery tests**

```bash
.venv/bin/pytest tests/test_telegram.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/notifications/telegram.py tests/test_telegram.py
git commit -m "feat(telegram): retry deliveries and fail closed on recipients"
```

---

### Task 5: Run inbound polling from the API lifespan

**Files:**
- Modify: `backend/app/notifications/telegram.py::poll_once`, `run_polling`, and add thread helpers
- Modify: `backend/app/main.py:44-60`
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `poll_once(db, offset=None, transport=None, long_poll_seconds=0, bootstrap=False) -> Optional[int]`.
- Produces: `start_polling_thread() -> bool`, `stop_polling_thread() -> None`, `polling_status() -> Dict[str, Any]`, `_allowlist_from_db() -> List[str]`.
- Produces: `SystemSetting` key `telegram_poll_offset` holding the next offset as a string.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_poll_once_persists_offset_and_sanitizes_errors(allowlist, db, monkeypatch):
    from app.models.predictions import SystemSetting

    updates = {
        "ok": True,
        "result": [
            {"update_id": 10, "message": {"chat": {"id": 42}, "text": "/today"}},
            {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/nonsense"}},
        ],
    }
    monkeypatch.setattr(
        telegram,
        "handle_command",
        lambda text, session: (_ for _ in ()).throw(RuntimeError("secret detail")),
    )
    replies = []

    def transport(url, payload):
        if "getUpdates" in url:
            return updates
        replies.append(payload["text"])
        return {"ok": True}

    try:
        next_offset = telegram.poll_once(db, offset=None, transport=transport)
        setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "telegram_poll_offset"))
        assert next_offset == 12
        assert setting.value == "12"
        assert replies and "secret detail" not in replies[0]
        assert "Reference" in replies[0]
    finally:
        db.query(SystemSetting).filter(SystemSetting.key == "telegram_poll_offset").delete()
        db.commit()


def test_poll_once_bootstraps_offset_without_running_backlog(allowlist, db, monkeypatch):
    calls = []

    def transport(url, payload):
        calls.append(payload)
        return {"ok": True, "result": [{"update_id": 99}]}

    called = []
    monkeypatch.setattr(telegram, "handle_command", lambda text, session: called.append(text) or "ok")
    next_offset = telegram.poll_once(db, offset=None, transport=transport, bootstrap=True)
    assert next_offset == 100
    assert called == []
```

Add `from sqlalchemy import select` at the top of the test file.

- [ ] **Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_telegram.py::test_poll_once_persists_offset_and_sanitizes_errors -q
```

Expected: FAIL (offset is not persisted and the raw exception leaks).

- [ ] **Step 3: Implement offset persistence and the sanitized reply**

Replace `poll_once` and `run_polling` with:

```python
def _get_offset(db: Session) -> Optional[int]:
    from app.models.predictions import SystemSetting

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "telegram_poll_offset"))
    if row is None or row.value is None:
        return None
    try:
        return int(row.value)
    except (TypeError, ValueError):
        return None


def _save_offset(db: Session, offset: int) -> None:
    from app.models.predictions import SystemSetting

    row = db.scalar(select(SystemSetting).where(SystemSetting.key == "telegram_poll_offset"))
    if row is None:
        row = SystemSetting(key="telegram_poll_offset", value=str(offset))
        db.add(row)
    else:
        row.value = str(offset)
    db.commit()


def poll_once(
    db: Session,
    offset: Optional[int] = None,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    long_poll_seconds: int = 0,
    bootstrap: bool = False,
) -> Optional[int]:
    """Process one batch of updates; returns the next offset."""
    token = _token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    transport = transport or _post
    offset = offset if offset is not None else _get_offset(db)

    if offset is None and bootstrap:
        latest = transport(
            f"{API_BASE}/bot{token}/getUpdates",
            {"offset": -1, "timeout": 0},
        )
        results = latest.get("result") or []
        next_offset = (results[-1].get("update_id", 0) + 1) if results else 0
        _save_offset(db, next_offset)
        return next_offset

    payload: Dict[str, Any] = {"timeout": long_poll_seconds}
    if offset is not None:
        payload["offset"] = offset
    update = transport(f"{API_BASE}/bot{token}/getUpdates", payload)
    next_offset = offset or 0
    for item in update.get("result", []) or []:
        next_offset = item.get("update_id", 0) + 1
        message = item.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text") or ""
        if not text:
            _save_offset(db, next_offset)
            continue
        if not is_authorized(chat_id, db):
            _save_offset(db, next_offset)
            continue
        reference = uuid.uuid4().hex[:6]
        try:
            reply = handle_command(text, db)
        except Exception:
            logger.exception("Telegram command failed (ref %s)", reference)
            reply = f"Command failed. Reference {reference}."
        send_message(reply, chat_id=chat_id, transport=transport)
        _save_offset(db, next_offset)
    return next_offset


_poll_thread: Optional[threading.Thread] = None
_poll_stop = threading.Event()


def _allowlist_from_db() -> List[str]:
    """Resolve the allowlist through a short-lived session (env or settings)."""
    from app.database import get_db

    db = next(get_db())
    try:
        return allowed_chat_ids(db)
    finally:
        db.close()


def polling_status() -> Dict[str, Any]:
    return {
        "running": bool(_poll_thread and _poll_thread.is_alive()),
        "configured": bool(_token()) and bool(_allowlist_from_db()),
    }


def _poll_loop() -> None:
    from app.database import get_db

    offset: Optional[int] = None
    db = next(get_db())
    try:
        offset = _get_offset(db)
    finally:
        db.close()
    bootstrap = offset is None
    while not _poll_stop.is_set():
        db = next(get_db())
        try:
            offset = poll_once(db, offset=offset, long_poll_seconds=25, bootstrap=bootstrap)
            bootstrap = False
        except Exception:
            logger.exception("Telegram polling cycle failed")
            _poll_stop.wait(5)
        finally:
            db.close()
        _poll_stop.wait(1)


def start_polling_thread() -> bool:
    global _poll_thread
    if os.getenv("TELEGRAM_POLLING", "").lower() in ("off", "0", "false"):
        return False
    if not _token() or not _allowlist_from_db():
        return False
    if _poll_thread and _poll_thread.is_alive():
        return True
    _poll_stop.clear()
    _poll_thread = threading.Thread(target=_poll_loop, name="telegram-poll", daemon=True)
    _poll_thread.start()
    logger.info("Telegram polling thread started")
    return True


def stop_polling_thread() -> None:
    global _poll_thread
    _poll_stop.set()
    if _poll_thread and _poll_thread.is_alive():
        _poll_thread.join(timeout=5)
    _poll_thread = None


if __name__ == "__main__":
    run_polling()
```

Add `import threading` and `import uuid` to the imports, and keep `run_polling` as a thin wrapper:

```python
def run_polling(interval: float = 2.0) -> None:
    start_polling_thread()
```

Note: `_poll_loop` uses `next(get_db())`, so it needs `from app.database import get_db` inside the function (as shown).

- [ ] **Step 4: Wire the lifespan**

In `backend/app/main.py`, add `from contextlib import asynccontextmanager` and define before `app = FastAPI(...)`:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    from app.notifications.telegram import start_polling_thread, stop_polling_thread

    start_polling_thread()
    try:
        yield
    finally:
        stop_polling_thread()
```

Pass `lifespan=_lifespan` to the `FastAPI(...)` constructor. Do not remove the existing `start_scheduler()` call.

- [ ] **Step 5: Run the polling tests**

```bash
.venv/bin/pytest tests/test_telegram.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/notifications/telegram.py backend/app/main.py tests/test_telegram.py
git commit -m "feat(telegram): poll commands inside the API lifespan"
```

---

### Task 6: Add `/status`, `/pending`, `/retry`, and `/sweep`

**Files:**
- Modify: `backend/app/tasks/scheduler.py::start_scheduler` and add `scheduler_status`
- Modify: `backend/app/notifications/telegram.py::HELP_TEXT`, `handle_command`, add handlers
- Test: `tests/test_telegram.py`

**Interfaces:**
- Produces: `scheduler.scheduler_status() -> {"running": bool, "jobs": [{"id", "next_run"}]}`.
- Produces: `telegram._status_text(db)`, `_pending_text(db)`, `_retry_text(db, id)`, `_sweep_text()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram.py`:

```python
def test_status_pending_retry_and_sweep_commands(db, allowlist):
    from app.notifications.telegram import queue_message

    assert "Telegram" in telegram.handle_command("/status", db)
    row = queue_message(db, "Test", "Body", dedupe_key="cmd:pending")
    try:
        pending = telegram.handle_command("/pending", db)
        assert "Test" in pending
        assert str(row) in pending
        assert "not found" in telegram.handle_command("/retry 999999", db)
        assert "queued" in telegram.handle_command(f"/retry {row}", db)
        sweep = telegram.handle_command("/sweep", db)
        assert "sweep" in sweep.lower() or "no" in sweep.lower()
    finally:
        db.query(Notification).filter(Notification.id == row).delete()
        db.commit()
```

- [ ] **Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_telegram.py::test_status_pending_retry_and_sweep_commands -q
```

Expected: FAIL with `Unknown command`.

- [ ] **Step 3: Add scheduler status**

In `backend/app/tasks/scheduler.py`, add a module-level `_scheduler = None` near `_started`. In `start_scheduler`, after `scheduler.start()` set `_scheduler = scheduler`. Add:

```python
def scheduler_status() -> Dict[str, Any]:
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": bool(_scheduler.running),
        "jobs": [
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in _scheduler.get_jobs()
        ],
    }
```

Use `global _started, _scheduler` in `start_scheduler`.

- [ ] **Step 4: Add the handlers**

In `backend/app/notifications/telegram.py`, add to `HELP_TEXT`:

```python
    "/status - bot, queue and scheduler state\n"
    "/pending - queued and failed notifications\n"
    "/retry <id> - requeue a failed notification\n"
    "/sweep - tier comparison summary\n"
```

Add before `handle_command`:

```python
def _status_text(db: Session) -> str:
    from app.models.predictions import Notification
    from app.tasks.scheduler import scheduler_status

    pending = db.scalar(
        select(func.count(Notification.id)).where(Notification.status == "pending")
    )
    failed = db.scalar(
        select(func.count(Notification.id)).where(Notification.status == "failed")
    )
    sent_today = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.status == "sent",
            Notification.sent_at >= _today_bounds()[0],
        )
    )
    status = scheduler_status()
    poll = polling_status()
    jobs = ", ".join(job["id"] for job in status["jobs"]) or "none"
    return (
        f"Telegram polling: {'running' if poll['running'] else 'stopped'}\n"
        f"Allowlist source: {allowlist_source(db)}\n"
        f"Scheduler: {'running' if status['running'] else 'stopped'} ({jobs})\n"
        f"Queue: {pending} pending · {failed} failed · {sent_today} sent today"
    )


def _pending_text(db: Session) -> str:
    from app.models.predictions import Notification

    rows = db.scalars(
        select(Notification)
        .where(Notification.status.in_(("pending", "failed")))
        .order_by(Notification.created_at.desc())
        .limit(10)
    ).all()
    if not rows:
        return "No pending or failed notifications."
    lines = []
    for row in rows:
        error = (row.last_error or "")[:60]
        lines.append(
            f"#{row.id} {row.status} · attempts {row.attempts or 0} · "
            f"{(row.subject or row.body)[:40]}" + (f" · {error}" if error else "")
        )
    return "\n".join(lines)


def _retry_text(db: Session, notification_id: int) -> str:
    from app.models.predictions import Notification

    row = db.get(Notification, notification_id)
    if row is None:
        return f"Notification {notification_id} not found."
    if row.status != "failed":
        return f"Notification {notification_id} is {row.status}; only failed rows are retried."
    row.status = "pending"
    row.attempts = 0
    row.last_error = None
    row.next_attempt_at = None
    db.commit()
    return f"Notification {notification_id} queued for retry."


def _sweep_text() -> str:
    from app.analytics.combo_backtest import load_sweep

    report = load_sweep() or {}
    recommendation = report.get("recommendation") or {}
    if not recommendation:
        return "No tier sweep saved yet. Run POST /combos/backtest/sweep."
    return (
        f"Tier sweep: {float(recommendation.get('tier') or 0):.0%} leads on training · "
        f"holdout confirmed: {bool(recommendation.get('holdout_confirmed'))}\n"
        f"{recommendation.get('guidance') or ''}"
    )
```

Add `func` to the SQLAlchemy import at the top of `telegram.py` (`from sqlalchemy import select, func`). In `handle_command`, add:

```python
    if command == "/status":
        return _status_text(db)
    if command == "/pending":
        return _pending_text(db)
    if command == "/retry":
        if not argument.isdigit():
            return "Usage: /retry <notification_id>"
        return _retry_text(db, int(argument))
    if command == "/sweep":
        return _sweep_text()
```

- [ ] **Step 5: Run the command tests**

```bash
.venv/bin/pytest tests/test_telegram.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/notifications/telegram.py backend/app/tasks/scheduler.py tests/test_telegram.py
git commit -m "feat(telegram): add status, pending, retry and sweep commands"
```

---

### Task 7: Record published shortlist tickets in the ledger

**Files:**
- Modify: `backend/app/betting/settlement.py::record_tracked_bet`
- Modify: `backend/app/tasks/scheduler.py::build_shortlist`
- Test: `tests/test_shortlist.py` (create)

**Interfaces:**
- Produces: `record_tracked_bet(combo, matchday_label, db, currency="UGX", bankroll=1000.0, dedupe_key=None, tier=None) -> TrackedBet`.
- Produces: dedupe key format `shortlist:{target_date}:{size_key}:{ticket_signature}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_shortlist.py`:

```python
"""The scheduler shortlist records one ledger row per size, once."""
from __future__ import annotations

from app.database import SessionLocal
from app.models.bet import TrackedBet
from app.tasks import scheduler


def _report():
    def leg(fid, selection, probability, odds):
        return {
            "fixture_id": fid,
            "match": f"Team {fid} vs Rival {fid}",
            "competition": "UPL",
            "team": "Team",
            "selection": selection,
            "market_key": "home_win",
            "market_type": "1X2",
            "odds": odds,
            "probability": probability,
            "odds_source": "market",
            "kickoff_eat": "16:00 EAT",
        }

    two = {
        "size": 2,
        "legs": [leg(1, "Team 1 to win", 0.8, 1.3), leg(2, "Team 2 to win", 0.8, 1.3)],
        "combined_probability": 0.64,
        "total_odds": 1.69,
        "expected_value_percent": 8.2,
    }
    return {
        "matchday_label": "Friday, 13 Nov 2026",
        "target_date": "2026-11-13",
        "params": {"min_leg_probability": 0.75},
        "combos": {"2_leg": [two], "3_leg": []},
        "best": two,
    }


def test_build_shortlist_records_once(monkeypatch):
    db = SessionLocal()
    monkeypatch.setattr("app.betting.daily_combos.build_daily_combos", lambda session: _report())
    monkeypatch.setattr(scheduler, "deliver_notifications", lambda: {"status": "ok"})
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)
    try:
        scheduler.build_shortlist()
        scheduler.build_shortlist()
        rows = db.query(TrackedBet).filter(TrackedBet.dedupe_key.like("shortlist:%")).all()
        assert len(rows) == 1
        assert rows[0].tier == 0.75
    finally:
        db.query(TrackedBet).filter(TrackedBet.dedupe_key.like("shortlist:%")).delete()
        db.commit()
        db.close()
```

- [ ] **Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_shortlist.py -q
```

Expected: FAIL because no row is recorded.

- [ ] **Step 3: Extend `record_tracked_bet`**

Change the signature and add the dedupe check at the top:

```python
def record_tracked_bet(
    combo: Dict[str, Any],
    matchday_label: str,
    db: Session,
    currency: str = "UGX",
    bankroll: float = 1000.0,
    dedupe_key: Optional[str] = None,
    tier: Optional[float] = None,
) -> TrackedBet:
```

After the docstring:

```python
    if dedupe_key:
        existing = db.scalar(select(TrackedBet).where(TrackedBet.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
```

Remove `whatsapp_alert_sent=True` and `whatsapp_settlement_sent=False` from the constructor, and add:

```python
        dedupe_key=dedupe_key,
        tier=tier,
        settlement_notified=False,
```

- [ ] **Step 4: Record from `build_shortlist`**

Recording needs the session that built the report, so replace the whole function with this version (it keeps `db` open through recording, then closes it before queueing):

```python
def build_shortlist() -> Dict[str, Any]:
    from app.notifications.telegram import NO_QUALIFIED, format_daily_report

    try:
        from app.betting.daily_combos import build_daily_combos
        from app.betting.settlement import record_tracked_bet

        db = _session()
        try:
            report = build_daily_combos(db)
            matchday = report.get("matchday_label") or "Today"
            day = report.get("target_date") or matchday
            tier = float((report.get("params") or {}).get("min_leg_probability") or 0.75)
            stake = float(os.getenv("FIE_SHORTLIST_STAKE", "10"))
            for size_key in ("2_leg", "3_leg"):
                tickets = (report.get("combos") or {}).get(size_key) or []
                if not tickets:
                    continue
                ticket = tickets[0]
                legs = ticket.get("legs") or []
                combo = {
                    **ticket,
                    "legs_count": ticket.get("size", len(legs)),
                    "avg_leg_probability": (
                        sum(float(leg.get("probability") or 0) for leg in legs) / len(legs)
                        if legs else 0.0
                    ),
                    "kelly_stake_amount": stake,
                }
                record_tracked_bet(
                    combo,
                    matchday,
                    db,
                    currency=os.getenv("FIE_CURRENCY", "UGX"),
                    bankroll=float(os.getenv("FIE_BANKROLL", "1000")),
                    dedupe_key=f"shortlist:{day}:{size_key}:{_ticket_signature(ticket)}",
                    tier=tier,
                )
        finally:
            db.close()
    except Exception as error:
        logger.error("[Scheduler] shortlist failed: %s", error)
        return {"status": "error", "error": str(error)}

    subject = f"Daily shortlist · {report.get('matchday_label') or ''}".strip()
    text = format_daily_report(report)
    dedupe = _shortlist_dedupe(report)
    if not report.get("best"):
        _notify(subject, text, dedupe_key=dedupe)
        return {"status": "empty", "message": NO_QUALIFIED, "delivery": deliver_notifications()}

    _notify(subject, text, dedupe_key=dedupe, related_type="combos")
    return {
        "status": "ok",
        "tickets": sum(len(rows) for rows in (report.get("combos") or {}).values()),
        "delivery": deliver_notifications(),
    }
```

`_ticket_signature` and `_shortlist_dedupe` come from Task 2; do not define a second signature helper.

- [ ] **Step 5: Run the shortlist tests**

```bash
.venv/bin/pytest tests/test_shortlist.py tests/test_daily_combos.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/betting/settlement.py backend/app/tasks/scheduler.py tests/test_shortlist.py
git commit -m "feat(scheduler): record published shortlist tickets once"
```

---

### Task 8: Settle bets through the Telegram queue on a schedule

**Files:**
- Modify: `backend/app/betting/settlement.py` (imports, `settle_pending_bets`, formatter name)
- Modify: `backend/app/tasks/scheduler.py::start_scheduler` and add `settle_and_capture`
- Modify: `backend/app/main.py::ledger_settle`
- Test: `tests/test_settlement_queue.py` (create)
- Test: `tests/test_new_features.py:264,362`

**Interfaces:**
- Produces: `settle_pending_bets(db) -> List[TrackedBet]` with no WhatsApp parameters.
- Produces: `format_settlement_message(bet, stats, currency="UGX") -> str`.
- Produces: scheduler job `settlement` running `CronTrigger(minute="*/30")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_settlement_queue.py`:

```python
"""Settlement queues one Telegram notification per finished bet."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.betting import settlement
from app.database import SessionLocal
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.predictions import Notification


def test_settlement_queues_once(monkeypatch):
    db = SessionLocal()
    fixture = Fixture(
        dedupe_key="settle-queue-fixture",
        kickoff_utc=datetime.now(timezone.utc),
        home_team="KCCA",
        away_team="Vipers",
        competition="UPL",
        country="Uganda",
        source="test",
        status="finished",
        home_score=2,
        away_score=0,
    )
    db.add(fixture)
    db.commit()
    bet = TrackedBet(
        slip_type="2-Team Combo",
        matchday_label="Friday",
        total_odds=2.0,
        avg_confidence=0.8,
        accum_probability=0.64,
        stake_amount=10.0,
        stake_currency="UGX",
        stake_usd=0.0,
        status="PENDING",
        profit_loss=0.0,
        profit_loss_usd=0.0,
        legs_json=json.dumps([{
            "fixture_id": fixture.id,
            "match": "KCCA vs Vipers",
            "selection": "KCCA to win",
            "market_key": "home_win",
            "odds": 2.0,
            "probability": 0.8,
            "status": "PENDING",
        }]),
        dedupe_key="settle-queue-bet",
    )
    db.add(bet)
    db.commit()
    monkeypatch.setattr(settlement, "sync_finished_match_scores_from_espn", lambda session: 0)
    try:
        first = settlement.settle_pending_bets(db)
        second = settlement.settle_pending_bets(db)
        db.refresh(bet)
        rows = db.query(Notification).filter(Notification.dedupe_key == f"settle:{bet.id}").all()
        assert len(first) == 1
        assert second == []
        assert bet.status == "WON"
        assert bet.settlement_notified is True
        assert len(rows) == 1
    finally:
        db.query(Notification).filter(Notification.dedupe_key == f"settle:{bet.id}").delete()
        db.delete(bet)
        db.delete(fixture)
        db.commit()
        db.close()
```

- [ ] **Step 2: Confirm failure**

```bash
.venv/bin/pytest tests/test_settlement_queue.py -q
```

Expected: FAIL (no notification and the second call re-settles).

- [ ] **Step 3: Update settlement**

- Remove `from app.notifications.whatsapp import send_whatsapp_message`.
- Rename `format_settlement_whatsapp_message` to `format_settlement_message`.
- Change the signature to `settle_pending_bets(db: Session)`.
- Replace the block starting `# Dispatch settlement outcome to WhatsApp` with:

```python
        if bet.status in ("WON", "LOST") and not bet.settlement_notified:
            from app.notifications.telegram import queue_message

            stats = get_performance_ledger_summary(db)
            currency = (bet.stake_currency or os.getenv("FIE_CURRENCY", "UGX")).upper()
            queue_message(
                db,
                f"Settlement · {bet.slip_type}",
                format_settlement_message(bet, stats, currency=currency),
                dedupe_key=f"settle:{bet.id}",
                related_type="tracked_bet",
                related_id=bet.id,
            )
            bet.settlement_notified = True
            db.commit()
```

- [ ] **Step 4: Add the scheduler job**

In `backend/app/tasks/scheduler.py`, add:

```python
def settle_and_capture() -> Dict[str, Any]:
    try:
        from app.betting.clv import capture_closing_prices
        from app.betting.settlement import settle_pending_bets

        db = _session()
        try:
            captured = capture_closing_prices(db)
            settled = settle_pending_bets(db)
        finally:
            db.close()
        if settled:
            logger.info("[Scheduler] settled %s tracked bets", len(settled))
        return {"captured": captured, "settled": len(settled)}
    except Exception as error:
        logger.error("[Scheduler] settlement failed: %s", error)
        return {"error": str(error)}
```

Register it in `start_scheduler`:

```python
        scheduler.add_job(settle_and_capture, CronTrigger(minute="*/30"), id="settlement")
```

- [ ] **Step 5: Update `/ledger/settle`**

In `backend/app/main.py`, remove the `phone`, `apikey`, `provider`, and `currency` query parameters and call `settle_pending_bets(db)`.

- [ ] **Step 6: Update the old settlement test**

In `tests/test_new_features.py`, change the import to `format_settlement_message` and the call at line 362 accordingly.

- [ ] **Step 7: Run the settlement tests**

```bash
.venv/bin/pytest tests/test_settlement_queue.py tests/test_new_features.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/betting/settlement.py backend/app/tasks/scheduler.py backend/app/main.py tests/test_settlement_queue.py tests/test_new_features.py
git commit -m "feat(settlement): queue outcomes and settle on a schedule"
```

---

### Task 9: Delete the sentinel and patch the Streamlit dashboards

**Files:**
- Delete: `backend/app/tasks/sentinel.py`, `backend/app/notifications/whatsapp.py`
- Modify: `backend/app/main.py` (remove sentinel and WhatsApp endpoints)
- Modify: `streamlit_app.py`, `dashboard.py`
- Modify: `render.yaml`, `.env.example`, `README.md`
- Modify: `tests/test_new_features.py` (remove WhatsApp and sentinel tests)

**Interfaces:**
- Consumes: `queue_message`, `allowed_chat_ids`, `Notification`.
- Produces: no remaining `app.notifications.whatsapp` or `app.tasks.sentinel` imports.

- [ ] **Step 1: Remove the endpoints and modules**

Delete the five endpoints under the `24/7 Sentinel & WhatsApp Alert Endpoints` banner in `backend/app/main.py` (`/sentinel/status`, `/sentinel/start`, `/sentinel/stop`, `/sentinel/scan`, `/whatsapp/test`).

Then:

```bash
git rm backend/app/tasks/sentinel.py backend/app/notifications/whatsapp.py
```

- [ ] **Step 2: Remove the old tests**

In `tests/test_new_features.py`, delete `test_whatsapp_message_formatting` and `test_sentinel_daemon_status_and_endpoints`. Remove any now-unused imports.

- [ ] **Step 3: Patch `streamlit_app.py`**

Remove the two imports at lines 111 and 116. Replace the auto-boot block with nothing. Replace the sentinel chip with a queue chip computed from the database:

```python
from app.models.predictions import Notification  # add near the other model imports

try:
    _db_chip = next(get_db())
    _pending_count = _db_chip.query(Notification).filter(Notification.status == "pending").count()
    _failed_count = _db_chip.query(Notification).filter(Notification.status == "failed").count()
    _db_chip.close()
except Exception:
    _pending_count = _failed_count = 0

_alert_chip = (
    f'<span class="fie-chip live">● Telegram · queued {_pending_count} · failed {_failed_count}</span>'
)
```

Use `_alert_chip` in place of `_sentinel_chip` in the hero. Replace the sidebar sentinel block with:

```python
    st.header("📨 Telegram alerts")
    st.caption("Alerts are queued by the scheduler and delivered by the bot. Configure the token and allowlist on the server.")
```

Remove the WhatsApp phone/key inputs and the test-send button (lines 505-540). Remove the "Send to WhatsApp" button block (lines 1018-1030).

- [ ] **Step 4: Patch `dashboard.py`**

Remove the imports at lines 20-21. Remove the WhatsApp credential block (lines 116-131), the sentinel block (lines 143-161), and the "Send to WhatsApp" call (line 390). Add one caption where the sentinel block was:

```python
    st.caption("Telegram delivery is configured on the server; this dashboard no longer sends WhatsApp.")
```

- [ ] **Step 5: Remove deployment and documentation references**

- `render.yaml`: delete the `WHATSAPP_PHONE` and `WHATSAPP_APIKEY` entries.
- `.env.example`: delete the WhatsApp block and the Twilio variables.
- `README.md`: change `notifications/  Telegram polling + WhatsApp alerts` to
  `notifications/  Telegram polling and queue delivery`; delete the
  `WHATSAPP_*`, `TWILIO_*` row from the environment table.

- [ ] **Step 6: Verify no imports remain**

```bash
grep -rn "notifications.whatsapp\|tasks.sentinel\|WHATSAPP_\|TWILIO_" \
  --exclude-dir=node_modules --exclude-dir=.next --exclude-dir=.git \
  backend frontend streamlit_app.py dashboard.py render.yaml .env.example README.md || true
python -m compileall -q streamlit_app.py dashboard.py
```

Expected: no import hits; `compileall` exits 0.

- [ ] **Step 7: Run the backend suite**

```bash
.venv/bin/pytest -q
```

Expected: PASS with zero failures.

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py streamlit_app.py dashboard.py render.yaml .env.example README.md tests/test_new_features.py
git add -u backend/app/tasks/sentinel.py backend/app/notifications/whatsapp.py
git commit -m "refactor(notifications): retire the WhatsApp sentinel"
```

---

### Task 10: Align the dashboard settings copy

**Files:**
- Modify: `frontend/app/settings/page.tsx:218-240,330-335`
- Test: `frontend` typecheck and build

**Interfaces:**
- Produces: the notification channel selector offering only `telegram`.

- [ ] **Step 1: Restrict the channel selector**

In the "Queue a notification" form, replace the channel `<select>` options with a single `telegram` option and a disabled note:

```tsx
<select className={inputClass} value={channel} onChange={(e) => setChannel(e.target.value)}>
  <option value="telegram">telegram</option>
</select>
```

Remove the `whatsapp` and `email` options. Add below the form:

```tsx
<p className="text-xs text-mist/60">
  Only Telegram is delivered. The server token and chat allowlist are configured
  in the environment, and <code>TELEGRAM_ALLOWED_CHAT_IDS</code> overrides
  <code>telegram_allowlist</code> when set.
</p>
```

- [ ] **Step 2: Typecheck and build**

```bash
cd frontend
npx tsc --noEmit
npm run build
```

Expected: both exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/settings/page.tsx
git commit -m "fix(settings): offer only the delivered notification channel"
```

---

### Task 11: Final verification and push

**Files:** none.

- [ ] **Step 1: Run the complete backend suite**

```bash
.venv/bin/pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Smoke the running API**

With `uvicorn app.main:app --app-dir backend --port 8001` running:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/notifications
curl -s http://127.0.0.1:8001/settings | head -c 300
```

Expected: `200`, `200`, and a settings payload containing `telegram_allowlist`.

- [ ] **Step 3: Confirm the retired routes are gone**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/sentinel/status
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8001/whatsapp/test
```

Expected: `404` for both.

- [ ] **Step 4: Review the diff for secrets and synthetic data**

```bash
git status --short
git diff --stat 508cf2a..HEAD
git diff 508cf2a..HEAD | grep -iE "api[_-]?key|token=|secret|password" || true
```

Expected: no credential values; no `fie.db` or `sentinel_state.json` staged.

- [ ] **Step 5: Push**

```bash
git push origin main
```

- [ ] **Step 6: Report**

Report test counts, smoke status codes, and the push range. Do not claim success without the command output.
