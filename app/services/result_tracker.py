from __future__ import annotations
"""
result_tracker.py – Fetch final scores for completed fixtures and evaluate model prediction accuracy.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture
from app.services.probabilities import analyze_fixture

LEAGUE_CODES = [
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.nations",
    "uefa.champions", "uefa.europa", "eng.2", "ned.1", "por.1",
    "bra.1", "arg.1", "usa.1"
]

def fetch_and_update_results(db: Session) -> Dict[str, Any]:
    """Fetch final scores from ESPN for scheduled fixtures that played recently."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)
    pending = db.scalars(
        select(Fixture)
        .where(Fixture.status.in_(["scheduled", "pending_result"]))
        .where(Fixture.kickoff_utc <= cutoff)
        .where(Fixture.home_score.is_(None))
    ).all()

    updated = 0
    errors = 0
    headers = {"User-Agent": "Mozilla/5.0"}

    for f in pending:
        if not f.external_fixture_id:
            continue
        matched = False
        for lc in LEAGUE_CODES:
            try:
                url = f"http://site.api.espn.com/apis/site/v2/sports/soccer/{lc}/scoreboard"
                r = requests.get(url, headers=headers, timeout=4)
                if r.status_code != 200:
                    continue
                for ev in r.json().get("events", []):
                    if str(ev.get("id")) != str(f.external_fixture_id):
                        continue
                    comp = ev.get("competitions", [{}])[0]
                    stype = comp.get("status", {}).get("type", {}).get("name", "")
                    if stype in ("STATUS_FINAL", "STATUS_FULL_TIME"):
                        competitors = comp.get("competitors", [])
                        hs = next((int(c.get("score", 0)) for c in competitors if c.get("homeAway") == "home"), None)
                        as_ = next((int(c.get("score", 0)) for c in competitors if c.get("homeAway") == "away"), None)
                        if hs is not None and as_ is not None:
                            f.home_score = hs
                            f.away_score = as_
                            f.status = "finished"
                            db.commit()
                            updated += 1
                            matched = True
                    break
                if matched:
                    break
            except Exception:
                errors += 1

    return {"updated": updated, "errors": errors, "pending_checked": len(pending)}

def calculate_prediction_accuracy(db: Session, days: int = 30) -> Dict[str, Any]:
    """Calculate historical prediction accuracy over the last N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    finished = db.scalars(
        select(Fixture)
        .where(Fixture.status == "finished")
        .where(Fixture.home_score.isnot(None))
        .where(Fixture.kickoff_utc >= cutoff)
        .order_by(Fixture.kickoff_utc.desc())
        .limit(100)
    ).all()

    total = 0
    correct = 0
    details = []

    for f in finished:
        try:
            analysis = analyze_fixture(f, db=db)
            probs = analysis.get("all_markets", {})
            outcomes = {
                "home_win": probs.get("home_win", 0.0),
                "draw": probs.get("draw", 0.0),
                "away_win": probs.get("away_win", 0.0)
            }
            predicted = max(outcomes, key=outcomes.get)
            h, a = f.home_score, f.away_score
            actual = "home_win" if h > a else ("draw" if h == a else "away_win")
            is_correct = (predicted == actual)
            if is_correct:
                correct += 1
            total += 1
            details.append({
                "match": f"{f.home_team} {h}-{a} {f.away_team}",
                "predicted": predicted.replace("_", " ").title(),
                "actual": actual.replace("_", " ").title(),
                "confidence": round(outcomes[predicted] * 100, 1),
                "correct": is_correct,
            })
        except Exception:
            continue

    accuracy = round(correct / total * 100, 1) if total > 0 else 0.0
    return {
        "period_days": days,
        "total_matches": total,
        "correct_predictions": correct,
        "accuracy_percent": accuracy,
        "details": details[:20],
    }
