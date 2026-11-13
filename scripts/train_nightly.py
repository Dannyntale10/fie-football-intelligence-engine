#!/usr/bin/env python3
"""Nightly champion/challenger run over a rolling multi-season window.

Every configuration in the grid is replayed on the same leak-free windows.
The winning configuration is promoted only when it clears the gate; the
artifacts are written to ``--out-dir`` so tests run before anything is
committed by the workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.analytics.artifacts import write_ratings
from app.analytics.blending import fit_report
from app.analytics.blending import save_report as save_blend_report
from app.analytics.learning import build_report as build_learning_report
from app.analytics.learning import save_report as save_learning_report
from app.analytics.model_gate import (
    HALF_LIFE_GRID,
    evaluate_config,
    gate_decision,
    load_champion,
    save_champion,
)
from app.analytics.ratings import calculate_ratings_from_fixtures
from app.data_sources.fixtures import clean_competition_name
from app.data_sources.seeder import history_season_codes
from app.database import SessionLocal
from app.live.fit import fetch_export_rows, fit_intensity
from app.models.fixture import Fixture


def _league_averages(db) -> dict:
    averages = defaultdict(lambda: [0, 0.0, 0.0])
    rows = db.query(
        Fixture.competition, Fixture.home_score, Fixture.away_score
    ).filter(
        Fixture.home_score.isnot(None), Fixture.away_score.isnot(None)
    ).all()
    for competition, home_score, away_score in rows:
        bucket = averages[clean_competition_name(competition)]
        bucket[0] += 1
        bucket[1] += home_score
        bucket[2] += away_score
    return {
        competition: (values[1] / values[0], values[2] / values[0])
        for competition, values in averages.items()
        if values[0]
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _ingest(seasons: list, refresh: int) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "ingest_history.py"),
            "--seasons",
            ",".join(seasons),
            "--refresh-seasons",
            str(refresh),
            "--no-ratings",
            "--quiet",
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )


def _export_unavailable(exc: Exception) -> dict:
    """A bad export must be recorded, never fatal, and never echo its payload."""
    return {
        "status": "export_unavailable",
        "k": 1.0,
        "sample": 0,
        "validation": None,
        "generated_at": None,
        "error": type(exc).__name__,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=str(ROOT / "data"))
    parser.add_argument("--window-days", type=int, default=730)
    parser.add_argument("--refit-days", type=int, default=14)
    parser.add_argument("--seasons", type=int, default=10)
    parser.add_argument("--refresh-seasons", type=int, default=2)
    parser.add_argument("--skip-ingest", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    started_at = datetime.now(timezone.utc)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seasons = history_season_codes(
        started_at.year - args.seasons + 1, started_at.year
    )
    if not args.skip_ingest:
        _ingest(seasons, args.refresh_seasons)

    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        results = [
            evaluate_config(
                db,
                half_life_days=half_life,
                window_days=args.window_days,
                refit_days=args.refit_days,
                now=now,
            )
            for half_life in HALF_LIFE_GRID
        ]
        champion = load_champion()
        decision = gate_decision(results, (champion or {}).get("config"))
        report = {
            "generated_at": now.isoformat(),
            "training_seasons": len(seasons),
            "window_days": args.window_days,
            "dry_run": args.dry_run,
            "results": results,
            "decision": {
                "promote": decision["promote"],
                "reason": decision["reason"],
            },
        }

        if decision["promote"]:
            winning = decision["challenger"]
            half_life = float(winning["half_life_days"])
            if args.dry_run:
                report["promoted"] = {"half_life_days": half_life}
            else:
                calculate_ratings_from_fixtures(db, replace=True, half_life_days=half_life)

                rating_meta = write_ratings(
                    out_dir / "ratings.json",
                    db,
                    half_life_days=half_life,
                    trained_through=now.isoformat(),
                    league_averages=_league_averages(db),
                )

                blend = fit_report(db, half_life_days=half_life)
                save_blend_report(blend, out_dir / "blending.json")

                learning = build_learning_report(db)
                if learning.get("status") == "fitted":
                    save_learning_report(learning, out_dir / "learning.json")

                if not args.dry_run:
                    save_champion(
                        {
                            "artifact_version": 1,
                            "config": {"half_life_days": half_life},
                            "promoted_at": now.isoformat(),
                            "trained_through": now.isoformat(),
                            "training_seasons": len(seasons),
                            "gate": {
                                "champion_brier": decision["champion"]["brier"],
                                "challenger_brier": winning["brier"],
                                "holdout_matches": winning["n_matches"],
                                "window": f"{args.window_days}d",
                                "per_competition": winning["per_competition"],
                                "reason": decision["reason"],
                            },
                            "artifacts": {
                                "ratings.json": rating_meta["content_hash"],
                            },
                            "git_sha": _git_sha(),
                        },
                        out_dir / "model_champion.json",
                    )

                report["promoted"] = {
                    "half_life_days": half_life,
                    "ratings": rating_meta,
                    "blend_verdict": blend.get("verdict"),
                }

        engine_rows = []
        url = os.getenv("FIE_ENGINE_URL", "").strip()
        if url:
            try:
                engine_rows = fetch_export_rows(url, os.getenv("FIE_API_KEY", ""), days=90)
            except (
                requests.RequestException,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                report["live_intensity"] = _export_unavailable(exc)
        if "live_intensity" not in report:
            try:
                live_fit = fit_intensity(engine_rows)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                live_fit = _export_unavailable(exc)
            report["live_intensity"] = live_fit
            if live_fit["status"] == "fitted" and not args.dry_run:
                (out_dir / "live_calibration.json").write_text(
                    json.dumps(live_fit, indent=2, sort_keys=True)
                )

        (out_dir / "nightly_report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["decision"]))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
