from __future__ import annotations
"""
settlement.py – Automated Bet Settlement & Transparent Performance Ledger.

Features:
  1. Records placed multi-bet banker slips (2-Team & 3-Team) into database.
  2. Syncs final match scores across all leagues via live ESPN scoreboard feeds.
  3. Evaluates every individual leg's market condition (1X2, DC, DNB, Goals, Asian Handicap).
  4. Automatically grades overall accumulator slips (WON / LOST / VOID).
  5. Calculates transparent PnL & ROI (dual UGX / USD currency).
  6. Dispatches celebratory or transparent outcome notifications to WhatsApp!
"""
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import requests
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.fixture import Fixture
from app.models.bet import TrackedBet
from app.services.currency import convert_amount, format_stake_dual
from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)
EAT_TZ = timezone(timedelta(hours=3), name="EAT")


def evaluate_leg_outcome(
    market_key: str,
    home_score: int,
    away_score: int,
) -> str:
    """Evaluate whether an individual market leg WON, LOST, or was VOID.

    Returns: 'WON', 'LOST', or 'VOID'.
    """
    total_goals = home_score + away_score

    if market_key == "home_win":
        return "WON" if home_score > away_score else "LOST"

    elif market_key == "away_win":
        return "WON" if away_score > home_score else "LOST"

    elif market_key == "draw":
        return "WON" if home_score == away_score else "LOST"

    elif market_key == "double_chance_1x":
        return "WON" if home_score >= away_score else "LOST"

    elif market_key == "double_chance_x2":
        return "WON" if away_score >= home_score else "LOST"

    elif market_key == "double_chance_12":
        return "WON" if home_score != away_score else "LOST"

    elif market_key == "home_over_0_5":
        return "WON" if home_score >= 1 else "LOST"

    elif market_key == "away_over_0_5":
        return "WON" if away_score >= 1 else "LOST"

    elif market_key == "over_1_5":
        return "WON" if total_goals >= 2 else "LOST"

    elif market_key == "under_3_5":
        return "WON" if total_goals <= 3 else "LOST"

    elif market_key == "dnb_home":
        if home_score > away_score:
            return "WON"
        elif home_score == away_score:
            return "VOID"
        return "LOST"

    elif market_key == "dnb_away":
        if away_score > home_score:
            return "WON"
        elif home_score == away_score:
            return "VOID"
        return "LOST"

    elif market_key == "ah_away_plus_1_5":
        return "WON" if (away_score + 1.5) > home_score else "LOST"

    # Default fallback based on positive margin
    return "LOST"


def record_tracked_bet(
    combo: Dict[str, Any],
    matchday_label: str,
    db: Session,
    currency: str = "UGX",
    bankroll: float = 1000.0,
) -> TrackedBet:
    """Save an approved high-confidence banker combo into the performance tracking ledger."""
    legs_count = combo.get("legs_count", len(combo.get("legs", [])))
    slip_type = f"{legs_count}-Team Combo"
    tot_odds = combo.get("total_odds", 2.00)
    avg_conf = combo.get("avg_leg_probability", 0.85) * 100
    acc_prob = combo.get("combined_probability", 0.85) * 100
    stake_amt = combo.get("kelly_stake_amount", 50.0)

    stake_ugx = stake_amt if currency.upper() == "UGX" else convert_amount(stake_amt, "USD", "UGX")
    stake_usd = stake_amt if currency.upper() == "USD" else convert_amount(stake_amt, "UGX", "USD")

    # Build initial legs structure
    legs_data = []
    for l in combo.get("legs", []):
        legs_data.append({
            "fixture_id": l.get("fixture_id"),
            "match": l.get("match"),
            "competition": l.get("competition"),
            "team": l.get("team"),
            "selection": l.get("selection"),
            "market_key": l.get("market_key"),
            "market_type": l.get("market_type"),
            "odds": l.get("odds"),
            "probability": l.get("probability"),
            "odds_source": l.get("odds_source", "market"),
            "kickoff_eat": l.get("kickoff_eat"),
            "status": "PENDING",
            "final_score": None,
        })

    tracked = TrackedBet(
        slip_type=slip_type,
        matchday_label=matchday_label,
        total_odds=tot_odds,
        avg_confidence=avg_conf,
        accum_probability=acc_prob,
        stake_amount=stake_ugx if currency.upper() == "UGX" else stake_usd,
        stake_currency=currency.upper(),
        stake_usd=stake_usd,
        status="PENDING",
        profit_loss=0.0,
        profit_loss_usd=0.0,
        legs_json=json.dumps(legs_data),
        whatsapp_alert_sent=True,
        whatsapp_settlement_sent=False,
    )
    db.add(tracked)
    db.commit()
    db.refresh(tracked)
    return tracked


