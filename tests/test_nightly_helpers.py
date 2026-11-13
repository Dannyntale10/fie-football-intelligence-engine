"""The nightly run refreshes only the newest seasons it needs."""
import importlib.util
import json
from pathlib import Path

from app.data_sources.seeder import seasons_to_refresh

ROOT = Path(__file__).resolve().parents[1]


def _trainer():
    spec = importlib.util.spec_from_file_location("train_nightly", ROOT / "scripts" / "train_nightly.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refresh_window_takes_the_newest_seasons():
    seasons = ["2122", "2223", "2324", "2425", "2526"]
    assert seasons_to_refresh(seasons, 2) == {"2425", "2526"}
    assert seasons_to_refresh(seasons, 0) == set()
    assert seasons_to_refresh(seasons, -1) == set()
    assert seasons_to_refresh(seasons, 10) == set(seasons)


def test_a_bad_export_is_recorded_without_its_message():
    payload = _trainer()._export_unavailable(RuntimeError("api-key-should-not-leak"))

    assert payload == {
        "status": "export_unavailable",
        "k": 1.0,
        "sample": 0,
        "validation": None,
        "generated_at": None,
        "error": "RuntimeError",
    }
    assert "api-key-should-not-leak" not in json.dumps(payload)
