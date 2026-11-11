from __future__ import annotations
"""
scheduler.py – Background scheduler for automated fixture updates and score tracking.
"""
import logging
from datetime import timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)
_started = False
EAT_TZ = timezone(timedelta(hours=3), name="EAT")

def _job_live():
    try:
        from app.database import get_db
        from app.services.live_feed import fetch_live_upcoming_fixtures_espn
        db = next(get_db())
        res = fetch_live_upcoming_fixtures_espn(db)
        db.close()
        logger.info(f"[Scheduler] Live feed synced: {res.get('inserted', 0)} new fixtures.")
    except Exception as e:
        logger.error(f"[Scheduler] Live feed error: {e}")

def _job_results():
    try:
        from app.database import get_db
        from app.services.result_tracker import fetch_and_update_results
        db = next(get_db())
        res = fetch_and_update_results(db)
        db.close()
        logger.info(f"[Scheduler] Results updated: {res.get('updated', 0)} matches.")
    except Exception as e:
        logger.error(f"[Scheduler] Results error: {e}")

def start_scheduler() -> Optional[object]:
    """Start APScheduler background jobs if available."""
    global _started
    if _started:
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        s = BackgroundScheduler(timezone=EAT_TZ)
        s.add_job(_job_live, CronTrigger(hour=6, minute=0, timezone=EAT_TZ), id="daily_fetch")
        s.add_job(_job_live, CronTrigger(minute="*/30", hour="6-23", timezone=EAT_TZ), id="odds_refresh")
        s.add_job(_job_results, CronTrigger(hour=2, minute=30, timezone=EAT_TZ), id="result_fetch")
        s.start()
        _started = True
        logger.info("[Scheduler] Background scheduler active.")
        return s
    except ImportError:
        logger.warning("[Scheduler] apscheduler not installed; running in manual trigger mode.")
        return None
    except Exception as e:
        logger.error(f"[Scheduler] Failed to start scheduler: {e}")
        return None
