from __future__ import annotations
"""
odds_movement.py – Track bookmaker odds line movement to detect sharp money signals.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture

def record_opening_odds(db: Session, fixture_id: int) -> bool:
    """Snapshot current odds as opening odds if not yet set."""
    f = db.scalar(select(Fixture).where(Fixture.id == fixture_id))
    if not f or f.opening_home_odds is not None:
        return False
    f.opening_home_odds = f.home_odds
    f.opening_draw_odds = f.draw_odds
    f.opening_away_odds = f.away_odds
    f.odds_updated_at = datetime.now(timezone.utc)
    db.commit()
    return True

def get_odds_movement(db: Session, upcoming_only: bool = True) -> List[Dict[str, Any]]:
    """Return fixtures with significant odds movement."""
    now = datetime.now(timezone.utc)
    query = select(Fixture).where(
        Fixture.opening_home_odds.isnot(None),
        Fixture.home_odds.isnot(None),
    )
    if upcoming_only:
        query = query.where(Fixture.kickoff_utc > now)

    fixtures = db.scalars(query).all()
    movements = []

    for f in fixtures:
        oh, ch = f.opening_home_odds, f.home_odds
        oa, ca = f.opening_away_odds, f.away_odds
        od, cd = f.opening_draw_odds, f.draw_odds

        def pct(old, new):
            if not old or not new or old == 0:
                return 0.0
            return round((new - old) / old * 100, 1)

        h_chg = pct(oh, ch)
        a_chg = pct(oa, ca)
        d_chg = pct(od, cd)

        signals = []
        if abs(h_chg) >= 8:
            signals.append(f"Home {'⬇️ shortened' if h_chg < 0 else '⬆️ drifted'} {abs(h_chg):.0f}% ({oh:.2f} → {ch:.2f})")
        if abs(a_chg) >= 8:
            signals.append(f"Away {'⬇️ shortened' if a_chg < 0 else '⬆️ drifted'} {abs(a_chg):.0f}% ({oa:.2f} → {ca:.2f})")
        if abs(d_chg) >= 8:
            signals.append(f"Draw {'⬇️ shortened' if d_chg < 0 else '⬆️ drifted'} {abs(d_chg):.0f}% ({od:.2f} → {cd:.2f})")

        if signals:
            movements.append({
                "fixture_id": f.id,
                "match": f"{f.home_team} vs {f.away_team}",
                "competition": f.competition,
                "kickoff_utc": f.kickoff_utc.isoformat() if f.kickoff_utc else None,
                "signals": signals,
                "home_change_pct": h_chg,
                "away_change_pct": a_chg,
                "draw_change_pct": d_chg,
                "opening_odds": {"home": oh, "draw": od, "away": oa},
                "current_odds": {"home": ch, "draw": cd, "away": ca},
                "sharp_signal": "⚡ SHARP MOVE" if max(abs(h_chg), abs(a_chg)) >= 18 else "📉 Line Move",
            })

    movements.sort(key=lambda x: max(abs(x["home_change_pct"]), abs(x["away_change_pct"])), reverse=True)
    return movements
