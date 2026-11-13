from __future__ import annotations
"""Learning from results – the engine's self-correction loop.

A model that keeps claiming an edge it does not have will keep losing. This
module closes that loop honestly:

1. Harvest out-of-sample predictions (walk-forward, never in-sample fits) and
   every settled leg from the real ledger.
2. Fit Platt scaling (``q = sigmoid(a * logit(p) + b)``) per market so a
   systematically over-confident market is pushed back toward reality.
3. Shrink each adjustment toward the identity and *validate* it on a held-out
   slice of the most recent history. An adjustment is only stored as active
   when it measurably reduces log loss, so noise cannot rewrite the model.
4. Learn a per-market edge premium from realised results: markets where claimed
   probabilities have not materialised require a larger edge to bet.

Synthetic/demo slips are never used as evidence, and every parameter carries
the sample size that produced it.
"""
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

LEARNING_VERSION = "learn-1"
LEARNING_PATH = Path(__file__).resolve().parents[3] / "data" / "learning.json"

OUTCOMES = ("home_win", "draw", "away_win")

# Markets that partition the same outcome space. Calibrating each side of a
# group independently would let the group sum drift away from 1.0 and create
# phantom edges on double chance, BTTS or totals, so groups are renormalised
# after their members are adjusted.
CALIBRATION_GROUPS = (
    ("home_win", "draw", "away_win"),
    ("double_chance_1x", "double_chance_x2", "double_chance_12"),
    ("btts_yes", "btts_no"),
    ("over_0_5", "under_0_5"),
    ("over_1_5", "under_1_5"),
    ("over_2_5", "under_2_5"),
    ("over_3_5", "under_3_5"),
    ("dnb_home", "dnb_away"),
)

# An adjustment must survive this much held-out data before it may touch prices.
MIN_TRAIN_PAIRS = 150
MIN_VALIDATION_PAIRS = 60
MIN_IMPROVEMENT = 0.002
SHRINKAGE_K = 300.0

# Real-ledger evidence for secondary markets arrives one slip at a time.
EDGE_MIN_SAMPLE = 25
MAX_EDGE_PREMIUM = 2.0

_CACHE: Dict[str, Any] = {"mtime": None, "report": None}


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p: float, eps: float = 1e-6) -> float:
    p = min(1.0 - eps, max(eps, p))
    return math.log(p / (1.0 - p))


def _pair_logloss(pairs: Sequence[Tuple[float, float]], a: float, b: float) -> float:
    total = 0.0
    for p, y in pairs:
        q = _sigmoid(a * _logit(p) + b)
        q = min(1.0 - 1e-9, max(1e-9, q))
        total -= y * math.log(q) + (1.0 - y) * math.log(1.0 - q)
    return total / len(pairs) if pairs else 0.0


def fit_platt(pairs: Sequence[Tuple[float, float]], l2: float = 2.0, iterations: int = 50) -> Tuple[float, float]:
    """Newton-Raphson fit of a 1-feature logistic calibration with L2 shrinkage.

    L2 pulls the parameters toward (1, 0), i.e. toward "do nothing", which is
    exactly the behaviour we want on small or uninformative samples.
    """
    a, b = 1.0, 0.0
    if not pairs:
        return a, b

    for _ in range(iterations):
        g_a, g_b = l2 * (a - 1.0), l2 * b
        h_aa = h_bb = l2
        h_ab = 0.0
        for p, y in pairs:
            z = _logit(p)
            q = _sigmoid(a * z + b)
            w = q * (1.0 - q)
            g_a += (q - y) * z
            g_b += q - y
            h_aa += w * z * z
            h_ab += w * z
            h_bb += w

        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-9:
            break
        delta_a = (h_bb * g_a - h_ab * g_b) / det
        delta_b = (h_aa * g_b - h_ab * g_a) / det
        if abs(delta_a) < 1e-9 and abs(delta_b) < 1e-9:
            break

        current = _pair_logloss(pairs, a, b)
        step = 1.0
        for _ in range(12):
            candidate = (a - step * delta_a, b - step * delta_b)
            if _pair_logloss(pairs, *candidate) <= current:
                break
            step *= 0.5
        a = max(0.25, min(4.0, a - step * delta_a))
        b = max(-1.5, min(1.5, b - step * delta_b))

    return round(a, 4), round(b, 4)


