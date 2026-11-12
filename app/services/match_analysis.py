from __future__ import annotations
"""match_analysis.py – single-fixture deep dossier.

The accumulator builder answers "which legs fit today's ticket". This module
answers a different question: "what do we actually know about this one match?"

It gathers, for one fixture:

* model probabilities for every priced market, compared with the de-vigged
  bookmaker price (the only comparison that can reveal an edge);
* recent form with home/away splits, goals, clean sheets and both-teams-to-score
  rates from finished matches in the database;
* head-to-head history;
* an *advisory* squad/team-news note (never a hard filter – no verified lineup
  feed is configured);
* an explicit statement that player-level form data is unavailable rather than
  an invented list of in-form players;
* every reason the dossier might be wrong, as a machine-readable risk list.

No external scouting feed is invented here. Anything not grounded in the
database or the ingested prices is labelled advisory.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.services.ai_squad_checker import check_squad_and_injuries
from app.services.combo import (
    _devigged_probabilities,
    _fixture_market_odds,
    _format_eat,
    _selection_label,
    _team_label,
)
from app.services.h2h import get_h2h_stats
from app.services.learning import calibrate_markets, load_report as load_learning_report
from app.services.probabilities import analyze_fixture
from app.services.ratings import STALE_AFTER_DAYS, data_age_days

DOSSIER_VERSION = "dossier-1"
MAX_MARKETS = 24
MAX_BEST_BETS = 5


def _recent_matches(
    db: Session,
    team: str,
    venue: Optional[str] = None,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Finished matches for a team, newest first, from that team's perspective."""
    query = (
        select(Fixture)
        .where(Fixture.status == "finished")
        .where(Fixture.home_score.isnot(None))
        .where(Fixture.away_score.isnot(None))
    )
    if venue == "home":
        query = query.where(Fixture.home_team == team)
    elif venue == "away":
        query = query.where(Fixture.away_team == team)
    else:
        query = query.where(or_(Fixture.home_team == team, Fixture.away_team == team))

    rows = db.scalars(query.order_by(Fixture.kickoff_utc.desc()).limit(limit)).all()

    history: List[Dict[str, Any]] = []
    for match in rows:
        is_home = match.home_team == team
        goals_for = match.home_score if is_home else match.away_score
        goals_against = match.away_score if is_home else match.home_score
        result = "W" if goals_for > goals_against else ("D" if goals_for == goals_against else "L")
        history.append({
            "kickoff_eat": _format_eat(match.kickoff_utc),
            "opponent": match.away_team if is_home else match.home_team,
            "venue": "home" if is_home else "away",
            "competition": match.competition,
            "score": f"{goals_for}-{goals_against}",
            "result": result,
        })
    return history


def _form_block(db: Session, team: str) -> Dict[str, Any]:
    """Aggregate a team's last five plus venue splits and scoring habits."""
    overall = _recent_matches(db, team)
    n = len(overall)
    wins = sum(1 for m in overall if m["result"] == "W")
    draws = sum(1 for m in overall if m["result"] == "D")
    losses = sum(1 for m in overall if m["result"] == "L")
    goals_for = sum(int(m["score"].split("-")[0]) for m in overall)
    goals_against = sum(int(m["score"].split("-")[1]) for m in overall)
    clean_sheets = sum(1 for m in overall if int(m["score"].split("-")[1]) == 0)
    failed_to_score = sum(1 for m in overall if int(m["score"].split("-")[0]) == 0)
    btts = sum(
        1 for m in overall
        if int(m["score"].split("-")[0]) > 0 and int(m["score"].split("-")[1]) > 0
    )
    over_2_5 = sum(1 for m in overall if sum(int(x) for x in m["score"].split("-")) >= 3)

    def _split(venue: str) -> Dict[str, Any]:
        rows = _recent_matches(db, team, venue=venue)
        return {
            "played": len(rows),
            "record": "".join(r["result"] for r in rows),
            "results": rows,
        }

    denominator = max(1, n)
    return {
        "team": team,
        "played": n,
        "record": f"{wins}W-{draws}D-{losses}L",
        "form": "".join(m["result"] for m in overall),
        "points": wins * 3 + draws,
        "points_per_game": round((wins * 3 + draws) / denominator, 2),
        "goals_for_per_match": round(goals_for / denominator, 2),
        "goals_against_per_match": round(goals_against / denominator, 2),
        "clean_sheet_rate": round(clean_sheets / denominator, 2),
        "failed_to_score_rate": round(failed_to_score / denominator, 2),
        "btts_rate": round(btts / denominator, 2),
        "over_2_5_rate": round(over_2_5 / denominator, 2),
        "matches": overall,
        "home": _split("home"),
        "away": _split("away"),
    }


