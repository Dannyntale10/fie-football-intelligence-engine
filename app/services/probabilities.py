from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
try:
    from scipy.stats import poisson
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

def _poisson_pmf(k: int, mu: float) -> float:
    """Calculate Poisson probability mass function P(X = k) for mean mu."""
    if mu < 0:
        raise ValueError("Expected goals (lambda) must be non-negative")
    if mu == 0:
        return 1.0 if k == 0 else 0.0
    if HAS_SCIPY:
        return float(poisson.pmf(k, mu))
    # Pure Python implementation
    return (math.exp(-mu) * (mu ** k)) / math.factorial(k)

def _dixon_coles_tau(h: int, a: int, home_lambda: float, away_lambda: float, rho: float) -> float:
    """Dixon & Coles (1997) bivariate low-score adjustment factor for scores (0-0, 1-0, 0-1, 1-1)."""
    if rho == 0.0:
        return 1.0
    if h == 0 and a == 0:
        return max(0.0, 1.0 - (home_lambda * away_lambda * rho))
    elif h == 0 and a == 1:
        return max(0.0, 1.0 + (home_lambda * rho))
    elif h == 1 and a == 0:
        return max(0.0, 1.0 + (away_lambda * rho))
    elif h == 1 and a == 1:
        return max(0.0, 1.0 - rho)
    return 1.0

def poisson_market_probabilities(
    home_lambda: float,
    away_lambda: float,
    max_goals: int = 10,
    qualification_threshold: float = 0.85,
    rho: float = -0.10
) -> Dict[str, Any]:
    """Illustrative Dixon-Coles / independent-Poisson market probabilities.

    Applies the Dixon-Coles (1997) parameter rho (default -0.10) to adjust
    for real-world draw correlation on low scorelines (0-0, 1-1, 1-0, 0-1).
    Identifies which markets meet or exceed the qualification threshold (default 85%).
    """
    if home_lambda < 0 or away_lambda < 0:
        raise ValueError("Expected goals must be non-negative")

    scores = [
        (
            h,
            a,
            _dixon_coles_tau(h, a, home_lambda, away_lambda, rho)
            * _poisson_pmf(h, home_lambda)
            * _poisson_pmf(a, away_lambda)
        )
        for h in range(max_goals + 1)
        for a in range(max_goals + 1)
    ]

    # Normalize truncated grid to avoid omitted tail mass
    total = sum(p for _, _, p in scores)
    if total <= 0:
        total = 1.0
    scores = [(h, a, p / total) for h, a, p in scores]

    def prob(test) -> float:
        return round(sum(p for h, a, p in scores if test(h, a)), 4)

    markets = {
        # Match Outcome 1X2
        "home_win": prob(lambda h, a: h > a),
        "draw": prob(lambda h, a: h == a),
        "away_win": prob(lambda h, a: h < a),

        # Double Chance
        "double_chance_1x": prob(lambda h, a: h >= a),
        "double_chance_x2": prob(lambda h, a: a >= h),
        "double_chance_12": prob(lambda h, a: h != a),

        # Over / Under
        "over_0_5": prob(lambda h, a: h + a >= 1),
        "under_0_5": prob(lambda h, a: h + a < 1),
        "over_1_5": prob(lambda h, a: h + a >= 2),
        "under_1_5": prob(lambda h, a: h + a < 2),
        "over_2_5": prob(lambda h, a: h + a >= 3),
        "under_2_5": prob(lambda h, a: h + a < 3),
        "over_3_5": prob(lambda h, a: h + a >= 4),
        "under_3_5": prob(lambda h, a: h + a < 4),

        # Both Teams To Score
        "btts_yes": prob(lambda h, a: h >= 1 and a >= 1),
        "btts_no": prob(lambda h, a: h == 0 or a == 0),

        # Team Totals
        "home_over_0_5": prob(lambda h, a: h >= 1),
        "home_over_1_5": prob(lambda h, a: h >= 2),
        "away_over_0_5": prob(lambda h, a: a >= 1),
        "away_over_1_5": prob(lambda h, a: a >= 2),
        "either_team_over_1_5": prob(lambda h, a: h >= 2 or a >= 2),

        # Asian Handicap lines
        "ah_home_minus_0_5": prob(lambda h, a: h > a),
        "ah_home_minus_1_5": prob(lambda h, a: h >= a + 2),
        "ah_home_minus_2_5": prob(lambda h, a: h >= a + 3),
        "ah_away_plus_0_5": prob(lambda h, a: a >= h),
        "ah_away_plus_1_5": prob(lambda h, a: a + 1 >= h),
        "ah_away_plus_2_5": prob(lambda h, a: a + 2 >= h),
    }

    # Top 10 most likely exact scorelines
    sorted_scores = sorted(scores, key=lambda x: x[2], reverse=True)[:10]
    correct_score_top5 = [
        {
            "score": f"{h}-{a}",
            "probability": round(p, 4),
            "odds_equiv": round(1.0 / max(0.0001, p), 1)
        }
        for h, a, p in sorted_scores
    ]

    # Filter markets that qualify at >= qualification_threshold
    qualifying: List[Dict[str, Any]] = [
        {"market": m, "probability": p}
        for m, p in markets.items()
        if p >= qualification_threshold
    ]
    qualifying.sort(key=lambda x: x["probability"], reverse=True)

    return {
        "inputs": {"home_lambda": home_lambda, "away_lambda": away_lambda},
        "qualification_threshold": qualification_threshold,
        "qualifying_count": len(qualifying),
        "qualifying_markets": qualifying,
        "correct_score_top5": correct_score_top5,
        "markets": markets,
        # Preserve backwards compatibility keys
        "btts_yes": markets["btts_yes"],
        "over_1_5": markets["over_1_5"],
        "over_2_5": markets["over_2_5"],
        "over_3_5": markets["over_3_5"],
        "home_over_1_5": markets["home_over_1_5"],
        "away_over_1_5": markets["away_over_1_5"],
        "either_team_over_1_5": markets["either_team_over_1_5"],
        "double_chance_1x": markets["double_chance_1x"],
        "double_chance_x2": markets["double_chance_x2"],
        "double_chance_12": markets["double_chance_12"],
        "note": "Research/demo baseline only; not calibrated and not eligible for real selections without historical backtesting.",
    }

