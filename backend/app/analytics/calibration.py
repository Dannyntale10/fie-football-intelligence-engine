from __future__ import annotations
"""Probability calibration and leak-free walk-forward evaluation.

The rating model is only worth using if it beats the bookmaker's own
de-vigged prices on outcomes it never saw. This module measures exactly that:
it replays history in chronological order, refits ratings using only matches
played *before* each prediction window, and scores the resulting probabilities
against both the actual results and the market baseline.
"""
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.data_sources.fixtures import clean_competition_name
from app.analytics.probabilities import poisson_market_probabilities
from app.analytics.ratings import (
    DEFAULT_LEAGUE_AVG_AWAY,
    DEFAULT_LEAGUE_AVG_HOME,
    IGNORED_COMPETITIONS,
    decay_weight,
    fit_competition,
    rating_key,
)

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "calibration.json"

OUTCOMES = ("home_win", "draw", "away_win")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def brier_score(probabilities: Iterable[Dict[str, float]], labels: Iterable[str]) -> Optional[float]:
    """Multiclass Brier score: mean squared error across the probability vector."""
    total, count = 0.0, 0
    for probs, label in zip(probabilities, labels):
        total += sum((probs.get(k, 0.0) - (1.0 if k == label else 0.0)) ** 2 for k in OUTCOMES)
        count += 1
    return round(total / count, 6) if count else None


def log_loss(probabilities: Iterable[Dict[str, float]], labels: Iterable[str], floor: float = 1e-6) -> Optional[float]:
    total, count = 0.0, 0
    for probs, label in zip(probabilities, labels):
        total -= math.log(max(floor, probs.get(label, 0.0)))
        count += 1
    return round(total / count, 6) if count else None


def reliability_table(pairs: List[Tuple[float, int]], bins: int = 10) -> List[Dict[str, Any]]:
    """Bin predictions by confidence and compare to the observed frequency."""
    buckets: List[List[Tuple[float, int]]] = [[] for _ in range(bins)]
    for confidence, hit in pairs:
        idx = min(bins - 1, max(0, int(confidence * bins)))
        buckets[idx].append((confidence, hit))

    table = []
    for idx, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_conf = sum(c for c, _ in bucket) / len(bucket)
        hit_rate = sum(h for _, h in bucket) / len(bucket)
        table.append({
            "bin": f"{idx / bins:.1f}-{(idx + 1) / bins:.1f}",
            "predictions": len(bucket),
            "mean_confidence": round(mean_conf, 4),
            "observed_rate": round(hit_rate, 4),
            "gap": round(hit_rate - mean_conf, 4),
        })
    return table


def expected_calibration_error(pairs: List[Tuple[float, int]], bins: int = 10) -> Optional[float]:
    """Sample-weighted mean |observed - predicted| across confidence bins."""
    if not pairs:
        return None
    table = reliability_table(pairs, bins=bins)
    total = len(pairs)
    ece = sum(row["predictions"] / total * abs(row["gap"]) for row in table)
    return round(ece, 4)


def _outcome_label(home: int, away: int) -> str:
    if home > away:
        return "home_win"
    if home == away:
        return "draw"
    return "away_win"


def _fair_1x2(home_odds: float, draw_odds: float, away_odds: float) -> Dict[str, float]:
    raw = {"home_win": 1.0 / home_odds, "draw": 1.0 / draw_odds, "away_win": 1.0 / away_odds}
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def _rows_for(matches: List[Fixture], reference: datetime) -> List[Dict[str, Any]]:
    rows = []
    for m in matches:
        kickoff = _utc(m.kickoff_utc)
        rows.append({
            "home": rating_key(m.home_team),
            "away": rating_key(m.away_team),
            "home_goals": int(m.home_score),
            "away_goals": int(m.away_score),
            "weight": decay_weight(kickoff, reference),
            "kickoff": kickoff,
        })
    return rows


