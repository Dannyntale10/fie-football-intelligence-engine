#!/usr/bin/env python3
"""Create every platform table on the configured database.

SQLite (the default) is created on first use; this script makes the step
explicit and prints what now exists. It is safe to run repeatedly.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    from sqlalchemy import inspect

    import app.models  # noqa: F401  (registers every table)
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    tables = sorted(inspect(engine).get_table_names())
    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    print(f"Tables created/verified: {len(tables)}")
    for table in tables:
        print(f"  - {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