def sync_finished_match_scores_from_espn(db: Session) -> int:
    """Fetch recent completed scores from ESPN to update fixtures."""
    from app.services.live_feed import COMPETITION_TARGETS

    now = datetime.now(timezone.utc)
    # Check yesterday and today
    dates_to_check = [
        (now - timedelta(days=1)).strftime("%Y%m%d"),
        now.strftime("%Y%m%d"),
    ]

    updated_fixtures = 0

    for comp_code, comp_name, _, _ in COMPETITION_TARGETS[:15]:  # prioritize primary leagues
        for d_str in dates_to_check:
            url = f"http://site.api.espn.com/apis/site/v2/sports/soccer/{comp_code}/scoreboard?dates={d_str}"
            try:
                res = requests.get(url, timeout=6)
                if res.status_code != 200:
                    continue
                data = res.json()
            except Exception:
                continue

            events = data.get("events", [])
            for event in events:
                comp_data = event.get("competitions", [{}])[0]
                status_state = comp_data.get("status", {}).get("type", {}).get("state", "")
                if status_state != "post":
                    continue  # not finished yet

                competitors = comp_data.get("competitors", [])
                if len(competitors) < 2:
                    continue

                home_c = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away_c = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not home_c or not away_c:
                    continue

                h_name = home_c["team"].get("displayName") or home_c["team"].get("name")
                a_name = away_c["team"].get("displayName") or away_c["team"].get("name")
                try:
                    h_score = int(home_c.get("score", 0))
                    a_score = int(away_c.get("score", 0))
                except Exception:
                    continue

                # Match against existing fixtures in DB
                fix = db.scalar(
                    select(Fixture)
                    .where(Fixture.home_team.ilike(f"%{h_name}%"))
                    .where(Fixture.away_team.ilike(f"%{a_name}%"))
                    .where(Fixture.status != "finished")
                )
                if fix:
                    fix.home_score = h_score
                    fix.away_score = a_score
                    fix.status = "finished"
                    db.commit()
                    updated_fixtures += 1

    return updated_fixtures


def settle_pending_bets(
    db: Session,
    phone: Optional[str] = None,
    apikey: Optional[str] = None,
    provider: str = "callmebot",
    currency: str = "UGX",
) -> List[TrackedBet]:
    """Scan and settle all pending bets whose match fixtures have finished."""
    pending_bets = db.scalars(
        select(TrackedBet).where(TrackedBet.status == "PENDING")
    ).all()

    if not pending_bets:
        return []

    # Sync latest scores
    sync_finished_match_scores_from_espn(db)

    settled_slips = []

    for bet in pending_bets:
        legs = json.loads(bet.legs_json)
        all_legs_finished = True
        has_lost_leg = False
        slip_legs_updated = []

        eff_odds = 1.0

        for leg in legs:
            fix_id = leg.get("fixture_id")
            fix = db.get(Fixture, fix_id) if fix_id else None

            # Fallback by match name if fixture_id lookup misses
            if not fix and leg.get("match"):
                parts = leg["match"].split(" vs ")
                if len(parts) == 2:
                    fix = db.scalar(
                        select(Fixture)
                        .where(Fixture.home_team == parts[0].strip())
                        .where(Fixture.away_team == parts[1].strip())
                    )

            if fix and fix.status == "finished" and fix.home_score is not None and fix.away_score is not None:
                leg_result = evaluate_leg_outcome(
                    market_key=leg.get("market_key", ""),
                    home_score=fix.home_score,
                    away_score=fix.away_score,
                )
                leg["status"] = leg_result
                leg["final_score"] = f"{fix.home_score} - {fix.away_score}"

                if leg_result == "LOST":
                    has_lost_leg = True
                elif leg_result == "WON":
                    eff_odds *= leg.get("odds", 1.0)
                elif leg_result == "VOID":
                    eff_odds *= 1.0
            else:
                # Leg is not yet finished
                all_legs_finished = False

            slip_legs_updated.append(leg)

        bet.legs_json = json.dumps(slip_legs_updated)

        # If any leg lost, the accumulator is lost immediately
        if has_lost_leg:
            bet.status = "LOST"
            bet.settled_at = datetime.now(timezone.utc)
            bet.profit_loss = -abs(bet.stake_amount)
            bet.profit_loss_usd = -abs(bet.stake_usd)
            db.commit()
            settled_slips.append(bet)

        elif all_legs_finished:
            # All legs won (or void)
            bet.status = "WON"
            bet.settled_at = datetime.now(timezone.utc)
            gross_payout = bet.stake_amount * eff_odds
            gross_payout_usd = bet.stake_usd * eff_odds
            bet.profit_loss = round(gross_payout - bet.stake_amount, 2)
            bet.profit_loss_usd = round(gross_payout_usd - bet.stake_usd, 2)
            db.commit()
            settled_slips.append(bet)

        # Dispatch settlement outcome to WhatsApp
        if bet.status in ("WON", "LOST") and not bet.whatsapp_settlement_sent:
            if phone and apikey:
                stats = get_performance_ledger_summary(db)
                msg = format_settlement_whatsapp_message(bet, stats, currency=currency)
                send_res = send_whatsapp_message(msg, phone=phone, apikey=apikey, provider=provider)
                if send_res.get("success"):
                    bet.whatsapp_settlement_sent = True
                    db.commit()

    return settled_slips


