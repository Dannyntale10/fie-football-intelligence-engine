"""Settings and notification endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.predictions import Notification, SystemSetting
from app.market_engine.qualification import QUALIFICATION_THRESHOLD

router = APIRouter(tags=["system"])

DEFAULTS = {
    "timezone": "Africa/Kampala",
    "timezone_label": "EAT",
    "qualification_threshold": str(QUALIFICATION_THRESHOLD),
    "minimum_data_confidence": "0.60",
    "paper_trading": "true",
    "auto_place_bets": "false",
    "schedule_fixture_scan": "06:00",
    "schedule_statistics_sync": "06:30",
    "schedule_model_run": "07:00",
    "schedule_shortlist": "07:30",
    "schedule_odds_refresh_minutes": "30",
    "schedule_lineups_minutes_before_kickoff": "75",
    "telegram_allowlist": "",
}


class SettingIn(BaseModel):
    value: str


class NotificationIn(BaseModel):
    channel: str = "telegram"
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: str
    related_type: Optional[str] = None
    related_id: Optional[int] = None


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)) -> Dict[str, Any]:
    stored = {row.key: row.value for row in db.scalars(select(SystemSetting)).all()}
    values = {**DEFAULTS, **stored}
    values["auto_place_bets"] = str(values.get("auto_place_bets", "false")).lower()
    return {"settings": values, "editable": sorted(DEFAULTS)}

@router.put("/settings/{key}")
def update_setting(key: str, payload: SettingIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if key == "auto_place_bets" and payload.value.lower() not in ("false", "0", "no"):
        raise HTTPException(status_code=400, detail="Automatic bet placement is disabled by policy")
    if key == "qualification_threshold" and payload.value != str(QUALIFICATION_THRESHOLD):
        raise HTTPException(status_code=400, detail="The 85% qualification threshold cannot be changed")
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        row = SystemSetting(key=key, value=payload.value)
        db.add(row)
    else:
        row.value = payload.value
        row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"key": key, "value": payload.value}


@router.get("/notifications")
def list_notifications(
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    statement = select(Notification).order_by(Notification.created_at.desc()).limit(min(limit, 200))
    if status:
        statement = statement.where(Notification.status == status)
    return [
        {
            "id": row.id,
            "channel": row.channel,
            "recipient": row.recipient,
            "subject": row.subject,
            "body": row.body,
            "status": row.status,
            "related_type": row.related_type,
            "related_id": row.related_id,
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in db.scalars(statement).all()
    ]


@router.post("/notifications")
def create_notification(payload: NotificationIn, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if payload.channel != "telegram":
        raise HTTPException(status_code=400, detail="Telegram is the only notification channel")
    row = Notification(
        channel=payload.channel,
        recipient=payload.recipient,
        subject=payload.subject,
        body=payload.body,
        related_type=payload.related_type,
        related_id=payload.related_id,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "status": row.status}


@router.post("/notifications/{notification_id}/sent")
def mark_sent(notification_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    row = db.get(Notification, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.status = "sent"
    row.sent_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": row.id, "status": row.status}
