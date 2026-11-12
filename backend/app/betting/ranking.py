from __future__ import annotations
"""ranking.py – deterministic 0-100 prioritisation of legs and slips.

The score orders what the gates already allowed; it never admits a leg or slip
that a selection rule rejected. It is a presentation heuristic, not a second
probability model, so a high rank is not evidence of an edge on its own.
"""
from typing import Any, Dict, List

RANKING_VERSION = "rank-1"

# Weights are in points and sum to 100. Normalisation caps live next to them so
# a component cannot contribute more than its budget.
LEG_WEIGHTS = {
    "edge": (45.0, 12.0),          # model edge over the fair price, capped at 12%
    "probability": (30.0, 0.45),   # probability above 50%
    "expected_value": (15.0, 60.0),  # EV percent, capped at 60%
    "evidence": (10.0, 1.0),       # real price + learned calibration + ratings
}

# Structure choice is probability-first: an 85% floor is already enforced before
# scoring, and the 60-point probability budget keeps a high-probability
# structure ahead of a lower-probability one that merely carries a bigger edge.
TOP_PICK_WEIGHTS = {
    "probability": (60.0, 0.80, 0.20),  # 80% -> 0 points, 100% -> full budget
    "edge": (15.0, 10.0),               # model edge over the de-vigged price
    "expected_value": (10.0, 30.0),
    "evidence": (15.0, 1.0),            # data-confidence score
}

COMBO_WEIGHTS = {
    "ev": {
        "edge": (40.0, 20.0),
        "joint_probability": (25.0, 0.65),
        "expected_value": (25.0, 60.0),
        "evidence": (10.0, 1.0),
    },
    "certainty": {
        "average_probability": (45.0, 0.38),
        "joint_probability": (25.0, 0.65),
        "expected_value": (20.0, 60.0),
        "evidence": (10.0, 1.0),
    },
}

