"""Bankroll arithmetic: the strike rate a target really needs, and stake caps."""
from __future__ import annotations

import pytest

from app.betting.bankroll import (
    daily_exposure,
    plan,
    preset_plans,
    required_strike_rate,
)


def test_required_strike_rate_is_one_over_the_price():
    assert required_strike_rate(1.20) == pytest.approx(0.8333, abs=1e-4)
    assert required_strike_rate(6.00) == pytest.approx(0.1667, abs=1e-4)
    with pytest.raises(ValueError):
        required_strike_rate(1.0)


def test_low_risk_plan_is_fifty_staked_for_ten_profit():
    result = plan(50.0, 1.20, target_profit=10.0)

    assert result["potential_return"] == 60.0
    assert result["potential_profit"] == 10.0
    assert result["required_strike_rate_percent"] == pytest.approx(83.33, abs=0.01)
    assert result["stake_for_target_profit"] == pytest.approx(50.0, abs=0.01)
    assert result["warnings"] == []


def test_high_odds_plan_is_ten_staked_for_fifty_profit():
    result = plan(10.0, 6.00, target_profit=50.0)

    assert result["potential_return"] == 60.0
    assert result["potential_profit"] == 50.0
    assert result["required_strike_rate_percent"] == pytest.approx(16.67, abs=0.01)
    assert result["stake_for_target_profit"] == pytest.approx(10.0, abs=0.01)


def test_plan_warns_when_the_stake_breaks_the_cap():
    result = plan(60.0, 1.20, bankroll=1000.0, max_stake_percent=5.0)

    assert result["max_stake_by_policy"] == 50.0
    assert result["stake_within_cap"] is False
    assert [row["code"] for row in result["warnings"]] == ["stake_above_cap"]


def test_plan_rejects_impossible_inputs():
    with pytest.raises(ValueError):
        plan(10.0, 1.0)
    with pytest.raises(ValueError):
        plan(0.0, 2.0)
    with pytest.raises(ValueError):
        plan(10.0, 2.0, bankroll=0.0)


def test_daily_exposure_respects_the_cap():
    within = daily_exposure(3, 50.0, bankroll=1000.0, max_daily_exposure_percent=20.0)
    over = daily_exposure(5, 50.0, bankroll=1000.0, max_daily_exposure_percent=20.0)

    assert within["total_stake"] == 150.0
    assert within["exposure_within_cap"] is True
    assert over["total_stake"] == 250.0
    assert over["exposure_within_cap"] is False
    assert over["warnings"][0]["code"] == "daily_exposure_above_cap"


def test_presets_use_the_same_arithmetic():
    presets = {row["key"]: row for row in preset_plans()["presets"]}

    assert presets["low_risk"]["plan"]["potential_profit"] == 10.0
    assert presets["high_odds"]["plan"]["potential_profit"] == 50.0
    assert presets["high_odds"]["plan"]["total_odds"] == 6.0


def test_bankroll_route_returns_the_arithmetic():
    from fastapi.testclient import TestClient

    from app.main import app

    body = (
        TestClient(app)
        .get(
            "/bankroll/plan",
            params={"stake": 50, "odds": 1.2, "bankroll": 1000, "target_profit": 10},
        )
        .json()
    )

    assert body["potential_return"] == 60.0
    assert body["potential_profit"] == 10.0
    assert body["daily_exposure"]["total_stake"] == 50.0
    assert body["stake_within_cap"] is True


def test_bankroll_route_rejects_odds_at_one():
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/bankroll/plan", params={"stake": 50, "odds": 1.0})

    assert response.status_code == 422


def test_presets_route_serves_both_readings():
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/bankroll/presets").json()

    assert {row["key"] for row in body["presets"]} == {"low_risk", "high_odds"}
