"""Market price helpers shared by selection paths.

Only prices stored on the fixture are returned. A price computed from a model
probability is an estimate and must be labelled as such by its caller; this
module never invents one.
"""
from __future__ import annotations

import json
from typing import Any, Dict

# Markets whose prices sum to one outcome set and can therefore be de-vigged
# together. Asian handicap pairs are complementary for the listed half lines.
# Double chance is deliberately absent: its three outcomes are not mutually
# exclusive (they sum to roughly two), so it is derived from the 1X2 fair
# probabilities below instead.
DEVIG_GROUPS = (
    ("home_win", "draw", "away_win"),
    ("btts_yes", "btts_no"),
    ("over_0_5", "under_0_5"),
    ("over_1_5", "under_1_5"),
    ("over_2_5", "under_2_5"),
    ("over_3_5", "under_3_5"),
    ("dnb_home", "dnb_away"),
    ("ah_home_minus_0_5", "ah_away_plus_0_5"),
    ("ah_home_minus_1_5", "ah_away_plus_1_5"),
    ("ah_home_minus_2_5", "ah_away_plus_2_5"),
)


def fixture_market_odds(fixture: Any) -> Dict[str, float]:
    """Real bookmaker prices for a fixture, keyed by model market name."""
    odds: Dict[str, float] = {}
    raw = getattr(fixture, "market_odds_json", None)
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            for key, value in parsed.items():
                if isinstance(value, dict):
                    value = value.get("price", value.get("odds"))
                if isinstance(value, (int, float)) and value > 1.0:
                    odds[str(key)] = float(value)

    for key, value in (
        ("home_win", getattr(fixture, "home_odds", None)),
        ("draw", getattr(fixture, "draw_odds", None)),
        ("away_win", getattr(fixture, "away_odds", None)),
    ):
        if key not in odds and value and value > 1.0:
            odds[key] = float(value)

    # Use the best price available across ingested books when one exists.
    for key, best in (
        ("home_win", getattr(fixture, "max_home_odds", None)),
        ("draw", getattr(fixture, "max_draw_odds", None)),
        ("away_win", getattr(fixture, "max_away_odds", None)),
    ):
        if best and best > odds.get(key, 0.0):
            odds[key] = float(best)
    return odds


def devigged_probabilities(
    market_odds: Dict[str, float],
) -> tuple[Dict[str, float], Dict[str, float]]:
    """Return (raw implied, vig-free implied) probabilities from decimal prices."""
    implied = {key: 1.0 / price for key, price in market_odds.items()}
    fair = dict(implied)
    for group in DEVIG_GROUPS:
        if not all(key in implied for key in group):
            continue
        overround = sum(implied[key] for key in group)
        if overround <= 1.0:  # arbitrage or bad data; leave raw prices untouched
            continue
        for key in group:
            fair[key] = implied[key] / overround

    # P(1X) = P(H) + P(D) exactly, so the fair double-chance probabilities come
    # from the de-vigged 1X2 group. Normalising the DC trio itself would be
    # wrong: those outcomes overlap and their implied prices sum to about two.
    if "home_win" in fair and "draw" in fair and "away_win" in fair:
        home, draw, away = fair["home_win"], fair["draw"], fair["away_win"]
        derived = {
            "double_chance_1x": home + draw,
            "double_chance_x2": draw + away,
            "double_chance_12": home + away,
        }
        for key, probability in derived.items():
            if key in implied:
                fair[key] = probability
    return implied, fair
