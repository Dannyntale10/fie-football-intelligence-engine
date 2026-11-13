"""The scheduler shortlist records one ledger row per size, once."""
from __future__ import annotations

from app.database import SessionLocal
from app.models.bet import TrackedBet
from app.tasks import scheduler


def _report():
    def leg(fid, selection, probability, odds):
        return {
            "fixture_id": fid,
            "match": f"Team {fid} vs Rival {fid}",
            "competition": "UPL",
            "team": "Team",
            "selection": selection,
            "market_key": "home_win",
            "market_type": "1X2",
            "odds": odds,
            "probability": probability,
            "odds_source": "market",
            "kickoff_eat": "16:00 EAT",
        }

    two = {
        "size": 2,
        "legs": [leg(1, "Team 1 to win", 0.8, 1.3), leg(2, "Team 2 to win", 0.8, 1.3)],
        "combined_probability": 0.64,
        "total_odds": 1.69,
        "expected_value_percent": 8.2,
    }
    return {
        "matchday_label": "Friday, 13 Nov 2026",
        "target_date": "2026-11-13",
        "params": {"min_leg_probability": 0.75},
        "combos": {"2_leg": [two], "3_leg": []},
        "best": two,
    }


def test_build_shortlist_records_once(monkeypatch):
    db = SessionLocal()
    monkeypatch.setattr("app.betting.daily_combos.build_daily_combos", lambda session: _report())
    monkeypatch.setattr(scheduler, "deliver_notifications", lambda: {"status": "ok"})
    monkeypatch.setattr(scheduler, "_notify", lambda *args, **kwargs: None)
    try:
        scheduler.build_shortlist()
        scheduler.build_shortlist()
        rows = db.query(TrackedBet).filter(TrackedBet.dedupe_key.like("shortlist:%")).all()
        assert len(rows) == 1
        assert rows[0].tier == 0.75
    finally:
        db.query(TrackedBet).filter(TrackedBet.dedupe_key.like("shortlist:%")).delete()
        db.commit()
        db.close()
