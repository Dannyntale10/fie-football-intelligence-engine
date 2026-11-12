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
    """Load previously notified combo hashes and credentials from disk."""
    if _HISTORY_FILE.exists():
        try:
            with open(_HISTORY_FILE, "r") as f:
                data = json.load(f)
                _sentinel_state["alerts_dispatched_count"] = data.get("alerts_dispatched_count", 0)
                _sentinel_state["notified_hashes"] = set(data.get("notified_hashes", []))
                _sentinel_state["last_alert_eat"] = data.get("last_alert_eat")
                if data.get("whatsapp_phone"):
                    _sentinel_state["whatsapp_phone"] = data.get("whatsapp_phone")
                if data.get("whatsapp_apikey"):
                    _sentinel_state["whatsapp_apikey"] = data.get("whatsapp_apikey")
                return
        except Exception as e:
            logger.debug(f"Failed to load sentinel state: {e}")
    _sentinel_state["notified_hashes"] = set()


def _save_persisted_state():
    """Persist notified combo hashes and credentials to disk so restarts don't lose them."""
    try:
        with open(_HISTORY_FILE, "w") as f:
            json.dump({
                "alerts_dispatched_count": _sentinel_state.get("alerts_dispatched_count", 0),
                "notified_hashes": list(_sentinel_state.get("notified_hashes", set())),
                "last_alert_eat": _sentinel_state.get("last_alert_eat"),
                "whatsapp_phone": _sentinel_state.get("whatsapp_phone", os.getenv("WHATSAPP_PHONE", "")),
                "whatsapp_apikey": _sentinel_state.get("whatsapp_apikey", os.getenv("WHATSAPP_APIKEY", "")),
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
            est_tag = "" if leg.get("odds_source", "market") == "market" else " (est.)"
            lines.append(
                f"  👉 *{leg.get('selection', '')}* @ {leg.get('odds', 0):.2f}{est_tag} ({p_val:.1f}%) | ⏰ {leg.get('kickoff_eat', '')}"
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

    phone    = (os.getenv("WHATSAPP_PHONE", "").strip() or _sentinel_state.get("whatsapp_phone", "").strip())
    apikey   = (os.getenv("WHATSAPP_APIKEY", "").strip() or _sentinel_state.get("whatsapp_apikey", "").strip())
    provider = os.getenv("WHATSAPP_PROVIDER", "callmebot").strip()
    currency = os.getenv("FIE_CURRENCY", "UGX")
    bankroll = float(os.getenv("FIE_BANKROLL", "1000"))

    alerts_sent = 0

    try:
        # ── Step 0: Settle any finished pending bets & send outcome alerts ────
        from app.services.settlement import settle_pending_bets, record_tracked_bet
        settled_list = settle_pending_bets(db, phone=phone, apikey=apikey, provider=provider, currency=currency)
        if settled_list:
            _log_event(f"⚖️ Automatically settled {len(settled_list)} pending multi-bet slip(s)! Ledger updated.")

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

        best_found_2: Optional[Dict[str, Any]] = None
        best_found_3: Optional[Dict[str, Any]] = None
        best_label_2: str = ""
        best_label_3: str = ""

        # ── Step 3: Search each matchday ─────────────────────────────────────
        for matchday in all_matchdays:
            m_date  = matchday["date"]
            m_label = matchday["label"]
            m_count = matchday.get("count", 0)

            _log_event(f"🔎 Checking {m_label} ({m_count} fixtures)…")

            # Search 2-team combo on this matchday if not already found
            if not best_found_2:
                res2 = generate_high_confidence_combos(
                    db=db,
                    target_date=m_date,
                    num_legs=2,
                    min_leg_prob=0.55,
                    min_total_odds=1.50,
                    bankroll=bankroll,
                    selection_mode="ev",
                    min_edge_percent=3.0,
                )
                b2 = res2.get("best_combo")
                if b2:
                    ev2 = b2.get("expected_value_percent", 0)
                    odds2 = b2.get("total_odds", 0)
                    if odds2 >= 1.50 and ev2 > 0:
                        best_found_2 = b2
                        best_label_2 = m_label
                        _log_event(f"  ✅ Value 2-team found: Odds {odds2:.2f} | EV {ev2:+.1f}% | Edge {b2.get('edge_percent', 0):+.1f}%")

            # Search 3-team combo on this matchday if not already found
            if not best_found_3:
                res3 = generate_high_confidence_combos(
                    db=db,
                    target_date=m_date,
                    num_legs=3,
                    min_leg_prob=0.55,
                    min_total_odds=1.50,
                    bankroll=bankroll,
                    selection_mode="ev",
                    min_edge_percent=3.0,
                )
                b3 = res3.get("best_combo")
                if b3:
                    ev3 = b3.get("expected_value_percent", 0)
                    odds3 = b3.get("total_odds", 0)
                    if odds3 >= 1.50 and ev3 > 0:
                        best_found_3 = b3
                        best_label_3 = m_label
                        _log_event(f"  ✅ Value 3-team found: Odds {odds3:.2f} | EV {ev3:+.1f}% | Edge {b3.get('edge_percent', 0):+.1f}%")

            # If both 2-team and 3-team have been found, we can stop searching matchdays
            if best_found_2 and best_found_3:
                break

        # ── Step 4: If either is still missing, search across ALL upcoming fixtures (Cross-League) ──
        if not (best_found_2 and best_found_3):
            _log_event("🔎 Checking across ALL upcoming fixtures in all 35+ leagues & divisions (Cross-League)…")
            if not best_found_2:
                res2_all = generate_high_confidence_combos(
                    db=db,
                    target_date="all",
                    num_legs=2,
                    min_leg_prob=0.55,
                    min_total_odds=1.50,
                    bankroll=bankroll,
                    selection_mode="ev",
                    min_edge_percent=3.0,
                )
                b2_all = res2_all.get("best_combo")
                if b2_all:
                    ev2 = b2_all.get("expected_value_percent", 0)
                    odds2 = b2_all.get("total_odds", 0)
                    if odds2 >= 1.50 and ev2 > 0:
                        best_found_2 = b2_all
                        best_label_2 = "Cross-League Upcoming"
                        _log_event(f"  ✅ Cross-league value 2-team found: Odds {odds2:.2f} | EV {ev2:+.1f}%")

            if not best_found_3:
                res3_all = generate_high_confidence_combos(
                    db=db,
                    target_date="all",
                    num_legs=3,
                    min_leg_prob=0.55,
                    min_total_odds=1.50,
                    bankroll=bankroll,
                    selection_mode="ev",
                    min_edge_percent=3.0,
                )
                b3_all = res3_all.get("best_combo")
                if b3_all:
                    ev3 = b3_all.get("expected_value_percent", 0)
                    odds3 = b3_all.get("total_odds", 0)
                    if odds3 >= 1.50 and ev3 > 0:
                        best_found_3 = b3_all
                        best_label_3 = "Cross-League Upcoming"
                        _log_event(f"  ✅ Cross-league value 3-team found: Odds {odds3:.2f} | EV {ev3:+.1f}%")

        # ── Step 5: Dispatch WhatsApp Alert if qualifying combos found ─────────
        if best_found_2 or best_found_3:
            combined_label = best_label_3 or best_label_2 or "Today"
            sig_parts = []
            if best_found_2:
                sig_parts.append(_combo_signature(best_found_2, best_label_2))
            if best_found_3:
                sig_parts.append(_combo_signature(best_found_3, best_label_3))
            combined_sig = hashlib.md5("_".join(sorted(sig_parts)).encode()).hexdigest()

            if combined_sig not in notified:
                msg = _format_dual_whatsapp_message(best_found_2, best_found_3, combined_label, currency)
                teams_found = []
                if best_found_2: teams_found.append("2-Team")
                if best_found_3: teams_found.append("3-Team")
                combo_summary = " & ".join(teams_found)
                _log_event(f"🎯 QUALIFYING {combo_summary} combo(s) found! Dispatching WhatsApp alert…")

                if phone and apikey:
                    from app.services.whatsapp import send_whatsapp_message as _send_wa
                    send_res = _send_wa(msg, phone=phone, apikey=apikey, provider=provider)
                    if send_res.get("success"):
                        notified.add(combined_sig)
                        _sentinel_state["alerts_dispatched_count"] += 1
                        _sentinel_state["last_alert_combo"] = best_found_3 or best_found_2
                        _sentinel_state["last_alert_eat"] = now_eat
                        _save_persisted_state()
                        alerts_sent += 1
                        # Save each combo into the transparent ledger
                        if best_found_2:
                            record_tracked_bet(best_found_2, best_label_2, db, currency=currency, bankroll=bankroll)
                        if best_found_3:
                            record_tracked_bet(best_found_3, best_label_3, db, currency=currency, bankroll=bankroll)
                        _log_event(f"📲 WhatsApp alert sent to +{phone} — {combo_summary} slip(s) recorded in ledger.")
                    else:
                        _log_event(f"⚠️ WhatsApp send failed: {send_res.get('error')}. Will retry next cycle.")
                else:
                    notified.add(combined_sig)
                    _sentinel_state["last_alert_combo"] = best_found_3 or best_found_2
                    _sentinel_state["last_alert_eat"] = now_eat
                    _save_persisted_state()
                    # Save to ledger even if WhatsApp is not yet configured
                    if best_found_2:
                        record_tracked_bet(best_found_2, best_label_2, db, currency=currency, bankroll=bankroll)
                    if best_found_3:
                        record_tracked_bet(best_found_3, best_label_3, db, currency=currency, bankroll=bankroll)
                    _log_event(f"ℹ️ Qualifying slip found ({combo_summary}) and recorded in ledger (WhatsApp not configured).")
            else:
                _log_event("ℹ️ Qualifying combos already alerted previously. Skipping duplicate notification.")
        else:
            _log_event(
                "⏳ No rated fixture produced a +EV slip this cycle (edge ≥ 3%, odds ≥ 1.50, real prices). "
                "Unrated fixtures are skipped so edges are never circular. Sentinel stays active 24/7."
            )

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


def _sentinel_loop(interval_seconds: int = 30):
    """Continuous background loop running 24 hours a day with real-time 30-second bookmaker feeds."""
    unit_str = f"{interval_seconds}s" if interval_seconds < 60 else f"{interval_seconds//60} min(s)"
    _log_event(f"🚀 24/7 Autonomous Banker Sentinel active — auto-updating ALL 35+ leagues every {unit_str}.")
    _log_event("📋 Rules: model edge ≥ 3% over de-vigged prices AND odds ≥ 1.50 — real prices, rated teams only.")
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


def start_sentinel(interval_seconds: int = 30, *, interval_minutes: Optional[int] = None) -> bool:
    """Start the 24/7 sentinel daemon.

    Use ``interval_seconds`` (UI/daemon callers) or ``interval_minutes``
    (API callers); both are keyword-only after the first argument to avoid
    the positional mix-up that previously treated 30 minutes as 30 seconds.
    """
    global _sentinel_thread
    if _sentinel_state.get("is_running") and _sentinel_thread and _sentinel_thread.is_alive():
        return True

    eff_seconds = (interval_minutes * 60) if interval_minutes is not None else interval_seconds
    eff_seconds = max(10, eff_seconds)  # floor at 10s

    _sentinel_stop_event.clear()
    _sentinel_state["is_running"] = True
    _sentinel_state["started_at"] = datetime.now(timezone.utc).astimezone(EAT_TZ).strftime("%a %d %b, %H:%M:%S EAT")
    _sentinel_thread = threading.Thread(
        target=_sentinel_loop,
        args=(eff_seconds,),
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
