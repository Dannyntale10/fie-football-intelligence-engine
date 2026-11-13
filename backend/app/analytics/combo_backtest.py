"""Replay the daily 2–3 leg combo strategy on settled fixtures.

The replay is deliberately the same decision rule the live engine uses: one
1X2 selection per fixture, de-vigged market probability, a probability tier,
and combos ranked by joint probability. Two things differ because history is
all we have:

* legs are priced from the stored flat 1X2 columns (older fixtures predate
  per-feed attribution), so results describe the market-price strategy rather
  than one specific bookmaker;
* settlement uses the stored final score, and every combo is staked flat.

The output separates what happened in the past from what can be trusted going
forward: the latest 30% of matchdays form a holdout that also decides whether a
fitted calibration may be stored for live use.
"""
from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.learning import fit_market
from app.betting.daily_combos import (
    COMBO_SIZES,
    DEFAULT_TIER,
    EAT_TZ,
    MARKET_KEYS,
    MAX_CANDIDATE_LEGS,
    as_utc,
    calibrate,
    is_placeholder_team,
    learning_path as combo_learning_path,
    same_match,
)
from app.betting.pricing import devigged_probabilities, fixture_market_odds
from app.models.fixture import Fixture

BACKTEST_VERSION = "combo-backtest-1"
SWEEP_VERSION = "combo-sweep-1"
SWEEP_TIERS = (0.65, 0.70, 0.75, 0.80, 0.85)
MIN_TRAIN_TICKETS = 20
DEFAULT_STAKE = 10.0
HOLDOUT_SHARE = 0.30

PROBABILITY_BUCKETS = (
    (0.0, 0.50),
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.0001),
)


def backtest_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "combo_backtest.json"


def sweep_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "combo_sweep.json"


def _outcome(fixture: Fixture) -> Optional[str]:
    if fixture.home_score is None or fixture.away_score is None:
        return None
    if fixture.home_score > fixture.away_score:
        return "home_win"
    if fixture.home_score < fixture.away_score:
        return "away_win"
    return "draw"


def _stored_odds(fixture: Fixture, price_mode: str) -> Dict[str, float]:
    """Historical prices for a replay.

    ``flat`` keeps one bookmaker's 1X2 trio (the stored Bet365 columns from the
    season import), which is the conservative view of what was available.
    ``best`` takes the highest stored price per outcome, mirroring the live
    best-price merge across feeds but assuming accounts at every book.
    """
    if price_mode == "best":
        return fixture_market_odds(fixture)
    return {
        key: float(value)
        for key, value in (
            ("home_win", fixture.home_odds),
            ("draw", fixture.draw_odds),
            ("away_win", fixture.away_odds),
        )
        if isinstance(value, (int, float)) and value > 1.0
    }