def _weighted_averages(rows: List[Dict[str, Any]]) -> Tuple[float, float]:
    weight = sum(r["weight"] for r in rows) or 1.0
    return (
        sum(r["home_goals"] * r["weight"] for r in rows) / weight,
        sum(r["away_goals"] * r["weight"] for r in rows) / weight,
    )


def iter_walkforward_predictions(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    min_weighted_matches: float = 3.0,
    rho: float = -0.10,
):
    """Yield out-of-sample predictions with ratings that only know the past.

    Each item is a dict with the fixture, model probabilities, de-vigged market
    probabilities, the realized label and the 1X2 prices. Both the calibration
    report and the backtest consume this so they cannot drift apart.
    """
    query = select(Fixture).where(
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None),
        Fixture.home_odds.isnot(None),
        Fixture.draw_odds.isnot(None),
        Fixture.away_odds.isnot(None),
        Fixture.home_odds > 1.0,
        Fixture.draw_odds > 1.0,
        Fixture.away_odds > 1.0,
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    matches = db.scalars(query.order_by(Fixture.kickoff_utc)).all()
    by_competition: Dict[str, List[Fixture]] = defaultdict(list)
    for m in matches:
        comp = clean_competition_name(m.competition or "")
        if not comp or comp in IGNORED_COMPETITIONS:
            continue
        by_competition[comp].append(m)

    for comp, comp_matches in by_competition.items():
        start = _utc(comp_matches[0].kickoff_utc)
        last = _utc(comp_matches[-1].kickoff_utc)
        window_start = start

        while window_start <= last:
            window_end = window_start + timedelta(days=refit_days)
            past = [m for m in comp_matches if _utc(m.kickoff_utc) < window_start]
            window = [m for m in comp_matches if window_start <= _utc(m.kickoff_utc) < window_end]
            window_start = window_end

            if len(past) < 30 or not window:
                continue

            rows = _rows_for(past, window_end)
            avg_home, avg_away = _weighted_averages(rows)
            avg_home = avg_home or DEFAULT_LEAGUE_AVG_HOME
            avg_away = avg_away or DEFAULT_LEAGUE_AVG_AWAY
            fitted = fit_competition(rows, avg_home, avg_away, iterations=6)

            for m in window:
                home = fitted.get(rating_key(m.home_team))
                away = fitted.get(rating_key(m.away_team))
                if not home or not away:
                    continue
                if home["weighted_matches"] < min_weighted_matches or away["weighted_matches"] < min_weighted_matches:
                    continue

                home_lambda = max(0.1, min(8.0, home["attack"] * away["defence"] * avg_home * (1.0 + min(0.35, home["home_boost"] * 0.25))))
                away_lambda = max(0.1, min(8.0, away["attack"] * home["defence"] * avg_away))
                markets = poisson_market_probabilities(home_lambda, away_lambda, rho=rho)["markets"]
                yield {
                    "fixture": m,
                    "competition": comp,
                    "home_lambda": home_lambda,
                    "away_lambda": away_lambda,
                    "model": {k: markets[k] for k in OUTCOMES},
                    "market": _fair_1x2(m.home_odds, m.draw_odds, m.away_odds),
                    "odds": {"home_win": float(m.home_odds), "draw": float(m.draw_odds), "away_win": float(m.away_odds)},
                    "label": _outcome_label(int(m.home_score), int(m.away_score)),
                }


