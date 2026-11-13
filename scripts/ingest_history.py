#!/usr/bin/env python3
"""Backfill football-data.co.uk history into the local database.

Every division that source publishes is available from 2000/01 onward, which
is the largest keyless history of results plus closing 1X2 prices. CSVs are
cached on disk so a failed run resumes without re-downloading, imports are
committed per file, and ratings are rebuilt once at the end instead of after
every file.

Coverage is 18 European divisions in 12 countries, top two tiers. It is not
worldwide football, and matches before 2002/03 only carry the bookmakers that
existed then (William Hill, Ladbrokes, Interwetten, Gamebookers).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BASE_URL = "https://football-data.co.uk/mmz4281/{season}/{division}.csv"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _download(url: str, destination: Path, force: bool) -> bool:
    """Fetch ``url`` to ``destination``; returns True when a request was made."""
    if destination.exists() and destination.stat().st_size > 0 and not force:
        return False
    import requests

    destination.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    response.raise_for_status()
    destination.write_bytes(response.content)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill football-data.co.uk history.")
    parser.add_argument("--first-year", type=int, default=2000, help="Season start year (2000 = 2000/01).")
    parser.add_argument("--last-year", type=int, default=2026, help="Season start year (2026 = 2026/27).")
    parser.add_argument(
        "--divisions",
        default="all",
        help="Comma-separated division codes (E0,SP1,...) or 'all'.",
    )
    parser.add_argument("--cache-dir", default="data/historical_cache", help="CSV cache directory.")
    parser.add_argument(
        "--seasons",
        default=None,
        help="Comma-separated season codes (0001,2324,...) to retry instead of a year range.",
    )
    parser.add_argument("--delay", type=float, default=0.15, help="Seconds between downloads.")
    parser.add_argument(
        "--refresh-seasons",
        type=int,
        default=0,
        help="Force re-download the newest N seasons (current plus N-1).",
    )
    parser.add_argument("--force", action="store_true", help="Re-download cached files.")
    parser.add_argument("--no-ratings", action="store_true", help="Skip the final ratings rebuild.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary.")
    args = parser.parse_args()

    from app.analytics.ratings import calculate_ratings_from_fixtures
    from app.data_sources.seeder import (
        HISTORICAL_DIVISIONS,
        history_season_codes,
        import_football_data_dataframe,
        read_football_data_csv,
        seasons_to_refresh,
    )
    from app.database import SessionLocal

    if args.divisions == "all":
        divisions = HISTORICAL_DIVISIONS
    else:
        wanted = {code.strip().upper() for code in args.divisions.split(",") if code.strip()}
        divisions = [(code, name) for code, name in HISTORICAL_DIVISIONS if code in wanted]
        unknown = wanted - {code for code, _ in divisions}
        if unknown:
            print(f"Unknown division codes: {sorted(unknown)}", file=sys.stderr)
            return 2

    seasons = (
        [code.strip() for code in args.seasons.split(",") if code.strip()]
        if args.seasons
        else history_season_codes(args.first_year, args.last_year)
    )
    refresh = seasons_to_refresh(seasons, args.refresh_seasons)
    cache_dir = (ROOT / args.cache_dir) if not Path(args.cache_dir).is_absolute() else Path(args.cache_dir)

    db = SessionLocal()
    totals = {
        "files": 0,
        "new_files": 0,
        "processed": 0,
        "inserted": 0,
        "duplicates": 0,
        "priced": 0,
        "malformed": 0,
    }
    errors = []

    try:
        for season in seasons:
            season_rows = season_priced = 0
            for code, name in divisions:
                cached = cache_dir / season / f"{code}.csv"
                try:
                    fetched = _download(
                        BASE_URL.format(season=season, division=code),
                        cached,
                        args.force or season in refresh,
                    )
                    if fetched:
                        totals["new_files"] += 1
                        time.sleep(args.delay)
                    frame, malformed = read_football_data_csv(cached.read_bytes())
                    result = import_football_data_dataframe(
                        db,
                        frame,
                        default_source=f"{name} {season}",
                        recalculate_ratings=False,
                    )
                    db.expunge_all()
                    totals["processed"] += result["total_processed"]
                    totals["inserted"] += result["inserted"]
                    totals["duplicates"] += result["duplicates"]
                    totals["priced"] += result["priced"]
                    totals["malformed"] += malformed
                    season_rows += result["total_processed"]
                    season_priced += result["priced"]
                except Exception as exc:  # keep going: one bad file must not stop the backfill
                    errors.append(f"{season}/{code}: {exc}")
                totals["files"] += 1
            if not args.quiet:
                print(
                    f"{season}: {season_rows} matches ({season_priced} priced)  "
                    f"total inserted {totals['inserted']}",
                    flush=True,
                )

        ratings = {}
        if not args.no_ratings:
            if not args.quiet:
                print("Rebuilding ratings from the full history...", flush=True)
            ratings = calculate_ratings_from_fixtures(db)
    finally:
        db.close()

    summary = {
        "seasons": f"{seasons[0]}..{seasons[-1]}",
        "divisions": len(divisions),
        "files": totals["files"],
        "downloaded": totals["new_files"],
        "processed": totals["processed"],
        "inserted": totals["inserted"],
        "duplicates": totals["duplicates"],
        "priced": totals["priced"],
        "malformed": totals["malformed"],
        "teams_rated": ratings.get("updated_teams", 0),
        "competitions_rated": len(ratings.get("competitions", [])),
        "errors": errors,
    }
    print(json.dumps(summary, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
