from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

WEIGHTS = {
    "historical_records": 25,
    "recent_statistics": 20,
    "home_away_splits": 15,
    "team_news": 15,
    "advanced_stats": 10,
    "source_freshness": 10,
    "market_information": 5,
}

def grade_data_quality(signals: Dict[str, Any]) -> dict:
    """Score completeness/reliability signals, each normalized to 0..1.

    Missing values receive zero; unknown is never treated as confirmed availability.
    This is a provisional completeness score, not a prediction probability.
    """
    score = 0.0
    present_weight = 0
    signals_breakdown: dict[str, Any] = {}

    for key, weight in WEIGHTS.items():
        value = signals.get(key)
        if value is None:
            signals_breakdown[key] = {"weight": weight, "value": 0.0, "status": "missing"}
            continue
        try:
            val = float(value)
            val = max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            val = 0.0
        score += val * weight
        present_weight += weight
        signals_breakdown[key] = {"weight": weight, "value": val, "status": "present"}

    # Missing dimensions remain missing and contribute zero to the full score.
    score_rounded = round(score)
    grade = "A" if score_rounded >= 85 else "B" if score_rounded >= 70 else "C" if score_rounded >= 50 else "D"
    return {
        "score": score_rounded,
        "grade": grade,
        "eligible_for_analysis": grade in {"A", "B"},
        "covered_weight": present_weight,
        "total_weight": sum(WEIGHTS.values()),
        "breakdown": signals_breakdown,
    }

def baseline_signals() -> dict[str, None]:
    """Sample imports have no verified research attached by default, so they must not qualify."""
    return {key: None for key in WEIGHTS}

def evaluate_fixture_signals(
    fixture: Any,
    custom_signals: Optional[Dict[str, Any]] = None,
    has_ratings: bool = False
) -> dict:
    """Derive initial quality signals from fixture metadata, overlaid with verified research and team stats."""
    signals: dict[str, Any] = baseline_signals()

    # Check for market odds availability
    has_odds = (
        getattr(fixture, "home_odds", None) is not None
        and getattr(fixture, "away_odds", None) is not None
    )
    if has_odds:
        signals["market_information"] = 1.0

    # Source freshness (basic check if recent source is attached)
    if getattr(fixture, "source", None) and fixture.source != "unknown":
        signals["source_freshness"] = 0.5

    # If team historical ratings and statistics exist, enrich quality dimensions
    if has_ratings:
        signals["historical_records"] = 0.85
        signals["recent_statistics"] = 0.80
        signals["home_away_splits"] = 0.75
        signals["advanced_stats"] = 0.70

    # Apply any custom/manual research signals
    if custom_signals:
        for k, v in custom_signals.items():
            if k in WEIGHTS and v is not None:
                signals[k] = v

    return signals
