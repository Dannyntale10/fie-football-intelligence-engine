from __future__ import annotations
"""
sentinel.py – 24/7 Autonomous Banker Sentinel & WhatsApp Alert Daemon.

Runs continuously in the background:
  1. Refreshes live upcoming fixtures and live DraftKings odds across all 35+ bookmaker leagues.
  2. Runs Dixon-Coles Poisson + ML rating analysis across all market structures.
  3. Pre-verifies team performance, H2H, and player injuries/squad availability via AI.
  4. Generates optimal 2-team & 3-team combos strictly meeting:
       - Combined Odds >= 2.00
       - AVG TEAM CONFIDENCE >= 85.0% (min 85% to 100% max)
       - Expected Value (+EV > 0%)
       - AI Squad/Injury Check = APPROVED
  5. Deduplicates: if a qualifying ticket is found that hasn't been alerted today,
     it immediately dispatches a formatted alert to your WhatsApp!
"""
import os
import time
import json
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EAT_TZ = timezone(timedelta(hours=3), name="EAT")
_HISTORY_FILE = Path(__file__).resolve().parent.parent.parent / "sentinel_state.json"

_sentinel_thread: Optional[threading.Thread] = None
_sentinel_stop_event = threading.Event()
_sentinel_state: Dict[str, Any] = {
    "is_running": False,
    "started_at": None,
    "last_scan_utc": None,
    "last_scan_eat": None,
    "scans_count": 0,
    "fixtures_scanned_last": 0,
    "alerts_dispatched_count": 0,
    "last_alert_combo": None,
    "last_alert_eat": None,
    "recent_logs": [],
}


def _load_persisted_state():
    """Load previously notified combo hashes from disk."""
    if _HISTORY_FILE.exists():
        try:
            with open(_HISTORY_FILE, "r") as f:
                data = json.load(f)
                _sentinel_state["alerts_dispatched_count"] = data.get("alerts_dispatched_count", 0)
                _sentinel_state["notified_hashes"] = set(data.get("notified_hashes", []))
                _sentinel_state["last_alert_eat"] = data.get("last_alert_eat")
                return
        except Exception as e:
            logger.debug(f"Failed to load sentinel state: {e}")
    _sentinel_state["notified_hashes"] = set()


def _save_persisted_state():
    """Persist notified combo hashes to disk so restarts don't spam."""
    try:
        with open(_HISTORY_FILE, "w") as f:
            json.dump({
                "alerts_dispatched_count": _sentinel_state.get("alerts_dispatched_count", 0),
                "notified_hashes": list(_sentinel_state.get("notified_hashes", set())),
                "last_alert_eat": _sentinel_state.get("last_alert_eat"),
            }, f, indent=2)
    except Exception as e:
        logger.debug(f"Failed to save sentinel state: {e}")


_load_persisted_state()


def _log_event(msg: str):
    eat_time = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%H:%M:%S EAT")
    entry = f"[{eat_time}] {msg}"
    logs = _sentinel_state.setdefault("recent_logs", [])
    logs.append(entry)
    if len(logs) > 50:
        _sentinel_state["recent_logs"] = logs[-50:]
    logger.info(f"[Sentinel] {msg}")


def _combo_signature(combo: Dict[str, Any], matchday: str) -> str:
    """Create a unique hash signature for a combo to prevent duplicate alerts."""
    leg_ids = sorted([f"{l.get('fixture_id')}_{l.get('market_key')}" for l in combo.get("legs", [])])
    raw = f"{matchday}_{'_'.join(leg_ids)}"
    return hashlib.md5(raw.encode()).hexdigest()