def calculate_bookmaker_margin(
    home_odds: float,
    draw_odds: float,
    away_odds: float
) -> Dict[str, Any]:
    """Calculate bookmaker overround/margin and fair (vig-free) implied probabilities for 1X2."""
    if home_odds <= 1.0 or draw_odds <= 1.0 or away_odds <= 1.0:
        raise ValueError("Decimal odds must be greater than 1.0")

    raw_home = 1.0 / home_odds
    raw_draw = 1.0 / draw_odds
    raw_away = 1.0 / away_odds
    total_raw = raw_home + raw_draw + raw_away
    margin = total_raw - 1.0

    fair_home = raw_home / total_raw
    fair_draw = raw_draw / total_raw
    fair_away = raw_away / total_raw

    return {
        "overround": round(total_raw, 4),
        "margin_percent": round(margin * 100, 2),
        "raw_implied": {
            "home": round(raw_home, 4),
            "draw": round(raw_draw, 4),
            "away": round(raw_away, 4),
        },
        "fair_implied": {
            "home": round(fair_home, 4),
            "draw": round(fair_draw, 4),
            "away": round(fair_away, 4),
        },
        "fair_odds": {
            "home": round(1.0 / fair_home, 2),
            "draw": round(1.0 / fair_draw, 2),
            "away": round(1.0 / fair_away, 2),
        },
    }