def get_performance_ledger_summary(db: Session) -> Dict[str, Any]:
    """Calculate overall performance metrics for the transparent betting ledger."""
    all_bets = db.scalars(
        select(TrackedBet).order_by(desc(TrackedBet.placed_at))
    ).all()

    total_slips = len(all_bets)
    settled = [b for b in all_bets if b.status in ("WON", "LOST")]
    pending = [b for b in all_bets if b.status == "PENDING"]
    won = [b for b in settled if b.status == "WON"]
    lost = [b for b in settled if b.status == "LOST"]

    win_count = len(won)
    settled_count = len(settled)
    win_rate = (win_count / settled_count * 100.0) if settled_count > 0 else 0.0

    total_profit_ugx = sum(b.profit_loss for b in settled if b.stake_currency == "UGX")
    total_staked_ugx = sum(b.stake_amount for b in settled if b.stake_currency == "UGX")
    total_profit_usd = sum(b.profit_loss_usd for b in settled)
    total_staked_usd = sum(b.stake_usd for b in settled)

    roi_pct = (total_profit_usd / total_staked_usd * 100.0) if total_staked_usd > 0 else 0.0

    return {
        "total_slips": total_slips,
        "settled_count": settled_count,
        "pending_count": len(pending),
        "won_count": win_count,
        "lost_count": len(lost),
        "win_rate": round(win_rate, 1),
        "total_profit_ugx": round(total_profit_ugx, 0),
        "total_profit_usd": round(total_profit_usd, 2),
        "roi_percent": round(roi_pct, 1),
        "recent_bets": all_bets[:25],
    }


def format_settlement_whatsapp_message(
    bet: TrackedBet,
    stats: Dict[str, Any],
    currency: str = "UGX",
) -> str:
    """Format an outcome alert message for WhatsApp when a slip finishes."""
    is_win = bet.status == "WON"
    status_icon = "✅ WON" if is_win else "❌ LOST"
    header = "🎉 *FIE SLIP SETTLEMENT RESULT*" if is_win else "📋 *FIE SLIP SETTLEMENT UPDATE*"

    pnl_str = format_stake_dual(bet.profit_loss, currency)
    stake_str = format_stake_dual(bet.stake_amount, currency)

    lines = [
        header,
        f"🏆 *{bet.slip_type}: {status_icon}*",
        f"📅 Matchday: *{bet.matchday_label}*",
        f"💰 Odds: *{bet.total_odds:.2f}* | Stake: *{stake_str}*",
        f"💵 Net Outcome: *{'+' if is_win else ''}{pnl_str}*",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "📋 *LEG RESULTS:*",
    ]

    legs = json.loads(bet.legs_json)
    for i, leg in enumerate(legs, 1):
        leg_st = leg.get("status", "PENDING")
        icon = "✅" if leg_st == "WON" else ("❌" if leg_st == "LOST" else "⏳")
        score_txt = f"[{leg.get('final_score')}]" if leg.get("final_score") else ""
        lines.append(f"{i}. {leg.get('match', '')} {score_txt}")
        lines.append(f"   👉 {leg.get('selection', '')} ➔ {icon} *{leg_st}*")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 *Transparent Ledger Stats:*",
        f"⭐ Win Rate: *{stats.get('win_rate', 0):.1f}%* ({stats.get('won_count', 0)}W / {stats.get('lost_count', 0)}L)",
        f"📈 Total PnL: *{'+' if stats.get('total_profit_usd', 0) >= 0 else ''}${stats.get('total_profit_usd', 0):.2f}* (ROI: *{stats.get('roi_percent', 0):+.1f}%*)",
        "",
        "⚡ _Football Intelligence Engine — 24/7 Verified Ledger_",
    ]

    return "\n".join(lines)
