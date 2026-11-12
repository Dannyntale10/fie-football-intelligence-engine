#!/usr/bin/env python3
"""Check which free data providers are configured and, optionally, live.

Without --live this only reads environment variables and the provider
catalogue. It never invents a successful status: an absent key is reported as
unconfigured. With --live, one request per named provider is attempted and the
result count/status is printed.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify free data-provider access.")
    parser.add_argument(
        "--live",
        nargs="*",
        metavar="PROVIDER",
        help="Attempt a real request for these providers (or all keyed ones if empty).",
    )
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (UTC).")
    parser.add_argument("--csv", default=None, help="CSV path for the manual_csv provider.")
    args = parser.parse_args()

    from app.data_sources.providers import ProviderError, build_provider, is_configured, provider_catalogue

    catalogue = provider_catalogue()
    print("Provider catalogue")
    print("-" * 78)
    for entry in catalogue:
        configured = is_configured(entry)
        key = entry["key_env"] or "no key required"
        quota = entry["daily_quota"] if entry["daily_quota"] is not None else "see docs"
        print(
            f"{entry['name']:<20} configured={str(configured):<5} key={key:<22} "
            f"daily_quota={quota}"
        )

    if args.live is None:
        print("\nRead-only check. Add --live to make real requests.")
        return 0

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid --date: {args.date}")
        return 2

    requested = args.live or [
        entry["name"]
        for entry in catalogue
        if entry["key_env"] and os.getenv(entry["key_env"])
    ]
    if not requested:
        print("\nNo provider is configured, so no live request was made.")
        return 0

    failures = 0
    for name in requested:
        kwargs = {}
        if name == "manual_csv":
            if not args.csv:
                print(f"{name}: skipped (pass --csv PATH)")
                continue
            kwargs["csv_path"] = Path(args.csv)
        try:
            provider = build_provider(name, **kwargs)
            if name == "odds_api":
                result = provider.fetch_odds()
            else:
                result = provider.fetch_fixtures(target)
        except ProviderError as error:
            print(f"{name}: FAILED — {error}")
            failures += 1
            continue
        print(f"{name}: {result.status} — {len(result.records)} records — {result.message or ''}")
        if result.status not in ("ok", "cached", "empty"):
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