def calculate_kelly_stake(
    model_probability: float,
    bookmaker_odds: float,
    bankroll: float = 1000.0,
    fraction: float = 0.25,
    max_stake_percent: float = 5.0
) -> Dict[str, Any]:
    """Calculate Fractional Kelly Criterion stake sizing.

    f* = (b * p - (1 - p)) / b
    where b = decimal_odds - 1.0, p = model_probability.
    """
    if bookmaker_odds <= 1.0:
        return {"full_kelly": 0.0, "kelly_fraction": fraction, "stake_percent": 0.0, "stake_amount": 0.0}

    b = bookmaker_odds - 1.0
    p = model_probability
    q = 1.0 - p

    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return {
            "full_kelly": 0.0,
            "kelly_fraction": fraction,
            "stake_percent": 0.0,
            "stake_amount": 0.0,
        }

    applied_kelly = full_kelly * fraction
    capped_percent = min(applied_kelly * 100.0, max_stake_percent)
    stake_amount = round(bankroll * (capped_percent / 100.0), 2)

    return {
        "full_kelly": round(full_kelly, 4),
        "kelly_fraction": fraction,
        "stake_percent": round(capped_percent, 2),
        "stake_amount": stake_amount,
    }

def calculate_expected_value(
    model_probability: float,
    bookmaker_odds: float,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_stake_percent: float = 5.0
) -> Dict[str, Any]:
    """Calculate Expected Value (EV), mathematical edge, and Kelly staking."""
    if bookmaker_odds <= 1.0:
        raise ValueError("Decimal odds must be greater than 1.0")
    if not (0.0 <= model_probability <= 1.0):
        raise ValueError("Model probability must be between 0.0 and 1.0")

    implied = 1.0 / bookmaker_odds
    ev = (model_probability * bookmaker_odds) - 1.0
    edge = model_probability - implied
    kelly = calculate_kelly_stake(
        model_probability=model_probability,
        bookmaker_odds=bookmaker_odds,
        bankroll=bankroll,
        fraction=kelly_fraction,
        max_stake_percent=max_stake_percent
    )

    return {
        "model_probability": round(model_probability, 4),
        "bookmaker_odds": round(bookmaker_odds, 2),
        "implied_probability": round(implied, 4),
        "edge": round(edge, 4),
        "expected_value_percent": round(ev * 100, 2),
        "is_value": ev > 0.0,
        "kelly": kelly,
    }

def estimate_lambdas_from_odds(
    home_odds: Optional[float],
    draw_odds: Optional[float],
    away_odds: Optional[float],
    default_total_goals: float = 2.6
) -> tuple[float, float]:
    """Derive estimated expected goals (lambdas) from bookmaker 1X2 odds or sensible defaults."""
    if not home_odds or not away_odds or home_odds <= 1.0 or away_odds <= 1.0:
        return 1.45, 1.15

    raw_h = 1.0 / home_odds
    raw_a = 1.0 / away_odds
    denom = raw_h + raw_a
    if denom <= 0:
        return 1.45, 1.15

    home_ratio = raw_h / denom
    home_lambda = max(0.2, min(5.0, round(default_total_goals * home_ratio, 2)))
    away_lambda = max(0.2, min(5.0, round(default_total_goals * (1.0 - home_ratio), 2)))
    return home_lambda, away_lambda

