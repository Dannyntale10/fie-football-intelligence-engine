from __future__ import annotations
"""
live_feed.py – Real-time live & upcoming football fixture ingestion.

Fetches genuine upcoming pre-match fixtures with real-time DraftKings odds
from the public ESPN live sports scoreboard across active competitions:
- UEFA Nations League (playing right now & tomorrow!)
- European domestic leagues (Premier League, La Liga, Serie A, etc.)
Strictly ensures kickoff_utc > now so no past matches are imported as upcoming.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.fixtures import make_dedupe_key


def _next_7_days() -> list:
    """Generate date param strings for today + next 7 days (ESPN ?dates=YYYYMMDD format)."""
    from datetime import date, timedelta
    today = date.today()
    return [""] + [f"?dates={(today + timedelta(days=i)).strftime('%Y%m%d')}" for i in range(8)]


COMPETITION_TARGETS = [
    # Format: (espn_code, display_name, country, date_params)
    # ── UEFA & International ───────────────────────────────────────────────────
    ("uefa.nations",        "UEFA Nations League",      "Europe",       _next_7_days()),
    ("uefa.champions",      "UEFA Champions League",    "Europe",       _next_7_days()),
    ("uefa.europa",         "UEFA Europa League",       "Europe",       _next_7_days()),
    ("fifa.worldq.uefa",    "FIFA World Cup Qualifying","Europe",       _next_7_days()),
    ("fifa.worldq.conmebol","CONMEBOL WC Qualifying",   "South America",_next_7_days()),
    ("conmebol.copa",       "Copa Libertadores",        "South America",_next_7_days()),
    # ── Top 5 European Leagues ─────────────────────────────────────────────────
    ("eng.1",   "Premier League",   "England",     _next_7_days()),
    ("esp.1",   "La Liga",          "Spain",       _next_7_days()),
    ("ita.1",   "Serie A",          "Italy",       _next_7_days()),
    ("ger.1",   "Bundesliga",       "Germany",     _next_7_days()),
    ("fra.1",   "Ligue 1",          "France",      _next_7_days()),
    # ── Secondary European Leagues ─────────────────────────────────────────────
    ("eng.2",   "Championship",     "England",     _next_7_days()),
    ("ned.1",   "Eredivisie",       "Netherlands", _next_7_days()),
    ("por.1",   "Primeira Liga",    "Portugal",    _next_7_days()),
    ("tur.1",   "Super Lig",        "Turkey",      _next_7_days()),
    ("bel.1",   "Belgian Pro League","Belgium",    _next_7_days()),
    ("sco.1",   "Scottish Premiership","Scotland", _next_7_days()),
    ("gre.1",   "Super League Greece","Greece",    _next_7_days()),
    ("rus.1",   "Russian Premier League","Russia", _next_7_days()),
    ("aut.1",   "Austrian Bundesliga","Austria",   _next_7_days()),
    ("cze.1",   "Czech First League","Czech Rep",  _next_7_days()),
    ("den.1",   "Danish Superliga", "Denmark",     _next_7_days()),
    ("swe.1",   "Allsvenskan",      "Sweden",      _next_7_days()),
    ("nor.1",   "Eliteserien",      "Norway",      _next_7_days()),
    ("swi.1",   "Super League",     "Switzerland", _next_7_days()),
    ("cro.1",   "HNL",              "Croatia",     _next_7_days()),
    ("srb.1",   "Super Liga",       "Serbia",      _next_7_days()),
    # ── Americas ──────────────────────────────────────────────────────────────
    ("usa.1",   "MLS",              "USA",         _next_7_days()),
    ("mex.1",   "Liga MX",          "Mexico",      _next_7_days()),
    ("bra.1",   "Serie A Brazil",   "Brazil",      _next_7_days()),
    ("arg.1",   "Liga Profesional", "Argentina",   _next_7_days()),
    # ── Africa & Asia ─────────────────────────────────────────────────────────
    ("caf.champions",  "CAF Champions League","Africa",    _next_7_days()),
    ("afc.champions",  "AFC Champions League","Asia",      _next_7_days()),
    ("jpn.1",   "J1 League",        "Japan",       _next_7_days()),
    ("aus.1",   "A-League",         "Australia",   _next_7_days()),
    ("sau.1",   "Saudi Pro League", "Saudi Arabia",_next_7_days()),
]


def american_to_decimal(odd_val: Any) -> Optional[float]:
    """Convert American moneyline odds string (e.g. '+135', '-150') to decimal odds (e.g. 2.35, 1.67)."""
    if not odd_val:
        return None
    s = str(odd_val).strip()
    try:
        val = int(s)
        if val > 0:
            return round(1.0 + (val / 100.0), 2)
        elif val < 0:
            return round(1.0 + (100.0 / abs(val)), 2)
    except Exception:
        pass
    try:
        f = float(s)
        if f > 1.0:
            return round(f, 2)
    except Exception:
        pass
    return None


def fetch_live_upcoming_fixtures_espn(db: Session) -> Dict[str, Any]:
    """Fetch all real upcoming matches playing today, tomorrow, and upcoming days with live DraftKings odds."""
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    total_found = 0
    headers = {"User-Agent": "Mozilla/5.0"}
    session = requests.Session()

    seen_event_ids = set()

    for comp_code, comp_name, country, date_params in COMPETITION_TARGETS:
        for d_param in date_params:
            url = f"http://site.api.espn.com/apis/site/v2/sports/soccer/{comp_code}/scoreboard{d_param}"
            try:
                res = session.get(url, headers=headers, timeout=4)
                if res.status_code != 200:
                    continue
                data = res.json()
            except Exception:
                continue

            events = data.get("events", [])
            for event in events:
                event_id = event.get("id")
                if event_id and event_id in seen_event_ids:
                    continue
                if event_id:
                    seen_event_ids.add(event_id)

                comp_data = event.get("competitions", [{}])[0]
                status_state = comp_data.get("status", {}).get("type", {}).get("state", "pre")

                raw_date = event.get("date")
                if not raw_date:
                    continue
                try:
                    kickoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except Exception:
                    continue

                # STRICT RULE: Must be strictly in the future!
                if kickoff <= now:
                    continue

                competitors = comp_data.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home_team = next(
                    (c["team"].get("displayName") or c["team"].get("name")
                     for c in competitors if c.get("homeAway") == "home"),
                    None
                )
                away_team = next(
                    (c["team"].get("displayName") or c["team"].get("name")
                     for c in competitors if c.get("homeAway") == "away"),
                    None
                )
                if not home_team or not away_team:
                    continue

                # Extract DraftKings odds
                h_odds, d_odds, a_odds = None, None, None
                odds_list = comp_data.get("odds", [])
                if odds_list and isinstance(odds_list[0], dict):
                    od = odds_list[0]
                    ml = od.get("moneyline", {})
                    h_odds = american_to_decimal(ml.get("home", {}).get("close", {}).get("odds"))
                    d_odds = american_to_decimal(ml.get("draw", {}).get("close", {}).get("odds"))
                    a_odds = american_to_decimal(ml.get("away", {}).get("close", {}).get("odds"))

                    if not h_odds:
                        h_odds = american_to_decimal(ml.get("home", {}).get("open", {}).get("odds"))
                    if not d_odds:
                        d_odds = american_to_decimal(ml.get("draw", {}).get("open", {}).get("odds"))
                    if not a_odds:
                        a_odds = american_to_decimal(ml.get("away", {}).get("open", {}).get("odds"))

                total_found += 1
                dedupe = make_dedupe_key(kickoff, home_team, away_team, comp_name)

                existing = db.scalar(select(Fixture).where(Fixture.dedupe_key == dedupe))
                if existing:
                    if h_odds and existing.home_odds is None:
                        existing.home_odds = h_odds
                        existing.draw_odds = d_odds
                        existing.away_odds = a_odds
                        existing.source = "ESPN_DraftKings"
                        db.commit()
                        updated += 1
                else:
                    new_fix = Fixture(
                        dedupe_key=dedupe,
                        kickoff_utc=kickoff,
                        home_team=home_team,
                        away_team=away_team,
                        competition=comp_name,
                        country=country,
                        source="ESPN_DraftKings",
                        external_fixture_id=str(event_id) if event_id else None,
                        home_odds=h_odds,
                        draw_odds=d_odds,
                        away_odds=a_odds,
                        status="scheduled",
                    )
                    db.add(new_fix)
                    db.commit()
                    inserted += 1

    return {
        "inserted": inserted,
        "updated": updated,
        "total_found": total_found,
        "message": f"Successfully ingested {inserted} genuine upcoming live matches ({updated} updated with DraftKings odds) across UEFA Nations League & top leagues."
    }
