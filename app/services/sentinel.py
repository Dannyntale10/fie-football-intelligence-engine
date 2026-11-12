from __future__ import annotations
"""
sentinel.py – 24/7 Autonomous Banker Sentinel & WhatsApp Alert Daemon.

STRICT RULES (all three must be satisfied simultaneously — zero fallbacks):
  1. AVG TEAM CONFIDENCE   >= 85%  (every individual leg >= 85% confidence)
  2. ACCUMULATOR WIN PROB  >= 85%  (joint probability of all selected legs)
  3. TOTAL ODDS            >= 2.00 (hard floor, never overridden)

Sentinel behaviour:
  • On each cycle: pulls ALL scheduled fixtures from ALL recognised leagues/divisions
    across all available upcoming matchdays (not just today).
  • For EACH matchday found: tries to find a qualifying 2-team AND 3-team combo.
  • If a qualifying combo is found on ANY matchday, sends a WhatsApp alert combining
    BOTH the 2-team and 3-team slips into a single message.
  • Already-alerted combos are deduped via MD5 hash — no spam.
  • Runs 24 hours a day on a configurable interval (default 30 mins).
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


def _format_dual_whatsapp_message(
    combo_2: Optional[Dict[str, Any]],
    combo_3: Optional[Dict[str, Any]],
    matchday_label: str,
    currency: str = "UGX",
) -> str:
    """Format a single WhatsApp message containing BOTH the 2-team and 3-team qualifying slips."""
    from app.services.currency import format_stake_dual

    eat_now = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%a %d %b %Y, %H:%M EAT")
    lines = [
        "🏆 *FIE FOOTBALL INTELLIGENCE ENGINE*",
        f"🎯 *QUALIFYING BANKER MULTI-BET ALERT*",
        f"📅 Matchday: *{matchday_label}*",
        f"🕐 Generated: {eat_now}",
        "",
        "✅ *ALL 3 RULES MET (No Exceptions):*",
        "  • AVG TEAM CONFIDENCE ≥ 85%",
        "  • ACCUMULATOR WIN PROB ≥ 85%",
        "  • TOTAL ODDS ≥ 2.00",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for label, combo in [("2-TEAM COMBO", combo_2), ("3-TEAM COMBO", combo_3)]:
        if not combo:
            continue
        avg_p  = combo.get("avg_leg_probability", 0) * 100
        acc_p  = combo.get("combined_probability", 0) * 100
        odds   = combo.get("total_odds", 0)
        ev     = combo.get("expected_value_percent", 0)
        stake  = format_stake_dual(combo.get("kelly_stake_amount", 0), currency)
        s_pct  = combo.get("kelly_stake_percent", 0)

        lines += [
            f"",
            f"🔥 *{label}*",
            f"💰 Odds: *{odds:.2f}* | ⭐ Avg Conf: *{avg_p:.1f}%* | 🎯 Win Prob: *{acc_p:.1f}%*",
            f"📈 EV: *{ev:+.1f}%* | 💵 Stake: *{stake} ({s_pct}%)*",
            f"",
        ]
        for i, leg in enumerate(combo.get("legs", []), 1):
            p_val = leg.get("probability", 0) * 100
            lines.append(
                f"  *Leg {i}:* {leg.get('match', '')} ({leg.get('competition', '')})"
            )
            lines.append(
                f"  👉 *{leg.get('selection', '')}* @ {leg.get('odds', 0):.2f} ({p_val:.1f}%) | ⏰ {leg.get('kickoff_eat', '')}"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    lines.append("")
    lines.append("⚡ _Football Intelligence Engine — 24/7 Banker Sentinel_")
    return "\n".join(lines)


def run_sentinel_scan_now() -> Dict[str, Any]:
    """
    Execute a full sentinel scan.

    Strategy:
    1. Refresh ALL fixtures from ALL recognised leagues across all upcoming matchdays.
    2. For EACH matchday: search for a qualifying 2-team AND 3-team combo
       (both must satisfy AVG CONF >= 85%, ACCUM PROB >= 85%, Odds >= 2.00).
    3. As soon as qualifying combos are found on any matchday, send ONE WhatsApp
       message containing both slips and stop searching further matchdays.
    4. Already-sent combos are skipped (dedup via MD5 hash).
    """
    from app.database import get_db
    from app.services.live_feed import fetch_live_upcoming_fixtures_espn
    from app.services.combo import generate_high_confidence_combos, get_available_matchdays
    from app.services.whatsapp import send_whatsapp_message

    db = next(get_db())
    now_eat = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%a %d %b, %H:%M:%S EAT")
    _sentinel_state["last_scan_utc"] = datetime.now(timezone.utc).isoformat()
    _sentinel_state["last_scan_eat"] = now_eat
    _sentinel_state["scans_count"] = _sentinel_state.get("scans_count", 0) + 1

    phone    = os.getenv("WHATSAPP_PHONE", "").strip()
    apikey   = os.getenv("WHATSAPP_APIKEY", "").strip()
    provider = os.getenv("WHATSAPP_PROVIDER", "callmebot").strip()
    currency = os.getenv("FIE_CURRENCY", "UGX")
    bankroll = float(os.getenv("FIE_BANKROLL", "1000"))

    alerts_sent = 0

    try:
        # ── Step 1: Refresh ALL fixtures from ALL leagues ─────────────────────
        _log_event("🔍 Pulling latest fixtures & odds from ALL recognised leagues/divisions…")
        feed_res = fetch_live_upcoming_fixtures_espn(db)
        total_updated = feed_res.get("inserted", 0) + feed_res.get("updated", 0)
        _sentinel_state["fixtures_scanned_last"] = total_updated
        _log_event(f"✅ Fixture sync complete: {feed_res.get('inserted', 0)} new | {feed_res.get('updated', 0)} odds refreshed across all leagues.")

        # ── Step 2: Get ALL available matchdays (not just today) ──────────────
        all_matchdays = get_available_matchdays(db)
        if not all_matchdays:
            _log_event("⚠️ No upcoming scheduled fixtures found in any league. Will retry next cycle.")
            return {"success": True, "scan_time": now_eat, "alerts_sent": 0, "fixtures_scanned": total_updated}

        _log_event(f"📅 Scanning {len(all_matchdays)} matchday(s) across all leagues for qualifying combos…")

        notified = _sentinel_state.setdefault("notified_hashes", set())

        # ── Step 3: Search every matchday until we find qualifying combos ─────
        for matchday in all_matchdays:
            m_date  = matchday["date"]
            m_label = matchday["label"]
            m_count = matchday.get("count", 0)

            _log_event(f"🔎 Checking {m_label} ({m_count} fixtures)…")

            found_2: Optional[Dict[str, Any]] = None
            found_3: Optional[Dict[str, Any]] = None

            # Search for 2-team combo on this matchday
            res2 = generate_high_confidence_combos(
                db=db,
                target_date=m_date,
                num_legs=2,
                min_leg_prob=0.85,
                min_accumulator_prob=0.85,  # HARD RULE: ACCUM WIN PROB >= 85%
                min_total_odds=2.00,         # HARD RULE: Odds >= 2.00
                bankroll=bankroll,
            )
            best2 = res2.get("best_combo")
            if best2:
                avg2   = best2.get("avg_leg_probability", 0) * 100
                acc2   = best2.get("combined_probability", 0) * 100
                odds2  = best2.get("total_odds", 0)
                # Belt-and-suspenders final check
                if odds2 >= 2.00 and avg2 >= 85.0 and acc2 >= 85.0:
                    found_2 = best2
                    _log_event(f"  ✅ Qualifying 2-team found: Odds {odds2:.2f} | AvgConf {avg2:.1f}% | AccumProb {acc2:.1f}%")
                else:
                    _log_event(f"  ⛔ 2-team rejected (Odds {odds2:.2f}, AvgConf {avg2:.1f}%, Accum {acc2:.1f}%) — not all >= threshold.")

            # Search for 3-team combo on this matchday
            res3 = generate_high_confidence_combos(
                db=db,
                target_date=m_date,
                num_legs=3,
                min_leg_prob=0.85,
                min_accumulator_prob=0.85,  # HARD RULE: ACCUM WIN PROB >= 85%
                min_total_odds=2.00,         # HARD RULE: Odds >= 2.00
                bankroll=bankroll,
            )
            best3 = res3.get("best_combo")
            if best3:
                avg3   = best3.get("avg_leg_probability", 0) * 100
                acc3   = best3.get("combined_probability", 0) * 100
                odds3  = best3.get("total_odds", 0)
                if odds3 >= 2.00 and avg3 >= 85.0 and acc3 >= 85.0:
                    found_3 = best3
                    _log_event(f"  ✅ Qualifying 3-team found: Odds {odds3:.2f} | AvgConf {avg3:.1f}% | AccumProb {acc3:.1f}%")
                else:
                    _log_event(f"  ⛔ 3-team rejected (Odds {odds3:.2f}, AvgConf {avg3:.1f}%, Accum {acc3:.1f}%) — not all >= threshold.")

            # Only alert if we have AT LEAST ONE qualifying combo (2 or 3-team)
            # Prefer days where BOTH are found — keep searching if neither found
            if not found_2 and not found_3:
                _log_event(f"  ↩️  No qualifying combo on {m_label}. Checking next matchday…")
                continue

            # Build a combined signature for dedup (covers whichever combos we found)
            sig_parts = []
            if found_2:
                sig_parts.append(_combo_signature(found_2, m_label))
            if found_3:
                sig_parts.append(_combo_signature(found_3, m_label))
            combined_sig = hashlib.md5("_".join(sorted(sig_parts)).encode()).hexdigest()

            if combined_sig in notified:
                _log_event(f"  ℹ️ Combos on {m_label} already alerted. Skipping duplicate.")
                continue

            # ── Step 4: Send combined WhatsApp alert ─────────────────────────
            msg = _format_dual_whatsapp_message(found_2, found_3, m_label, currency)

            teams_found = []
            if found_2: teams_found.append("2-team")
            if found_3: teams_found.append("3-team")
            combo_summary = " + ".join(teams_found)
            _log_event(f"🎯 QUALIFYING {combo_summary} combo(s) found on {m_label}! Dispatching WhatsApp alert…")

            if phone and apikey:
                from app.services.whatsapp import send_whatsapp_message as _send_wa
                send_res = _send_wa(msg, phone=phone, apikey=apikey, provider=provider)
                if send_res.get("success"):
                    notified.add(combined_sig)
                    _sentinel_state["alerts_dispatched_count"] += 1
                    _sentinel_state["last_alert_combo"] = found_3 or found_2
                    _sentinel_state["last_alert_eat"] = now_eat
                    _save_persisted_state()
                    alerts_sent += 1
                    _log_event(f"📲 WhatsApp alert sent to +{phone} — {combo_summary} slip(s) for {m_label}.")
                else:
                    _log_event(f"⚠️ WhatsApp send failed: {send_res.get('error')}. Will retry next cycle.")
            else:
                # Save the combo even if WhatsApp not configured, so user can see it in the UI
                notified.add(combined_sig)
                _sentinel_state["last_alert_combo"] = found_3 or found_2
                _sentinel_state["last_alert_eat"] = now_eat
                _log_event(f"ℹ️ Qualifying slip found ({combo_summary}) but WhatsApp not configured. Set WHATSAPP_PHONE + WHATSAPP_APIKEY to receive alerts.")

            # Stop searching further matchdays — found qualifying combo(s)
            break

        return {
            "success": True,
            "scan_time": now_eat,
            "alerts_sent": alerts_sent,
            "fixtures_scanned": total_updated,
            "matchdays_checked": len(all_matchdays),
        }

    except Exception as e:
        _log_event(f"❌ Error during sentinel scan: {e}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def _sentinel_loop(interval_seconds: int = 1800):
    """Continuous background loop running 24 hours a day."""
    _log_event(f"🚀 24/7 Autonomous Banker Sentinel started — scanning ALL leagues every {interval_seconds//60} mins.")
    _log_event("📋 Strict rules: AVG CONF >= 85% AND ACCUM WIN PROB >= 85% AND Odds >= 2.00 — no fallbacks.")
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