_GRADE_THRESHOLDS = ((78.0, "A"), (62.0, "B"), (45.0, "C"))
_GRADE_LABELS = {
    "A": "Top pick",
    "B": "Strong",
    "C": "Fair",
    "D": "Marginal",
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _grade(score: float) -> str:
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "D"


def _leg_evidence(leg: Dict[str, Any]) -> float:
    points = 0.0
    if leg.get("odds_source") == "market":
        points += 0.5
    if leg.get("calibrated"):
        points += 0.3
    if leg.get("model_source") == "team_ratings":
        points += 0.2
    return _clamp01(points)


def _combo_evidence(combo: Dict[str, Any]) -> float:
    legs = combo.get("legs") or []
    if not legs:
        return 0.0
    points = 0.0
    if all(leg.get("odds_source") == "market" for leg in legs):
        points += 0.6
    if all(leg.get("calibrated") for leg in legs):
        points += 0.2
    if all(leg.get("model_source") == "team_ratings" for leg in legs):
        points += 0.2
    return _clamp01(points)


def leg_score(leg: Dict[str, Any]) -> Dict[str, Any]:
    """Score one candidate leg. Only fields the selector already produced are used."""
    edge = float(leg.get("edge_percent") or 0.0)
    probability = float(leg.get("probability") or 0.0)
    ev = float(leg.get("expected_value_percent") or 0.0)

    components = {
        "edge": round(_clamp01(edge / LEG_WEIGHTS["edge"][1]) * LEG_WEIGHTS["edge"][0], 2),
        "probability": round(
            _clamp01((probability - 0.50) / LEG_WEIGHTS["probability"][1])
            * LEG_WEIGHTS["probability"][0],
            2,
        ),
        "expected_value": round(
            _clamp01(ev / LEG_WEIGHTS["expected_value"][1]) * LEG_WEIGHTS["expected_value"][0],
            2,
        ),
        "evidence": round(_leg_evidence(leg) * LEG_WEIGHTS["evidence"][0], 2),
    }
    score = round(min(100.0, sum(components.values())), 1)
    return {
        "score": score,
        "grade": _grade(score),
        "grade_label": _GRADE_LABELS[_grade(score)],
        "components": components,
    }


def top_pick_score(row: Dict[str, Any]) -> Dict[str, Any]:
    """Score one already-qualified structure, probability first.

    The caller is responsible for the 85% probability and confidence floors;
    this score only decides which qualified structure is the better pick.
    """
    probability = float(row.get("probability") or 0.0)
    edge = float(row.get("edge_percent") or 0.0)
    ev = float(row.get("expected_value_percent") or 0.0)
    confidence = float(row.get("confidence") or 0.0)

    probability_budget, probability_floor, probability_span = TOP_PICK_WEIGHTS["probability"]
    edge_budget, edge_cap = TOP_PICK_WEIGHTS["edge"]
    ev_budget, ev_cap = TOP_PICK_WEIGHTS["expected_value"]
    evidence_budget, evidence_cap = TOP_PICK_WEIGHTS["evidence"]

    components = {
        "probability": round(
            _clamp01((probability - probability_floor) / probability_span)
            * probability_budget,
            2,
        ),
        "edge": round(_clamp01(edge / edge_cap) * edge_budget, 2),
        "expected_value": round(_clamp01(ev / ev_cap) * ev_budget, 2),
        "evidence": round(_clamp01(confidence / evidence_cap) * evidence_budget, 2),
    }
    score = round(min(100.0, sum(components.values())), 1)
    return {
        "score": score,
        "grade": _grade(score),
        "grade_label": _GRADE_LABELS[_grade(score)],
        "components": components,
    }


def combo_score(combo: Dict[str, Any], mode: str = "ev") -> Dict[str, Any]:
    """Score one generated slip using the weights of its selection mode."""
    weights = COMBO_WEIGHTS["certainty" if str(mode).lower() == "certainty" else "ev"]
    edge = float(combo.get("edge_percent") or 0.0)
    joint = float(combo.get("combined_probability") or 0.0)
    avg = float(combo.get("avg_leg_probability") or joint)
    ev = float(combo.get("expected_value_percent") or 0.0)

    components: Dict[str, float] = {}
    if "edge" in weights:
        budget, cap = weights["edge"]
        components["edge"] = round(_clamp01(edge / cap) * budget, 2)
    if "average_probability" in weights:
        budget, span = weights["average_probability"]
        components["average_probability"] = round(
            _clamp01((avg - 0.60) / span) * budget, 2
        )
    budget, span = weights["joint_probability"]
    components["joint_probability"] = round(_clamp01((joint - 0.15) / span) * budget, 2)
    budget, cap = weights["expected_value"]
    components["expected_value"] = round(_clamp01(ev / cap) * budget, 2)
    budget = weights["evidence"][0]
    components["evidence"] = round(_combo_evidence(combo) * budget, 2)

    score = round(min(100.0, sum(components.values())), 1)
    return {
        "score": score,
        "grade": _grade(score),
        "grade_label": _GRADE_LABELS[_grade(score)],
        "components": components,
    }


def _ranked(rows: List[Dict[str, Any]], score_fn) -> List[Dict[str, Any]]:
    scored = []
    for row in rows:
        enriched = dict(row)
        enriched.update(score_fn(row))
        scored.append(enriched)
    scored.sort(
        key=lambda r: (
            r["score"],
            float(r.get("expected_value_percent") or 0.0),
            float(r.get("combined_probability") or r.get("probability") or 0.0),
            -float(r.get("total_odds") or r.get("odds") or 0.0),
        ),
        reverse=True,
    )
    for position, row in enumerate(scored, start=1):
        row["rank"] = position
    return scored


def rank_legs(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return every leg with a ``rank``, highest priority first."""
    return _ranked(legs, leg_score)


def rank_combos(combos: List[Dict[str, Any]], mode: str = "ev") -> List[Dict[str, Any]]:
    """Return every slip with a ``rank``, highest priority first."""
    return _ranked(combos, lambda combo: combo_score(combo, mode))


def combo_row(combo: Dict[str, Any]) -> Dict[str, Any]:
    """Compact ranked-slip row for tables and API payloads."""
    return {
        "rank": combo.get("rank"),
        "rank_score": combo.get("score"),
        "grade": combo.get("grade"),
        "legs_count": combo.get("legs_count"),
        "total_odds": combo.get("total_odds"),
        "combined_probability": combo.get("combined_probability"),
        "expected_value_percent": combo.get("expected_value_percent"),
        "edge_percent": combo.get("edge_percent"),
        "legs": " + ".join(leg.get("selection", "") for leg in combo.get("legs", [])),
    }


def leg_row(leg: Dict[str, Any]) -> Dict[str, Any]:
    """Compact ranked-leg row for tables and API payloads."""
    return {
        "rank": leg.get("rank"),
        "rank_score": leg.get("score"),
        "grade": leg.get("grade"),
        "match": leg.get("match"),
        "selection": leg.get("selection"),
        "market_type": leg.get("market_type"),
        "odds": leg.get("odds"),
        "probability": leg.get("probability"),
        "probability_model": leg.get("probability_raw"),
        "blended": leg.get("blended"),
        "confidence": leg.get("confidence"),
        "edge_percent": leg.get("edge_percent"),
        "expected_value_percent": leg.get("expected_value_percent"),
        "odds_source": leg.get("odds_source"),
        "structures_considered": leg.get("structures_considered"),
        "runner_ups": leg.get("runner_ups", []),
    }


def payload(
    combos: List[Dict[str, Any]],
    legs: List[Dict[str, Any]],
    limit: int = 25,
) -> Dict[str, Any]:
    """Build the ranking block returned by the combo engine."""
    return {
        "version": RANKING_VERSION,
        "method": "composite score: value, probability, evidence (weights per selection mode)",
        "limit": limit,
        "total_combos_ranked": len(combos),
        "total_legs_ranked": len(legs),
        "combos": [combo_row(c) for c in combos[:limit]],
        "matches": [leg_row(l) for l in legs[:limit]],
    }
