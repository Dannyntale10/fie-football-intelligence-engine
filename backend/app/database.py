import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings

logger = logging.getLogger(__name__)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

def ensure_schema_compatibility(eng):
    """Safely add newly introduced columns to existing SQLite tables if not present."""
    try:
        with eng.connect() as conn:
            # Check fixtures columns
            res = conn.execute(text("PRAGMA table_info(fixtures)")).fetchall()
            if res:
                col_names = {row[1] for row in res}
                new_cols = [
                    ("opening_home_odds", "REAL"),
                    ("opening_draw_odds", "REAL"),
                    ("opening_away_odds", "REAL"),
                    ("odds_updated_at", "TIMESTAMP"),
                    ("market_odds_json", "TEXT"),
                    ("live_clock", "VARCHAR(20)"),
                    ("live_observed_at", "TIMESTAMP"),
                    ("espn_league_code", "VARCHAR(30)"),
                ]
                for col, col_type in new_cols:
                    if col not in col_names:
                        conn.execute(text(f"ALTER TABLE fixtures ADD COLUMN {col} {col_type}"))
                conn.commit()

            # Check team_ratings columns
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

            # Check tracked_bets provenance, dedupe and settlement columns
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

ensure_schema_compatibility(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
