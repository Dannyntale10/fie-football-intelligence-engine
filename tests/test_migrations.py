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
        bet_indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(tracked_bets)"))}
        settled = conn.execute(text("SELECT settlement_notified FROM tracked_bets WHERE id = 1")).scalar()

    assert {"attempts", "next_attempt_at", "last_error", "dedupe_key"} <= notif_cols
    assert {"dedupe_key", "tier", "settlement_notified"} <= bet_cols
    assert "ux_notifications_dedupe_key" in indexes
    assert "ux_tracked_bets_dedupe_key" in bet_indexes
    assert settled == 1
