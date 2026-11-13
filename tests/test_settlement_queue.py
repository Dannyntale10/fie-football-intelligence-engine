"""Settlement queues one Telegram notification per finished bet."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.betting import settlement
from app.database import SessionLocal
from app.models.bet import TrackedBet
from app.models.fixture import Fixture
from app.models.predictions import Notification


def test_settlement_queues_once(monkeypatch):
    db = SessionLocal()
    fixture = Fixture(
        dedupe_key=f"settle-queue-fixture-{uuid.uuid4().hex[:8]}",
        kickoff_utc=datetime.now(timezone.utc),
        home_team="KCCA",
        away_team="Vipers",
        competition="UPL",
        country="Uganda",
        source="test",
        status="finished",
        home_score=2,
        away_score=0,
    )
    db.add(fixture)
    db.commit()
    bet = TrackedBet(
        slip_type="2-Team Combo",
        matchday_label="Friday",
        total_odds=2.0,
        avg_confidence=0.8,
        accum_probability=0.64,
        stake_amount=10.0,
        stake_currency="UGX",
        stake_usd=0.0,
        status="PENDING",
        profit_loss=0.0,
        profit_loss_usd=0.0,
        legs_json=json.dumps([{
            "fixture_id": fixture.id,
            "match": "KCCA vs Vipers",
            "selection": "KCCA to win",
            "market_key": "home_win",
            "odds": 2.0,
            "probability": 0.8,
            "status": "PENDING",
        }]),
        dedupe_key=f"settle-queue-bet-{uuid.uuid4().hex[:8]}",
    )
    db.add(bet)
    db.commit()
    monkeypatch.setattr(settlement, "sync_finished_match_scores_from_espn", lambda session: 0)
    try:
        first = settlement.settle_pending_bets(db)
        second = settlement.settle_pending_bets(db)
        db.refresh(bet)
        rows = db.query(Notification).filter(Notification.dedupe_key == f"settle:{bet.id}").all()
        assert len(first) == 1
        assert second == []
        assert bet.status == "WON"
        assert bet.settlement_notified is True
        assert len(rows) == 1
    finally:
        db.query(Notification).filter(Notification.dedupe_key == f"settle:{bet.id}").delete()
        db.delete(bet)
        db.delete(fixture)
        db.commit()
        db.close()