def analyze_fixture(
    fixture: Any,
    home_lambda: Optional[float] = None,
    away_lambda: Optional[float] = None,
    qualification_threshold: float = 0.85,
    rho: float = -0.10,
    db: Optional[Any] = None,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25
) -> Dict[str, Any]:
    """Perform comprehensive market probability, bookmaker margin, and +EV value analysis for a fixture."""
    ratings_found = False
    lambda_source = "manual" if (home_lambda is not None and away_lambda is not None) else None

    # Determine lambdas: prefer team ratings if available and lambdas not manually passed
    if home_lambda is None or away_lambda is None:
        if db is not None:
            try:
                from app.services.ratings import estimate_lambdas_from_ratings
                r_h, r_a, found = estimate_lambdas_from_ratings(
                    db=db,
                    home_team=getattr(fixture, "home_team", ""),
                    away_team=getattr(fixture, "away_team", ""),
                    competition=getattr(fixture, "competition", "")
                )
                if found and r_h is not None and r_a is not None:
                    ratings_found = True
                    lambda_source = "team_ratings"
                    final_h = home_lambda if home_lambda is not None else r_h
                    final_a = away_lambda if away_lambda is not None else r_a
            except Exception:
                pass

        if not ratings_found:
            est_h, est_a = estimate_lambdas_from_odds(
                getattr(fixture, "home_odds", None),
                getattr(fixture, "draw_odds", None),
                getattr(fixture, "away_odds", None)
            )
            final_h = home_lambda if home_lambda is not None else est_h
            final_a = away_lambda if away_lambda is not None else est_a
            lambda_source = "odds_implied" if (home_lambda is None and away_lambda is None) else "mixed"
    else:
        final_h, final_a = home_lambda, away_lambda

    # Model probabilities with Dixon-Coles adjustment
    poisson_res = poisson_market_probabilities(
        home_lambda=final_h,
        away_lambda=final_a,
        qualification_threshold=qualification_threshold,
        rho=rho
    )
    markets = poisson_res["markets"]

    # Bookmaker margin & Fair odds
    margin_info = None
    value_bets: List[Dict[str, Any]] = []

    h_odds = getattr(fixture, "home_odds", None)
    d_odds = getattr(fixture, "draw_odds", None)
    a_odds = getattr(fixture, "away_odds", None)

    if h_odds and d_odds and a_odds and h_odds > 1.0 and d_odds > 1.0 and a_odds > 1.0:
        margin_info = calculate_bookmaker_margin(h_odds, d_odds, a_odds)

        # 1X2 EV Analysis
        market_mapping = [
            ("Home Win (1)", "home_win", h_odds, margin_info["fair_odds"]["home"]),
            ("Draw (X)", "draw", d_odds, margin_info["fair_odds"]["draw"]),
            ("Away Win (2)", "away_win", a_odds, margin_info["fair_odds"]["away"]),
        ]

        for label, m_key, odds_val, fair_odds_val in market_mapping:
            prob_val = markets.get(m_key, 0.0)
            ev_data = calculate_expected_value(
                prob_val,
                odds_val,
                bankroll=bankroll,
                kelly_fraction=kelly_fraction
            )
            ev_data["market"] = label
            ev_data["market_key"] = m_key
            ev_data["fair_odds"] = fair_odds_val
            value_bets.append(ev_data)

    # Sort value bets by EV descending
    value_bets.sort(key=lambda x: x["expected_value_percent"], reverse=True)

    # Also compute fixture data quality, enriched if team ratings exist
    from app.services.quality import evaluate_fixture_signals, grade_data_quality
    signals = evaluate_fixture_signals(fixture, has_ratings=ratings_found)
    quality = grade_data_quality(signals)

    return {
        "fixture_id": getattr(fixture, "id", None),
        "match": f"{getattr(fixture, 'home_team', '')} vs {getattr(fixture, 'away_team', '')}",
        "competition": getattr(fixture, "competition", ""),
        "country": getattr(fixture, "country", "Unknown"),
        "kickoff_utc": getattr(fixture, "kickoff_utc", None),
        "source": getattr(fixture, "source", ""),
        "estimated_lambdas": {"home_lambda": final_h, "away_lambda": final_a},
        "lambda_source": lambda_source,
        "dixon_coles_rho": rho,
        "qualification_threshold": qualification_threshold,
        "bookmaker_margin": margin_info,
        "value_bets": value_bets,
        "qualifying_markets": poisson_res["qualifying_markets"],
        "correct_score_top5": poisson_res.get("correct_score_top5", []),
        "all_markets": markets,
        "data_quality": quality,
    }