def run_sentinel_scan_now() -> Dict[str, Any]:
    """Execute a single full sentinel analysis scan immediately."""
    from app.database import get_db
    from app.services.live_feed import fetch_live_upcoming_fixtures_espn
    from app.services.combo import generate_high_confidence_combos
    from app.services.whatsapp import send_combo_whatsapp_alert

    db = next(get_db())
    now_eat = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%a %d %b, %H:%M:%S EAT")
    _sentinel_state["last_scan_utc"] = datetime.now(timezone.utc).isoformat()
    _sentinel_state["last_scan_eat"] = now_eat
    _sentinel_state["scans_count"] = _sentinel_state.get("scans_count", 0) + 1

    try:
        _log_event("🔍 Syncing latest odds & fixtures across 35+ bookmaker leagues…")
        feed_res = fetch_live_upcoming_fixtures_espn(db)
        _sentinel_state["fixtures_scanned_last"] = feed_res.get("inserted", 0) + feed_res.get("updated", 0)
        _log_event(f"✅ Fixtures updated ({feed_res.get('inserted', 0)} new, {feed_res.get('updated', 0)} odds refreshed).")

        # Evaluate 2-team and 3-team banker multi-bets with STRICT rules:
        # ALL THREE must be satisfied or combo is skipped — no exceptions:
        #   • AVG TEAM CONFIDENCE   >= 85%
        #   • ACCUMULATOR WIN PROB  >= 85%
        #   • TOTAL ODDS            >= 2.00
        alerts_sent = 0
        phone = os.getenv("WHATSAPP_PHONE", "").strip()
        apikey = os.getenv("WHATSAPP_APIKEY", "").strip()
        provider = os.getenv("WHATSAPP_PROVIDER", "callmebot").strip()
        currency = os.getenv("FIE_CURRENCY", "UGX")

        for legs_target in [2, 3]:
            c_data = generate_high_confidence_combos(
                db=db,
                target_date=None,  # next active matchday
                num_legs=legs_target,
                min_leg_prob=0.85,
                min_accumulator_prob=0.85,  # enforce ACCUM WIN PROB >= 85%
                min_total_odds=2.00,
                bankroll=float(os.getenv("FIE_BANKROLL", "1000")),
            )
            best = c_data.get("best_combo")
            m_label = c_data.get("matchday_label", "Today")
            if not best:
                _log_event(f"ℹ️ No qualifying {legs_target}-team combo today (all 3 rules: AVG≥85%, ACCUM≥85%, Odds≥2.00).")
                continue

            avg_conf   = best.get("avg_leg_probability", 0) * 100
            accum_prob = best.get("combined_probability", 0) * 100
            odds_val   = best.get("total_odds", 0)

            # Double-check strict rules before alerting (belt-and-suspenders)
            if odds_val >= 2.00 and avg_conf >= 85.0 and accum_prob >= 85.0:
                sig = _combo_signature(best, m_label)
                notified = _sentinel_state.setdefault("notified_hashes", set())

                if sig not in notified:
                    _log_event(f"🎯 Qualifying {legs_target}-team Banker found! Odds: {odds_val:.2f} | Avg Conf: {avg_conf:.1f}% | Accum Win Prob: {accum_prob:.1f}%")
                    if phone and apikey:
                        send_res = send_combo_whatsapp_alert(
                            combo=best,
                            matchday_label=m_label,
                            phone=phone,
                            apikey=apikey,
                            currency=currency,
                            provider=provider,
                        )
                        if send_res.get("success"):
                            notified.add(sig)
                            _sentinel_state["alerts_dispatched_count"] += 1
                            _sentinel_state["last_alert_combo"] = best
                            _sentinel_state["last_alert_eat"] = now_eat
                            _save_persisted_state()
                            alerts_sent += 1
                            _log_event(f"📲 WhatsApp alert sent to {phone} for {m_label} ({odds_val:.2f} odds)")
                        else:
                            _log_event(f"⚠️ WhatsApp send failed: {send_res.get('error')}")
                    else:
                        _log_event(f"ℹ️ Ticket meets all conditions, but WhatsApp phone/key not configured in settings.")
                        _sentinel_state["last_alert_combo"] = best
                        _sentinel_state["last_alert_eat"] = now_eat
                else:
                    _log_event(f"ℹ️ {legs_target}-team slip ({odds_val:.2f} odds) already alerted previously. Skipped to prevent spam.")

        return {
            "success": True,
            "scan_time": now_eat,
            "alerts_sent": alerts_sent,
            "fixtures_scanned": _sentinel_state["fixtures_scanned_last"],
        }
    except Exception as e:
        _log_event(f"❌ Error during sentinel scan: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def _sentinel_loop(interval_seconds: int = 1800):
    """Continuous background loop running 24 hours a day."""
    _log_event(f"🚀 24/7 Autonomous Banker Sentinel started (Scanning every {interval_seconds//60} mins).")
    while not _sentinel_stop_event.is_set():
        try:
            run_sentinel_scan_now()
        except Exception as e:
            _log_event(f"Loop cycle error: {e}")

        # Sleep in small slices to respond promptly to stop event
        for _ in range(interval_seconds):
            if _sentinel_stop_event.is_set():
                break
            time.sleep(1)

    _sentinel_state["is_running"] = False
    _log_event("🛑 24/7 Autonomous Banker Sentinel stopped.")


def start_sentinel(interval_minutes: int = 30) -> bool:
    """Start 24/7 background sentinel daemon."""
    global _sentinel_thread
    if _sentinel_state.get("is_running") and _sentinel_thread and _sentinel_thread.is_alive():
        return True

    _sentinel_stop_event.clear()
    _sentinel_state["is_running"] = True
    _sentinel_state["started_at"] = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%a %d %b, %H:%M:%S EAT")
    _sentinel_thread = threading.Thread(
        target=_sentinel_loop,
        args=(max(60, interval_minutes * 60),),
        daemon=True,
        name="FIE_24_7_Sentinel"
    )
    _sentinel_thread.start()
    return True


def stop_sentinel() -> bool:
    """Stop the 24/7 background sentinel."""
    _sentinel_stop_event.set()
    _sentinel_state["is_running"] = False
    return True


def get_sentinel_status() -> Dict[str, Any]:
    """Return live status of the 24/7 background sentinel."""
    return {
        "is_running": _sentinel_state.get("is_running", False) and (_sentinel_thread is not None and _sentinel_thread.is_alive()),
        "started_at": _sentinel_state.get("started_at"),
        "last_scan_eat": _sentinel_state.get("last_scan_eat"),
        "scans_count": _sentinel_state.get("scans_count", 0),
        "fixtures_scanned_last": _sentinel_state.get("fixtures_scanned_last", 0),
        "alerts_dispatched_count": _sentinel_state.get("alerts_dispatched_count", 0),
        "last_alert_combo": _sentinel_state.get("last_alert_combo"),
        "last_alert_eat": _sentinel_state.get("last_alert_eat"),
        "recent_logs": _sentinel_state.get("recent_logs", []),
    }
