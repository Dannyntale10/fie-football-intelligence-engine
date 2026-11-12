"""Data-source and provider status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.providers import is_configured, provider_catalogue
from app.database import get_db
from app.models.data import DataProvider, ProviderSyncLog

router = APIRouter(prefix="/sources", tags=["sources"])


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    """SQLite drops tzinfo; every stored timestamp here is UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@router.get("")
def list_sources(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.scalars(select(DataProvider).order_by(DataProvider.name)).all()
    stored = {
        row.name: {
            "status": row.status,
            "daily_quota": row.daily_quota,
            "quota_used": row.quota_used,
            "last_sync_at": _iso_utc(row.last_sync_at),
            "notes": row.notes,
        }
        for row in rows
    }
    catalogue = provider_catalogue()
    for entry in catalogue:
        entry["configured"] = is_configured(entry)
        entry.setdefault("last_sync_at", None)
        saved = stored.get(entry["name"])
        if saved:
            entry["status"] = saved["status"]
            entry["quota_used"] = saved["quota_used"]
            entry["last_sync_at"] = saved["last_sync_at"]
            entry["notes"] = saved["notes"]
            if saved["daily_quota"] is not None:
                entry["daily_quota"] = saved["daily_quota"]
    return {
        "providers": catalogue,
        "configured_count": sum(1 for entry in catalogue if entry["configured"]),
        "note": "Free-plan limits are recorded for planning; verify current limits with each provider before relying on them.",
    }


@router.post("/refresh")
def refresh_sources(
    max_events: Optional[int] = Query(None, ge=1, le=500, description="Cap events per feed"),
) -> Dict[str, Any]:
    """Pull fixtures and real prices from every public bookmaker feed."""
    from app.data_sources.bookmaker_sync import sync_bookmakers

    return sync_bookmakers(max_events=max_events)


@router.get("/{name}/status")
def source_status(name: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    entry = next((row for row in provider_catalogue() if row["name"] == name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown provider")
    entry["configured"] = is_configured(entry)
    stored = db.scalar(select(DataProvider).where(DataProvider.name == name))
    if stored:
        entry["status"] = stored.status
        entry["quota_used"] = stored.quota_used
        entry["notes"] = stored.notes
        entry["last_sync_at"] = _iso_utc(stored.last_sync_at)
    return entry


@router.get("/sync-logs")
def sync_logs(limit: int = 50, db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    rows = db.scalars(
        select(ProviderSyncLog).order_by(ProviderSyncLog.started_at.desc()).limit(min(limit, 200))
    ).all()
    return [
        {
            "id": row.id,
            "provider_id": row.provider_id,
            "entity": row.entity,
            "status": row.status,
            "records_written": row.records_written,
            "request_count": row.request_count,
            "started_at": _iso_utc(row.started_at),
            "finished_at": _iso_utc(row.finished_at),
            "message": row.message,
        }
        for row in rows
    ]
