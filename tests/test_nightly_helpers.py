"""The nightly run refreshes only the newest seasons it needs."""
import importlib.util
import json
import sys
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


ARTIFACTS = ("ratings.json", "blending.json", "learning.json",
             "live_calibration.json", "model_champion.json")


def test_a_dry_run_writes_no_model_artifacts(tmp_path, monkeypatch):
    trainer = _trainer()
    monkeypatch.delenv("FIE_ENGINE_URL", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_nightly",
            "--dry-run",
            "--skip-ingest",
            "--out-dir",
            str(tmp_path),
            "--window-days",
            "30",
            "--seasons",
            "1",
        ],
    )

    assert trainer.main() == 0

    report = json.loads((tmp_path / "nightly_report.json").read_text())
    assert report["dry_run"] is True
    assert isinstance(report["decision"]["reason"], str)
    for name in ARTIFACTS:
        assert not (tmp_path / name).exists(), name
