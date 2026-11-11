from __future__ import annotations
"""
value_score.py – Composite 0-100 Value Index per fixture.

Combines:
  1. Data Quality Score (0-30 points)
  2. Maximum Expected Value (+EV) (0-25 points)
  3. Banker Confidence above 85% (0-25 points)
  4. Model Peak Confidence (0-20 points)
"""
from typing import Any, Dict

def compute_value_score(fixture: Any, analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a composite 0-100 value score for a fixture."""
    try:
        from app.services.quality import evaluate_fixture_signals, grade_data_quality
        sigs = evaluate_fixture_signals(fixture)
        q = grade_data_quality(sigs)
        dq_pts = round(q["score"] / 100 * 30, 1)
    except Exception:
        dq_pts = 15.0

    vbs = analysis.get("value_bets", [])
    best_ev = max((vb["expected_value_percent"] for vb in vbs if vb.get("is_value")), default=0.0)
    ev_pts = round(min(best_ev, 25.0), 1) if best_ev > 0 else 0.0

    q_mkts = analysis.get("qualifying_markets", [])
    best_q_prob = max((m["probability"] for m in q_mkts), default=0.0)
    q_pts = round(max(0, (best_q_prob - 0.85) / 0.15 * 25), 1) if best_q_prob >= 0.85 else 0.0

    all_mkts = analysis.get("all_markets", {})
    max_prob = max(all_mkts.values(), default=0.0) if all_mkts else 0.0
    conf_pts = round(min(max_prob * 20, 20.0), 1)

    total = int(round(min(100, dq_pts + ev_pts + q_pts + conf_pts)))

    if total >= 80:
        grade, label = "A", "⭐ High Value"
    elif total >= 60:
        grade, label = "B", "🟢 Good Value"
    elif total >= 40:
        grade, label = "C", "🟡 Moderate"
    else:
        grade, label = "D", "🔴 Low Value"

    return {
        "score": total,
        "grade": grade,
        "label": label,
        "components": {
            "data_quality_pts": dq_pts,
            "ev_pts": ev_pts,
            "qualifying_market_pts": q_pts,
            "confidence_pts": conf_pts,
        },
    }
