"""Run the public bookmaker feeds and record what each run actually produced.

Each feed writes its own ``DataProvider`` and ``ProviderSyncLog`` rows, so the
dashboard can show last sync, coverage and failures per source instead of one
merged number. A failed feed never blocks the others.
"""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

FEEDS: List[Dict[str, str]] = [
    {
        "name": "betpawa",
        "label": "BetPawa",
        "base_url": "https://www.betpawa.ug",
        "notes": "Public betPawa Uganda feed. Every price keeps its bookmaker, source URL and capture time.",
    },
    {
        "name": "sportpesa",
        "label": "SportPesa",
        "base_url": "https://www.sportpesa.com",
        "notes": "Public SportPesa upcoming-games feed; a 30-game window, so coverage is honest but bounded.",
    },
    {
        "name": "fansport",
        "label": "FANSPORT",
        "base_url": "https://fsportug.icu",
        "notes": "Public FANSPORT main-line feed behind fsportug.icu; prices come from the site's own JSON, not estimates.",
    },
    {
        "name": "betika",
        "label": "Betika",
        "base_url": "https://www.betika.com/en-ug/",
        "notes": "Public Betika Uganda match feed; 1X2 plus detail markets, EAT kickoffs, real prices only.",
    },
]

_RUNNERS: Dict[str, tuple[str, str]] = {
    "betpawa": ("app.data_sources.betpawa_feed", "ingest_betpawa_odds"),
    "sportpesa": ("app.data_sources.sportpesa_feed", "ingest_sportpesa_odds"),
    "fansport": ("app.data_sources.fansport_feed", "ingest_fansport_odds"),
    "betika": ("app.data_sources.betika_feed", "ingest_betika_odds"),
}


def _ingest(name: str, db: Session, max_events: Optional[int]) -> Dict[str, Any]:
    module_name, function_name = _RUNNERS[name]
    ingest: Callable[..., Dict[str, Any]] = getattr(importlib.import_module(module_name), function_name)
    if max_events is None:
        return ingest(db)
    return ingest(db, max_events=max_events)


def _status(stats: Dict[str, Any]) -> str:
    if not stats.get("fetched_events"):
        return "empty"
    if stats.get("odds_updated") or stats.get("inserted_new"):
        return "success"
    return "partial"


def _short_reason(error: Exception) -> str:
    """Compact failure text for the dashboard; the sync log keeps the full error."""
    text = " ".join(str(error).split())
    if "NameResolutionError" in text or "nodename nor servname" in text:
        return "DNS lookup failed; the feed host did not resolve"
    if "ConnectTimeout" in text or "timed out" in text:
        return "connection timed out"
    if "Max retries exceeded" in text:
        return "network error reaching feed"
    return text[:200]


def _summary(stats: Dict[str, Any]) -> str:
    return (
        f"{stats.get('fetched_events', 0)} fetched, "
        f"{stats.get('matched_existing', 0)} matched, "
        f"{stats.get('inserted_new', 0)} new, "
        f"{stats.get('skipped_unknown_competition', 0)} untracked competition"
    )


def _upsert_provider(db: Session, feed: Dict[str, str]) -> Any:
    from app.models.data import DataProvider

    provider = db.scalar(select(DataProvider).where(DataProvider.name == feed["name"]))
    if provider is None:
        provider = DataProvider(name=feed["name"])
        db.add(provider)
    provider.kind = "odds"
    provider.base_url = feed["base_url"]
    provider.free_plan = True
    provider.notes = feed["notes"]
    return provider


def sync_feed(
    feed: Dict[str, str],
    *,
    max_events: Optional[int] = None,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    """Ingest one feed in its own session and log the outcome."""
    from app.models.data import DataProvider, ProviderSyncLog

    factory = session_factory or _default_session_factory()
    db = factory()
    started_at = datetime.now(timezone.utc)
    log: Optional[ProviderSyncLog] = None
    try:
        provider = _upsert_provider(db, feed)
        db.commit()
        db.refresh(provider)

        log = ProviderSyncLog(provider_id=provider.id, entity="odds", status="running")
        db.add(log)
        db.commit()
        db.refresh(log)

        stats = _ingest(feed["name"], db, max_events)
        status = _status(stats)
        finished_at = datetime.now(timezone.utc)

        log.status = status
        log.records_written = int(stats.get("odds_updated", 0)) + int(stats.get("inserted_new", 0))
        log.request_count = int(stats.get("requests", 1))
        log.finished_at = finished_at
        log.message = _summary(stats)
        provider.last_sync_at = finished_at
        provider.status = status
        db.commit()

        return {
            **stats,
            "provider": feed["name"],
            "label": feed["label"],
            "status": status,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }
    except Exception as error:
        db.rollback()
        finished_at = datetime.now(timezone.utc)
        logger.warning("bookmaker sync failed for %s: %s", feed["name"], error)
        try:
            if log is not None and log.id is not None:
                stored = db.get(ProviderSyncLog, log.id)
                if stored is not None:
                    stored.status = "failed"
                    stored.finished_at = finished_at
                    stored.message = str(error)[:500]
            provider = db.scalar(select(DataProvider).where(DataProvider.name == feed["name"]))
            if provider is not None:
                provider.status = "failed"
                provider.last_sync_at = finished_at
            db.commit()
        except Exception:  # pragma: no cover - logging must not mask the failure
            db.rollback()
        return {
            "provider": feed["name"],
            "label": feed["label"],
            "status": "failed",
            "error": _short_reason(error),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }
    finally:
        db.close()


def _default_session_factory() -> Callable[[], Session]:
    from app.database import SessionLocal

    return SessionLocal


def sync_bookmakers(
    *,
    max_events: Optional[int] = None,
    feeds: Optional[List[Dict[str, str]]] = None,
    session_factory: Optional[Callable[[], Session]] = None,
) -> Dict[str, Any]:
    """Run every configured public bookmaker feed, one isolated session each."""
    results: Dict[str, Any] = {}
    for feed in feeds if feeds is not None else FEEDS:
        results[feed["name"]] = sync_feed(
            feed, max_events=max_events, session_factory=session_factory
        )

    totals = {
        key: sum(int(body.get(key, 0) or 0) for body in results.values())
        for key in ("fetched_events", "matched_existing", "odds_updated", "inserted_new")
    }
    return {
        "feeds": results,
        "totals": totals,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
