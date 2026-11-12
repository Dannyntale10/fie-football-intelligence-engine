#!/usr/bin/env python3
"""One-time local setup for the platform.

Steps:
  1. verify the Python version and that dependencies import
  2. create/verify every database table
  3. optionally load the bundled historical sample and fit ratings

No provider key is required and nothing is fabricated: without data the
database simply stays empty and the UI shows the honest empty state.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _check_imports() -> None:
    missing = []
    for module in ("fastapi", "sqlalchemy", "pydantic_settings", "pandas", "numpy"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        print("Missing dependencies: " + ", ".join(missing))
        print("Activate the virtual environment and run: pip install -r requirements.txt")
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the FIE local database.")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Load data/historical_epl_sample.csv and compute initial ratings.",
    )
    args = parser.parse_args()

    if sys.version_info < (3, 9):
        print(f"Python 3.9+ is required, found {sys.version.split()[0]}")
        return 1

    _check_imports()

    if not (ROOT / ".env").exists():
        print("No .env found. The defaults work, or run: cp .env.example .env")

    from sqlalchemy import inspect

    import app.models  # noqa: F401
    from app.database import Base, engine

    Base.metadata.create_all(bind=engine)
    tables = inspect(engine).get_table_names()
    print(f"Database ready: {engine.url.render_as_string(hide_password=True)} ({len(tables)} tables)")

    if args.seed:
        from app.database import SessionLocal
        from app.data_sources.seeder import seed_sample_historical_data

        db = SessionLocal()
        try:
            result = seed_sample_historical_data(db)
        finally:
            db.close()
        print("Seed result:", result)

    print("\nNext steps:")
    print("  backend : uvicorn app.main:app --reload --app-dir backend")
    print("  frontend: cd frontend && npm install && npm run dev")
    print("  tests   : python -m pytest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
