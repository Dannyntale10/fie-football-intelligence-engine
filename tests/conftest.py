"""Point every test at a throwaway database.

Before this file existed, tests that used ``SessionLocal`` wrote fixtures,
ratings and tracked bets into the real ``./fie.db``. The app then displayed
those synthetic "Test League" slips as a 17W/0L ledger with a six-figure
profit. Tests must never touch the working database, so the override happens
before any application module is imported.
"""
import atexit
import os
import shutil
import tempfile

_TEST_DB_DIR = tempfile.mkdtemp(prefix="fie-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'test.db')}"
atexit.register(shutil.rmtree, _TEST_DB_DIR, True)

# Importing app.main must never seed the shared throwaway database from a
# committed ratings artifact.
os.environ["FIE_SKIP_ARTIFACT_LOAD"] = "1"

import app.models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.database import Base, engine  # noqa: E402

Base.metadata.create_all(engine)
