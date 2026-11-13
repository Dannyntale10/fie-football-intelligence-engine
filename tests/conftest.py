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

import pytest

_TEST_DB_DIR = tempfile.mkdtemp(prefix="fie-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'test.db')}"
atexit.register(shutil.rmtree, _TEST_DB_DIR, True)

# Importing app.main must never seed the shared throwaway database from a
# committed ratings artifact.
os.environ["FIE_SKIP_ARTIFACT_LOAD"] = "1"

import app.models  # noqa: E402,F401  (registers every table on Base.metadata)
from app.database import Base, engine  # noqa: E402

Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Keep one test's request burst from spending the next test's budget."""
    from app.core import security

    security.limiter.reset()
    yield
    security.limiter.reset()


@pytest.fixture(autouse=True)
def _isolate_written_artifacts(tmp_path, monkeypatch):
    """Send every combo artifact write to a temp dir.

    ``POST /combos/backtest`` and ``POST /combos/backtest/sweep`` persist into
    ``data/`` on a real machine. A test that reaches those routes with any
    settled history would otherwise overwrite the operator's replay, learning
    and sweep artifacts with throwaway rows. All writers resolve these path
    helpers at call time, so patching them here also covers the API aliases.
    """
    from app.analytics import combo_backtest
    from app.betting import daily_combos

    artifacts = tmp_path / "artifacts"

    monkeypatch.setattr(
        combo_backtest, "backtest_path", lambda: artifacts / "combo_backtest.json"
    )
    monkeypatch.setattr(
        combo_backtest, "sweep_path", lambda: artifacts / "combo_sweep.json"
    )
    monkeypatch.setattr(
        combo_backtest, "combo_learning_path",
        lambda: artifacts / "combo_learning.json",
    )
    monkeypatch.setattr(
        daily_combos, "learning_path", lambda: artifacts / "combo_learning.json"
    )

