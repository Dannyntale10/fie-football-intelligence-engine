from __future__ import annotations
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture
from app.data_sources.fixtures import make_dedupe_key, clean_team_name, _float_or_none
from app.analytics.ratings import calculate_ratings_from_fixtures

DIV_MAPPING = {
    "E0": "Premier League",
    "E1": "Championship",
    "E2": "League One",
    "E3": "League Two",
    "SP1": "La Liga",
    "SP2": "Segunda Division",
    "I1": "Serie A",
    "I2": "Serie B",
    "D1": "Bundesliga",
    "D2": "2. Bundesliga",
    "F1": "Ligue 1",
    "F2": "Ligue 2",
    "N1": "Eredivisie",
    "B1": "Belgian Pro League",
    "P1": "Primeira Liga",
    "T1": "Super Lig",
    "G1": "Super League Greece",
    "SC0": "Scottish Premiership",
}

# Pre-2002/03 files predate B365/Avg columns and only carry these books.
# Order is a preference for one real bookmaker price, never a synthetic one.
_HOME_PRICE_COLUMNS = ("B365H", "PSH", "BFDH", "WHH", "LBH", "SBH", "IWH", "GBH", "AvgH", "MaxH")
_DRAW_PRICE_COLUMNS = ("B365D", "PSD", "BFDD", "WHD", "LBD", "SBD", "IWD", "GBD", "AvgD", "MaxD")
_AWAY_PRICE_COLUMNS = ("B365A", "PSA", "BFDA", "WHA", "LBA", "SBA", "IWA", "GBA", "AvgA", "MaxA")


def _first_price(row, columns) -> Optional[float]:
    """First usable decimal price among ``columns``.

    ``row.get`` returns NaN for a present-but-empty cell, and NaN is truthy,
    so a plain ``or`` cascade silently loses valid fallbacks.
    """
    for column in columns:
        value = _float_or_none(row.get(column))
        if value is not None and value > 1.0:
            return value
    return None


