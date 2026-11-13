"""Scheduled automation in East Africa Time (default).

Jobs are idempotent and fail soft: a missing provider or an empty database
logs a skip instead of inventing work. The 85% gate is never bypassed here.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
_started = False
EAT_TZ = timezone(timedelta(hours=3), name="EAT")

SCHEDULE = {
    "fixtures": "06:00",
    "statistics": "06:30",
    "models": "07:00",
    "shortlist": "07:30",
    "odds_minutes": 30,
    "odds_window": "06-23",
    "lineup_lead_minutes": "60-90",
    "results": "02:30",
    "analytics": "23:00",
    "delivery_minutes": 2,
}


def _session():
    from app.database import get_db

    return next(get_db())


def _notify(subject: str, body: str, related_type: Optional[str] = None, related_id: Optional[int] = None) -> None:
    try:
        from app.models.predictions import Notification

        db = _session()
        db.add(
            Notification(
                channel="telegram",
                subject=subject,
                body=body,
                related_type=related_type,
                related_id=related_id,
                status="pending",
            )
        )
        db.commit()
        db.close()
    except Exception as error:  # pragma: no cover - notification is best effort
        logger.warning("[Scheduler] notification not queued: %s", error)


def scan_fixtures() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    try:
        from app.data_sources.live_feed import fetch_live_upcoming_fixtures_espn

        db = _session()
        result["fixtures"] = fetch_live_upcoming_fixtures_espn(db)
        db.close()
    except Exception as error:
        logger.error("[Scheduler] fixture scan failed: %s", error)
        result["fixtures_error"] = str(error)
    return result


def refresh_odds() -> Dict[str, Any]:
    try:
        from app.data_sources.bookmaker_sync import sync_bookmakers

        result = sync_bookmakers()
        totals = result.get("totals") or {}
        logger.info(
            "[Scheduler] odds refreshed: %s matched, %s new across %s feeds",
            totals.get("matched_existing"),
            totals.get("inserted_new"),
            len(result.get("feeds") or {}),
        )
        return result
    except Exception as error:
        logger.error("[Scheduler] odds refresh failed: %s", error)
        return {"error": str(error)}


def sync_statistics() -> Dict[str, Any]:
    from app.data_sources.providers import provider_catalogue

    configured = [
        entry for entry in provider_catalogue()
        if entry["key_env"] and os.getenv(entry["key_env"])
    ]
    if not configured:
        logger.info("[Scheduler] statistics sync skipped: no statistics provider configured")
        return {"status": "skipped", "reason": "no_configured_provider"}
    return {"status": "ready", "provider": configured[0]["name"]}


def run_models() -> Dict[str, Any]:
    try:
        from app.analytics.learning import build_report, save_report

        db = _session()
        report = build_report(db)
        db.close()
        settled = (report.get("sources") or {}).get("settled_legs", 0)
        if settled:
            save_report(report)
            return {"status": "trained", "settled_legs": settled}
        return {"status": "skipped", "reason": "no_settled_real_legs"}
    except Exception as error:
        logger.error("[Scheduler] model run failed: %s", error)
        return {"status": "error", "error": str(error)}


def deliver_notifications() -> Dict[str, Any]:
    """Send queued Telegram rows to allowlisted chats and record each outcome."""
    try:
        from app.notifications.telegram import deliver_pending

        db = _session()
        try:
            result = deliver_pending(db)
        finally:
            db.close()
        if result.get("sent") or result.get("failed"):
            logger.info(
                "[Scheduler] notifications delivered: %s sent, %s failed",
                result.get("sent"),
                result.get("failed"),
            )
        return result
    except Exception as error:
        logger.error("[Scheduler] notification delivery failed: %s", error)
        return {"status": "error", "error": str(error)}


def build_shortlist() -> Dict[str, Any]:
    from app.notifications.telegram import NO_QUALIFIED, format_daily_report

    try:
        from app.betting.daily_combos import build_daily_combos

        db = _session()
        try:
            report = build_daily_combos(db)
        finally:
            db.close()
    except Exception as error:
        logger.error("[Scheduler] shortlist failed: %s", error)
        return {"status": "error", "error": str(error)}

    subject = f"Daily shortlist · {report.get('matchday_label') or ''}".strip()
    text = format_daily_report(report)
    if not report.get("best"):
        _notify(subject, text)
        return {"status": "empty", "message": NO_QUALIFIED, "delivery": deliver_notifications()}

    _notify(subject, text, related_type="combos")
    return {
        "status": "ok",
        "tickets": sum(len(rows) for rows in (report.get("combos") or {}).values()),
        "delivery": deliver_notifications(),
    }


def monitor_lineups() -> Dict[str, Any]:
    try:
        from app.data_sources.providers import provider_catalogue

        lineup_provider = next(
            (entry for entry in provider_catalogue() if "lineup" in str(entry.get("free_plan", "")).lower()),
            None,
        )
        if lineup_provider is None or not os.getenv(lineup_provider.get("key_env") or ""):
            logger.info("[Scheduler] lineup monitor skipped: no lineup provider configured")
            return {"status": "skipped", "reason": "no_lineup_provider"}
        return {"status": "ready", "provider": lineup_provider["name"]}
    except Exception as error:
        logger.error("[Scheduler] lineup monitor failed: %s", error)
        return {"status": "error", "error": str(error)}


def update_results() -> Dict[str, Any]:
    try:
        from app.betting.result_tracker import fetch_and_update_results

        db = _session()
        result = fetch_and_update_results(db)
        db.close()
        return result
    except Exception as error:
        logger.error("[Scheduler] results update failed: %s", error)
        return {"error": str(error)}


def daily_analytics() -> Dict[str, Any]:
    try:
        from app.analytics.learning import build_report

        db = _session()
        report = build_report(db)
        db.close()
        summary = {
            "walkforward_matches": (report.get("sources") or {}).get("walkforward_matches"),
            "settled_legs": (report.get("sources") or {}).get("settled_legs"),
            "applied_markets": report.get("applied_markets"),
        }
        _notify("Daily analytics", str(summary)[:900])
        return {"status": "ok", "summary": summary}
    except Exception as error:
        logger.error("[Scheduler] analytics failed: %s", error)
        return {"status": "error", "error": str(error)}


def refit_blend() -> Dict[str, Any]:
    """Refit the 1X2 model-market blend on leak-free history (weekly)."""
    try:
        from app.analytics.blending import fit_report, save_report

        db = _session()
        report = fit_report(db)
        db.close()
    except Exception as error:
        logger.error("[Scheduler] blend refit failed: %s", error)
        return {"status": "error", "error": str(error)}

    if report.get("status") != "fitted":
        logger.info("[Scheduler] blend refit skipped: %s", report.get("message"))
        return {"status": "skipped", "reason": report.get("message")}

    save_report(report)
    holdout = report.get("holdout") or {}
    _notify(
        "Model blend refitted",
        f"Weight {report['weights']['default']:.3f} · holdout Brier "
        f"{holdout.get('blend', {}).get('brier')} vs market "
        f"{holdout.get('market', {}).get('brier')} · {report.get('verdict')}",
        related_type="blend",
    )
    return {
        "status": "fitted",
        "weight": report["weights"]["default"],
        "verdict": report.get("verdict"),
    }


def start_scheduler() -> Optional[object]:
    """Start APScheduler with the published EAT schedule."""
    global _started
    if _started:
        return None
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        scheduler = BackgroundScheduler(timezone=EAT_TZ)
        scheduler.add_job(scan_fixtures, CronTrigger(hour=6, minute=0, timezone=EAT_TZ), id="fixture_scan")
        scheduler.add_job(sync_statistics, CronTrigger(hour=6, minute=30, timezone=EAT_TZ), id="statistics_sync")
        scheduler.add_job(run_models, CronTrigger(hour=7, minute=0, timezone=EAT_TZ), id="model_run")
        scheduler.add_job(build_shortlist, CronTrigger(hour=7, minute=30, timezone=EAT_TZ), id="shortlist")
        scheduler.add_job(
            refresh_odds,
            CronTrigger(minute=f"*/{SCHEDULE['odds_minutes']}", hour=SCHEDULE["odds_window"], timezone=EAT_TZ),
            id="odds_refresh",
        )
        scheduler.add_job(monitor_lineups, CronTrigger(minute="*/15", hour="5-23", timezone=EAT_TZ), id="lineups")
        scheduler.add_job(
            deliver_notifications,
            CronTrigger(minute=f"*/{SCHEDULE['delivery_minutes']}", hour="6-23", timezone=EAT_TZ),
            id="notify_delivery",
        )
        scheduler.add_job(update_results, CronTrigger(hour=2, minute=30, timezone=EAT_TZ), id="results")
        scheduler.add_job(daily_analytics, CronTrigger(hour=23, minute=0, timezone=EAT_TZ), id="analytics")
        scheduler.add_job(refit_blend, CronTrigger(day_of_week="sun", hour=23, minute=30, timezone=EAT_TZ), id="blend_refit")
        scheduler.start()
        _started = True
        logger.info("[Scheduler] EAT schedule active.")
        return scheduler
    except ImportError:
        logger.warning("[Scheduler] apscheduler not installed; manual triggers only.")
        return None
    except Exception as error:
        logger.error("[Scheduler] failed to start: %s", error)
        return None


# Backwards-compatible job names used by older entry points.
def _job_live():
    scan_fixtures()
    refresh_odds()


def _job_results():
    update_results()