def run_walk_forward(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    min_weighted_matches: float = 3.0,
    rho: float = -0.10,
) -> Dict[str, Any]:
    """Replay history with ratings that only ever see the past.

    Predictions are grouped into ``refit_days`` windows. Before each window the
    model is refitted on every match that kicked off earlier, which removes the
    lookahead bias in the legacy backtest (where ratings were computed from all
    results, including the ones being predicted).
    """
    model_probs: List[Dict[str, float]] = []
    market_probs: List[Dict[str, float]] = []
    labels: List[str] = []
    per_competition: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"model": [], "market": [], "labels": []}
    )

    for prediction in iter_walkforward_predictions(
        db,
        competition=competition,
        refit_days=refit_days,
        min_weighted_matches=min_weighted_matches,
        rho=rho,
    ):
        model_probs.append(prediction["model"])
        market_probs.append(prediction["market"])
        labels.append(prediction["label"])
        bucket = per_competition[prediction["competition"]]
        bucket["model"].append(prediction["model"])
        bucket["market"].append(prediction["market"])
        bucket["labels"].append(prediction["label"])

    if not labels:
        return {
            "verdict": "insufficient_data",
            "message": "Not enough rated matches before each window to evaluate the model.",
            "n_matches": 0,
        }

    per_competition_report = {}
    for comp, bucket in per_competition.items():
        pairs = [
            (max(p.values()), 1 if max(p, key=p.get) == lbl else 0)
            for p, lbl in zip(bucket["model"], bucket["labels"])
        ]
        per_competition_report[comp] = {
            "n_matches": len(bucket["labels"]),
            "model_brier": brier_score(bucket["model"], bucket["labels"]),
            "market_brier": brier_score(bucket["market"], bucket["labels"]),
            "model_log_loss": log_loss(bucket["model"], bucket["labels"]),
            "market_log_loss": log_loss(bucket["market"], bucket["labels"]),
            "ece": expected_calibration_error(pairs),
        }

    pairs = [
        (max(p.values()), 1 if max(p, key=p.get) == lbl else 0)
        for p, lbl in zip(model_probs, labels)
    ]
    correct = sum(hit for _, hit in pairs)
    model_b = brier_score(model_probs, labels)
    market_b = brier_score(market_probs, labels)
    model_ll = log_loss(model_probs, labels)
    market_ll = log_loss(market_probs, labels)
    ece = expected_calibration_error(pairs)

    improvement = round((market_b or 0.0) - (model_b or 0.0), 6)
    if improvement > 0.002:
        verdict = "model_beats_market"
    elif improvement < -0.002:
        verdict = "market_beats_model"
    else:
        verdict = "model_matches_market"

    market_correct = sum(
        1 for p, lbl in zip(market_probs, labels) if max(p, key=p.get) == lbl
    )

    # Demand a larger edge when the model is miscalibrated or demonstrably
    # weaker than the de-vigged market on unseen matches. This is consumed by
    # the selection engine, so an unvalidated model cannot silently keep
    # generating three-percent "edges".
    deficit = max(0.0, (model_b or 0.0) - (market_b or 0.0))
    multiplier = round(min(3.0, max(1.0, 1.0 + 10.0 * deficit + 2.0 * (ece or 0.0))), 2)

    report = {
        "verdict": verdict,
        "n_matches": len(labels),
        "n_competitions": len(per_competition_report),
        "refit_days": refit_days,
        "min_weighted_matches": min_weighted_matches,
        "model": {
            "brier": model_b,
            "log_loss": model_ll,
            "ece": ece,
            "accuracy_percent": round(correct / len(labels) * 100.0, 2),
        },
        "market": {
            "brier": market_b,
            "log_loss": market_ll,
            "accuracy_percent": round(market_correct / len(labels) * 100.0, 2),
        },
        "skill": {
            "brier_improvement": improvement,
            "log_loss_improvement": round((market_ll or 0.0) - (model_ll or 0.0), 6),
            "validated": verdict == "model_beats_market",
        },
        "reliability": reliability_table(pairs),
        "per_competition": dict(sorted(per_competition_report.items(), key=lambda kv: -kv[1]["n_matches"])),
        "edge_threshold_multiplier": multiplier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def save_report(report: Dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2))


def load_report() -> Optional[Dict[str, Any]]:
    if not REPORT_PATH.exists():
        return None
    try:
        return json.loads(REPORT_PATH.read_text())
    except (ValueError, OSError):
        return None


def edge_threshold_multiplier() -> float:
    """Multiplier applied to the minimum edge, widened when the model is miscalibrated."""
    report = load_report()
    if not report:
        return 1.0
    try:
        return float(report.get("edge_threshold_multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0
