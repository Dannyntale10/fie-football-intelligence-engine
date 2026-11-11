from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import settings

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
                ]
                for col, col_type in new_tr_cols:
                    if col not in tr_cols:
                        conn.execute(text(f"ALTER TABLE team_ratings ADD COLUMN {col} {col_type}"))
                conn.commit()
    except Exception:
        pass

ensure_schema_compatibility(engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
