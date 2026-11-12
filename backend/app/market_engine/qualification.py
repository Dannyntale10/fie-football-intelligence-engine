"""The 85% qualification gate.

Probability and confidence are separate decisions. A selection qualifies only
when its modelled probability reaches the published threshold AND the data
behind it is trustworthy enough to publish. The threshold is a constant: no
caller may lower it to fill a report.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

QUALIFICATION_THRESHOLD = 0.85
MIN_DATA_CONFIDENCE = 0.60
NO_QUALIFIED_SELECTIONS = "NO QUALIFIED SELECTIONS FOUND."


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def data_confidence(
    selection: Any,
    context: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Score how much real data stands behind a probability, with reasons."""
    now = now or datetime.now(timezone.utc)
    score = 0.40
    reasons: List[str] = ["baseline: model inputs present"]

    lambda_source = context.get("lambda_source")
    if context.get("home_lambda") is not None and context.get("away_lambda") is not None:
        if lambda_source == "team_ratings":
            score += 0.30
            reasons.append("expected goals from historical team ratings")
        elif lambda_source:
            score += 0.10
            reasons.append(f"expected goals from {lambda_source}")

    captured = _parse_timestamp(getattr(selection, "captured_at", None))
    if captured is not None:
        age_hours = max(0.0, (now - captured).total_seconds() / 3600.0)
        if age_hours <= 24:
            score += 0.15
            reasons.append("odds captured within 24 hours")
        elif age_hours <= 72:
            score += 0.05
            reasons.append("odds captured within 3 days")

    if context.get("cross_verified"):
        score += 0.15
        reasons.append("fixture/odds cross-verified across providers")

    score = round(min(1.0, score), 2)
    return {"confidence": score, "reasons": reasons}


def qualify(
    probability: Optional[float],
    confidence: Optional[float],
    data_status: str = "ok",
) -> Dict[str, Any]:
    """Apply the published gate without altering the threshold."""
    if probability is None:
        return {"qualified": False, "reason": "probability_unavailable"}
    if data_status != "ok":
        return {"qualified": False, "reason": "data_unavailable"}
    if probability < QUALIFICATION_THRESHOLD:
        return {
            "qualified": False,
            "reason": f"probability_below_{QUALIFICATION_THRESHOLD:.2f}",
        }
    if confidence is None or confidence < MIN_DATA_CONFIDENCE:
        return {"qualified": False, "reason": "low_data_confidence"}
    return {"qualified": True, "reason": "meets_probability_and_data_gate"}


def qualified(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in rows if row.get("qualified")]


def summary(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    passed = qualified(rows)
    return {
        "threshold": QUALIFICATION_THRESHOLD,
        "minimum_confidence": MIN_DATA_CONFIDENCE,
        "evaluated": len(rows),
        "qualified_count": len(passed),
        "message": NO_QUALIFIED_SELECTIONS if not passed else f"{len(passed)} qualified selections",
    }