def _shrink(a: float, b: float, n: int) -> Tuple[float, float]:
    weight = n / (n + SHRINKAGE_K)
    return round(1.0 + (a - 1.0) * weight, 4), round(b * weight, 4)


def fit_market(pairs: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Fit, shrink and validate one market's calibration on a time-ordered sample."""
    n = len(pairs)
    result: Dict[str, Any] = {
        "n": n,
        "a": 1.0,
        "b": 0.0,
        "applied": False,
        "reason": "insufficient history",
    }
    if n < MIN_TRAIN_PAIRS + MIN_VALIDATION_PAIRS:
        return result

    split = int(n * 0.7)
    train, validation = pairs[:split], pairs[split:]
    if len(validation) < MIN_VALIDATION_PAIRS:
        return result

    raw_a, raw_b = fit_platt(train)
    a, b = _shrink(raw_a, raw_b, n)

    before = _pair_logloss(validation, 1.0, 0.0)
    after = _pair_logloss(validation, a, b)
    improvement = before - after

    result.update({
        "n_train": len(train),
        "n_validation": len(validation),
        "raw_a": raw_a,
        "raw_b": raw_b,
        "a": a,
        "b": b,
        "logloss_before": round(before, 5),
        "logloss_after": round(after, 5),
        "improvement": round(improvement, 5),
        "applied": improvement >= MIN_IMPROVEMENT,
        "reason": (
            "validated on held-out recent matches"
            if improvement >= MIN_IMPROVEMENT
            else "held-out log loss did not improve enough to justify a change"
        ),
    })
    return result


def settled_legs(db: Session) -> List[Dict[str, Any]]:
    """Every settled leg from a real (non-synthetic) slip."""
    from app.models.bet import TrackedBet
    from app.betting.settlement import _is_synthetic_bet

    rows: List[Dict[str, Any]] = []
    for bet in db.scalars(select(TrackedBet).where(TrackedBet.status.in_(("WON", "LOST")))).all():
        if _is_synthetic_bet(bet):
            continue
        try:
            legs = json.loads(bet.legs_json or "[]")
        except (TypeError, ValueError):
            continue
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            status = leg.get("status")
            key = leg.get("market_key")
            probability = leg.get("probability")
            if status not in ("WON", "LOST") or not key or probability is None:
                continue
            rows.append({
                "market_key": str(key),
                "probability": float(probability),
                "won": status == "WON",
                "odds": leg.get("odds"),
                "competition": leg.get("competition"),
            })
    return rows


def _edge_premiums(legs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for leg in legs:
        grouped[leg["market_key"]].append(leg)

    premiums: Dict[str, Dict[str, Any]] = {}
    for market, rows in grouped.items():
        n = len(rows)
        if n < EDGE_MIN_SAMPLE:
            continue
        claimed = sum(r["probability"] for r in rows) / n
        observed = sum(1.0 for r in rows if r["won"]) / n
        gap = max(0.0, claimed - observed)
        weight = n / (n + 50.0)
        premium = 1.0 + gap * 3.0 * weight
        premiums[market] = {
            "premium": round(min(MAX_EDGE_PREMIUM, premium), 3),
            "n": n,
            "claimed_probability": round(claimed, 4),
            "observed_win_rate": round(observed, 4),
        }
    return premiums


def build_report(db: Session) -> Dict[str, Any]:
    """Train every market that has enough evidence. Expensive; call explicitly."""
    from app.analytics.calibration import iter_walkforward_predictions

    pairs: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    matches = 0
    for prediction in iter_walkforward_predictions(db):
        matches += 1
        for key in OUTCOMES:
            pairs[key].append(
                (float(prediction["model"][key]), 1.0 if prediction["label"] == key else 0.0)
            )

    legs = settled_legs(db)
    for leg in legs:
        pairs[leg["market_key"]].append((leg["probability"], 1.0 if leg["won"] else 0.0))

    markets = {key: fit_market(sample) for key, sample in pairs.items()}
    premiums = _edge_premiums(legs)

    notes = []
    if not legs:
        notes.append(
            "No settled real slips yet: secondary-market calibration is waiting for evidence, "
            "and only the 1X2 markets have enough history."
        )
    unmet = [k for k, v in premiums.items() if v["n"] < EDGE_MIN_SAMPLE]
    if unmet:
        notes.append(
            f"{len(unmet)} market(s) had some settled legs but below the {EDGE_MIN_SAMPLE}-leg "
            "minimum for an edge premium."
        )

    return {
        "version": LEARNING_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "walkforward_matches": matches,
            "settled_legs": len(legs),
        },
        "markets": dict(sorted(markets.items())),
        "applied_markets": sorted(k for k, v in markets.items() if v.get("applied")),
        "edge_premiums": dict(sorted(premiums.items())),
        "notes": notes,
    }


def save_report(report: Dict[str, Any], path: Optional[Path] = None) -> None:
    target = Path(path) if path is not None else LEARNING_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True))
    if path is None:
        _CACHE["mtime"] = LEARNING_PATH.stat().st_mtime
        _CACHE["report"] = report


