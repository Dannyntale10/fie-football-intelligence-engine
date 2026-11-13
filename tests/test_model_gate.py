"""The gate compares configurations on the same windows, never a stale number."""
import json

from app.analytics.model_gate import gate_decision, save_champion


def _result(half_life, brier, log_loss=0.70, comps=None):
    return {
        "half_life_days": half_life,
        "n_matches": 1000,
        "brier": brier,
        "log_loss": log_loss,
        "per_competition": comps or {},
    }


def test_bootstrap_promotes_the_best_configuration():
    results = [_result(90, 0.610), _result(120, 0.600), _result(150, 0.605)]

    decision = gate_decision(results, None)

    assert decision["promote"] is True
    assert decision["reason"] == "bootstrap"
    assert decision["challenger"]["half_life_days"] == 120


def test_a_material_brier_gain_promotes():
    results = [_result(90, 0.598), _result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is True
    assert decision["reason"] == "brier_improved"


def test_a_small_gain_keeps_the_champion():
    results = [_result(90, 0.600), _result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "brier_gain_too_small"


def test_a_worse_log_loss_keeps_the_champion():
    results = [_result(90, 0.590, log_loss=0.75), _result(150, 0.601, log_loss=0.70)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "log_loss_worse"


def test_a_competition_regression_is_rejected():
    results = [
        _result(90, 0.590, comps={"Premier League": {"n": 500, "brier": 0.620}}),
        _result(150, 0.601, comps={"Premier League": {"n": 500, "brier": 0.600}}),
    ]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"].startswith("competition_regression")


def test_the_champion_config_alone_is_not_a_challenger():
    results = [_result(150, 0.601)]

    decision = gate_decision(results, {"half_life_days": 150.0})

    assert decision["promote"] is False
    assert decision["reason"] == "config_unchanged"


def test_a_champion_can_be_staged_to_an_explicit_path(tmp_path):
    target = tmp_path / "champion.json"

    save_champion({"config": {"half_life_days": 150.0}}, target)

    assert json.loads(target.read_text()) == {"config": {"half_life_days": 150.0}}
