from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.fixture import Fixture
from app.analytics.probabilities import analyze_fixture


def run_walkforward_backtest(
    db: Session,
    min_edge_percent: float = 3.0,
    min_leg_prob: float = 0.50,
    staking: str = "flat",
    flat_stake_percent: float = 2.0,
    initial_bankroll: float = 1000.0,
    refit_days: int = 14,
    competition: Optional[str] = None,
) -> Dict[str, Any]:
    """Backtest the EV strategy with ratings that never see the future.

    Unlike :func:`run_backtest`, every prediction here comes from a model
    refitted only on matches played before the prediction window, so the ROI
    is not inflated by lookahead bias. Only 1X2 is evaluated because that is
    the only market with historical prices in the database.
    """
    from app.analytics.calibration import brier_score, log_loss, iter_walkforward_predictions

    predictions = list(iter_walkforward_predictions(
        db, competition=competition, refit_days=refit_days
    ))
    if not predictions:
        return {
            "strategy": "ev_walkforward",
            "total_bets": 0,
            "message": "Not enough rated history to run a walk-forward backtest.",
        }

    bankroll = float(initial_bankroll)
    peak = bankroll
    max_drawdown = 0.0
    total_staked = 0.0
    wins = 0
    losses = 0
    edges: List[float] = []
    history: List[Dict[str, Any]] = []
    curve: List[Dict[str, Any]] = []

    for prediction in predictions:
        odds = prediction["odds"]
        probs = prediction["model"]
        fair = prediction["market"]

        best = None
        for key in ("home_win", "draw", "away_win"):
            price = odds[key]
            prob = probs[key]
            edge = (prob - fair[key]) * 100.0
            ev = (prob * price - 1.0) * 100.0
            if edge < min_edge_percent or ev <= 0 or prob < min_leg_prob:
                continue
            if best is None or edge > best["edge_percent"]:
                best = {
                    "market_key": key,
                    "price": price,
                    "probability": prob,
                    "edge_percent": round(edge, 2),
                }
        if best is None:
            continue

        if staking == "flat" or staking == "quarter_kelly":
            stake = round(bankroll * (flat_stake_percent / 100.0), 2)
            if staking == "quarter_kelly":
                b = best["price"] - 1.0
                full_kelly = (b * best["probability"] - (1.0 - best["probability"])) / b if b > 0 else 0.0
                stake = round(bankroll * min(5.0, max(0.0, full_kelly * 0.25 * 100.0)) / 100.0, 2)
        else:
            stake = round(bankroll * (flat_stake_percent / 100.0), 2)

        stake = max(1.0, min(stake, bankroll))
        if bankroll <= 0:
            break

        won = prediction["label"] == best["market_key"]
        profit = round(stake * (best["price"] - 1.0), 2) if won else round(-stake, 2)
        if won:
            wins += 1
        else:
            losses += 1
        total_staked += stake
        bankroll = round(bankroll + profit, 2)
        edges.append(best["edge_percent"])

        peak = max(peak, bankroll)
        drawdown = ((peak - bankroll) / peak) * 100.0 if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

        fixture = prediction["fixture"]
        history.append({
            "date": fixture.kickoff_utc.strftime("%Y-%m-%d") if fixture.kickoff_utc else "",
            "match": f"{fixture.home_team} vs {fixture.away_team}",
            "market": best["market_key"],
            "odds": round(best["price"], 2),
            "edge_percent": best["edge_percent"],
            "stake": stake,
            "outcome": "WON" if won else "LOST",
            "profit": profit,
            "bankroll": bankroll,
        })
        if len(history) % 25 == 0:
            curve.append({"bets": len(history), "bankroll": bankroll})

    total_bets = wins + losses
    model_probs = [p["model"] for p in predictions]
    market_probs = [p["market"] for p in predictions]
    labels = [p["label"] for p in predictions]

    return {
        "strategy": "ev_walkforward",
        "staking": staking,
        "total_predictions": len(predictions),
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": round(wins / total_bets * 100.0, 2) if total_bets else 0.0,
        "avg_edge_percent": round(sum(edges) / len(edges), 2) if edges else 0.0,
        "initial_bankroll": initial_bankroll,
        "final_bankroll": bankroll,
        "net_profit": round(bankroll - initial_bankroll, 2),
        "roi_percent": round((bankroll - initial_bankroll) / total_staked * 100.0, 2) if total_staked else 0.0,
        "max_drawdown_percent": round(max_drawdown, 2),
        "validation": {
            "model_brier": brier_score(model_probs, labels),
            "market_brier": brier_score(market_probs, labels),
            "model_log_loss": log_loss(model_probs, labels),
            "market_log_loss": log_loss(market_probs, labels),
        },
        "bankroll_curve": curve,
        "history": history,
    }