def load_report() -> Optional[Dict[str, Any]]:
    """Cached read of the last learned parameters."""
    if not LEARNING_PATH.exists():
        return None
    try:
        mtime = LEARNING_PATH.stat().st_mtime
        if _CACHE["mtime"] == mtime:
            return _CACHE["report"]
        report = json.loads(LEARNING_PATH.read_text())
        _CACHE["mtime"] = mtime
        _CACHE["report"] = report
        return report
    except (ValueError, OSError):
        return None


def apply_calibration(
    market_key: str,
    probability: float,
    report: Optional[Dict[str, Any]] = None,
) -> Tuple[float, bool]:
    """Return the calibrated probability and whether an adjustment was applied."""
    report = report if report is not None else load_report()
    if not report:
        return probability, False
    market = (report.get("markets") or {}).get(market_key)
    if not market or not market.get("applied"):
        return probability, False
    adjusted = _sigmoid(float(market["a"]) * _logit(probability) + float(market["b"]))
    return round(min(0.995, max(0.005, adjusted)), 4), True


def calibrate_markets(
    probabilities: Dict[str, Optional[float]],
    report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Tuple[float, bool]]:
    """Calibrate a fixture's markets while keeping outcome groups coherent.

    Returns ``{market_key: (probability, adjusted)}``. Groups with at least one
    adjusted member are renormalised to sum to one, so a calibration can never
    manufacture a double-chance edge out of broken arithmetic.
    """
    report = report if report is not None else load_report()
    results: Dict[str, Tuple[float, bool]] = {}
    for key, probability in probabilities.items():
        if probability is None:
            continue
        results[key] = apply_calibration(key, probability, report)

    if not results:
        return results

    for group in CALIBRATION_GROUPS:
        if not all(member in results for member in group):
            continue
        if not any(results[member][1] for member in group):
            continue
        total = sum(results[member][0] for member in group)
        if total <= 0:
            continue
        for member in group:
            original = results[member][0]
            normalised = round(min(0.995, max(0.005, original / total)), 4)
            results[member] = (normalised, results[member][1] or abs(normalised - original) > 1e-9)
    return results


def edge_premium(
    market_key: str,
    report: Optional[Dict[str, Any]] = None,
) -> float:
    """Multiplier applied to a market's required edge after poor realised results."""
    report = report if report is not None else load_report()
    if not report:
        return 1.0
    entry = (report.get("edge_premiums") or {}).get(market_key)
    return float(entry["premium"]) if entry else 1.0


def summary() -> Dict[str, Any]:
    """Compact view for API/UI headers."""
    report = load_report()
    if not report:
        return {
            "trained": False,
            "version": LEARNING_VERSION,
            "applied_markets": [],
            "note": "No learned parameters yet. Train from results to enable self-correction.",
        }
    return {
        "trained": True,
        "version": report.get("version"),
        "trained_at": report.get("trained_at"),
        "sources": report.get("sources"),
        "applied_markets": report.get("applied_markets", []),
        "edge_premiums": report.get("edge_premiums", {}),
        "notes": report.get("notes", []),
    }
