"""Choose the rating half-life by comparing configurations, not snapshots.

A stored champion score was measured on older data. Comparing it to a fresh
challenger would either freeze the model or reward a data refresh rather than
a better configuration. Every candidate is therefore replayed on the same
leak-free windows in the same run.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.analytics.calibration import brier_score, iter_walkforward_predictions, log_loss

HALF_LIFE_GRID = (90.0, 120.0, 150.0, 200.0)
DEFAULT_HALF_LIFE = 150.0
PROMOTION_MARGIN = 0.002
COMPETITION_FLOOR = 0.005
MIN_COMPETITION_SAMPLE = 150

CHAMPION_PATH = Path(__file__).resolve().parents[3] / "data" / "model_champion.json"


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def evaluate_config(
    db: Session,
    *,
    half_life_days: float,
    window_days: int = 730,
    refit_days: int = 14,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    probabilities: List[Dict[str, float]] = []
    labels: List[str] = []
    per_competition: Dict[str, Dict[str, List]] = defaultdict(
        lambda: {"probs": [], "labels": []}
    )

    for prediction in iter_walkforward_predictions(
        db, refit_days=refit_days, half_life_days=half_life_days
    ):
        kickoff = prediction["fixture"].kickoff_utc
        if kickoff is None or _utc(kickoff) < cutoff:
            continue
        probabilities.append(prediction["model"])
        labels.append(prediction["label"])
        bucket = per_competition[prediction["competition"]]
        bucket["probs"].append(prediction["model"])
        bucket["labels"].append(prediction["label"])

    if not labels:
        return {
            "half_life_days": half_life_days,
            "n_matches": 0,
            "brier": None,
            "log_loss": None,
            "per_competition": {},
        }

    by_competition = {}
    for competition, bucket in per_competition.items():
        by_competition[competition] = {
            "n": len(bucket["labels"]),
            "brier": brier_score(bucket["probs"], bucket["labels"]),
            "log_loss": log_loss(bucket["probs"], bucket["labels"]),
        }

    return {
        "half_life_days": half_life_days,
        "n_matches": len(labels),
        "brier": brier_score(probabilities, labels),
        "log_loss": log_loss(probabilities, labels),
        "per_competition": by_competition,
    }


def gate_decision(
    results: List[Dict[str, Any]],
    champion_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    usable = [row for row in results if row["n_matches"]]
    if not usable:
        return {"promote": False, "reason": "no_evaluation_data", "challenger": None, "champion": None}

    if champion_config is None:
        challenger = min(usable, key=lambda row: row["brier"])
        return {
            "promote": True,
            "reason": "bootstrap",
            "challenger": challenger,
            "champion": challenger,
        }

    champion_half_life = float(
        champion_config.get("half_life_days", DEFAULT_HALF_LIFE)
    )
    champion = next(
        (row for row in usable if float(row["half_life_days"]) == champion_half_life),
        None,
    )
    if champion is None:
        return {
            "promote": False,
            "reason": "champion_config_not_evaluated",
            "challenger": None,
            "champion": None,
        }

    challenger = min(usable, key=lambda row: row["brier"])
    if float(challenger["half_life_days"]) == champion_half_life:
        return {
            "promote": False,
            "reason": "config_unchanged",
            "challenger": challenger,
            "champion": champion,
        }
    if challenger["brier"] > champion["brier"] - PROMOTION_MARGIN:
        reason = "brier_gain_too_small"
    elif challenger["log_loss"] > champion["log_loss"]:
        reason = "log_loss_worse"
    else:
        regressed = [
            name
            for name, metrics in challenger["per_competition"].items()
            if metrics["n"] >= MIN_COMPETITION_SAMPLE
            and metrics["brier"]
            > champion["per_competition"].get(name, {}).get("brier", 1.0)
            + COMPETITION_FLOOR
        ]
        reason = (
            f"competition_regression:{regressed[0]}"
            if regressed
            else "brier_improved"
        )

    return {
        "promote": reason == "brier_improved",
        "reason": reason,
        "challenger": challenger,
        "champion": champion,
    }


def load_champion() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(CHAMPION_PATH.read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def save_champion(payload: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else CHAMPION_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
