"""Value detection on top of analysed market selections.

EV is only reported when a real price exists. When odds are missing the result
says ``odds_unavailable`` instead of producing a number from a guess.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

ValueKey = Tuple[Optional[str], Optional[str], Optional[float]]


def _key(row: Dict[str, Any]) -> ValueKey:
    return (row.get("family"), row.get("selection"), row.get("line"))


def best_prices(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Highest observed price per selection, with its bookmaker attribution."""
    best: Dict[ValueKey, Dict[str, Any]] = {}
    for row in rows:
        price = row.get("price")
        if not isinstance(price, (int, float)) or price <= 1.0:
            continue
        key = _key(row)
        current = best.get(key)
        if current is None or price > current["price"]:
            best[key] = {
                "family": row.get("family"),
                "selection": row.get("selection"),
                "line": row.get("line"),
                "price": float(price),
                "bookmaker": row.get("bookmaker"),
                "source": row.get("source"),
                "captured_at": row.get("captured_at"),
                "probability": row.get("probability"),
                "ev": row.get("ev"),
            }
    return sorted(best.values(), key=lambda item: item.get("ev") or -1.0, reverse=True)


def detect_value(
    rows: Iterable[Dict[str, Any]],
    min_edge: float = 0.0,
    price_history: Optional[Dict[str, List[float]]] = None,
) -> Dict[str, Any]:
    rows = list(rows)
    priced = [row for row in rows if isinstance(row.get("price"), (int, float))]
    with_probability = [row for row in priced if isinstance(row.get("probability"), (int, float))]
    value: List[Dict[str, Any]] = []
    history = price_history or {}

    # One entry per selection, evaluated at the best available price.
    best_by_key: Dict[ValueKey, Dict[str, Any]] = {}
    for row in with_probability:
        key = _key(row)
        current = best_by_key.get(key)
        if current is None or float(row["price"]) > float(current["price"]):
            best_by_key[key] = row

    for key, row in best_by_key.items():
        ev = row.get("ev")
        if not isinstance(ev, (int, float)) or ev <= min_edge:
            continue
        entry = {
            "family": row.get("family"),
            "label": row.get("label"),
            "selection": row.get("selection"),
            "line": row.get("line"),
            "bookmaker": row.get("bookmaker"),
            "source": row.get("source"),
            "price": row.get("price"),
            "probability": row.get("probability"),
            "market_probability": row.get("devigged_probability"),
            "implied_probability": row.get("implied_probability"),
            "ev": round(float(ev), 4),
            "edge": round(float(row["probability"]) - float(row.get("devigged_probability") or row.get("implied_probability") or 0.0), 4),
            "captured_at": row.get("captured_at"),
        }
        prices = history.get(str(key)) or []
        if len(prices) >= 2 and prices[0] > 0:
            entry["movement"] = round((prices[-1] - prices[0]) / prices[0], 4)
            entry["opening_price"] = prices[0]
        value.append(entry)

    value.sort(key=lambda item: item["ev"], reverse=True)
    return {
        "ev_available": bool(priced),
        "priced_selections": len(priced),
        "probabilities_available": len(with_probability),
        "value_count": len(value),
        "value_selections": value,
        "best_prices": best_prices(priced),
        "unavailable_reason": None if priced else "odds_unavailable",
    }
