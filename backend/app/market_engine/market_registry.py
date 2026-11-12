"""Probability-model registry.

Families are only modelled when a registered analyzer can price them from data
the platform actually holds. Each analyzer receives a context dict (lambdas,
expected corners/cards, player inputs supplied by a player model, ...) and
returns probability keys compatible with the catalogue's model keys.

An analyzer that lacks inputs returns an empty dict; the analysis layer reports
``insufficient_model_data`` for those selections rather than guessing.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.analytics.probabilities import (
    dixon_coles_score_grid,
    poisson_market_probabilities,
)

Analyzer = Callable[[Dict[str, Any]], Dict[str, float]]

_REGISTRY: Dict[str, Analyzer] = {}
_REQUIREMENTS: Dict[str, Tuple[str, ...]] = {}

DEFAULT_CORNER_LINES = (7.5, 8.5, 9.5, 10.5, 11.5, 12.5)
DEFAULT_CARD_LINES = (2.5, 3.5, 4.5, 5.5, 6.5)


def register_model(
    name: str,
    analyzer: Analyzer,
    requires: Iterable[str] = (),
) -> None:
    _REGISTRY[name] = analyzer
    _REQUIREMENTS[name] = tuple(requires)


def registered_models() -> List[str]:
    return sorted(_REGISTRY)


def model_requirements(name: str) -> Tuple[str, ...]:
    return _REQUIREMENTS.get(name, ())


def _suffix(line: float) -> str:
    return f"{line:g}".replace(".", "_")


def poisson_cdf(k: int, mean: float) -> float:
    """P(X <= k) for a Poisson mean; used by count-market analyzers."""
    if mean < 0:
        raise ValueError("Poisson mean must be non-negative")
    if k < 0:
        return 0.0
    term = math.exp(-mean)
    total = term
    for i in range(1, k + 1):
        term *= mean / i
        total += term
    return min(1.0, total)


def _over_under(mean: float, lines: Iterable[float]) -> Dict[str, float]:
    probabilities: Dict[str, float] = {}
    for line in lines:
        under = poisson_cdf(int(math.floor(line)), mean)
        probabilities[f"over_{_suffix(line)}"] = round(1.0 - under, 4)
        probabilities[f"under_{_suffix(line)}"] = round(under, 4)
    return probabilities


def _dixon_coles_analyzer(context: Dict[str, Any]) -> Dict[str, float]:
    home_lambda = context.get("home_lambda")
    away_lambda = context.get("away_lambda")
    if home_lambda is None or away_lambda is None:
        return {}
    rho = context.get("rho", -0.10)
    grid = dixon_coles_score_grid(float(home_lambda), float(away_lambda), rho=rho)

    base = poisson_market_probabilities(
        float(home_lambda),
        float(away_lambda),
        rho=rho,
    )["markets"]
    probabilities = dict(base)

    def total(mask) -> float:
        return round(sum(p for (h, a), p in grid.items() if mask(h, a)), 4)

    probabilities["clean_sheet_home"] = total(lambda h, a: a == 0)
    probabilities["clean_sheet_away"] = total(lambda h, a: h == 0)
    probabilities["win_to_nil_home"] = total(lambda h, a: h > a and a == 0)
    probabilities["win_to_nil_away"] = total(lambda h, a: a > h and h == 0)
    probabilities["odd_even_odd"] = total(lambda h, a: (h + a) % 2 == 1)
    probabilities["odd_even_even"] = total(lambda h, a: (h + a) % 2 == 0)

    ranges = {
        "0_1": lambda h, a: h + a <= 1,
        "0_2": lambda h, a: h + a <= 2,
        "2_3": lambda h, a: 2 <= h + a <= 3,
        "3_plus": lambda h, a: h + a >= 3,
        "4_plus": lambda h, a: h + a >= 4,
    }
    for label, mask in ranges.items():
        probabilities[f"goal_range_{label}"] = total(mask)

    for (h, a), p in grid.items():
        probabilities[f"correct_score_{h}_{a}"] = round(p, 4)

    results = {"1": lambda h, a: h > a, "x": lambda h, a: h == a, "2": lambda h, a: a > h}
    for name, mask in results.items():
        both = total(lambda h, a, mask=mask: mask(h, a) and h >= 1 and a >= 1)
        probabilities[f"result_btts_{name}_yes"] = both
        probabilities[f"result_btts_{name}_no"] = round(
            total(lambda h, a, mask=mask: mask(h, a) and not (h >= 1 and a >= 1)), 4
        )
        for line in (1.5, 2.5, 3.5):
            over = total(lambda h, a, mask=mask, line=line: mask(h, a) and h + a >= math.ceil(line))
            probabilities[f"result_goals_{name}_over_{_suffix(line)}"] = over
            probabilities[f"result_goals_{name}_under_{_suffix(line)}"] = round(
                total(lambda h, a, mask=mask, line=line: mask(h, a) and h + a < math.ceil(line)), 4
            )

    for name, mask in {
        "1x": lambda h, a: h >= a,
        "x2": lambda h, a: a >= h,
        "12": lambda h, a: h != a,
    }.items():
        for line in (1.5, 2.5, 3.5):
            probabilities[f"dc_goals_{name}_over_{_suffix(line)}"] = total(
                lambda h, a, mask=mask, line=line: mask(h, a) and h + a >= math.ceil(line)
            )
            probabilities[f"dc_goals_{name}_under_{_suffix(line)}"] = total(
                lambda h, a, mask=mask, line=line: mask(h, a) and h + a < math.ceil(line)
            )

    for name, mask in {
        "yes": lambda h, a: h >= 1 and a >= 1,
        "no": lambda h, a: not (h >= 1 and a >= 1),
    }.items():
        for line in (1.5, 2.5, 3.5):
            probabilities[f"btts_goals_{name}_over_{_suffix(line)}"] = total(
                lambda h, a, mask=mask, line=line: mask(h, a) and h + a >= math.ceil(line)
            )
            probabilities[f"btts_goals_{name}_under_{_suffix(line)}"] = total(
                lambda h, a, mask=mask, line=line: mask(h, a) and h + a < math.ceil(line)
            )

    return probabilities


def _corners_analyzer(context: Dict[str, Any]) -> Dict[str, float]:
    expected = context.get("expected_corners")
    lines = context.get("corner_lines") or DEFAULT_CORNER_LINES
    if expected is None:
        return {}
    result = {
        f"corners_{key}": value
        for key, value in _over_under(float(expected), lines).items()
    }
    for side in ("home", "away"):
        side_expected = context.get(f"expected_corners_{side}")
        if side_expected is None:
            continue
        side_probs = _over_under(float(side_expected), lines)
        for key, value in side_probs.items():
            result[f"corners_{side}_{key}"] = value
    return result


def _cards_analyzer(context: Dict[str, Any]) -> Dict[str, float]:
    expected = context.get("expected_cards")
    lines = context.get("card_lines") or DEFAULT_CARD_LINES
    if expected is None:
        return {}
    probabilities = _over_under(float(expected), lines)
    result = {f"cards_{key}": value for key, value in probabilities.items()}
    for side in ("home", "away"):
        side_expected = context.get(f"expected_cards_{side}")
        if side_expected is None:
            continue
        side_probs = _over_under(float(side_expected), lines)
        for key, value in side_probs.items():
            result[f"cards_{side}_{key}"] = value
    yellow_expected = context.get("expected_yellow_cards")
    if yellow_expected is not None:
        for key, value in _over_under(float(yellow_expected), lines).items():
            result[f"yellow_cards_{key}"] = value
    return result


def _player_analyzer(context: Dict[str, Any]) -> Dict[str, float]:
    supplied = context.get("player_probabilities") or {}
    return {str(k): float(v) for k, v in supplied.items() if 0.0 <= float(v) <= 1.0}


register_model("dixon_coles", _dixon_coles_analyzer)
register_model("corners", _corners_analyzer, requires=("expected_corners",))
register_model("cards", _cards_analyzer, requires=("expected_cards",))
register_model("player", _player_analyzer, requires=("player_probabilities",))


def evaluate_model(name: str, context: Dict[str, Any]) -> Dict[str, float]:
    analyzer = _REGISTRY.get(name)
    if analyzer is None:
        return {}
    try:
        output = analyzer(context) or {}
    except (TypeError, ValueError):
        return {}
    return {
        str(key): round(float(value), 4)
        for key, value in output.items()
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0
    }


def model_probabilities(context: Dict[str, Any]) -> Dict[str, Any]:
    """Run every registered model and merge their probability keys."""
    probabilities: Dict[str, float] = {}
    status: Dict[str, str] = {}
    for name in registered_models():
        output = evaluate_model(name, context)
        status[name] = "ok" if output else "insufficient_inputs"
        probabilities.update(output)
    return {
        "probabilities": probabilities,
        "model_status": status,
        "models_registered": registered_models(),
    }
