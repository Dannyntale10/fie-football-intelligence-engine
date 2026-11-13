"""Fit the one live knob from settled snapshots.

The multiplier is the ratio of goals actually scored after a snapshot to the
goals the frozen prior expected, aggregated over every usable snapshot
(spec 6.4). The later 30% of snapshots is used as a temporal check: the fitted
multiplier must beat a neutral one there by MIN_IMPROVEMENT. Because that
slice is also part of the aggregate, the check is a diagnostic rather than a
clean holdout.

Callers must pass snapshots oldest-first. ``/live/export`` orders them by
``observed_at``; a shuffled or reversed list would move the tail and silently
change the verdict.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from app.live.engine import INTENSITY_BOUNDS

MIN_SNAPSHOTS = 500
MIN_IMPROVEMENT = 0.002
MIN_SHIFT = 0.05
VALIDATION_FRACTION = 0.30


def _poisson_logpmf(goals: int, mean: float) -> float:
    mean = max(mean, 1e-9)
    return goals * math.log(mean) - mean - math.lgamma(goals + 1)


def _mean_log_likelihood(rows: List[Dict[str, Any]], intensity: float) -> float:
    total = 0.0
    count = 0
    for row in rows:
        for side in ("home", "away"):
            mean = float(row.get(f"remaining_{side}_lambda") or 0.0) * intensity
            goals = int(row.get(f"extra_{side}_goals") or 0)
            total += _poisson_logpmf(goals, mean)
            count += 1
    return total / count if count else 0.0


def fit_intensity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    usable = [
        row
        for row in rows
        if (row.get("remaining_home_lambda") or 0) > 0
        or (row.get("remaining_away_lambda") or 0) > 0
    ]
    if not usable:
        return {
            "status": "no_data",
            "k": 1.0,
            "sample": 0,
            "validation": None,
            "generated_at": None,
        }

    total_extra = sum(
        (row.get("extra_home_goals") or 0) + (row.get("extra_away_goals") or 0)
        for row in usable
    )
    total_remaining = sum(
        (row.get("remaining_home_lambda") or 0.0)
        + (row.get("remaining_away_lambda") or 0.0)
        for row in usable
    )
    if total_remaining <= 0:
        return {
            "status": "no_data",
            "k": 1.0,
            "sample": len(usable),
            "validation": None,
            "generated_at": None,
        }

    k = total_extra / total_remaining
    k = max(INTENSITY_BOUNDS[0], min(INTENSITY_BOUNDS[1], k))

    split = max(1, int(len(usable) * (1.0 - VALIDATION_FRACTION)))
    validation = usable[split:]
    fitted_ll = _mean_log_likelihood(validation, k)
    base_ll = _mean_log_likelihood(validation, 1.0)
    improvement = fitted_ll - base_ll

    if len(usable) < MIN_SNAPSHOTS:
        status = "insufficient_sample"
    elif improvement < MIN_IMPROVEMENT:
        status = "rejected"
    elif abs(k - 1.0) < MIN_SHIFT:
        status = "rejected"
    else:
        status = "fitted"

    return {
        "status": status,
        "k": round(k, 4),
        "sample": len(usable),
        "validation": {
            "n": len(validation),
            "improvement": round(improvement, 6),
        },
        "generated_at": None,
    }


def fetch_export_rows(url: str, api_key: str, days: int = 90) -> List[Dict[str, Any]]:
    import requests

    headers = {"X-API-Key": api_key} if api_key else {}
    response = requests.get(
        f"{url.rstrip('/')}/live/export",
        params={"days": days},
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("rows") or []
    return rows if isinstance(rows, list) else []
