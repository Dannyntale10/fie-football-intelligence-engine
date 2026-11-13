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
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.data_sources.fixtures import clean_competition_name
from app.analytics.probabilities import outcome_1x2_from_lambdas
from app.analytics.ratings import (
    DEFAULT_LEAGUE_AVG_AWAY,
    DEFAULT_LEAGUE_AVG_HOME,
    IGNORED_COMPETITIONS,
    MIN_MATCH_WEIGHT,
    RATING_HALF_LIFE_DAYS,
    fit_strength_arrays,
    rating_key,
)

REPORT_PATH = Path(__file__).resolve().parents[3] / "data" / "calibration.json"

OUTCOMES = ("home_win", "draw", "away_win")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _ns(dt: datetime) -> int:
    """Exact epoch nanoseconds; a truncated second would admit a match up to 1s early."""
    aware = _utc(dt)
    return int(aware.timestamp()) * 1_000_000_000 + aware.microsecond * 1_000


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


_NANOS_PER_DAY = 86_400_000_000_000


def _walkforward_for_competition(
    competition: str,
    matches: List[Fixture],
    refit_days: int,
    min_weighted_matches: float,
    rho: float,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
    since: Optional[datetime] = None,
):
    """Yield out-of-sample predictions for one competition, in kickoff order.

    Matches are held as arrays so each 14-day window costs a few vector
    operations rather than a Python loop over the whole history. The model
    fitted for a window only ever sees matches that kicked off before it.

    ``since`` only limits which windows and matches are *scored*: the fit for
    each window still sees the same history it would without it, so a caller
    filtering by date gets identical numbers for a fraction of the work.
    """
    team_keys = sorted(
        {rating_key(m.home_team) for m in matches}
        | {rating_key(m.away_team) for m in matches}
    )
    index = {key: i for i, key in enumerate(team_keys)}
    n_teams = len(team_keys)

    home_idx = np.fromiter(
        (index[rating_key(m.home_team)] for m in matches), dtype=np.int64, count=len(matches)
    )
    away_idx = np.fromiter(
        (index[rating_key(m.away_team)] for m in matches), dtype=np.int64, count=len(matches)
    )
    home_goals = np.fromiter(
        (int(m.home_score) for m in matches), dtype=np.float64, count=len(matches)
    )
    away_goals = np.fromiter(
        (int(m.away_score) for m in matches), dtype=np.float64, count=len(matches)
    )
    kick_ns = np.fromiter(
        (_ns(m.kickoff_utc) for m in matches),
        dtype=np.int64,
        count=len(matches),
    )

    step_ns = int(refit_days * _NANOS_PER_DAY)
    window_start = int(kick_ns.min())
    last_kick = int(kick_ns.max())
    since_ns = _ns(since) if since is not None else None

    while window_start <= last_kick:
        window_end = window_start + step_ns
        past = kick_ns < window_start
        floor = window_start if since_ns is None else max(window_start, since_ns)
        window = (kick_ns >= floor) & (kick_ns < window_end)
        window_start = window_end

        if int(past.sum()) < 30 or not bool(window.any()):
            continue

        age_days = np.maximum(0.0, (window_end - kick_ns) / _NANOS_PER_DAY)
        weights = np.maximum(
            MIN_MATCH_WEIGHT, 0.5 ** (age_days / half_life_days)
        )
        past_weights = weights[past]
        total_weight = float(past_weights.sum()) or 1.0
        avg_home = float((past_weights * home_goals[past]).sum()) / total_weight
        avg_away = float((past_weights * away_goals[past]).sum()) / total_weight
        avg_home = avg_home or DEFAULT_LEAGUE_AVG_HOME
        avg_away = avg_away or DEFAULT_LEAGUE_AVG_AWAY

        fitted = fit_strength_arrays(
            home_idx=home_idx[past],
            away_idx=away_idx[past],
            weights=past_weights,
            home_goals=home_goals[past],
            away_goals=away_goals[past],
            n_teams=n_teams,
            league_avg_home=avg_home,
            league_avg_away=avg_away,
            iterations=6,
        )
        attack = fitted["attack"]
        defence = fitted["defence"]
        weighted = fitted["weighted_matches"]
        boost = fitted["boost"]

        kept = []
        for j in np.nonzero(window)[0]:
            home = int(home_idx[j])
            away = int(away_idx[j])
            if weighted[home] < min_weighted_matches or weighted[away] < min_weighted_matches:
                continue

            home_lambda = max(
                0.1,
                min(
                    8.0,
                    attack[home]
                    * defence[away]
                    * avg_home
                    * (1.0 + min(0.35, boost[home] * 0.25)),
                ),
            )
            away_lambda = max(0.1, min(8.0, attack[away] * defence[home] * avg_away))
            kept.append((int(j), home_lambda, away_lambda))

        if not kept:
            continue

        probabilities = outcome_1x2_from_lambdas(
            [item[1] for item in kept], [item[2] for item in kept], rho=rho
        )
        for (j, home_lambda, away_lambda), model in zip(kept, probabilities):
            fixture = matches[j]
            yield {
                "fixture": fixture,
                "competition": competition,
                "home_lambda": home_lambda,
                "away_lambda": away_lambda,
                "model": model,
                "market": _fair_1x2(fixture.home_odds, fixture.draw_odds, fixture.away_odds),
                "odds": {
                    "home_win": float(fixture.home_odds),
                    "draw": float(fixture.draw_odds),
                    "away_win": float(fixture.away_odds),
                },
                "label": _outcome_label(int(fixture.home_score), int(fixture.away_score)),
            }


def iter_walkforward_predictions(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    min_weighted_matches: float = 3.0,
    rho: float = -0.10,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
    since: Optional[datetime] = None,
):
    """Yield out-of-sample predictions with ratings that only know the past.

    Each item is a dict with the fixture, model probabilities, de-vigged market
    probabilities, the realized label and the 1X2 prices. Both the calibration
    report and the backtest consume this so they cannot drift apart.

    ``since`` drops predictions for matches that kicked off before it; the
    ratings behind each window are unchanged, so results are identical to
    filtering the full replay.
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
        yield from _walkforward_for_competition(
            comp,
            comp_matches,
            refit_days=refit_days,
            min_weighted_matches=min_weighted_matches,
            rho=rho,
            half_life_days=half_life_days,
            since=since,
        )


def run_walk_forward(
    db: Session,
    competition: Optional[str] = None,
    refit_days: int = 14,
    min_weighted_matches: float = 3.0,
    rho: float = -0.10,
    half_life_days: float = RATING_HALF_LIFE_DAYS,
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
        half_life_days=half_life_days,
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
