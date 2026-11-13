"""Model-market blending for 1X2 probabilities.

The rating model and the bookmaker measure the same thing from different
evidence. Blending them in log-odds space (a per-outcome log-opinion pool)
produces a probability that is anchored to the market without throwing the
model away.

The weight is fitted honestly: walk-forward predictions are ordered by kickoff,
the earliest 70% chooses the weight, and the latest 30% scores it. A weight
fitted on matches it is later judged on would be worthless, so the split is
chronological and never shuffled. Per-competition weights are shrunk toward
the global weight because a single league rarely carries enough history to fit
its own blend.

Only de-vigged bookmaker prices are blended. Estimated or model-derived prices
are never treated as a market prior.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.analytics.calibration import brier_score, iter_walkforward_predictions, log_loss
from app.analytics.ratings import RATING_HALF_LIFE_DAYS

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "blending.json"

OUTCOMES = ("home_win", "draw", "away_win")

TRAIN_FRACTION = 0.70
MIN_PREDICTIONS = 200
MIN_HOLDOUT = 60
MIN_COMPETITION_TRAIN = 150
MIN_COMPETITION_HOLDOUT = 20
PRIOR_STRENGTH = 150.0
VALIDATION_MARGIN = 0.002

_EPS = 1e-6
_REPORT_CACHE: Dict[str, Any] = {"mtime": None, "report": None}


def logit(probability: float) -> float:
    p = min(max(float(probability), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def sigmoid(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _clamp_weight(weight: float) -> float:
    return min(1.0, max(0.0, float(weight)))


def _temperature(value: float) -> float:
    return max(0.25, min(4.0, float(value)))


def _blend_outcome(
    model_logit: float, market_logit: float, weight: float, temperature: float = 1.0
) -> float:
    scaled = model_logit / _temperature(temperature)
    return sigmoid(weight * scaled + (1.0 - weight) * market_logit)


def blend_1x2(
    model: Dict[str, float],
    market: Dict[str, float],
    weight: float,
    temperature: float = 1.0,
) -> Dict[str, float]:
    """Pool model and market probabilities in log-odds space.

    ``weight`` is the share given to the model: 1.0 keeps the model, 0.0 keeps
    the market. ``temperature`` rescales the model logits first, so an
    overconfident model can still contribute information. The pooled vector is
    renormalised so the three outcomes still sum to one. Missing market
    probabilities fall back to the model value.
    """
    weight = _clamp_weight(weight)
    scale = _temperature(temperature)
    pooled: Dict[str, float] = {}
    for key in OUTCOMES:
        model_probability = model.get(key)
        market_probability = market.get(key)
        if model_probability is None:
            continue
        if market_probability is None or market_probability <= 0.0:
            pooled[key] = float(model_probability)
            continue
        if weight >= 1.0:
            scaled = logit(model_probability) / scale
            pooled[key] = sigmoid(scaled)
        elif weight <= 0.0:
            pooled[key] = float(market_probability)
        else:
            pooled[key] = _blend_outcome(
                logit(model_probability), logit(market_probability), weight, scale
            )

    total = sum(pooled.values())
    if total <= 0.0:
        return {key: float(value) for key, value in pooled.items()}
    return {key: round(value / total, 6) for key, value in pooled.items()}


def _row_logits(row: Dict[str, Any]) -> Tuple[Tuple[float, float], ...]:
    return tuple(
        (logit(row["model"][key]), logit(row["market"][key])) for key in OUTCOMES
    )


def _mean_log_loss(
    logits: Sequence[Tuple[Tuple[float, float], ...]],
    labels: Sequence[str],
    weight: float,
    temperature: float,
) -> float:
    total = 0.0
    for row_logits, label in zip(logits, labels):
        pooled = [
            _blend_outcome(model_logit, market_logit, weight, temperature)
            for model_logit, market_logit in row_logits
        ]
        normaliser = sum(pooled) or 1.0
        probability = pooled[OUTCOMES.index(label)] / normaliser
        total -= math.log(max(_EPS, probability))
    return total / len(labels) if labels else float("inf")


def fit_parameters(
    rows: Sequence[Dict[str, Any]], labels: Sequence[str]
) -> Tuple[float, float, float]:
    """Fit the model weight and temperature on ``rows`` by log loss.

    A coarse grid finds the region for each temperature, then the weight is
    refined. Returns ``(weight, temperature, log_loss)``.
    """
    if not rows:
        return 0.0, 1.0, float("inf")
    logits = [_row_logits(row) for row in rows]

    best = (0.0, 1.0, float("inf"))
    for temperature in (0.5, 0.6, 0.75, 1.0, 1.25, 1.5, 2.0):
        temp_weight, temp_loss = 0.0, float("inf")
        for i in range(21):
            weight = i / 20.0
            loss = _mean_log_loss(logits, labels, weight, temperature)
            if loss < temp_loss:
                temp_weight, temp_loss = weight, loss

        low = max(0.0, temp_weight - 0.05)
        high = min(1.0, temp_weight + 0.05)
        step = (high - low) / 20.0 if high > low else 0.0
        for i in range(21):
            weight = low + i * step
            loss = _mean_log_loss(logits, labels, weight, temperature)
            if loss < temp_loss:
                temp_weight, temp_loss = weight, loss

        if temp_loss < best[2] - 1e-12:
            best = (temp_weight, temperature, temp_loss)
    weight, temperature, loss = best
    return round(weight, 3), round(temperature, 2), round(loss, 6)


def fit_weight(rows: Sequence[Dict[str, Any]], labels: Sequence[str]) -> Tuple[float, float]:
    """Backwards-compatible wrapper returning ``(weight, log_loss)`` at the fitted temperature."""
    weight, _temperature_value, loss = fit_parameters(rows, labels)
    return weight, loss


def _validated_rows(predictions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for prediction in predictions:
        model = prediction.get("model") or {}
        market = prediction.get("market") or {}
        label = prediction.get("label")
        if label not in OUTCOMES:
            continue
        if any(not isinstance(model.get(key), (int, float)) or model[key] <= 0.0 for key in OUTCOMES):
            continue
        if any(not isinstance(market.get(key), (int, float)) or market[key] <= 0.0 for key in OUTCOMES):
            continue
        fixture = prediction.get("fixture")
        kickoff = getattr(fixture, "kickoff_utc", None) or prediction.get("kickoff")
        if isinstance(kickoff, datetime) and kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        rows.append(
            {
                "model": {key: float(model[key]) for key in OUTCOMES},
                "market": {key: float(market[key]) for key in OUTCOMES},
                "label": label,
                "competition": prediction.get("competition") or "Unknown",
                "kickoff": kickoff,
            }
        )
    return rows


def _metrics(rows: Sequence[Dict[str, Any]], probabilities: Sequence[Dict[str, float]]) -> Dict[str, Any]:
    labels = [row["label"] for row in rows]
    correct = sum(
        1 for probs, label in zip(probabilities, labels) if max(probs, key=probs.get) == label
    )
    return {
        "n_matches": len(labels),
        "brier": brier_score(probabilities, labels),
        "log_loss": log_loss(probabilities, labels),
        "accuracy_percent": round(correct / len(labels) * 100.0, 2) if labels else None,
    }


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return str(value) if value else None


def build_report(
    predictions: Iterable[Dict[str, Any]],
    train_fraction: float = TRAIN_FRACTION,
    prior_strength: float = PRIOR_STRENGTH,
) -> Dict[str, Any]:
    """Fit the blend on a chronological split of walk-forward predictions."""
    rows = sorted(
        _validated_rows(predictions),
        key=lambda row: row["kickoff"] or datetime.min.replace(tzinfo=timezone.utc),
    )
    params = {
        "train_fraction": train_fraction,
        "prior_strength": prior_strength,
        "min_predictions": MIN_PREDICTIONS,
        "min_holdout": MIN_HOLDOUT,
        "scope": "1x2",
    }

    if len(rows) < MIN_PREDICTIONS:
        return {
            "status": "insufficient_data",
            "message": (
                f"Only {len(rows)} leak-free 1X2 predictions are available; "
                f"{MIN_PREDICTIONS} are needed to fit a blend honestly."
            ),
            "n_matches": len(rows),
            "params": params,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    holdout_size = max(MIN_HOLDOUT, int(round(len(rows) * (1.0 - train_fraction))))
    split = len(rows) - holdout_size
    train, holdout = rows[:split], rows[split:]
    if len(holdout) < MIN_HOLDOUT or not train:
        return {
            "status": "insufficient_data",
            "message": "History is too short for a chronological holdout.",
            "n_matches": len(rows),
            "params": params,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    global_weight, global_temperature, train_log_loss = fit_parameters(
        train, [row["label"] for row in train]
    )

    model_probs = [row["model"] for row in holdout]
    market_probs = [row["market"] for row in holdout]
    labels = [row["label"] for row in holdout]
    blend_probs = [
        blend_1x2(row["model"], row["market"], global_weight, global_temperature)
        for row in holdout
    ]

    model_metrics = _metrics(holdout, model_probs)
    market_metrics = _metrics(holdout, market_probs)
    blend_metrics = _metrics(holdout, blend_probs)

    improvement = round((market_metrics["brier"] or 0.0) - (blend_metrics["brier"] or 0.0), 6)
    if improvement > VALIDATION_MARGIN:
        verdict = "blend_beats_market"
    elif improvement < -VALIDATION_MARGIN:
        verdict = "market_beats_blend"
    else:
        verdict = "blend_matches_market"

    per_competition: Dict[str, Dict[str, Any]] = {}
    competition_weights: Dict[str, float] = {}
    train_by_competition: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in train:
        train_by_competition[row["competition"]].append(row)
    holdout_by_competition: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in holdout:
        holdout_by_competition[row["competition"]].append(row)

    for competition, comp_train in train_by_competition.items():
        if len(comp_train) < MIN_COMPETITION_TRAIN:
            continue
        comp_holdout = holdout_by_competition.get(competition, [])
        if len(comp_holdout) < MIN_COMPETITION_HOLDOUT:
            continue

        raw_weight, _, _ = fit_parameters(comp_train, [row["label"] for row in comp_train])
        shrunk = (
            len(comp_train) * raw_weight + prior_strength * global_weight
        ) / (len(comp_train) + prior_strength)
        shrunk = round(shrunk, 3)
        competition_weights[competition] = shrunk

        comp_labels = [row["label"] for row in comp_holdout]
        per_competition[competition] = {
            "n_train": len(comp_train),
            "n_holdout": len(comp_holdout),
            "raw_weight": raw_weight,
            "weight": shrunk,
            "model_brier": brier_score([row["model"] for row in comp_holdout], comp_labels),
            "market_brier": brier_score([row["market"] for row in comp_holdout], comp_labels),
            "blend_brier": brier_score(
                [
                    blend_1x2(
                        row["model"], row["market"], shrunk, global_temperature
                    )
                    for row in comp_holdout
                ],
                comp_labels,
            ),
        }

    if global_weight <= 0.005:
        message = (
            f"The fitted weight is {global_weight:.3f}: on this history the model adds "
            "nothing to the de-vigged market price, so the blend falls back to the "
            "market. That is the honest result. Improve the model before expecting "
            "independent 1X2 edges; EV slips stay gated off."
        )
    elif verdict == "blend_beats_market":
        message = (
            f"The blend beat the de-vigged market on the holdout "
            f"(Brier {blend_metrics['brier']} vs {market_metrics['brier']}). "
            "That is one sample, not a guarantee."
        )
    elif verdict == "market_beats_blend":
        message = (
            f"The holdout still favours the market (Brier {market_metrics['brier']} vs "
            f"{blend_metrics['brier']}). The blend is anchored to the market at weight "
            f"{global_weight:.2f}, but prices remain the stronger signal; EV slips stay "
            "gated off by the calibration verdict."
        )
    else:
        message = (
            f"Blend and market are within {VALIDATION_MARGIN} Brier on the holdout "
            f"({blend_metrics['brier']} vs {market_metrics['brier']}). "
            "Treat the difference as noise."
        )

    return {
        "status": "fitted",
        "scope": "1x2",
        "method": "per-outcome log-opinion pool, renormalised",
        "verdict": verdict,
        "weights": {
            "default": global_weight,
            "temperature": global_temperature,
            "per_competition": competition_weights,
        },
        "train": {
            "n_matches": len(train),
            "n_competitions": len(train_by_competition),
            "weight": global_weight,
            "temperature": global_temperature,
            "log_loss": train_log_loss,
            "from": _iso(train[0]["kickoff"]),
            "to": _iso(train[-1]["kickoff"]),
        },
        "holdout": {
            "n_matches": len(holdout),
            "n_competitions": len(holdout_by_competition),
            "from": _iso(holdout[0]["kickoff"]),
            "to": _iso(holdout[-1]["kickoff"]),
            "model": model_metrics,
            "market": market_metrics,
            "blend": blend_metrics,
            "skill": {
                "brier_improvement_over_market": improvement,
                "log_loss_improvement_over_market": round(
                    (market_metrics["log_loss"] or 0.0) - (blend_metrics["log_loss"] or 0.0),
                    6,
                ),
                "validated": verdict == "blend_beats_market",
            },
        },
        "per_competition": dict(
            sorted(per_competition.items(), key=lambda kv: -kv[1]["n_holdout"])
        ),
        "params": params,
        "message": message,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def fit_report(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    train_fraction: float = TRAIN_FRACTION,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
) -> Dict[str, Any]:
    """Run the leak-free walk-forward and fit the blend on the result."""
    predictions = iter_walkforward_predictions(
        db,
        competition=competition,
        refit_days=refit_days,
        half_life_days=half_life_days,
    )
    return build_report(predictions, train_fraction=train_fraction)


def save_report(report: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2))
    _REPORT_CACHE["mtime"] = None
    _REPORT_CACHE["report"] = None


def load_report() -> Optional[Dict[str, Any]]:
    """Load the saved report, reusing the parsed copy until the file changes."""
    try:
        mtime = REPORT_PATH.stat().st_mtime
    except OSError:
        return None
    if _REPORT_CACHE["report"] is not None and _REPORT_CACHE["mtime"] == mtime:
        return _REPORT_CACHE["report"]
    try:
        report = json.loads(REPORT_PATH.read_text())
    except (ValueError, OSError):
        return None
    if not isinstance(report, dict):
        return None
    _REPORT_CACHE["mtime"] = mtime
    _REPORT_CACHE["report"] = report
    return report


def active_weight(competition: Optional[str] = None) -> Optional[float]:
    """The model weight to use right now, or ``None`` when no blend is fitted."""
    return blend_status(competition).get("weight")


def blend_status(competition: Optional[str] = None) -> Dict[str, Any]:
    """Describe the active blend without inventing one when it is absent."""
    report = load_report()
    if not report or report.get("status") != "fitted":
        return {
            "fitted": False,
            "weight": None,
            "scope": "1x2",
            "competition": competition,
            "reason": "no_fitted_blend_report",
        }

    weights = report.get("weights") or {}
    per_competition = weights.get("per_competition") or {}
    weight = per_competition.get(competition) if competition else None
    source = "per_competition" if weight is not None else "default"
    if weight is None:
        weight = weights.get("default")
    if weight is None:
        return {
            "fitted": False,
            "weight": None,
            "scope": "1x2",
            "competition": competition,
            "reason": "report_has_no_weight",
        }

    return {
        "fitted": True,
        "weight": float(weight),
        "temperature": float(weights.get("temperature", 1.0)),
        "weight_source": source,
        "scope": report.get("scope", "1x2"),
        "competition": competition,
        "verdict": report.get("verdict"),
        "generated_at": report.get("generated_at"),
    }
