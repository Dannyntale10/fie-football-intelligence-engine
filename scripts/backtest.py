#!/usr/bin/env python3
"""Run an honest, leak-free backtest over settled fixtures.

The default strategy is the walk-forward model: every pick is scored by a
model refitted only on matches played before that pick. The legacy fixed-fit
path is available for comparison and is clearly labelled as optimistic.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the platform's selection logic.")
    parser.add_argument("--strategy", choices=("walkforward", "legacy"), default="walkforward")
    parser.add_argument("--competition", default=None, help="Restrict to one competition name.")
    parser.add_argument("--refit-days", type=int, default=14, help="Walk-forward refit window.")
    parser.add_argument("--min-edge", type=float, default=3.0, help="Minimum edge percent.")
    parser.add_argument("--min-leg-prob", type=float, default=0.50)
    parser.add_argument("--staking", default=None, help="flat | quarter_kelly | half_kelly")
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--flat-stake-percent", type=float, default=2.0)
    args = parser.parse_args()

    from app.analytics.backtest import run_backtest, run_walkforward_backtest
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        if args.strategy == "legacy":
            result = run_backtest(
                db,
                min_ev_percent=args.min_edge,
                staking=args.staking or "quarter_kelly",
                initial_bankroll=args.bankroll,
                flat_stake_percent=args.flat_stake_percent,
                competition=args.competition,
            )
            result["lookahead_warning"] = (
                "Legacy rating fit includes future matches; treat this ROI as optimistic."
            )
        else:
            result = run_walkforward_backtest(
                db,
                min_edge_percent=args.min_edge,
                min_leg_prob=args.min_leg_prob,
                staking=args.staking or "flat",
                flat_stake_percent=args.flat_stake_percent,
                initial_bankroll=args.bankroll,
                refit_days=args.refit_days,
                competition=args.competition,
            )
    finally:
        db.close()

    print(json.dumps(result, indent=2, default=str))
    if not result.get("total_bets"):
        print("\nNo bets placed. An empty result is reported rather than simulated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