def run_backtest(
    db: Session,
    strategy: str = "value_bet",
    min_ev_percent: float = 3.0,
    min_quality_score: int = 50,
    staking: str = "quarter_kelly",
    initial_bankroll: float = 1000.0,
    flat_stake_percent: float = 2.0,
    competition: Optional[str] = None
) -> Dict[str, Any]:
    """Execute historical backtesting simulation on completed matches.

    Warning: this legacy path scores each match with ratings fitted on the full
    history, including matches that had not been played yet. Its ROI is
    optimistic. Use :func:`run_walkforward_backtest` for an honest estimate.
    """
    query = select(Fixture).where(
        Fixture.home_score.isnot(None),
        Fixture.away_score.isnot(None),
        Fixture.home_odds.isnot(None),
        Fixture.away_odds.isnot(None)
    )
    if competition:
        query = query.where(Fixture.competition.ilike(f"%{competition}%"))

    matches = db.scalars(query.order_by(Fixture.kickoff_utc)).all()

    current_bankroll = float(initial_bankroll)
    peak_bankroll = float(initial_bankroll)
    max_drawdown = 0.0
    total_staked = 0.0
    wins = 0
    losses = 0
    history: List[Dict[str, Any]] = []

    for m in matches:
        analysis = analyze_fixture(m, db=db, bankroll=current_bankroll)

        # Quality filter
        if analysis["data_quality"]["score"] < min_quality_score:
            continue

        selected_bet = None

        if strategy == "value_bet":
            candidates = [
                b for b in analysis["value_bets"]
                if b["is_value"] and b["expected_value_percent"] >= min_ev_percent
            ]
            if candidates:
                # Pick the highest expected value bet
                selected_bet = candidates[0]

        elif strategy == "high_confidence_85":
            # Target double chance or low over/under markets from qualified list
            qual = analysis["qualifying_markets"]
            for q in qual:
                m_name = q["market"]
                if m_name in ("double_chance_1x", "double_chance_x2", "over_0_5", "over_1_5", "under_3_5"):
                    # Estimate approximate bookmaker odds for double chance / totals
                    est_odds = round(1.0 / max(0.05, (q["probability"] * 0.94)), 2)
                    selected_bet = {
                        "market": m_name.replace("_", " ").title(),
                        "market_key": m_name,
                        "bookmaker_odds": est_odds,
                        "model_probability": q["probability"],
                        "expected_value_percent": 0.0,
                        "kelly": {"stake_amount": round(current_bankroll * 0.02, 2)}
                    }
                    break

        if not selected_bet:
            continue

        # Staking calculation
        if staking == "flat":
            stake = round(current_bankroll * (flat_stake_percent / 100.0), 2)
        elif staking == "half_kelly":
            fk = selected_bet.get("kelly", {}).get("full_kelly", 0.0)
            stake_pct = min(fk * 0.5 * 100.0, 5.0) if fk > 0 else 0.0
            stake = round(current_bankroll * (stake_pct / 100.0), 2)
        else:  # quarter_kelly default
            stake = selected_bet.get("kelly", {}).get("stake_amount", 0.0)

        # Ensure stake is within bounds
        stake = max(1.0, min(stake, current_bankroll * 0.10, current_bankroll))
        if current_bankroll <= 0:
            break

        # Check outcome
        h_score = m.home_score
        a_score = m.away_score
        m_key = selected_bet["market_key"]
        won = False

        if m_key == "home_win":
            won = (h_score > a_score)
        elif m_key == "draw":
            won = (h_score == a_score)
        elif m_key == "away_win":
            won = (h_score < a_score)
        elif m_key == "double_chance_1x":
            won = (h_score >= a_score)
        elif m_key == "double_chance_x2":
            won = (a_score >= h_score)
        elif m_key == "over_0_5":
            won = (h_score + a_score >= 1)
        elif m_key == "over_1_5":
            won = (h_score + a_score >= 2)
        elif m_key == "under_3_5":
            won = (h_score + a_score < 4)

        odds = selected_bet["bookmaker_odds"]
        if won:
            profit = round(stake * (odds - 1.0), 2)
            wins += 1
        else:
            profit = round(-stake, 2)
            losses += 1

        total_staked += stake
        current_bankroll = round(current_bankroll + profit, 2)

        # Track Drawdown
        if current_bankroll > peak_bankroll:
            peak_bankroll = current_bankroll
        dd = ((peak_bankroll - current_bankroll) / peak_bankroll) * 100.0 if peak_bankroll > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd

        history.append({
            "date": m.kickoff_utc.strftime("%Y-%m-%d") if m.kickoff_utc else "",
            "match": f"{m.home_team} {h_score}-{a_score} {m.away_team}",
            "market": selected_bet["market"],
            "odds": odds,
            "stake": stake,
            "outcome": "WON" if won else "LOST",
            "profit": profit,
            "bankroll": current_bankroll,
        })

    total_bets = wins + losses
    win_rate = round((wins / total_bets) * 100.0, 2) if total_bets > 0 else 0.0
    net_profit = round(current_bankroll - initial_bankroll, 2)
    roi = round((net_profit / total_staked) * 100.0, 2) if total_staked > 0 else 0.0

    return {
        "strategy": strategy,
        "staking": staking,
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": win_rate,
        "initial_bankroll": initial_bankroll,
        "final_bankroll": current_bankroll,
        "net_profit": net_profit,
        "roi_percent": roi,
        "max_drawdown_percent": round(max_drawdown, 2),
        "history": history,
    }