def _decode_csv_bytes(raw: bytes) -> str:
    """Decode a football-data CSV; the oldest files are Latin-1, not UTF-8."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


_LEGACY_TEAM_COLUMNS = {"HT": "HomeTeam", "AT": "AwayTeam"}


def read_football_data_csv(raw: bytes) -> tuple:
    """Parse a football-data.co.uk CSV, repairing ragged rows.

    The source files occasionally carry extra empty cells (or a short row),
    which makes ``pandas.read_csv`` abandon the whole file. Padding short rows
    and trimming trailing empty extras recovers those matches; a row whose
    extra cells hold real values is too ambiguous to align, so it is dropped
    and counted. Early Greek files also name the teams ``HT``/``AT``.

    Returns ``(frame, dropped_rows)``.
    """
    rows = list(csv.reader(_decode_csv_bytes(raw).splitlines()))
    if not rows:
        return pd.DataFrame(), 0

    header = rows[0]
    if "HomeTeam" not in header and "HT" in header:
        header = [_LEGACY_TEAM_COLUMNS.get(name, name) for name in header]

    width = len(header)
    kept = []
    dropped = 0
    for row in rows[1:]:
        if len(row) > width:
            extras = [cell for cell in row[width:] if cell.strip()]
            if extras:
                dropped += 1
                continue
            row = row[:width]
        elif len(row) < width:
            row = row + [""] * (width - len(row))
        kept.append(row)

    return pd.DataFrame(kept, columns=header), dropped


def _country_for_div(div: str) -> str:
    if div.startswith("E"):
        return "England"
    if div.startswith("SC"):
        return "Scotland"
    if div.startswith("SP"):
        return "Spain"
    if div.startswith("I"):
        return "Italy"
    if div.startswith("D"):
        return "Germany"
    if div.startswith("F"):
        return "France"
    if div.startswith("N"):
        return "Netherlands"
    if div.startswith("B"):
        return "Belgium"
    if div.startswith("P"):
        return "Portugal"
    if div.startswith("T"):
        return "Turkey"
    if div.startswith("G"):
        return "Greece"
    return "Europe"

def parse_football_data_date(date_str: str, time_str: Optional[str] = None) -> datetime:
    """Parse football-data.co.uk dates like '11/08/2023' and optional '20:00'."""
    raw = str(date_str).strip()
    time_part = str(time_str).strip() if time_str and str(time_str).strip() != "nan" else "15:00"

    # Try DD/MM/YYYY or DD/MM/YY
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            # Add time
            if ":" in time_part:
                parts = time_part.split(":")
                dt = dt.replace(hour=int(parts[0]), minute=int(parts[1]))
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    # Fallback to pandas
    return pd.to_datetime(f"{raw} {time_part}", utc=True).to_pydatetime()

def import_football_data_dataframe(
    db: Session,
    df: pd.DataFrame,
    default_source: str = "FootballData",
    recalculate_ratings: bool = True,
) -> Dict[str, Any]:
    """Ingest match rows from a Football-Data.co.uk formatted DataFrame."""
    required = {"HomeTeam", "AwayTeam", "Date"}
    if not required.issubset(set(df.columns)):
        raise ValueError(f"CSV missing essential football-data columns: {required - set(df.columns)}")

    inserted = 0
    duplicates = 0
    priced = 0

    for _, row in df.iterrows():
        home = str(row.get("HomeTeam", "")).strip()
        away = str(row.get("AwayTeam", "")).strip()
        if not home or not away:
            continue

        raw_div = str(row.get("Div", "E0")).strip()
        comp = DIV_MAPPING.get(raw_div, raw_div)
        country = _country_for_div(raw_div)

        time_val = str(row.get("Time", "")) if "Time" in df.columns else None
        kickoff = parse_football_data_date(str(row["Date"]), time_val)
        key = make_dedupe_key(kickoff, home, away, comp)

        h_odds = _first_price(row, _HOME_PRICE_COLUMNS)
        d_odds = _first_price(row, _DRAW_PRICE_COLUMNS)
        a_odds = _first_price(row, _AWAY_PRICE_COLUMNS)
        if h_odds is not None and d_odds is not None and a_odds is not None:
            priced += 1

        # Multi-bookmaker odds (BetVictor=BV, Betway=BW, Pinnacle=PS, Max=MaxOdds)
        bv_h = _float_or_none(row.get("BVH") or row.get("BWH"))
        bv_d = _float_or_none(row.get("BVD") or row.get("BWD"))
        bv_a = _float_or_none(row.get("BVA") or row.get("BWA"))
        bw_h = _float_or_none(row.get("BWH"))
        bw_d = _float_or_none(row.get("BWD"))
        bw_a = _float_or_none(row.get("BWA"))
        ps_h = _float_or_none(row.get("PSH"))
        ps_d = _float_or_none(row.get("PSD"))
        ps_a = _float_or_none(row.get("PSA"))
        max_h = _float_or_none(row.get("MaxH"))
        max_d = _float_or_none(row.get("MaxD"))
        max_a = _float_or_none(row.get("MaxA"))

        # Scores
        h_score = int(row["FTHG"]) if "FTHG" in row and str(row["FTHG"]).replace(".0", "").isdigit() else None
        a_score = int(row["FTAG"]) if "FTAG" in row and str(row["FTAG"]).replace(".0", "").isdigit() else None
        status = "finished" if (h_score is not None and a_score is not None) else "scheduled"

        existing = db.scalar(select(Fixture).where(Fixture.dedupe_key == key))
        if existing:
            updated = False
            if h_score is not None and existing.home_score is None:
                existing.home_score = h_score
                existing.away_score = a_score
                existing.status = status
                updated = True
            if existing.home_odds is None and h_odds is not None:
                existing.home_odds = h_odds
                existing.draw_odds = d_odds
                existing.away_odds = a_odds
                updated = True
            # Backfill multi-book odds if missing
            if existing.max_home_odds is None and max_h is not None:
                existing.max_home_odds = max_h
                existing.max_draw_odds = max_d
                existing.max_away_odds = max_a
                updated = True
            if existing.ps_home_odds is None and ps_h is not None:
                existing.ps_home_odds = ps_h
                existing.ps_draw_odds = ps_d
                existing.ps_away_odds = ps_a
                updated = True
            if updated:
                db.commit()
            duplicates += 1
        else:
            fix = Fixture(
                dedupe_key=key,
                kickoff_utc=kickoff,
                home_team=home,
                away_team=away,
                competition=comp,
                country=country,
                source=default_source,
                home_odds=h_odds,
                draw_odds=d_odds,
                away_odds=a_odds,
                bv_home_odds=bv_h,
                bv_draw_odds=bv_d,
                bv_away_odds=bv_a,
                bw_home_odds=bw_h,
                bw_draw_odds=bw_d,
                bw_away_odds=bw_a,
                ps_home_odds=ps_h,
                ps_draw_odds=ps_d,
                ps_away_odds=ps_a,
                max_home_odds=max_h,
                max_draw_odds=max_d,
                max_away_odds=max_a,
                home_score=h_score,
                away_score=a_score,
                status=status,
            )
            db.add(fix)
            inserted += 1

    db.commit()

    # Bulk history imports defer this: rebuilding every competition's ratings
    # after each of hundreds of files is quadratic and changes nothing.
    rating_res = (
        calculate_ratings_from_fixtures(db)
        if recalculate_ratings
        else {"updated_teams": 0}
    )

    calibrated = rating_res.get("updated_teams", 0)
    message = f"Successfully ingested {len(df)} matches ({inserted} new, {duplicates} updated)."
    if recalculate_ratings:
        message += f" Calibrated {calibrated} team ratings."
    else:
        message += " Ratings rebuild deferred."

    return {
        "inserted": inserted,
        "duplicates": duplicates,
        "total_processed": len(df),
        "priced": priced,
        "teams_rated": calibrated,
        "message": message,
    }

def seed_sample_historical_data(db: Session, filepath: str = "data/historical_epl_sample.csv") -> Dict[str, Any]:
    """Load bundled historical sample fixtures and compute initial ratings."""
    p = Path(filepath)
    if not p.is_absolute():
        cand1 = Path.cwd() / p
        cand2 = Path(__file__).resolve().parents[3] / p
        p = cand1 if cand1.exists() else cand2

    if not p.exists():
        raise FileNotFoundError(f"Historical file not found at: {p}")

    df = pd.read_csv(p)
    return import_football_data_dataframe(db, df, default_source="HistoricalEPL")

BACKTEST_HISTORY_SEASONS = (("E0", "2324"), ("E0", "2425"), ("SP1", "2425"))

def seed_backtest_history(db: Session, seasons=BACKTEST_HISTORY_SEASONS) -> Dict[str, Any]:
    """Import completed seasons (scores + closing odds) for strategy testing.

    Downloads from football-data.co.uk; if every download fails, loads the
    small bundled sample so the backtester still gets finished matches.
    """
    inserted = duplicates = seasons_loaded = 0
    errors: list = []
    for division_code, season in seasons:
        try:
            res = fetch_historical_league_season(db, division_code, season)
            inserted += int(res.get("inserted", 0))
            duplicates += int(res.get("duplicates", 0))
            seasons_loaded += 1
        except Exception as e:
            errors.append(f"{DIV_MAPPING.get(division_code, division_code)} {season}: {e}")

    if seasons_loaded == 0:
        sample = seed_sample_historical_data(db)
        return {
            "source": "bundled_sample",
            "inserted": int(sample.get("inserted", 0)),
            "duplicates": int(sample.get("duplicates", 0)),
            "seasons_loaded": 0,
            "errors": errors,
        }

    return {
        "source": "football-data.co.uk",
        "inserted": inserted,
        "duplicates": duplicates,
        "seasons_loaded": seasons_loaded,
        "errors": errors,
    }

def fetch_and_import_url(db: Session, url: str, default_source: str = "FootballDataLive") -> Dict[str, Any]:
    """Download CSV from live football-data.co.uk URL and ingest real fixtures and odds."""
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    res = requests.get(url, headers=headers, timeout=15)
    res.raise_for_status()
    df, _dropped = read_football_data_csv(res.content)
    return import_football_data_dataframe(db, df, default_source=default_source)

def fetch_live_upcoming_fixtures(db: Session) -> Dict[str, Any]:
    """Fetch all real upcoming European matches with live bookmaker odds."""
    url = "https://football-data.co.uk/fixtures.csv"
    return fetch_and_import_url(db, url, default_source="FootballDataUpcoming")

def fetch_historical_league_season(db: Session, division_code: str = "E0", season: str = "2324") -> Dict[str, Any]:
    """Fetch full historical league season (e.g. E0=Premier League, SP1=La Liga, I1=Serie A)."""
    url = f"https://football-data.co.uk/mmz4281/{season}/{division_code}.csv"
    comp_name = DIV_MAPPING.get(division_code, division_code)
    return fetch_and_import_url(db, url, default_source=f"{comp_name}_{season}")

MAJOR_DIVISIONS = [
    ("E0", "Premier League"),
    ("SP1", "La Liga"),
    ("I1", "Serie A"),
    ("D1", "Bundesliga"),
    ("F1", "Ligue 1"),
    ("E1", "Championship"),
    ("N1", "Eredivisie"),
    ("P1", "Primeira Liga"),
    ("B1", "Belgian Pro League"),
    ("T1", "Super Lig"),
    ("SC0", "Scottish Premiership"),
    ("G1", "Super League Greece"),
]

# Every division football-data.co.uk publishes, which is also what the
# historical backfill ingests: 12 countries, top two tiers, 2000/01 onward.
HISTORICAL_DIVISIONS = [
    ("E0", "Premier League"),
    ("E1", "Championship"),
    ("E2", "League One"),
    ("E3", "League Two"),
    ("SC0", "Scottish Premiership"),
    ("SP1", "La Liga"),
    ("SP2", "Segunda Division"),
    ("I1", "Serie A"),
    ("I2", "Serie B"),
    ("D1", "Bundesliga"),
    ("D2", "2. Bundesliga"),
    ("F1", "Ligue 1"),
    ("F2", "Ligue 2"),
    ("N1", "Eredivisie"),
    ("B1", "Belgian Pro League"),
    ("P1", "Primeira Liga"),
    ("T1", "Super Lig"),
    ("G1", "Super League Greece"),
]


def history_season_codes(first_year: int = 2000, last_year: int = 2026) -> List[str]:
    """Season codes like ``0001`` (2000/01) through ``2627`` (2026/27)."""
    if last_year < first_year:
        raise ValueError("last_year must not precede first_year")
    return [
        f"{year % 100:02d}{(year + 1) % 100:02d}"
        for year in range(first_year, last_year + 1)
    ]


def fetch_all_major_leagues(db: Session, season: str = "2324") -> Dict[str, Any]:
    """Automatically ingest complete season results & odds for all 5 major European leagues and calibrate team ratings."""
    total_inserted = 0
    total_processed = 0
    total_duplicates = 0
    league_reports = []

    for div, name in MAJOR_DIVISIONS:
        try:
            res = fetch_historical_league_season(db, division_code=div, season=season)
            ins = res.get("inserted", 0)
            dup = res.get("duplicates", 0)
            tot = res.get("total_processed", 0)
            total_inserted += ins
            total_duplicates += dup
            total_processed += tot
            league_reports.append({
                "league": name,
                "division": div,
                "matches": tot,
                "new": ins,
                "status": "success",
            })
        except Exception as e:
            league_reports.append({
                "league": name,
                "division": div,
                "matches": 0,
                "new": 0,
                "status": f"error: {str(e)}",
            })

    rating_res = calculate_ratings_from_fixtures(db)
    return {
        "status": "success",
        "season": season,
        "total_processed": total_processed,
        "total_inserted": total_inserted,
        "total_duplicates": total_duplicates,
        "teams_rated": rating_res.get("updated_teams", 0),
        "leagues": league_reports,
        "message": f"Successfully ingested {total_processed} matches across all 5 major leagues. Calibrated {rating_res.get('updated_teams', 0)} team ratings.",
    }
