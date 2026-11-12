#!/usr/bin/env python3
"""Fit the 1X2 model-market blend on leak-free walk-forward history.

The weight is chosen on the earliest 70% of predictions by log loss and then
scored on the latest 30%. A report is only saved when the fit produced a usable
weight; an insufficient-data result leaves the previous report untouched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _print_summary(report: dict) -> None:
    print(json.dumps(report, indent=2))
    if report.get("status") != "fitted":
        print("\nNo blend saved.", file=sys.stderr)
        return
    holdout = report["holdout"]
    print("\n--- 1X2 blend ---")
    print(f"model weight (global): {report['weights']['default']:.3f}")
    print(f"model temperature: {report['weights'].get('temperature', 1.0):.2f}")
    print(f"train matches: {report['train']['n_matches']}  holdout matches: {holdout['n_matches']}")
    print(f"holdout model   Brier {holdout['model']['brier']}  log loss {holdout['model']['log_loss']}")
    print(f"holdout market  Brier {holdout['market']['brier']}  log loss {holdout['market']['log_loss']}")
    print(f"holdout blend   Brier {holdout['blend']['brier']}  log loss {holdout['blend']['log_loss']}")
    print(f"verdict: {report['verdict']}")
    if report.get("weights", {}).get("per_competition"):
        print("\nper-competition weights (shrunk toward the global weight):")
        for name, weight in sorted(report["weights"]["per_competition"].items()):
            row = report["per_competition"].get(name, {})
            print(f"  {name:<32} weight {weight:.3f}  holdout blend Brier {row.get('blend_brier')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit the model-market probability blend.")
    parser.add_argument("--competition", default=None, help="Restrict to one competition name.")
    parser.add_argument("--refit-days", type=int, default=14, help="Walk-forward refit window.")
    parser.add_argument("--train-fraction", type=float, default=0.70, help="Chronological train share.")
    parser.add_argument("--no-save", action="store_true", help="Print the report without saving it.")
    args = parser.parse_args()

    if not 0.5 <= args.train_fraction <= 0.9:
        print("--train-fraction must be between 0.5 and 0.9", file=sys.stderr)
        return 2

    from app.analytics.blending import fit_report, save_report
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        report = fit_report(
            db,
            competition=args.competition,
            refit_days=args.refit_days,
            train_fraction=args.train_fraction,
        )
    finally:
        db.close()

    if report.get("status") == "fitted" and not args.no_save:
        save_report(report)
    _print_summary(report)
    return 0 if report.get("status") == "fitted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
