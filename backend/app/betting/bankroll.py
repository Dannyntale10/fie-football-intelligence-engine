"""Paper-only bankroll arithmetic.

The point of this module is to state the price of a target, not to promise it.
At 1.20 a stake of 50 returns 60, so 83.3% of tickets must win just to break
even; at 6.00 a stake of 10 returns 60, and 16.7% must win. Those are the same
arithmetic seen from both ends of the 50/10 question. No probability estimate
is invented here and no stake is placed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_MAX_STAKE_PERCENT = 5.0
DEFAULT_MAX_DAILY_EXPOSURE_PERCENT = 20.0
DEFAULT_CURRENCY = "UGX"

NOTE = (
    "Paper arithmetic only. A strike rate is a break-even threshold, not a "
    "forecast; no stake is placed and no profit is claimed."
)


def required_strike_rate(total_odds: float) -> float:
    """Share of tickets that must win for the price to break even."""
    if total_odds <= 1.0:
        raise ValueError("total odds must be greater than 1.0")
    return 1.0 / total_odds


def plan(
    stake: float,
    total_odds: float,
    bankroll: Optional[float] = None,
    target_profit: Optional[float] = None,
    currency: str = DEFAULT_CURRENCY,
    max_stake_percent: float = DEFAULT_MAX_STAKE_PERCENT,
) -> Dict[str, Any]:
    """Describe one paper stake: return, break-even strike and policy checks."""
    if stake <= 0.0:
        raise ValueError("stake must be greater than 0")
    if total_odds <= 1.0:
        raise ValueError("total odds must be greater than 1.0")
    if bankroll is not None and bankroll <= 0.0:
        raise ValueError("bankroll must be greater than 0")
    if target_profit is not None and target_profit < 0.0:
        raise ValueError("target profit cannot be negative")
    if max_stake_percent <= 0.0:
        raise ValueError("max stake percent must be greater than 0")

    strike = required_strike_rate(total_odds)
    potential_return = stake * total_odds
    potential_profit = potential_return - stake
    stake_percent = stake / bankroll * 100.0 if bankroll else None
    cap = bankroll * max_stake_percent / 100.0 if bankroll else None
    within_cap = cap is None or stake <= cap + 1e-9
    stake_for_target = target_profit / (total_odds - 1.0) if target_profit is not None else None

    warnings: List[Dict[str, str]] = []
    if cap is not None and not within_cap:
        warnings.append(
            {
                "code": "stake_above_cap",
                "message": (
                    f"{stake:.2f} {currency} is above the {max_stake_percent:.0f}% "
                    f"cap of {cap:.2f} {currency}."
                ),
            }
        )
    if stake_for_target is not None and stake_for_target > stake + 1e-9:
        warnings.append(
            {
                "code": "stake_below_target",
                "message": (
                    f"{stake_for_target:.2f} {currency} is needed at these odds to "
                    f"return {target_profit:.2f} {currency} profit; the stake shown is less."
                ),
            }
        )

    return {
        "stake": round(stake, 2),
        "currency": currency.upper(),
        "total_odds": round(total_odds, 2),
        "potential_return": round(potential_return, 2),
        "potential_profit": round(potential_profit, 2),
        "implied_probability": round(strike, 4),
        "required_strike_rate_percent": round(strike * 100.0, 2),
        "break_even_wins_per_10": round(strike * 10.0, 2),
        "bankroll": round(bankroll, 2) if bankroll is not None else None,
        "stake_percent_of_bankroll": round(stake_percent, 2) if stake_percent is not None else None,
        "max_stake_by_policy": round(cap, 2) if cap is not None else None,
        "stake_within_cap": within_cap,
        "max_stake_percent": max_stake_percent,
        "target_profit": round(target_profit, 2) if target_profit is not None else None,
        "stake_for_target_profit": (
            round(stake_for_target, 2) if stake_for_target is not None else None
        ),
        "warnings": warnings,
        "note": NOTE,
    }


def daily_exposure(
    slips_per_day: int,
    stake_per_slip: float,
    bankroll: float,
    max_daily_exposure_percent: float = DEFAULT_MAX_DAILY_EXPOSURE_PERCENT,
    currency: str = DEFAULT_CURRENCY,
) -> Dict[str, Any]:
    """Total paper exposure for one day against a bankroll cap."""
    if slips_per_day <= 0:
        raise ValueError("slips_per_day must be greater than 0")
    if stake_per_slip <= 0.0:
        raise ValueError("stake_per_slip must be greater than 0")
    if bankroll <= 0.0:
        raise ValueError("bankroll must be greater than 0")
    if max_daily_exposure_percent <= 0.0:
        raise ValueError("max daily exposure percent must be greater than 0")

    total = slips_per_day * stake_per_slip
    cap = bankroll * max_daily_exposure_percent / 100.0
    within = total <= cap + 1e-9
    warnings = []
    if not within:
        warnings.append(
            {
                "code": "daily_exposure_above_cap",
                "message": (
                    f"{total:.2f} {currency} is above the {max_daily_exposure_percent:.0f}% "
                    f"daily cap of {cap:.2f} {currency}."
                ),
            }
        )
    return {
        "slips_per_day": slips_per_day,
        "stake_per_slip": round(stake_per_slip, 2),
        "total_stake": round(total, 2),
        "currency": currency.upper(),
        "bankroll": round(bankroll, 2),
        "max_daily_exposure_percent": max_daily_exposure_percent,
        "max_daily_stake": round(cap, 2),
        "exposure_within_cap": within,
        "warnings": warnings,
    }


def preset_plans(bankroll: float = 1000.0) -> Dict[str, Any]:
    """The two readings of 50/10 computed by the same code path."""
    return {
        "currency": DEFAULT_CURRENCY,
        "max_stake_percent": DEFAULT_MAX_STAKE_PERCENT,
        "presets": [
            {
                "key": "low_risk",
                "label": "50 staked, 10 profit",
                "description": "An 85%+ style ticket near 1.20.",
                "plan": plan(50.0, 1.20, bankroll=bankroll, target_profit=10.0),
            },
            {
                "key": "high_odds",
                "label": "10 staked, 50 profit",
                "description": "A 6.00 ticket that needs roughly one win in six.",
                "plan": plan(10.0, 6.00, bankroll=bankroll, target_profit=50.0),
            },
        ],
        "note": NOTE,
    }
