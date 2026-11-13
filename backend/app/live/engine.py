"""Conditional win probabilities for a match already in play.

The model takes the pre-match goal expectation as fixed evidence and asks only
what the remaining goals can do to the current score. It deliberately knows
nothing about red cards, lineups or shots: those are not in the feed, and
inventing them would be worse than conditioning on less.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Tuple

REGULATION_MINUTES = 90
MAX_REMAINING_GOALS = 10
INTENSITY_BOUNDS = (0.5, 2.0)

CALIBRATION_PATH = Path(__file__).resolve().parents[3] / "data" / "live_calibration.json"


@dataclass(frozen=True)
class LiveState:
    home_goals: int
    away_goals: int
    home_lambda: float
    away_lambda: float
    minute: int


IntensityProvider = Callable[[LiveState], Tuple[float, str]]


def _pmf(goals: int, mean: float) -> float:
    if mean <= 0.0:
        return 1.0 if goals == 0 else 0.0
    return math.exp(-mean) * (mean ** goals) / math.factorial(goals)


def outcome_probabilities(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_lambda: float,
    remaining_fraction: float,
    intensity: float = 1.0,
) -> Dict[str, float]:
    """Home/draw/away probabilities after conditioning on the current score."""
    fraction = max(0.0, min(1.0, float(remaining_fraction)))
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], float(intensity)))

    home_mean = max(0.0, float(home_lambda) * fraction * k)
    away_mean = max(0.0, float(away_lambda) * fraction * k)

    home_pmf = [_pmf(extra, home_mean) for extra in range(MAX_REMAINING_GOALS + 1)]
    away_pmf = [_pmf(extra, away_mean) for extra in range(MAX_REMAINING_GOALS + 1)]

    home_win = draw = away_win = total = 0.0
    for extra_home, p_home in enumerate(home_pmf):
        for extra_away, p_away in enumerate(away_pmf):
            probability = p_home * p_away
            total += probability
            final_home = home_goals + extra_home
            final_away = away_goals + extra_away
            if final_home > final_away:
                home_win += probability
            elif final_home == final_away:
                draw += probability
            else:
                away_win += probability

    if total <= 0.0:
        return {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
    return {
        "home_win": home_win / total,
        "draw": draw / total,
        "away_win": away_win / total,
    }


def default_intensity(state: LiveState) -> Tuple[float, str]:
    return 1.0, "default"


def load_intensity() -> Dict[str, object]:
    """Read the fitted multiplier, or report the neutral default."""
    try:
        payload = json.loads(CALIBRATION_PATH.read_text())
    except (OSError, ValueError):
        return {"k": 1.0, "source": "default"}
    if not isinstance(payload, dict) or payload.get("status") != "fitted":
        return {"k": 1.0, "source": "default"}
    try:
        k = float(payload["k"])
    except (KeyError, TypeError, ValueError):
        return {"k": 1.0, "source": "default"}
    if not INTENSITY_BOUNDS[0] <= k <= INTENSITY_BOUNDS[1]:
        return {"k": 1.0, "source": "default"}
    return {"k": k, "source": "fitted"}


def fitted_intensity(state: LiveState) -> Tuple[float, str]:
    artifact = load_intensity()
    return float(artifact["k"]), str(artifact["source"])
