from __future__ import annotations
"""
inplay.py – Fetch live in-play scores for today's fixtures from ESPN.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture

EAT_TZ = timezone(timedelta(hours=3), name="EAT")

LEAGUE_CODES = [
    "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.nations",
    "uefa.champions", "uefa.europa", "eng.2", "ned.1", "por.1",
    "bra.1", "arg.1", "usa.1", "mex.1", "sau.1", "jpn.1",
]

def fetch_live_scores(db: Session) -> List[Dict[str, Any]]:
    """Fetch current live in-play and scheduled scores from ESPN for today's matches."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Today's fixtures from DB
    today_fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.kickoff_utc >= today_start)
        .where(Fixture.kickoff_utc <= now + timedelta(hours=24))
    ).all()

    ext_id_map = {str(f.external_fixture_id): f for f in today_fixtures if f.external_fixture_id}

    live_matches: List[Dict[str, Any]] = []
    headers = {"User-Agent": "Mozilla/5.0"}
    seen_ids = set()

    for lc in LEAGUE_CODES:
        try:
            url = f"http://site.api.espn.com/apis/site/v2/sports/soccer/{lc}/scoreboard"
            r = requests.get(url, headers=headers, timeout=4)
            if r.status_code != 200:
                continue
            events = r.json().get("events", [])
            for ev in events:
                ev_id = str(ev.get("id", ""))
                if ev_id in seen_ids:
                    continue
                seen_ids.add(ev_id)

                comp_data = ev.get("competitions", [{}])[0]
                status = comp_data.get("status", {})
                state = status.get("type", {}).get("state", "pre")  # pre, in, post
                display_clock = status.get("displayClock", "")
                period = status.get("period", 0)
                status_desc = status.get("type", {}).get("shortDetail", "")

                competitors = comp_data.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home_team = next((c["team"].get("displayName", "") for c in competitors if c.get("homeAway") == "home"), "")
                away_team = next((c["team"].get("displayName", "") for c in competitors if c.get("homeAway") == "away"), "")
                home_score = next((c.get("score", "") for c in competitors if c.get("homeAway") == "home"), "")
                away_score = next((c.get("score", "") for c in competitors if c.get("homeAway") == "away"), "")

                db_fixture = ext_id_map.get(ev_id)

                live_matches.append({
                    "event_id": ev_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_score": home_score,
                    "away_score": away_score,
                    "state": state,
                    "clock": display_clock,
                    "period": period,
                    "status_desc": status_desc,
                    "fixture_id": db_fixture.id if db_fixture else None,
                    "competition": db_fixture.competition if db_fixture else lc,
                    "is_live": state == "in",
                    "is_finished": state == "post",
                    "kickoff_eat": db_fixture.kickoff_utc.astimezone(EAT_TZ).strftime("%H:%M EAT") if (db_fixture and db_fixture.kickoff_utc) else "",
                })
        except Exception:
            continue

    state_order = {"in": 0, "pre": 1, "post": 2}
    live_matches.sort(key=lambda x: state_order.get(x["state"], 3))
    return live_matches
