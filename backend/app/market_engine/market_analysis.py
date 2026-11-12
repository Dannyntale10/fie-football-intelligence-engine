"""Analyse discovered markets with registered models and real prices.

The output distinguishes four honest states per selection: modelled and
qualified, modelled below threshold, modelled but lacking inputs, and not
modelled at all. Nothing is promoted to "analysed" unless a probability exists.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.analytics.blending import OUTCOMES as BLEND_OUTCOMES
from app.analytics.blending import blend_1x2, blend_status
from app.market_engine.catalogue import ENGINE_VERSION, get_definition
from app.market_engine.discovery import (
    DiscoveryResult,
    discover_from_market_odds,
    discover_from_raw_event,
)
from app.market_engine.market_registry import model_probabilities
from app.market_engine.qualification import (
    QUALIFICATION_THRESHOLD,
    data_confidence,
    qualify,
    summary,
)
from app.market_engine.value_detection import detect_value

AnalysisRow = Dict[str, Any]


def _implied(price: Optional[float]) -> Optional[float]:
    if not isinstance(price, (int, float)) or price <= 1.0:
        return None
    return 1.0 / price


def _devig(groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]]) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    results: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for key, rows in groups.items():
        unique = {}
        for row in rows:
            implied = _implied(row.get("price"))
            if implied is not None and row.get("selection") not in unique:
                unique[row["selection"]] = implied
        if len(unique) < 2:
            results[key] = {"fair": {}, "margin": None, "selections": len(unique)}
            continue
        overround = sum(unique.values())
        results[key] = {
            "fair": {selection: implied / overround for selection, implied in unique.items()},
            "margin": round(overround - 1.0, 4),
            "selections": len(unique),
        }
    return results


def _model_state(family: Optional[str], model_key: Optional[str], probabilities: Dict[str, float], statuses: Dict[str, str]) -> str:
    definition = get_definition(family) if family else None
    if definition is None:
        return "unmodelled"
    if not definition.model:
        return "no_model"
    if statuses.get(definition.model) != "ok":
        return "insufficient_model_data"
    if not model_key or model_key not in probabilities:
        return "key_not_modelled"
    return "ok"


def analyse_discovery(
    discovery: DiscoveryResult,
    context: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    context = dict(context or {})
    modelled = model_probabilities(context)
    probabilities = modelled["probabilities"]
    statuses = modelled["model_status"]

    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for market in discovery.markets:
        key = (market.bookmaker, market.family, market.line)
        groups.setdefault(key, []).append(market.as_dict())
    devigged = _devig(groups)

    override = context.get("blend_weight")
    if override is not None:
        weight = min(1.0, max(0.0, float(override)))
        temperature = float(context.get("blend_temperature", 1.0))
        blend_context: Dict[str, Any] = {
            "fitted": True,
            "weight": weight,
            "temperature": temperature,
            "weight_source": "context_override",
            "scope": "1x2",
            "competition": context.get("competition"),
            "verdict": "override",
            "generated_at": None,
        }
    else:
        blend_context = blend_status(context.get("competition"))
        weight = blend_context.get("weight")
        temperature = float(blend_context.get("temperature", 1.0))

    # Only real de-vigged prices become a prior. Each bookmaker's 1X2 triple is
    # pooled once so the three selections stay coherent with each other.
    blend_cache: Dict[Tuple[Any, ...], Dict[str, float]] = {}
    if weight is not None:
        for key, group_rows in groups.items():
            definition = get_definition(group_rows[0].get("family") or "")
            if definition is None or definition.settlement_group != "1x2":
                continue
            model_triple = {
                row["model_key"]: probabilities.get(row["model_key"])
                for row in group_rows
                if row.get("model_key") in BLEND_OUTCOMES
            }
            market_triple = {
                row["model_key"]: devigged[key]["fair"].get(row["selection"])
                for row in group_rows
                if row.get("model_key") in BLEND_OUTCOMES
            }
            if not all(model_triple.get(outcome) is not None for outcome in BLEND_OUTCOMES):
                continue
            if not all(market_triple.get(outcome) for outcome in BLEND_OUTCOMES):
                continue
            blend_cache[key] = blend_1x2(
                model_triple, market_triple, weight, temperature
            )

    rows: List[AnalysisRow] = []
    for market in discovery.markets:
        definition = get_definition(market.family) if market.family else None
        model_state = _model_state(market.family, market.model_key, probabilities, statuses)
        group_key = (market.bookmaker, market.family, market.line)
        model_probability = probabilities.get(market.model_key) if market.model_key else None
        blended_probability = blend_cache.get(group_key, {}).get(market.model_key)
        probability = blended_probability if blended_probability is not None else model_probability
        group = devigged[group_key]
        fair_market_probability = group["fair"].get(market.selection)
        implied = _implied(market.price)
        confidence = data_confidence(market, context, now=now)

        data_status = "ok" if model_state == "ok" else model_state
        gate = qualify(probability, confidence["confidence"], data_status)
        ev = (
            round(float(probability) * float(market.price) - 1.0, 4)
            if probability is not None and implied is not None
            else None
        )

        rows.append(
            {
                "engine_version": ENGINE_VERSION,
                "family": market.family,
                "label": definition.label if definition else market.raw_name,
                "category": definition.category if definition else None,
                "settlement_group": market.settlement_group,
                "selection": market.selection,
                "line": market.line,
                "model_key": market.model_key,
                "model": definition.model if definition else None,
                "raw_name": market.raw_name,
                "raw_selection": market.raw_selection,
                "bookmaker": market.bookmaker,
                "source": market.source,
                "captured_at": market.captured_at,
                "price": market.price,
                "probability": probability,
                "probability_model": model_probability,
                "blended": blended_probability is not None,
                "blend_weight": weight if blended_probability is not None else None,
                "blend_temperature": temperature if blended_probability is not None else None,
                "fair_odds": round(1.0 / probability, 3) if probability else None,
                "implied_probability": round(implied, 4) if implied is not None else None,
                "devigged_probability": round(fair_market_probability, 4) if fair_market_probability is not None else None,
                "group_margin": group["margin"],
                "group_selections": group["selections"],
                "ev": ev,
                "ev_status": "available" if ev is not None else "odds_or_probability_unavailable",
                "model_status": model_state,
                "data_status": data_status,
                "confidence": confidence["confidence"],
                "confidence_reasons": confidence["reasons"],
                "qualified": gate["qualified"],
                "qualification_reason": gate["reason"],
            }
        )

    result_summary = summary(rows)
    value = detect_value(rows)
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["model_status"]] = counts.get(row["model_status"], 0) + 1

    return {
        "engine_version": ENGINE_VERSION,
        "fixture_id": discovery.fixture_id,
        "source": discovery.source,
        "captured_at": discovery.captured_at,
        "data_status": discovery.data_status,
        "threshold": QUALIFICATION_THRESHOLD,
        "models": modelled["model_status"],
        "model_states": counts,
        "discovered_count": len(rows),
        "unmodelled": discovery.unmodelled,
        "selections": rows,
        "qualified": result_summary["qualified_count"],
        "evaluated": result_summary["evaluated"],
        "message": result_summary["message"],
        "blend": {
            **blend_context,
            "applied": bool(weight is not None),
            "selections_blended": sum(1 for row in rows if row["blended"]),
            "note": (
                "1X2 probabilities pool the model with the de-vigged price in log-odds space. "
                "Other families remain pure model output, and estimated prices are never blended."
            ),
        },
        "value_detection": value,
    }


def _context_for_fixture(fixture: Any, overrides: Optional[Dict[str, Any]], db: Any) -> Dict[str, Any]:
    context = dict(overrides or {})
    context.setdefault("competition", getattr(fixture, "competition", None))
    if context.get("home_lambda") is None or context.get("away_lambda") is None:
        from app.analytics.probabilities import analyze_fixture

        analysis = analyze_fixture(fixture, db=db)
        lambdas = analysis.get("estimated_lambdas") or {}
        context.setdefault("home_lambda", lambdas.get("home_lambda"))
        context.setdefault("away_lambda", lambdas.get("away_lambda"))
        context.setdefault("lambda_source", analysis.get("lambda_source"))
    return context


def analyse_fixture_markets(
    fixture: Any,
    context: Optional[Dict[str, Any]] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """Discover and analyse every market a fixture actually carries."""
    discovery = discover_from_market_odds(fixture)
    return analyse_discovery(discovery, _context_for_fixture(fixture, context, db))


def analyse_raw_event(
    event: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
    bookmaker: Optional[str] = None,
    source: str = "bookmaker_feed",
    captured_at: Optional[Any] = None,
) -> Dict[str, Any]:
    """Analyse a raw bookmaker payload without persisting it."""
    discovery = discover_from_raw_event(
        event,
        bookmaker=bookmaker,
        source=source,
        captured_at=captured_at,
    )
    # Raw odds imply expected goals through the same de-vigged market path.
    context = dict(context or {})
    if context.get("home_lambda") is None or context.get("away_lambda") is None:
        by_key = {
            market.model_key: market.price
            for market in discovery.markets
            if market.model_key
        }
        one_x_two = [by_key.get(k) for k in ("home_win", "draw", "away_win")]
        if all(one_x_two):
            stub = type(
                "FixtureStub",
                (),
                {
                    "home_odds": one_x_two[0],
                    "draw_odds": one_x_two[1],
                    "away_odds": one_x_two[2],
                    "market_odds_json": None,
                },
            )()
            from app.analytics.probabilities import estimate_lambdas_from_market

            home_lambda, away_lambda, lambda_source = estimate_lambdas_from_market(stub)
            context.setdefault("home_lambda", home_lambda)
            context.setdefault("away_lambda", away_lambda)
            context.setdefault("lambda_source", lambda_source)
    return analyse_discovery(discovery, context)
