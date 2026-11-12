"""Provider-driven market discovery.

A market enters the platform only when a source actually priced it. Discovery
keeps the source name, bookmaker and capture timestamp on every price; when a
source supplies nothing, the result says so instead of inventing markets.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.market_engine.catalogue import get_definition
from app.market_engine.normalizer import NormalizedSelection, normalize_model_key, normalize_raw_selection


@dataclass(frozen=True)
class DiscoveredMarket:
    family: Optional[str]
    category: Optional[str]
    label: Optional[str]
    settlement_group: Optional[str]
    selection: Optional[str]
    line: Optional[float]
    price: float
    model_key: Optional[str]
    raw_name: str
    raw_selection: str
    bookmaker: Optional[str]
    source: str
    captured_at: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryResult:
    fixture_id: Optional[int]
    source: str
    captured_at: Optional[str]
    markets: List[DiscoveredMarket]
    unmodelled: List[Dict[str, Any]]
    data_status: str

    def as_dict(self) -> Dict[str, Any]:
        family_counts: Dict[str, int] = {}
        for market in self.markets:
            if market.family:
                family_counts[market.family] = family_counts.get(market.family, 0) + 1
        return {
            "fixture_id": self.fixture_id,
            "source": self.source,
            "captured_at": self.captured_at,
            "data_status": self.data_status,
            "discovered_count": len(self.markets),
            "market_count": len(self.markets),
            "family_count": len(family_counts),
            "family_counts": family_counts,
            "families": sorted(family_counts),
            "unmodelled_count": len(self.unmodelled),
            "unmodelled": self.unmodelled,
            "markets": [m.as_dict() for m in self.markets],
        }


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if value:
        return str(value)
    return None


def _price_value(entry: Any) -> Optional[float]:
    if isinstance(entry, dict):
        entry = entry.get("price", entry.get("odds"))
    if isinstance(entry, (int, float)) and entry > 1.0:
        return float(entry)
    return None


def _market_from_selection(
    normalized: NormalizedSelection,
    price: float,
    bookmaker: Optional[str],
    source: str,
    captured_at: Optional[str],
) -> Optional[DiscoveredMarket]:
    if not normalized.family or not normalized.selection:
        return None
    definition = get_definition(normalized.family)
    return DiscoveredMarket(
        family=normalized.family,
        category=definition.category if definition else None,
        label=definition.label if definition else None,
        settlement_group=normalized.settlement_group,
        selection=normalized.selection,
        line=normalized.line,
        price=price,
        model_key=normalized.model_key,
        raw_name=normalized.raw_name,
        raw_selection=normalized.raw_selection,
        bookmaker=bookmaker,
        source=source,
        captured_at=captured_at,
    )


def _dedupe(markets: Iterable[DiscoveredMarket]) -> List[DiscoveredMarket]:
    seen = set()
    unique: List[DiscoveredMarket] = []
    for market in markets:
        key = (
            market.family,
            market.selection,
            market.line,
            market.bookmaker,
            market.raw_name.lower(),
            round(market.price, 4),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(market)
    return unique


def discover_from_market_odds(fixture: Any, bookmaker: Optional[str] = None) -> DiscoveryResult:
    """Discover markets stored in ``fixture.market_odds_json``."""
    raw = getattr(fixture, "market_odds_json", None)
    source = str(getattr(fixture, "source", None) or "fixture_store")
    captured_at = _iso(getattr(fixture, "odds_updated_at", None))

    if not raw:
        return DiscoveryResult(
            fixture_id=getattr(fixture, "id", None),
            source=source,
            captured_at=captured_at,
            markets=[],
            unmodelled=[],
            data_status="no_market_data",
        )
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    markets: List[DiscoveredMarket] = []
    unmodelled: List[Dict[str, Any]] = []
    for key, entry in payload.items():
        price = _price_value(entry)
        if price is None:
            continue
        entry_bookmaker = bookmaker
        entry_time = captured_at
        if isinstance(entry, dict):
            entry_bookmaker = entry.get("bookmaker") or bookmaker
            entry_time = _iso(entry.get("captured_at")) or captured_at

        normalized = normalize_model_key(str(key))
        market = _market_from_selection(normalized, price, entry_bookmaker, source, entry_time)
        if market is None:
            unmodelled.append(
                {
                    "raw_name": str(key),
                    "reason": "unsupported_selection" if normalized.family else "unknown_family",
                }
            )
            continue
        markets.append(market)

    markets = _dedupe(markets)
    return DiscoveryResult(
        fixture_id=getattr(fixture, "id", None),
        source=source,
        captured_at=captured_at,
        markets=markets,
        unmodelled=unmodelled,
        data_status="ok" if markets else "no_market_data",
    )


def discover_from_raw_event(
    event: Dict[str, Any],
    bookmaker: Optional[str] = None,
    source: str = "bookmaker_feed",
    captured_at: Optional[Any] = None,
) -> DiscoveryResult:
    """Discover markets from a raw bookmaker event payload."""
    timestamp = _iso(captured_at) or datetime.now(timezone.utc).isoformat()
    markets: List[DiscoveredMarket] = []
    unmodelled: List[Dict[str, Any]] = []

    for market_block in event.get("markets") or []:
        market_type = market_block.get("marketType") or {}
        market_name = str(market_type.get("name") or market_type.get("id") or "").strip()
        for row in market_block.get("row") or []:
            for price_block in row.get("prices") or []:
                if price_block.get("suspended"):
                    continue
                price = _price_value(price_block.get("odds"))
                if price is None:
                    continue
                label = str(price_block.get("name", "")).strip()
                normalized = normalize_raw_selection(
                    market_name,
                    label,
                    price_block.get("handicap"),
                )
                market = _market_from_selection(
                    normalized,
                    price,
                    bookmaker or price_block.get("bookmaker"),
                    source,
                    timestamp,
                )
                if market is None:
                    unmodelled.append(
                        {
                            "raw_name": f"{market_name} / {label}",
                            "handicap": price_block.get("handicap"),
                            "reason": "unsupported_selection" if normalized.family else "unknown_family",
                        }
                    )
                    continue
                markets.append(market)

    markets = _dedupe(markets)
    return DiscoveryResult(
        fixture_id=None,
        source=source,
        captured_at=timestamp,
        markets=markets,
        unmodelled=unmodelled,
        data_status="ok" if markets else "no_market_data",
    )


def discover_from_raw_markets(
    markets: Iterable[Dict[str, Any]],
    bookmaker: Optional[str] = None,
    source: str = "bookmaker_feed",
    captured_at: Optional[Any] = None,
) -> DiscoveryResult:
    """Discover markets from a bare list of ``{marketType, row}`` blocks."""
    return discover_from_raw_event(
        {"markets": list(markets)},
        bookmaker=bookmaker,
        source=source,
        captured_at=captured_at,
    )
