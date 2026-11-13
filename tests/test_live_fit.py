"""The intensity fit promotes only a material, validated shift."""
import math

from app.live.fit import fit_intensity


def _row(home_extra, away_extra, home_lambda=1.0, away_lambda=1.0):
    return {
        "remaining_home_lambda": home_lambda,
        "remaining_away_lambda": away_lambda,
        "extra_home_goals": home_extra,
        "extra_away_goals": away_extra,
    }


def test_too_few_snapshots_stays_insufficient():
    result = fit_intensity([_row(2, 1) for _ in range(499)])
    assert result["status"] == "insufficient_sample"


def test_a_neutral_multiplier_is_rejected():
    rows = [_row(1, 1) for _ in range(600)]
    result = fit_intensity(rows)
    assert result["status"] != "fitted"
    assert abs(result["k"] - 1.0) < 0.01


def test_a_material_shift_promotes():
    # Synthetic rows, oldest first: the held-out tail is where the shift lives.
    rows = [_row(1, 1) for _ in range(240)] + [_row(2, 1) for _ in range(360)]
    result = fit_intensity(rows)
    assert result["status"] == "fitted"
    assert 1.25 <= result["k"] <= 1.35
    assert result["validation"]["improvement"] > 0


def test_k_is_clamped_to_the_supported_band():
    rows = [_row(4, 3) for _ in range(600)]
    result = fit_intensity(rows)
    assert result["k"] <= 2.0