def _replay_rows(
    db: Session,
    competition: Optional[str] = None,
    price_mode: str = "flat",
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    query = (
        select(Fixture)
        .where(Fixture.home_score.is_not(None))
        .where(Fixture.away_score.is_not(None))
        .order_by(Fixture.kickoff_utc)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    rows: List[Dict[str, Any]] = []
    skipped = {"missing_kickoff": 0, "incomplete_1x2": 0, "placeholder": 0}
    for fixture in db.scalars(query).all():
        if not fixture.kickoff_utc:
            skipped["missing_kickoff"] += 1
            continue
        if is_placeholder_team(fixture.home_team) or is_placeholder_team(fixture.away_team):
            skipped["placeholder"] += 1
            continue
        odds = _stored_odds(fixture, price_mode)
        if not all(key in odds for key in MARKET_KEYS):
            skipped["incomplete_1x2"] += 1
            continue
        _, fair = devigged_probabilities({key: odds[key] for key in MARKET_KEYS})
        rows.append(
            {
                "fixture": fixture,
                "date": as_utc(fixture.kickoff_utc).astimezone(EAT_TZ).strftime("%Y-%m-%d"),
                "odds": odds,
                "fair": fair,
                "outcome": _outcome(fixture),
            }
        )
    return rows, skipped


def _select_legs(
    rows: Sequence[Dict[str, Any]],
    tier: float,
    learning: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One tier-qualified leg per match, best market outcome, de-duplicated."""
    legs: List[Dict[str, Any]] = []
    for row in rows:
        fair = row["fair"]
        best = max(MARKET_KEYS, key=lambda key: fair.get(key, 0.0))
        probability = calibrate(float(fair[best]), learning)
        if probability < tier:
            continue
        legs.append(
            {
                "_fixture": row["fixture"],
                "fixture_id": row["fixture"].id,
                "date": row["date"],
                "match": f"{row['fixture'].home_team} vs {row['fixture'].away_team}",
                "competition": row["fixture"].competition,
                "selection": best,
                "odds": round(float(row["odds"][best]), 4),
                "probability": round(probability, 6),
                "market_probability": round(float(fair[best]), 6),
                "won": best == row["outcome"],
            }
        )

    legs.sort(key=lambda leg: (leg["probability"], -leg["odds"]), reverse=True)
    unique: List[Dict[str, Any]] = []
    for leg in legs:
        if any(same_match(leg["_fixture"], kept["_fixture"]) for kept in unique):
            continue
        unique.append(leg)
        if len(unique) >= MAX_CANDIDATE_LEGS:
            break
    for leg in unique:
        leg.pop("_fixture", None)
    return unique


def _daily_legs(
    rows: Sequence[Dict[str, Any]],
    tier: float,
    learning: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[row["date"]].append(row)
    legs: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        legs.extend(_select_legs(by_day[day], tier, learning))
    return legs


def _settle(group: Sequence[Dict[str, Any]], stake: float) -> Dict[str, Any]:
    probability = 1.0
    odds = 1.0
    for leg in group:
        probability *= leg["probability"]
        odds *= leg["odds"]
    won = all(leg["won"] for leg in group)
    return {
        "date": group[0]["date"],
        "size": len(group),
        "legs": [
            {
                "match": leg["match"],
                "selection": leg["selection"],
                "odds": leg["odds"],
                "probability": round(leg["probability"], 4),
                "won": leg["won"],
            }
            for leg in group
        ],
        "combined_probability": round(probability, 6),
        "total_odds": round(odds, 2),
        "won": won,
        "staked": round(stake, 2),
        "returned": round(stake * odds, 2) if won else 0.0,
    }


def _combos(
    legs: Sequence[Dict[str, Any]],
    stake: float,
    limit_per_size: int,
) -> List[Dict[str, Any]]:
    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for leg in legs:
        by_day[leg["date"]].append(leg)

    combos: List[Dict[str, Any]] = []
    for day in sorted(by_day):
        day_legs = sorted(
            by_day[day], key=lambda leg: (leg["probability"], -leg["odds"]), reverse=True
        )
        for size in COMBO_SIZES:
            found = [
                _settle(group, stake) for group in itertools.combinations(day_legs, size)
            ]
            found.sort(
                key=lambda row: (row["combined_probability"], row["total_odds"]),
                reverse=True,
            )
            combos.extend(found[:limit_per_size])
    return combos


def _combo_metrics(combos: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(combos)
    if not n:
        return {"count": 0}
    wins = sum(1 for combo in combos if combo["won"])
    staked = sum(combo["staked"] for combo in combos)
    returned = sum(combo["returned"] for combo in combos)
    predicted = sum(combo["combined_probability"] for combo in combos) / n
    brier = sum(
        (combo["combined_probability"] - (1.0 if combo["won"] else 0.0)) ** 2
        for combo in combos
    ) / n
    logloss = -sum(
        math.log(max(1e-9, combo["combined_probability"]))
        if combo["won"]
        else math.log(max(1e-9, 1.0 - combo["combined_probability"]))
        for combo in combos
    ) / n
    return {
        "count": n,
        "wins": wins,
        "losses": n - wins,
        "hit_rate": round(wins / n, 4),
        "average_predicted_probability": round(predicted, 4),
        "calibration_gap": round(wins / n - predicted, 4),
        "brier": round(brier, 5),
        "log_loss": round(logloss, 5),
        "total_staked": round(staked, 2),
        "total_returned": round(returned, 2),
        "profit": round(returned - staked, 2),
        "roi_percent": round((returned - staked) / staked * 100, 2) if staked else None,
    }


def _by_size(combos: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for size in COMBO_SIZES:
        result[f"{size}_leg"] = _combo_metrics([c for c in combos if c["size"] == size])
    result["total"] = _combo_metrics(combos)
    return result


def _leg_metrics(legs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(legs)
    if not n:
        return {"count": 0}
    wins = sum(1 for leg in legs if leg["won"])
    predicted = sum(leg["probability"] for leg in legs) / n
    brier = sum(
        (leg["probability"] - (1.0 if leg["won"] else 0.0)) ** 2 for leg in legs
    ) / n
    logloss = -sum(
        math.log(max(1e-9, leg["probability"]))
        if leg["won"]
        else math.log(max(1e-9, 1.0 - leg["probability"]))
        for leg in legs
    ) / n

    buckets: List[Dict[str, Any]] = []
    ece = 0.0
    for low, high in PROBABILITY_BUCKETS:
        rows = [leg for leg in legs if low <= leg["probability"] < high]
        if not rows:
            continue
        average = sum(leg["probability"] for leg in rows) / len(rows)
        observed = sum(1 for leg in rows if leg["won"]) / len(rows)
        buckets.append(
            {
                "range": f"{low:.2f}–{min(high, 1.0):.2f}",
                "count": len(rows),
                "average_predicted": round(average, 4),
                "observed_win_rate": round(observed, 4),
                "gap": round(observed - average, 4),
            }
        )
        ece += (len(rows) / n) * abs(observed - average)

    return {
        "count": n,
        "wins": wins,
        "hit_rate": round(wins / n, 4),
        "average_predicted_probability": round(predicted, 4),
        "calibration_gap": round(wins / n - predicted, 4),
        "average_odds": round(sum(leg["odds"] for leg in legs) / n, 3),
        "brier": round(brier, 5),
        "log_loss": round(logloss, 5),
        "ece": round(ece, 4),
        "buckets": buckets,
    }


def _calibration_pairs(rows: Sequence[Dict[str, Any]]) -> List[Tuple[float, float]]:
    """The selected leg of every settled match: (market probability, won)."""
    pairs: List[Tuple[float, float]] = []
    for row in rows:
        fair = row["fair"]
        best = max(MARKET_KEYS, key=lambda key: fair.get(key, 0.0))
        pairs.append((float(fair[best]), 1.0 if best == row["outcome"] else 0.0))
    return pairs


def _split_days(rows: Sequence[Dict[str, Any]]) -> Tuple[set, set, List[str]]:
    days = sorted({row["date"] for row in rows})
    cut = int(len(days) * (1.0 - HOLDOUT_SHARE))
    return set(days[:cut]), set(days[cut:]), days


def _window(rows: Sequence[Dict[str, Any]], days: Sequence[str]) -> Dict[str, Any]:
    return {
        "first_matchday": days[0] if days else None,
        "last_matchday": days[-1] if days else None,
        "matchdays": len(days),
        "settled_matches": len(rows),
    }


def _learning_entry(fit: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "applied": bool(fit.get("applied")),
        "a": fit.get("a"),
        "b": fit.get("b"),
        "n": fit.get("n"),
        "n_train": fit.get("n_train"),
        "n_validation": fit.get("n_validation"),
        "raw_a": fit.get("raw_a"),
        "raw_b": fit.get("raw_b"),
        "logloss_before": fit.get("logloss_before"),
        "logloss_after": fit.get("logloss_after"),
        "improvement": fit.get("improvement"),
        "reason": fit.get("reason"),
    }


def run_combo_backtest(
    db: Session,
    competition: Optional[str] = None,
    min_leg_probability: float = DEFAULT_TIER,
    stake: float = DEFAULT_STAKE,
    limit_per_size: int = 1,
    price_mode: str = "flat",
) -> Dict[str, Any]:
    """Replay the combo rule on settled fixtures and fit its calibration."""
    tier = max(0.5, min(0.99, float(min_leg_probability)))
    stake = max(1.0, float(stake))
    price_mode = "best" if price_mode == "best" else "flat"
    rows, skipped = _replay_rows(db, competition=competition, price_mode=price_mode)

    if not rows:
        return {
            "version": BACKTEST_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "no_history",
            "params": {
                "min_leg_probability": tier,
                "combo_sizes": list(COMBO_SIZES),
                "stake": stake,
                "competition": competition,
                "price_mode": price_mode,
            },
            "scope": {"settled_fixtures": 0, "skipped": skipped},
            "message": (
                "No settled fixtures with complete 1X2 prices were found. "
                "Import results before testing the strategy."
            ),
            "notes": [
                "Estimated or synthetic odds are never replayed as if they were real prices."
            ],
        }

    train_days, holdout_days, days = _split_days(rows)
    train_rows = [row for row in rows if row["date"] in train_days]
    holdout_rows = [row for row in rows if row["date"] in holdout_days]

    all_legs = _daily_legs(rows, tier, None)
    train_legs = _daily_legs(train_rows, tier, None)
    holdout_legs = _daily_legs(holdout_rows, tier, None)

    all_combos = _combos(all_legs, stake, limit_per_size)
    holdout_combos = _combos(holdout_legs, stake, limit_per_size)
    train_combos = _combos(train_legs, stake, limit_per_size)

    all_pairs = _calibration_pairs(rows)
    train_pairs = _calibration_pairs(train_rows)
    fit = fit_market(all_pairs)
    # The live calibration may use every settled match, but the holdout
    # demonstration must not: a fit that has seen holdout days would make the
    # "after learning" holdout scores in-sample.
    train_fit = fit_market(train_pairs)

    learning = (
        {"a": fit["a"], "b": fit["b"], "n": fit.get("n", 0)}
        if fit.get("applied")
        else None
    )
    holdout_learning = (
        {"a": train_fit["a"], "b": train_fit["b"], "n": train_fit.get("n", 0)}
        if fit.get("applied") and train_fit.get("applied")
        else None
    )
    holdout_after = (
        _combos(_daily_legs(holdout_rows, tier, holdout_learning), stake, limit_per_size)
        if holdout_learning
        else None
    )

    window = _window(rows, days)
    price_note = (
        "Replay prices are the highest stored price per outcome, which assumes "
        "accounts at every book; this is an upper bound, not a guaranteed price."
        if price_mode == "best"
        else "Replay prices come from one stored bookmaker trio per fixture, so "
        "the result is conservative and does not assume access to every book."
    )
    notes = [
        "Historical fixtures predate per-feed attribution, so the replay cannot "
        "reproduce a specific feed's price; live legs still require attribution.",
        price_note,
        "Settlement uses the stored final score. Each combo is staked flat and "
        "returns stake × total odds only when every leg wins.",
        "One ticket per size per matchday is settled: the engine's top-ranked "
        "combo. Evaluating several overlapping tickets per day would count the "
        "same legs more than once.",
        "Combined probability is the product of de-vigged market probabilities, "
        "which assumes the legs are independent. Near-duplicate fixtures are "
        "collapsed to one leg before combinations are built.",
        f"The latest {HOLDOUT_SHARE:.0%} of matchdays is the holdout; the fit is "
        "validated on its most recent slice and stored only when it reduces "
        "held-out log loss.",
        "The after-learning holdout re-selects legs with a calibration fitted on "
        "training matchdays only, so that comparison stays out of sample.",
        "At a bookmaker's own prices, a negative ROI is expected: the margin is "
        "already inside the price. Hit rate above the required break-even is the "
        "measure of whether the tier is worth playing.",
    ]
    if learning and not holdout_learning:
        notes.append(
            "A calibration fitted on training matchdays only did not clear its own "
            f"held-out improvement bar ({train_fit.get('improvement')} < 0.002), so no "
            "after-learning holdout comparison is published."
        )

    return {
        "version": BACKTEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "window": window,
        "params": {
            "min_leg_probability": tier,
            "combo_sizes": list(COMBO_SIZES),
            "max_candidate_legs": MAX_CANDIDATE_LEGS,
            "combos_per_size": limit_per_size,
            "stake": round(stake, 2),
            "competition": competition,
            "price_mode": price_mode,
            "holdout_share": HOLDOUT_SHARE,
        },
        "scope": {
            "settled_fixtures": len(rows),
            "matchdays": len(days),
            "holdout_matchdays": len(holdout_days),
            "qualified_legs": len(all_legs),
            "combos": len(all_combos),
            "skipped": skipped,
        },
        "legs": {
            "all": _leg_metrics(all_legs),
            "holdout": _leg_metrics(holdout_legs),
        },
        "combos": {
            "all": _by_size(all_combos),
            "train": _by_size(train_combos),
            "holdout": _by_size(holdout_combos),
        },
        "learning": _learning_entry(fit),
        "holdout_fit": _learning_entry(train_fit),
        "holdout_after_learning": _by_size(holdout_after) if holdout_after else None,
        "notes": notes,
    }


def _with_break_even(
    metrics: Dict[str, Any], combos: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    if not combos or not metrics.get("count"):
        return metrics
    required = sum(1.0 / combo["total_odds"] for combo in combos) / len(combos)
    metrics["required_hit_rate"] = round(required, 4)
    metrics["edge_over_break_even"] = round(float(metrics.get("hit_rate", 0.0)) - required, 4)
    return metrics


def _by_size_with_break_even(combos: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    result = _by_size(combos)
    for size in COMBO_SIZES:
        key = f"{size}_leg"
        _with_break_even(result[key], [c for c in combos if c["size"] == size])
    _with_break_even(result["total"], list(combos))
    return result


def _pick_tier(results: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Choose a tier from training metrics only; holdout never participates."""
    eligible = [
        row
        for row in results
        if row["combos"]["train"]["total"].get("count", 0) >= MIN_TRAIN_TICKETS
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            row["combos"]["train"]["total"].get("roi_percent") or -1e9,
            row["combos"]["train"]["total"].get("hit_rate") or 0.0,
            -row["tier"],
        ),
    )


def run_tier_sweep(
    db: Session,
    competition: Optional[str] = None,
    tiers: Sequence[float] = SWEEP_TIERS,
    stake: float = DEFAULT_STAKE,
    limit_per_size: int = 1,
    price_mode: str = "flat",
) -> Dict[str, Any]:
    """Replay the same combo rule at several probability tiers and compare them.

    The recommended tier is chosen on training matchdays only. Holdout metrics
    are reported afterwards as confirmation evidence and never feed the choice,
    so the recommendation cannot be tuned on the days it is scored against.
    """
    tier_values: List[float] = sorted(
        {round(min(0.99, max(0.50, float(tier))), 4) for tier in tiers}
    ) or [DEFAULT_TIER]
    stake = max(1.0, float(stake))
    limit_per_size = max(1, int(limit_per_size))
    price_mode = "best" if price_mode == "best" else "flat"
    rows, skipped = _replay_rows(db, competition=competition, price_mode=price_mode)

    base_params = {
        "tiers": tier_values,
        "combo_sizes": list(COMBO_SIZES),
        "combos_per_size": limit_per_size,
        "stake": round(stake, 2),
        "competition": competition,
        "price_mode": price_mode,
        "holdout_share": HOLDOUT_SHARE,
        "published_default_tier": DEFAULT_TIER,
        "min_train_tickets": MIN_TRAIN_TICKETS,
    }
    if not rows:
        return {
            "version": SWEEP_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "no_history",
            "params": base_params,
            "scope": {"settled_fixtures": 0, "skipped": skipped},
            "rows": [],
            "recommendation": None,
            "message": (
                "No settled fixtures with complete 1X2 prices were found. "
                "Import results before comparing tiers."
            ),
        }

    train_days, holdout_days, days = _split_days(rows)
    train_rows = [row for row in rows if row["date"] in train_days]
    holdout_rows = [row for row in rows if row["date"] in holdout_days]

    results: List[Dict[str, Any]] = []
    for tier in tier_values:
        all_legs = _daily_legs(rows, tier, None)
        train_legs = _daily_legs(train_rows, tier, None)
        holdout_legs = _daily_legs(holdout_rows, tier, None)
        results.append(
            {
                "tier": tier,
                "legs": {
                    "all": _leg_metrics(all_legs),
                    "train": _leg_metrics(train_legs),
                    "holdout": _leg_metrics(holdout_legs),
                },
                "combos": {
                    "all": _by_size_with_break_even(_combos(all_legs, stake, limit_per_size)),
                    "train": _by_size_with_break_even(
                        _combos(train_legs, stake, limit_per_size)
                    ),
                    "holdout": _by_size_with_break_even(
                        _combos(holdout_legs, stake, limit_per_size)
                    ),
                },
            }
        )

    chosen = _pick_tier(results)
    if chosen is not None:
        train_total = chosen["combos"]["train"]["total"]
        holdout_total = chosen["combos"]["holdout"]["total"]
        confirmed = (
            holdout_total.get("count", 0) >= 10
            and (holdout_total.get("roi_percent") or -1.0) > 0
            and (holdout_total.get("edge_over_break_even") or -1.0) >= 0
        )
        if confirmed and chosen["tier"] != DEFAULT_TIER:
            note = (
                "The holdout confirms a different tier: it clears its break-even "
                "hit rate and returns a positive flat-stake ROI out of sample."
            )
            action = "consider_tier"
            guidance = (
                f"Holdout confirms {chosen['tier']:.0%}. The live engine stays at "
                f"{DEFAULT_TIER:.0%} until you deliberately change it."
            )
        elif confirmed:
            note = (
                "The holdout confirms the published default tier out of sample."
            )
            action = "keep_default"
            guidance = f"The default {DEFAULT_TIER:.0%} tier is confirmed out of sample."
        elif holdout_total.get("count", 0) < 10:
            note = (
                "The holdout holds too few tickets to confirm the tier; the "
                "recommendation is provisional until more matchdays settle."
            )
            action = "keep_default"
            guidance = (
                f"The live engine keeps {DEFAULT_TIER:.0%}; evidence is still too thin."
            )
        else:
            note = (
                "The holdout does not confirm the tier: either it missed its "
                "break-even hit rate or the flat-stake ROI was not positive."
            )
            action = "keep_default"
            guidance = (
                f"The live engine keeps {DEFAULT_TIER:.0%}: a tier that wins only "
                "on training is not enough to change it."
            )
        recommendation: Optional[Dict[str, Any]] = {
            "tier": chosen["tier"],
            "basis": (
                "highest training ROI among tiers with at least "
                f"{MIN_TRAIN_TICKETS} training tickets; the holdout is not used to choose"
            ),
            "action": action,
            "matches_published_default": chosen["tier"] == DEFAULT_TIER,
            "train": train_total,
            "holdout": holdout_total,
            "holdout_confirmed": confirmed,
            "note": note,
            "guidance": guidance,
        }
    else:
        recommendation = {
            "tier": None,
            "basis": f"no tier produced at least {MIN_TRAIN_TICKETS} training tickets",
            "action": "keep_default",
            "matches_published_default": False,
            "train": None,
            "holdout": None,
            "holdout_confirmed": False,
            "note": "History is too thin to choose a tier; the default stays in place.",
            "guidance": f"The live engine keeps {DEFAULT_TIER:.0%}.",
        }

    price_note = (
        "Replay prices are the highest stored price per outcome, which assumes "
        "accounts at every book; this is an upper bound, not a guaranteed price."
        if price_mode == "best"
        else "Replay prices come from one stored bookmaker trio per fixture, so "
        "the comparison is conservative and does not assume access to every book."
    )
    notes = [
        "Every tier is scored with the live decision rule: one 1X2 selection per "
        "match, de-vigged market probability, one top-ranked ticket per size per "
        "matchday, flat stake.",
        "The tier is chosen on training matchdays only; holdout metrics are "
        "confirmation evidence reported after the choice, never an input to it.",
        "A tier is only eligible to be recommended when it produced at least "
        f"{MIN_TRAIN_TICKETS} training tickets; small samples are excluded.",
        "Gaps smaller than about 5 percentage points of ROI sit inside replay "
        "noise: read the ranking as directional, not as a precise optimum.",
        "The published 85% selection gate is unchanged. Tiers here are internal "
        "combo filters and never lower that gate.",
        price_note,
        "Break-even hit rate is the average of 1 / total odds across the settled "
        "tickets, so it moves with the prices the replay actually took.",
    ]
    return {
        "version": SWEEP_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "window": _window(rows, days),
        "params": base_params,
        "scope": {
            "settled_fixtures": len(rows),
            "matchdays": len(days),
            "holdout_matchdays": len(holdout_days),
            "skipped": skipped,
        },
        "rows": results,
        "recommendation": recommendation,
        "notes": notes,
    }


def save_sweep(report: Dict[str, Any]) -> Path:
    path = sweep_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return path


def load_sweep() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(sweep_path().read_text())
    except (OSError, ValueError):
        return None


def save_learning(
    report: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist the fitted calibration, or an explicit inactive record."""
    learning = report.get("learning") or {}
    payload = {
        "version": BACKTEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "applied": bool(learning.get("applied")),
        "a": learning.get("a", 1.0),
        "b": learning.get("b", 0.0),
        "n": learning.get("n", 0),
        "n_train": learning.get("n_train"),
        "n_validation": learning.get("n_validation"),
        "logloss_before": learning.get("logloss_before"),
        "logloss_after": learning.get("logloss_after"),
        "improvement": learning.get("improvement"),
        "reason": learning.get("reason"),
        "tier": (report.get("params") or {}).get("min_leg_probability"),
        "window": report.get("window"),
        "source": (
            "de-vigged 1X2 market probability of the highest-probability "
            "outcome per settled fixture"
        ),
    }
    path = combo_learning_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return payload


def save_report(report: Dict[str, Any]) -> Path:
    path = backtest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return path


def load_report() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(backtest_path().read_text())
    except (OSError, ValueError):
        return None
