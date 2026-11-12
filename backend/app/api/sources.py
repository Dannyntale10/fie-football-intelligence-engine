"""Data-source and provider status endpoints."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data_sources.providers import is_configured, provider_catalogue
from app.database import get_db
from app.models.data import DataProvider, ProviderSyncLog

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("")
def list_sources(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = db.scalars(select(DataProvider).order_by(DataProvider.name)).all()
    stored = {
        row.name: {
            "status": row.status,
            "free_plan": row.free_plan,
            "daily_quota": row.daily_quota,
            "quota_used": row.quota_used,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
        }
        for row in rows
    }
    catalogue = provider_catalogue()
    for entry in catalogue:
        entry["configured"] = is_configured(entry)
        entry.update(stored.get(entry["name"], {}))
    return {
        "providers": catalogue,
        "configured_count": sum(1 for entry in catalogue if entry["configured"]),
        "note": "Free-plan limits are recorded for planning; verify current limits with each provider before relying on them.",
    }


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
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "message": row.message,
        }
        for row in rows
    ]
