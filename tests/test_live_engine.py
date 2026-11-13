"""The conditional model only knows the score and the time left."""
import json

import pytest

from app.live import engine
from app.live.engine import (
    INTENSITY_BOUNDS,
    LiveState,
    default_intensity,
    fitted_intensity,
    outcome_probabilities,
)


def test_probabilities_sum_to_one_and_favour_the_leader():
    probs = outcome_probabilities(1, 0, 1.5, 1.0, remaining_fraction=0.5)

    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert probs["home_win"] > probs["draw"] > probs["away_win"]


def test_a_finished_match_is_certain():
    probs = outcome_probabilities(2, 1, 1.4, 1.1, remaining_fraction=0.0)

    assert probs == {"home_win": 1.0, "draw": 0.0, "away_win": 0.0}


def test_conditioning_beats_the_static_pre_match_view():
    """At 2-1 with 89 minutes gone, the leader is far more likely than at 0-0."""
    late = outcome_probabilities(2, 1, 1.4, 1.1, remaining_fraction=1 / 90)
    early = outcome_probabilities(0, 0, 1.4, 1.1, remaining_fraction=1.0)

    assert late["home_win"] > early["home_win"] + 0.30


def test_intensity_scales_the_remaining_goals_and_is_clamped():
    base = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=1.0)
    excited = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=2.0)
    clamped = outcome_probabilities(0, 0, 2.0, 0.5, remaining_fraction=1 / 9, intensity=9.0)

    assert excited["home_win"] > base["home_win"]
    assert clamped == excited
    assert INTENSITY_BOUNDS == (0.5, 2.0)


def test_default_provider_is_neutral(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CALIBRATION_PATH", tmp_path / "missing.json")
    state = LiveState(0, 0, 1.4, 1.1, 30)
    assert default_intensity(state) == (1.0, "default")
    assert fitted_intensity(state) == (1.0, "default")


def test_fitted_provider_reads_and_clamps_the_artifact(tmp_path, monkeypatch):
    artifact = tmp_path / "live_calibration.json"
    artifact.write_text(json.dumps({"status": "fitted", "k": 1.18}))
    monkeypatch.setattr(engine, "CALIBRATION_PATH", artifact)

    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.18, "fitted")

    artifact.write_text(json.dumps({"status": "fitted", "k": 5.0}))
    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) != (5.0, "fitted")


def test_rejected_artifact_falls_back_to_neutral(tmp_path, monkeypatch):
    artifact = tmp_path / "live_calibration.json"
    artifact.write_text(json.dumps({"status": "rejected", "k": 1.2}))
    monkeypatch.setattr(engine, "CALIBRATION_PATH", artifact)

    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.0, "default")


def test_malformed_artifact_falls_back_to_neutral(tmp_path, monkeypatch):
    artifact = tmp_path / "live_calibration.json"
    monkeypatch.setattr(engine, "CALIBRATION_PATH", artifact)

    artifact.write_text(json.dumps([1, 2, 3]))
    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.0, "default")

    artifact.write_text("{not json")
    assert fitted_intensity(LiveState(0, 0, 1.4, 1.1, 30)) == (1.0, "default")
