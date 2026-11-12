#!/usr/bin/env python3
"""Apply SQL migrations in database/migrations in filename order.

Intended for PostgreSQL deployments. SQLite users should run
`python scripts/init_db.py`, which creates every table from the models.
Applied filenames are recorded in schema_migrations so re-runs are no-ops.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    from sqlalchemy import text

    from app.database import engine

    if engine.dialect.name == "sqlite":
        print("SQLite detected: run `python scripts/init_db.py` instead of SQL migrations.")
        return 0

    migrations = sorted((ROOT / "database" / "migrations").glob("*.sql"))
    if not migrations:
        print("No migration files found.")
        return 0

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "filename TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT NOW())"
            )
        )
        applied = {row[0] for row in conn.execute(text("SELECT filename FROM schema_migrations"))}
        for path in migrations:
            if path.name in applied:
                print(f"skip     {path.name}")
                continue
            print(f"applying {path.name}")
            conn.exec_driver_sql(path.read_text())
            conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:name)"),
                {"name": path.name},
            )
    print("Migrations up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