def _market_rows(
    analysis: Dict[str, Any],
    fixture: Fixture,
    learning_report: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Model vs de-vigged price for every market with a real ingested price."""
    prices = _fixture_market_odds(fixture)
    if not prices:
        return []

    _, fair = _devigged_probabilities(prices)
    markets = analysis.get("all_markets", {})
    calibrated = calibrate_markets(
        {key: markets.get(key) for key in prices}, learning_report
    )

    rows: List[Dict[str, Any]] = []
    for key, price in prices.items():
        model_probability = markets.get(key)
        if model_probability is None:
            continue
        calibrated_probability, was_calibrated = calibrated.get(
            key, (model_probability, False)
        )
        fair_probability = fair.get(key, 1.0 / price)
        edge_percent = (calibrated_probability - fair_probability) * 100.0
        expected_value_percent = (calibrated_probability * price - 1.0) * 100.0
        market_type, selection = _selection_label(key, fixture.home_team, fixture.away_team)
        rows.append({
            "market_key": key,
            "market_type": market_type,
            "selection": selection,
            "team": _team_label(key, fixture.home_team, fixture.away_team),
            "price": round(price, 2),
            "model_probability": round(model_probability, 4),
            "calibrated_probability": round(calibrated_probability, 4),
            "calibration_applied": was_calibrated,
            "fair_probability": round(fair_probability, 4),
            "fair_odds": round(1.0 / max(1e-6, fair_probability), 2),
            "edge_percent": round(edge_percent, 2),
            "expected_value_percent": round(expected_value_percent, 2),
            "is_value": edge_percent > 0 and expected_value_percent > 0,
        })

    rows.sort(key=lambda r: (r["edge_percent"], r["expected_value_percent"]), reverse=True)
    return rows[:MAX_MARKETS]


def _risk_list(
    db: Session,
    fixture: Fixture,
    analysis: Dict[str, Any],
    form_home: Dict[str, Any],
    form_away: Dict[str, Any],
    h2h: Dict[str, Any],
    rows: List[Dict[str, Any]],
    calibration: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    risks: List[Dict[str, str]] = []
    rating_meta = analysis.get("rating_meta") or {}
    lambda_source = analysis.get("lambda_source")

    if lambda_source != "team_ratings":
        risks.append({
            "code": "no_team_ratings",
            "severity": "high",
            "detail": (
                "No league ratings cover this fixture, so the goal model was fitted to "
                "the bookmaker's own prices. Edges on this match are circular, not independent."
            ),
        })
    elif rating_meta.get("stale"):
        risks.append({
            "code": "stale_ratings",
            "severity": "high",
            "detail": (
                f"Ratings are {rating_meta.get('data_age_days')} days old "
                f"(stale after {int(STALE_AFTER_DAYS)}). Refresh results before trusting the model."
            ),
        })

    if not rows:
        risks.append({
            "code": "no_market_prices",
            "severity": "high",
            "detail": "No ingested bookmaker prices for this fixture, so no edge can be measured.",
        })

    if max(form_home["played"], form_away["played"]) < 3:
        risks.append({
            "code": "thin_form_sample",
            "severity": "medium",
            "detail": "Fewer than three finished matches in the database for at least one side.",
        })

    if h2h.get("total", 0) < 3:
        risks.append({
            "code": "thin_h2h",
            "severity": "low",
            "detail": "Head-to-head history is too thin to carry weight.",
        })

    quality = analysis.get("data_quality") or {}
    if quality and not quality.get("eligible_for_analysis", True):
        risks.append({
            "code": "low_data_quality",
            "severity": "medium",
            "detail": (
                f"Data quality grade {quality.get('grade')} "
                f"({quality.get('score')}/100) is below the analysis floor."
            ),
        })

    risks.append({
        "code": "advisory_team_news",
        "severity": "medium",
        "detail": (
            "Team news, injuries and in-form players come from an advisory source or are "
            "unavailable. Never treat them as a verified lineup."
        ),
    })

    if not calibration or not (calibration.get("skill") or {}).get("validated"):
        risks.append({
            "code": "unvalidated_model",
            "severity": "high",
            "detail": (
                "Walk-forward evaluation has not shown this model beating the de-vigged "
                "market, so its probabilities are unvalidated."
            ),
        })

    if rows and not any(r["calibration_applied"] for r in rows):
        risks.append({
            "code": "no_learned_calibration",
            "severity": "low",
            "detail": "No validated learned calibration is active for these markets yet.",
        })

    if fixture.odds_updated_at is not None:
        age = data_age_days(fixture.odds_updated_at)
        if age is not None and age > 3:
            risks.append({
                "code": "old_odds",
                "severity": "medium",
                "detail": f"Last price update was {age} days ago.",
            })

    return risks


def build_match_dossier(
    db: Session,
    fixture: Fixture,
    home_lambda: Optional[float] = None,
    away_lambda: Optional[float] = None,
    rho: float = -0.10,
) -> Dict[str, Any]:
    """Everything we can honestly say about one fixture."""
    baseline = analyze_fixture(fixture, rho=rho, db=db)
    baseline_lambdas = baseline.get("estimated_lambdas") or {}

    # The UI seeds its λ inputs with the model's own estimates. Treating those
    # as manual overrides would hide the real λ source (ratings vs market), so
    # only values the caller actually changed are treated as overrides.
    if home_lambda is not None and abs(home_lambda - float(baseline_lambdas.get("home_lambda", home_lambda))) < 0.005:
        home_lambda = None
    if away_lambda is not None and abs(away_lambda - float(baseline_lambdas.get("away_lambda", away_lambda))) < 0.005:
        away_lambda = None

    analysis = (
        baseline if home_lambda is None and away_lambda is None
        else analyze_fixture(
            fixture, home_lambda=home_lambda, away_lambda=away_lambda, rho=rho, db=db
        )
    )

    learning_report = load_learning_report()
    rows = _market_rows(analysis, fixture, learning_report)
    best_bets = [r for r in rows if r["is_value"]][:MAX_BEST_BETS]

    form_home = _form_block(db, fixture.home_team)
    form_away = _form_block(db, fixture.away_team)
    h2h = get_h2h_stats(db, fixture.home_team, fixture.away_team)

    top_selection = (
        best_bets[0]["selection"] if best_bets
        else _selection_label("home_win", fixture.home_team, fixture.away_team)[1]
    )
    squad = check_squad_and_injuries(
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        competition=fixture.competition,
        selection=top_selection,
        db=db,
    )

    calibration = None
    try:
        from app.services.calibration import load_report as load_calibration_report
        calibration = load_calibration_report()
    except Exception:
        calibration = None

    risks = _risk_list(db, fixture, analysis, form_home, form_away, h2h, rows, calibration)

    rating_meta = analysis.get("rating_meta") or {}
    skill = (calibration or {}).get("skill") or {}
    model_validated = bool(skill.get("validated"))
    ratings_fresh = bool(
        analysis.get("lambda_source") == "team_ratings" and not rating_meta.get("stale")
    )

    if best_bets and model_validated and ratings_fresh:
        confidence = "high"
        headline = (
            f"Model value on {best_bets[0]['selection']} at {best_bets[0]['price']:.2f} "
            f"({best_bets[0]['edge_percent']:+.1f}% edge vs the de-vigged price)."
        )
    elif best_bets and ratings_fresh:
        confidence = "medium"
        headline = (
            f"Model value on {best_bets[0]['selection']} at {best_bets[0]['price']:.2f}, "
            "but the model itself is not yet validated against the market."
        )
    elif best_bets:
        confidence = "low"
        headline = (
            "Model spots an edge, but the ratings behind it are missing or stale, "
            "so the edge is not trustworthy."
        )
    else:
        confidence = "low"
        headline = "No edge: the model does not beat the de-vigged market on this fixture."

    return {
        "version": DOSSIER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": {
            "id": fixture.id,
            "match": f"{fixture.home_team} vs {fixture.away_team}",
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "competition": fixture.competition,
            "country": fixture.country,
            "kickoff_eat": _format_eat(fixture.kickoff_utc),
            "kickoff_utc": fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
            "status": fixture.status,
            "source": fixture.source,
        },
        "verdict": {
            "headline": headline,
            "confidence": confidence,
            "model_validated": model_validated,
            "ratings_fresh": ratings_fresh,
            "best_bet": best_bets[0] if best_bets else None,
        },
        "model": {
            "lambdas": analysis.get("estimated_lambdas"),
            "lambda_source": analysis.get("lambda_source"),
            "rating_meta": rating_meta,
            "bookmaker_margin": analysis.get("bookmaker_margin"),
            "data_quality": analysis.get("data_quality"),
            "correct_score_top5": analysis.get("correct_score_top5", []),
        },
        "markets": rows,
        "best_bets": best_bets,
        "form": {"home": form_home, "away": form_away},
        "h2h": h2h,
        "team_news": squad,
        "player_form": {
            "available": False,
            "reason": (
                "No verified player-level feed (lineups, minutes, individual form) is "
                "configured. Team-level scoring and defensive rates above are the closest "
                "available proxy; do not assume an in-form scorer is playing."
            ),
        },
        "risks": risks,
        "learning": {
            "active": bool(learning_report),
            "version": (learning_report or {}).get("version"),
            "applied_markets": (learning_report or {}).get("applied_markets", []),
            "calibrated_markets_here": [r["market_key"] for r in rows if r["calibration_applied"]],
        },
        "sources": {
            "prices": "ingested bookmaker feed",
            "results": "local finished-match database",
            "ratings": analysis.get("lambda_source"),
            "team_news": squad.get("source", "heuristic"),
        },
    }
