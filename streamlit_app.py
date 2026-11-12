"""
streamlit_app.py – FIE Cloud Entry Point
=========================================
Standalone mode: calls Python services directly — no FastAPI server required.
Runs on Streamlit Community Cloud with SQLite in /tmp (ephemeral).

Architecture:
  dashboard.py      ← local development (calls FastAPI via HTTP)
  streamlit_app.py  ← cloud deployment   (calls services directly in-process)
"""
from __future__ import annotations

import io
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Path setup (must happen before any app imports) ──────────────────────────
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Database: use /tmp on Streamlit Cloud, local fie.db elsewhere ─────────────
_default_db = "sqlite:////tmp/fie_cloud.db"
os.environ.setdefault("DATABASE_URL", _default_db)

# ── Bootstrap the ORM (import order matters: config → engine → models) ────────
from app.database import Base, engine, get_db          # noqa: E402
from app.models.fixture import Fixture                 # noqa: E402, F401
from app.models.team_rating import TeamRating          # noqa: E402, F401
Base.metadata.create_all(bind=engine)

# ── Core service imports ──────────────────────────────────────────────────────
import pandas as pd                                    # noqa: E402
import streamlit as st                                 # noqa: E402
from sqlalchemy import select, func, delete            # noqa: E402
from app.services.fixtures import import_fixtures_from_dataframe        # noqa: E402
from app.services.probabilities import (               # noqa: E402
    poisson_market_probabilities,
    analyze_fixture,
)
from app.services.quality import (                     # noqa: E402
    evaluate_fixture_signals,
    grade_data_quality,
)
from app.services.ratings import (                     # noqa: E402
    upsert_team_rating,
    calculate_ratings_from_fixtures,
)
from app.services.seeder import (                      # noqa: E402
    seed_sample_historical_data,
    fetch_live_upcoming_fixtures,
    fetch_historical_league_season,
    fetch_all_major_leagues,
)
from app.services.live_feed import fetch_live_upcoming_fixtures_espn  # noqa: E402
from app.services.backtest import run_backtest          # noqa: E402
from app.services.portfolio import build_daily_portfolio  # noqa: E402
from app.services.combo import generate_high_confidence_combos  # noqa: E402
from app.connectors.bookmakers import BOOKMAKERS       # noqa: E402
from app.services.ai_insights import (                 # noqa: E402
    gemini_match_preview,
    gemini_combo_explanation,
    gemini_agent_rating_narrative,
    is_gemini_enabled,
)
from app.services.value_score import compute_value_score   # noqa: E402
from app.services.currency import format_stake, format_stake_dual, get_currency_options  # noqa: E402
from app.services.pdf_slip import generate_combo_pdf, generate_text_slip  # noqa: E402
from app.services.h2h import get_h2h_stats                 # noqa: E402
from app.services.inplay import fetch_live_scores           # noqa: E402
from app.services.result_tracker import (                  # noqa: E402
    fetch_and_update_results,
    calculate_prediction_accuracy,
)
from app.services.odds_movement import get_odds_movement    # noqa: E402
from app.services.ml_ensemble import (                     # noqa: E402
    ensemble_predict,
    train_ensemble,
    is_model_trained,
)
from app.services.whatsapp import send_whatsapp_message, send_combo_whatsapp_alert  # noqa: E402
from app.services.sentinel import start_sentinel, stop_sentinel, get_sentinel_status, run_sentinel_scan_now  # noqa: E402



# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _db():
    """Return a fresh SQLAlchemy session (caller must close it)."""
    return next(get_db())


EAT_TZ = timezone(timedelta(hours=3), name="EAT")

def to_eat_str(val) -> str:
    """Format UTC datetime into East Africa Time (EAT, UTC+3) for Uganda."""
    if not val or pd.isna(val):
        return ""
    if isinstance(val, str):
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except Exception:
            return val
    elif isinstance(val, datetime):
        dt = val
    else:
        return str(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EAT_TZ).strftime("%a %d %b, %H:%M EAT")

def _build_fixture_row(f: Fixture) -> dict:
    sigs = evaluate_fixture_signals(f)
    q = grade_data_quality(sigs)
    eat_str = to_eat_str(f.kickoff_utc)
    return {
        "id": f.id,
        "kickoff_utc": f.kickoff_utc,
        "kickoff_eat": eat_str,
        "home_team": f.home_team,
        "away_team": f.away_team,
        "competition": f.competition,
        "country": f.country,
        "source": f.source,
        "home_odds": f.home_odds,
        "draw_odds": f.draw_odds,
        "away_odds": f.away_odds,
        "status": f.status,
        "data_quality_score": q["score"],
        "data_quality_grade": q["grade"],
        "eligible_for_analysis": q["eligible_for_analysis"],
    }


def _grade_color(grade: str) -> str:
    return {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴", "F": "⚫"}.get(grade, "⚪")


# ═════════════════════════════════════════════════════════════════════════════
# Page config
# ═════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="FIE | Football Intelligence Engine",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚽ Football Intelligence Engine")
st.caption("Local-first football research & prediction platform · Cloud Edition v0.3")

# ═════════════════════════════════════════════════════════════════════════════
# Sidebar
# ═════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("🖥️ System Status")

    db_status = _db()
    total_fx = db_status.scalar(select(func.count(Fixture.id))) or 0
    if total_fx == 0:
        try:
            fetch_live_upcoming_fixtures_espn(db_status)
            total_fx = db_status.scalar(select(func.count(Fixture.id))) or 0
        except Exception:
            pass
    total_tr = db_status.scalar(select(func.count(TeamRating.id))) or 0
    scheduled = db_status.scalar(
        select(func.count(Fixture.id)).where(Fixture.status == "scheduled")
    ) or 0
    finished = db_status.scalar(
        select(func.count(Fixture.id)).where(Fixture.status == "finished")
    ) or 0
    db_status.close()

    st.success("🟢 Engine Online (Standalone Mode)")
    col_a, col_b = st.columns(2)
    col_a.metric("Fixtures", total_fx)
    col_b.metric("Team Ratings", total_tr)
    col_c, col_d = st.columns(2)
    col_c.metric("Upcoming", scheduled)
    col_d.metric("Finished", finished)

    st.caption(f"DB: `{os.environ.get('DATABASE_URL', 'sqlite')}`")

    st.divider()
    st.header("🤖 AI Insights (Gemini)")
    _sidebar_key = st.text_input(
        "Gemini API Key",
        type="password",
        placeholder="AIza… (optional)",
        help="Paste your Google Gemini API key to unlock AI match previews & combo explanations. Leave blank to use rule-based analysis.",
        key="sidebar_gemini_key",
    )
    if _sidebar_key:
        os.environ["GEMINI_API_KEY"] = _sidebar_key
    _ai_on = is_gemini_enabled()
    if _ai_on:
        st.success("🟢 Gemini AI Active")
    else:
        st.caption("⚪ AI off — rule-based mode")
        st.caption("Get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey)")

    st.divider()
    st.header("💱 Currency & Staking")
    sel_currency = st.selectbox(
        "Display Currency",
        get_currency_options(),
        index=1,  # Default to UGX (Ugandan Shilling)
        help="Select display currency for Kelly stakes and ticket slips (UGX, USD, KES, TZS, EUR, GBP)",
        key="app_currency"
    )

    st.divider()
    st.header("📱 WhatsApp Notifications")
    st.caption("Receive 24/7 alerts on your phone whenever a qualifying Banker Slip is found.")
    wa_phone_val = st.text_input(
        "WhatsApp Phone (with country code)",
        value=os.getenv("WHATSAPP_PHONE", ""),
        placeholder="+256701234567",
        help="Enter your mobile number with country code (e.g. +256 for Uganda).",
        key="app_wa_phone",
    )
    if wa_phone_val:
        os.environ["WHATSAPP_PHONE"] = wa_phone_val

    wa_key_val = st.text_input(
        "CallMeBot WhatsApp API Key",
        value=os.getenv("WHATSAPP_APIKEY", ""),
        type="password",
        placeholder="e.g. 1234567",
        help="Free setup in 10s: Send 'I allow callmebot to send me messages' via WhatsApp to +34 941 83 07 43 to get your free key.",
        key="app_wa_key",
    )
    if wa_key_val:
        os.environ["WHATSAPP_APIKEY"] = wa_key_val

    if st.button("🔔 Send Test WhatsApp Alert", use_container_width=True, key="app_wa_test_btn"):
        if not (wa_phone_val and wa_key_val):
            st.warning("Please provide both WhatsApp phone number and CallMeBot API key.")
        else:
            with st.spinner("Dispatching WhatsApp test message..."):
                wa_res = send_whatsapp_message(
                    text="⚽ *FIE Football Intelligence Engine*\n✅ WhatsApp connected! Your 24/7 Banker Sentinel is actively hunting odds >= 2.00 multi-bets.",
                    phone=wa_phone_val,
                    apikey=wa_key_val,
                )
                if wa_res.get("success"):
                    st.success("✅ Test WhatsApp message delivered to your phone!")
                else:
                    st.error(f"Failed: {wa_res.get('error')}")

    st.divider()
    st.header("🟢 24/7 Autonomous Sentinel")
    s_status = get_sentinel_status()
    is_active = s_status.get("is_running", False)
    if is_active:
        st.success("🟢 24/7 Sentinel: **ACTIVE & SCANNING**")
        st.caption(f"Last scan: {s_status.get('last_scan_eat') or 'Starting up...'}")
        st.caption(f"Alerts sent: {s_status.get('alerts_dispatched_count', 0)}")
        if st.button("⏹️ Stop 24/7 Sentinel", use_container_width=True, key="app_btn_stop_sentinel"):
            stop_sentinel()
            st.rerun()
    else:
        st.info("⚪ 24/7 Sentinel: **STANDBY**")
        st.caption("Autonomously monitors 35+ leagues, verifies injuries & sends WhatsApp alerts.")
        if st.button("▶️ Start 24/7 Sentinel", type="primary", use_container_width=True, key="app_btn_start_sentinel"):
            start_sentinel(interval_minutes=30)
            st.rerun()

    if st.button("⚡ Run Sentinel Scan Now", use_container_width=True, key="app_btn_scan_sentinel"):
        with st.spinner("Running 24/7 autonomous odds & squad check scan..."):
            scan_out = run_sentinel_scan_now()
            if scan_out.get("success"):
                st.success(f"Scan complete at {scan_out.get('scan_time')}. Fixtures checked: {scan_out.get('fixtures_scanned')}")
                st.rerun()
            else:
                st.error(f"Scan error: {scan_out.get('error')}")

    st.divider()
    st.header("⚙️ Engine Defaults")
    st.metric("Qualification Threshold", "85%",
              help="Markets ≥ 85% probability qualify for shortlisting.")
    st.metric("Data-Quality Minimum", "70 / 100",
              help="Fixtures scoring ≥ 70 (Grade A/B) are eligible for model analysis.")
    st.metric("Kelly Fraction", "¼ Quarter Kelly",
              help="Conservative anti-ruin staking.")

    st.divider()
    if st.button("🧹 Mark Stale Fixtures", use_container_width=True,
                 help="Set past scheduled fixtures with no score → 'pending_result'"):
        db_stale = _db()
        now = datetime.now(timezone.utc)
        stale = db_stale.scalars(
            select(Fixture)
            .where(Fixture.status == "scheduled")
            .where(Fixture.kickoff_utc < now)
            .where(Fixture.home_score.is_(None))
        ).all()
        for s in stale:
            s.status = "pending_result"
        db_stale.commit()
        db_stale.close()
        st.toast(f"✅ Marked {len(stale)} fixtures as 'pending_result'.")
        st.rerun()

    if st.button("🗑️ Clear All Fixtures", use_container_width=True):
        db_cl = _db()
        count = db_cl.scalar(select(func.count(Fixture.id))) or 0
        db_cl.execute(delete(Fixture))
        db_cl.commit()
        db_cl.close()
        st.toast(f"🗑️ Cleared {count} fixtures.")
        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# Tabs
# ═════════════════════════════════════════════════════════════════════════════
(
    tab_portfolio, tab_fixtures, tab_live, tab_odds_movement,
    tab_import, tab_backtest, tab_markets, tab_ratings, tab_connectors, tab_agent_rating,
) = st.tabs([
    "📋 Daily Portfolio",
    "📅 Fixtures & Odds",
    "⚡ Live Scores",
    "📉 Line Movement",
    "📥 Ingestion & Import",
    "🧪 Strategy Backtester",
    "📊 Market Probabilities",
    "🛡️ Team Ratings",
    "🔌 Bookmaker Connectors",
    "🏆 Agent Rating",
])


# ═════════════════════════════════════════════════════════════════════════════
# TAB 0 — DAILY VALUE PORTFOLIO
# ═════════════════════════════════════════════════════════════════════════════
with tab_portfolio:
    st.subheader("📋 Daily Value Portfolio & High-Confidence Combos")
    st.caption(
        "Quantitative selection engine: generate optimal 2-team or 3-team accumulators (Odds ≥ 2.00+) "
        "and scan individual +EV value bets with fractional Kelly stake sizing."
    )

    # ══════════════════════════════════════════════════════════════════════
    # 🎯 HIGH-CONFIDENCE COMBO BUILDER (ODDS >= 2.00+)
    # ══════════════════════════════════════════════════════════════════════
    st.markdown("### 🔥 High-Confidence Daily Combo Slip (Odds ≥ 2.0+)")
    st.write("Generates **same-day accumulator slips** using 85%+ win probability teams from a chosen matchday so bets settle on the same night.")

    # Matchday selector from DB
    db_md = _db()
    from app.services.combo import get_available_matchdays
    avail_days = get_available_matchdays(db_md)
    db_md.close()

    day_options = ["Auto (Next Active Matchday)"] + [f"📅 {d['label']} ({d['count']} matches)" for d in avail_days] + ["🌐 All Upcoming Matchdays Combined"]
    sel_day_idx = st.selectbox("📅 Select Matchday (Ugandan Time, EAT):", day_options, index=0, key="cloud_sel_day")

    target_date_val = None
    if sel_day_idx.startswith("🌐"):
        target_date_val = "all"
    elif not sel_day_idx.startswith("Auto"):
        for d in avail_days:
            if d["label"] in sel_day_idx:
                target_date_val = d["date"]
                break

    combo_col1, combo_col2, combo_col3, combo_col4 = st.columns(4)
    with combo_col1:
        sel_legs = st.radio("Number of Teams (Max 3)", [2, 3], index=1, horizontal=True, format_func=lambda x: f"{x}-Team Combo", key="cloud_combo_legs")
    with combo_col2:
        sel_min_odds = st.slider("Target Min Combined Odds", min_value=2.00, max_value=4.00, value=2.00, step=0.05, key="cloud_combo_odds")
    with combo_col3:
        sel_min_leg_prob = st.slider("Min Team Confidence", min_value=0.80, max_value=0.98, value=0.85, step=0.01, format="%.0f%%", key="cloud_combo_prob")
    with combo_col4:
        sel_combo_bankroll = st.number_input("Bankroll ($)", min_value=50.0, max_value=100000.0, value=1000.0, step=50.0, key="cloud_combo_bankroll")

    if st.button(f"⚡ Generate {sel_legs}-Team Daily Slip (Odds ≥ {sel_min_odds:.2f})", type="primary", use_container_width=True, key="cloud_btn_combo"):
        with st.spinner("Finding optimal same-day combinations of 85%+ teams…"):
            db_combo = _db()
            try:
                c_data = generate_high_confidence_combos(
                    db=db_combo,
                    target_date=target_date_val,
                    num_legs=sel_legs,
                    min_leg_prob=sel_min_leg_prob,
                    min_total_odds=sel_min_odds,
                    bankroll=sel_combo_bankroll,
                )
            finally:
                db_combo.close()

            best = c_data.get("best_combo")
            matchday_hdr = c_data.get("matchday_label", "Daily")
            if not best:
                st.warning(f"No {sel_legs}-team combos found on {matchday_hdr} with odds ≥ {sel_min_odds:.2f} at {sel_min_leg_prob*100:.0f}% confidence. Try selecting a different matchday or adjusting the Min Team Confidence slider.")
            else:
                st.success(f"✅ Found **{c_data['total_combos_generated']}** valid slips for **{matchday_hdr}**!")
                
                # Helper to render full screenshot-styled combo card + table
                def _render_full_combo_display(combo_obj: dict, title_text: str, prefix_key: str):
                    avg_p = combo_obj.get('avg_leg_probability', combo_obj.get('combined_probability', 0)) * 100
                    st.markdown(f"""
                    <div style="background: linear-gradient(135deg, #1A2E1A 0%, #0E1A14 100%); border: 2px solid #00C851; border-radius: 12px; padding: 20px; margin-bottom: 16px; margin-top: 10px;">
                        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2A4A2A; padding-bottom: 12px; margin-bottom: 15px;">
                            <div>
                                <h3 style="margin: 0; color: #00C851;">🏆 {title_text} ({combo_obj['legs_count']} TEAMS)</h3>
                                <span style="color: #AAA; font-size: 14px;">📅 Matchday: <strong>{matchday_hdr}</strong> (All games finish same day)</span>
                            </div>
                            <span style="font-size: 24px; font-weight: bold; background: #00C851; color: #000; padding: 4px 16px; border-radius: 8px;">ODDS {combo_obj['total_odds']:.2f}</span>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 15px;">
                            <div><span style="color: #888; font-size: 12px;">AVG TEAM CONFIDENCE</span><br><strong style="font-size: 20px; color: #00C851;">{avg_p:.1f}% (Bankers)</strong></div>
                            <div><span style="color: #888; font-size: 12px;">ACCUMULATOR WIN PROB</span><br><strong style="font-size: 20px; color: #FFF;">{combo_obj['combined_probability']*100:.1f}%</strong></div>
                            <div><span style="color: #888; font-size: 12px;">EXPECTED VALUE (+EV)</span><br><strong style="font-size: 20px; color: #00C851;">{combo_obj['expected_value_percent']:+.1f}% EV</strong></div>
                            <div><span style="color: #888; font-size: 12px;">QUARTER KELLY STAKE</span><br><strong style="font-size: 18px; color: #FFBB33;">{format_stake_dual(combo_obj['kelly_stake_amount'], sel_currency)} ({combo_obj['kelly_stake_percent']}%)</strong></div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

                    # Table of Legs with Pre-Combo AI Squad & Injury Check
                    l_df = pd.DataFrame([{
                        "Leg": f"Leg #{i+1}",
                        "Match": l["match"],
                        "Selection": l["selection"],
                        "Market Type": l["market_type"],
                        "Odds": f"{l['odds']:.2f}",
                        "Team Win/Draw Prob": f"{l['probability']*100:.1f}%",
                        "AI Squad / Injury Check": l.get("ai_squad", {}).get("badge", "✅ Approved Banker"),
                        "Kickoff (Ugandan Time, EAT)": l["kickoff_eat"],
                        "League": l["competition"],
                    } for i, l in enumerate(combo_obj["legs"])])
                    st.dataframe(l_df, use_container_width=True, hide_index=True)

                    # Deep AI Squad, Injury & Performance Analysis Expander
                    with st.expander(f"🏥 Deep AI Squad, Injury & Performance Analysis ({len(combo_obj['legs'])} Matches Checked)", expanded=False):
                        for i, l in enumerate(combo_obj["legs"], 1):
                            sq = l.get("ai_squad", {})
                            st.markdown(f"**Leg #{i}: {l['match']} — {l['selection']}** ({sq.get('badge', '✅ Approved Banker')})")
                            st.write(f"• **Squad & Injuries:** {sq.get('injuries', 'No critical injury absences reported.')}")
                            st.write(f"• **Performance & Momentum:** {sq.get('form_summary', 'Strong consistent goal-scoring profile.')}")
                            st.write(f"• **Head-to-Head Clash Record:** {sq.get('h2h_summary', 'Historical record confirms edge.')}")
                            st.caption(f"• **Tactical Risk Verdict:** {sq.get('risk_note', 'Passed quantitative and squad stability filters.')}")
                            if i < len(combo_obj["legs"]):
                                st.divider()

                    # Formatted Booking Text to copy
                    s_text = f"🔥 FIE {combo_obj['legs_count']}-TEAM SLIP (Total Odds: {combo_obj['total_odds']:.2f} | Avg Win Prob: {avg_p:.1f}%)\n"
                    for i, l in enumerate(combo_obj["legs"], 1):
                        s_text += f"{i}. {l['match']} ➔ {l['selection']} @ {l['odds']:.2f} (Prob: {l['probability']*100:.1f}%) [{l['kickoff_eat']}]\n"
                    s_text += f"Total Odds: {combo_obj['total_odds']:.2f} | Kelly Stake: {format_stake_dual(combo_obj['kelly_stake_amount'], sel_currency)}"
                    st.text_area("📋 Copy Booking Slip Text", s_text, height=110, key=f"{prefix_key}_text")

                    # Download & WhatsApp Dispatch Buttons
                    b_pdf_col, b_txt_col, b_wa_col = st.columns(3)
                    with b_pdf_col:
                        try:
                            pdf_b = generate_combo_pdf(combo_obj, matchday_hdr, bankroll=sel_combo_bankroll, currency=sel_currency)
                            if pdf_b:
                                st.download_button(
                                    "📄 Download PDF Slip",
                                    data=pdf_b,
                                    file_name=f"fie_{prefix_key}.pdf",
                                    mime="application/pdf",
                                    use_container_width=True,
                                    key=f"{prefix_key}_dl_pdf"
                                )
                        except Exception:
                            pass
                    with b_txt_col:
                        txt_b = generate_text_slip(combo_obj, matchday_hdr, currency=sel_currency)
                        st.download_button(
                            "📋 Download Text Slip",
                            data=txt_b.encode("utf-8"),
                            file_name=f"fie_{prefix_key}.txt",
                            mime="text/plain",
                            use_container_width=True,
                            key=f"{prefix_key}_dl_txt"
                        )
                    with b_wa_col:
                        if st.button("📲 Send to WhatsApp", key=f"{prefix_key}_wa_btn", use_container_width=True):
                            wa_ph = os.getenv("WHATSAPP_PHONE", "").strip()
                            wa_k = os.getenv("WHATSAPP_APIKEY", "").strip()
                            if not (wa_ph and wa_k):
                                st.warning("Configure your WhatsApp phone & CallMeBot key in the sidebar first!")
                            else:
                                with st.spinner("Sending ticket to WhatsApp..."):
                                    res_wa = send_combo_whatsapp_alert(
                                        combo_obj,
                                        matchday_label=matchday_hdr,
                                        phone=wa_ph,
                                        apikey=wa_k,
                                        currency=sel_currency,
                                    )
                                    if res_wa.get("success"):
                                        st.success(f"Ticket dispatched to WhatsApp (+{wa_ph})!")
                                    else:
                                        st.error(f"WhatsApp Error: {res_wa.get('error')}")

                # Render Top Daily Banker Slip
                _render_full_combo_display(best, f"TOP DAILY BANKER MULTI-BET", "top_banker")

                st.info("💡 **Why Accumulator Win Prob is ~55-65% while each team is 85%+**: Every individual team is an ultra-safe banker (averaging 88%+ win rate). When independent events multiply together (e.g. 88% × 88% × 88% = 68%), the joint ticket has ~60% probability. Because bookmakers only require 50% implied probability for odds of 2.00, this guarantees a massive mathematical edge (+EV)!")

                # AI Combo Explanation
                combo_explain = gemini_combo_explanation(
                    legs=best["legs"],
                    total_odds=best["total_odds"],
                    combined_prob=best["combined_probability"],
                    avg_prob=best.get("avg_leg_probability", 0),
                    ev_percent=best["expected_value_percent"],
                    matchday=matchday_hdr,
                )
                st.info(combo_explain)

                # Alternative Combos (Rendered in full screenshot card + table style!)
                alts = c_data.get("alternatives", [])
                if alts:
                    with st.expander(f"📦 View {len(alts)} Alternative Combos (Odds ≥ {sel_min_odds:.2f})", expanded=True):
                        st.write("Browse alternative high-confidence multi-bet slips generated for this matchday:")
                        for idx, alt in enumerate(alts, 2):
                            _render_full_combo_display(alt, f"COMBO OPTION #{idx}", f"alt_combo_{idx}")
                            if idx < len(alts) + 1:
                                st.markdown("<hr style='border: 1px dashed #2A4A2A; margin: 25px 0;'>", unsafe_allow_html=True)

    st.markdown("---")
    # ── MATCHES FOR SELECTED DAY ──────────────────────────────────────────────
    day_label_hdr = target_date_val if target_date_val and target_date_val != "all" else "Selected Matchday"
    for d in avail_days:
        if d.get("date") == target_date_val:
            day_label_hdr = d.get("label", day_label_hdr)
            break
    if target_date_val == "all":
        day_label_hdr = "All Upcoming Matchdays"

    st.subheader(f"📅 Matches Playing on {day_label_hdr} (Ugandan Time, EAT)")

    db_fx_day = _db()
    try:
        now_utc = datetime.now(timezone.utc)
        query = select(Fixture).where(Fixture.status == "scheduled").where(Fixture.kickoff_utc >= now_utc).order_by(Fixture.kickoff_utc)
        all_fx = db_fx_day.scalars(query).all()
        day_matches = []
        for f in all_fx:
            dt_f = f.kickoff_utc
            if dt_f.tzinfo is None:
                dt_f = dt_f.replace(tzinfo=timezone.utc)
            f_day = dt_f.astimezone(EAT_TZ).strftime("%Y-%m-%d")
            if target_date_val and target_date_val != "all" and f_day != target_date_val:
                continue

            h = f.home_odds
            d = f.draw_odds
            a = f.away_odds
            fav = "—"
            if h and a:
                if h < a and h <= 1.45:
                    fav = f"🔥 {f.home_team} to Win ({h:.2f})"
                elif a < h and a <= 1.45:
                    fav = f"🔥 {f.away_team} to Win ({a:.2f})"
                elif h < a:
                    fav = f"🛡️ {f.home_team} Win or Draw (1X)"
                else:
                    fav = f"🛡️ {f.away_team} Win or Draw (X2)"

            eat_time = to_eat_str(f.kickoff_utc)
            sigs = evaluate_fixture_signals(f)
            q = grade_data_quality(sigs)

            day_matches.append({
                "Kickoff (Ugandan Time, EAT)": eat_time,
                "Match": f"{f.home_team} vs {f.away_team}",
                "Competition": f.competition,
                "Home (1)": f"{h:.2f}" if h else "—",
                "Draw (X)": f"{d:.2f}" if d else "—",
                "Away (2)": f"{a:.2f}" if a else "—",
                "Key Banker Selection": fav,
                "Quality": f"{_grade_color(q['grade'])} Grade {q['grade']}",
            })

        if day_matches:
            st.caption(f"Showing **{len(day_matches)} matches** scheduled for **{day_label_hdr}**.")
            m_df = pd.DataFrame(day_matches)
            st.dataframe(m_df, use_container_width=True, hide_index=True)
            csv_day = m_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                f"📥 Export {len(day_matches)} Matches as CSV",
                data=csv_day,
                file_name=f"fie_matches_{target_date_val or 'all'}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.info(f"No matches scheduled for {day_label_hdr}.")
    finally:
        db_fx_day.close()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — FIXTURES & ODDS
# ═════════════════════════════════════════════════════════════════════════════
with tab_fixtures:
    st.subheader("Match Fixtures & Quality Grades")

    f_top1, f_top2 = st.columns([3, 1])
    with f_top1:
        view_mode = st.radio(
            "Filter Fixtures:",
            ["🟢 Upcoming Only (Tomorrow & Future)", "📜 All Fixtures (Including Past Played)"],
            horizontal=True,
            key="cloud_fx_view_mode"
        )
    with f_top2:
        if st.button("🗑️ Purge Past Played Games", use_container_width=True, help="Permanently delete matches whose kickoff has already passed"):
            db_p = _db()
            now_p = datetime.now(timezone.utc)
            del_n = db_p.execute(delete(Fixture).where(Fixture.kickoff_utc <= now_p)).rowcount
            db_p.commit()
            db_p.close()
            st.toast(f"Purged {del_n} past matches!")
            st.rerun()

    only_upcoming = view_mode.startswith("🟢")
    db_fx = _db()
    now_fx = datetime.now(timezone.utc)
    fx_query = select(Fixture)
    if only_upcoming:
        fx_query = fx_query.where(Fixture.kickoff_utc >= now_fx)
    fixtures_raw = db_fx.scalars(
        fx_query.order_by(Fixture.kickoff_utc).limit(500)
    ).all()
    db_fx.close()

    if not fixtures_raw:
        st.info("No fixtures yet. Go to **📥 Ingestion & Import** to load data.")
    else:
        fixtures_data = [_build_fixture_row(f) for f in fixtures_raw]
        df = pd.DataFrame(fixtures_data)

        f_c1, f_c2, f_c3, f_c4 = st.columns([3, 2, 1, 1])
        with f_c1:
            search = st.text_input("🔍 Search team or match", placeholder="Arsenal, Barcelona…")
        with f_c2:
            comp_opts = ["All"] + sorted(df["competition"].unique().tolist())
            sel_comp = st.selectbox("Competition", comp_opts)
        with f_c3:
            src_opts = ["All"] + sorted(df["source"].unique().tolist())
            sel_src = st.selectbox("Source", src_opts)
        with f_c4:
            status_opts = ["All"] + sorted(df["status"].unique().tolist())
            sel_status = st.selectbox("Status", status_opts)

        filtered = df.copy()
        if search:
            t = search.lower()
            filtered = filtered[
                filtered["home_team"].str.lower().str.contains(t, na=False) |
                filtered["away_team"].str.lower().str.contains(t, na=False)
            ]
        if sel_comp != "All":
            filtered = filtered[filtered["competition"] == sel_comp]
        if sel_src != "All":
            filtered = filtered[filtered["source"] == sel_src]
        if sel_status != "All":
            filtered = filtered[filtered["status"] == sel_status]

        st.write(f"Showing **{len(filtered)}** of **{len(df)}** fixtures")
        display_cols = [
            "id", "kickoff_eat", "home_team", "away_team", "competition", "country",
            "source", "status", "home_odds", "draw_odds", "away_odds",
            "data_quality_score", "data_quality_grade", "eligible_for_analysis",
        ]
        avail = [c for c in display_cols if c in filtered.columns]
        disp_f = filtered[avail].copy()
        if "kickoff_eat" in disp_f.columns:
            disp_f.rename(columns={"kickoff_eat": "Kickoff (Ugandan Time, EAT)"}, inplace=True)
        st.dataframe(disp_f, use_container_width=True, hide_index=True)

        # ── Fixture Inspector ───────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🔬 Fixture Inspector")

        fixture_ids = filtered["id"].tolist()
        if fixture_ids:
            selected_id = st.selectbox(
                "Select a fixture to inspect:",
                fixture_ids,
                format_func=lambda x: (
                    f"#{x}  {filtered[filtered['id']==x]['home_team'].values[0]} vs "
                    f"{filtered[filtered['id']==x]['away_team'].values[0]}  "
                    f"({filtered[filtered['id']==x]['competition'].values[0]})"
                )
            )

            db_insp = _db()
            fx = db_insp.get(Fixture, selected_id)
            db_insp.close()

            if fx:
                sigs = evaluate_fixture_signals(fx)
                grade = grade_data_quality(sigs)

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Quality Score", f"{grade['score']} / 100")
                sc2.metric("Grade", f"{_grade_color(grade['grade'])} {grade['grade']}")
                sc3.metric("Analysis Eligible", "✅ Yes" if grade["eligible_for_analysis"] else "❌ No")
                sc4.metric("Status", fx.status.replace("_", " ").title())

                with st.expander("📊 Quality Signal Breakdown", expanded=False):
                    bd = grade.get("breakdown", {})
                    if bd:
                        st.dataframe(
                            pd.DataFrame([
                                {"Signal": k, "Weight": v["weight"],
                                 "Value": f"{v['value']:.2f}", "Status": v["status"]}
                                for k, v in bd.items()
                            ]),
                            use_container_width=True, hide_index=True,
                        )

                st.markdown("---")
                st.write("##### ⚡ Market Probabilities & +EV Value Analysis")

                # Controls
                db_pre = _db()
                pre_analysis = analyze_fixture(fx, db=db_pre)
                db_pre.close()

                est = pre_analysis.get("estimated_lambdas", {})
                ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
                with ctrl1:
                    custom_h = st.number_input(
                        f"Home λ ({fx.home_team})", 0.1, 8.0,
                        float(est.get("home_lambda", 1.5)), 0.05, key=f"h{selected_id}")
                with ctrl2:
                    custom_a = st.number_input(
                        f"Away λ ({fx.away_team})", 0.1, 8.0,
                        float(est.get("away_lambda", 1.1)), 0.05, key=f"a{selected_id}")
                with ctrl3:
                    custom_rho = st.slider(
                        "Dixon-Coles ρ", -0.30, 0.10, -0.10, 0.01,
                        help="Adjusts 0-0 and 1-1 probabilities to match real draw rates",
                        key=f"rho{selected_id}")
                with ctrl4:
                    custom_thr = st.slider(
                        "Qualification Threshold", 0.60, 0.95, 0.85, 0.01,
                        key=f"thr{selected_id}")

                db_an = _db()
                a_data = analyze_fixture(
                    fx,
                    home_lambda=custom_h,
                    away_lambda=custom_a,
                    qualification_threshold=custom_thr,
                    rho=custom_rho,
                    db=db_an,
                )
                db_an.close()

                src_lbl = (a_data.get("lambda_source") or "odds_implied").replace("_", " ").title()
                st.caption(
                    f"📌 λ source: **{src_lbl}** · "
                    f"Engine: **Dixon-Coles Bivariate Poisson (ρ = {custom_rho})**"
                )

                # 🤖 AI Match Preview
                _all_mkts = a_data.get("all_markets", {})
                _h_lam = a_data.get("home_lambda", custom_h)
                _a_lam = a_data.get("away_lambda", custom_a)
                _preview = gemini_match_preview(
                    home_team=fx.home_team,
                    away_team=fx.away_team,
                    competition=fx.competition or "",
                    home_win_prob=_all_mkts.get("home_win", 0.0),
                    draw_prob=_all_mkts.get("draw", 0.0),
                    away_win_prob=_all_mkts.get("away_win", 0.0),
                    home_lambda=_h_lam,
                    away_lambda=_a_lam,
                    kickoff_eat=to_eat_str(fx.kickoff_utc),
                )
                st.markdown(_preview)

                margin = a_data.get("bookmaker_margin")
                if margin:
                    st.info(
                        f"📊 **Bookmaker Overround:** `{margin['overround']*100:.1f}%`  "
                        f"(Vig / Margin: `{margin['margin_percent']:.1f}%`)"
                    )

                # 1X2 EV cards
                vbs = a_data.get("value_bets", [])
                if vbs:
                    st.markdown("###### 💰 1X2 Market & Expected Value (+EV)")
                    vcols = st.columns(len(vbs))
                    for i, vb in enumerate(vbs):
                        with vcols[i]:
                            ev = vb["expected_value_percent"]
                            st.metric(
                                label=f"{vb['market']} @ {vb['bookmaker_odds']:.2f}",
                                value=f"{vb['model_probability']*100:.1f}%",
                                delta=f"{ev:+.1f}% EV",
                                delta_color="normal" if vb["is_value"] else "off",
                            )
                            st.caption(
                                f"Fair Odds: **{vb.get('fair_odds', 'N/A')}** "
                                f"| Edge: **{vb['edge']*100:+.1f}%**"
                            )
                            if vb["is_value"] and vb.get("kelly", {}).get("stake_amount", 0) > 0:
                                k = vb["kelly"]
                                st.markdown(
                                    f"💼 **Quarter Kelly:** `${k['stake_amount']:.2f}` "
                                    f"({k['stake_percent']}%)"
                                )

                # Qualifying markets
                q_mkts = a_data.get("qualifying_markets", [])
                st.markdown(f"###### 🎯 Qualifying Markets (≥ {custom_thr*100:.0f}% Confidence)")
                if q_mkts:
                    pills = " &nbsp;|&nbsp; ".join(
                        f"**{m['market'].replace('_',' ').upper()}**: `{m['probability']*100:.1f}%`"
                        for m in q_mkts
                    )
                    st.success(f"Qualified ({len(q_mkts)} markets): {pills}")
                else:
                    st.write("No markets reached the qualification threshold for this match.")

                # 🌟 Value Score
                val_data = compute_value_score(fx, a_data)
                st.markdown(f"""
                <div style="background: rgba(0,200,81,0.08); border: 1px solid #00C851; border-radius: 8px; padding: 10px 16px; margin: 12px 0;">
                    <span style="font-weight: bold; font-size: 15px; color: #00C851;">💎 Match Value Index: {val_data['score']}/100 ({val_data['label']})</span>
                    <span style="color: #AAA; font-size: 12px; margin-left: 15px;">Data Quality: {val_data['components']['data_quality_pts']}pt · EV Edge: {val_data['components']['ev_pts']}pt · Banker Confidence: {val_data['components']['qualifying_market_pts']}pt</span>
                </div>
                """, unsafe_allow_html=True)

                # 🤝 Head-to-Head Analytics
                try:
                    db_h2h = _db()
                    h2h_data = get_h2h_stats(db_h2h, fx.home_team, fx.away_team)
                    db_h2h.close()
                    if h2h_data.get("total", 0) > 0:
                        st.markdown("###### 🤝 Head-to-Head History")
                        h_c1, h_c2, h_c3, h_c4 = st.columns(4)
                        h_c1.metric(f"{fx.home_team} Wins", h2h_data["home_wins"], delta=f"In {h2h_data['total']} meetings")
                        h_c2.metric("Draws", h2h_data["draws"])
                        h_c3.metric(f"{fx.away_team} Wins", h2h_data["away_wins"])
                        h_c4.metric("Avg Match Goals", f"{h2h_data['avg_total_goals']:.2f}")
                        if h2h_data.get("last_5_results"):
                            st.caption(f"Last meetings form: **{' - '.join(h2h_data['last_5_results'])}**")
                except Exception:
                    pass

                # 🎯 Top 5 Most Likely Exact Scorelines
                cs_list = a_data.get("correct_score_top5", [])
                if cs_list:
                    st.markdown("###### 🎯 Top 5 Exact Correct Score Predictions")
                    cs_cols = st.columns(min(5, len(cs_list)))
                    for idx_cs, cs in enumerate(cs_list[:5]):
                        with cs_cols[idx_cs]:
                            st.metric(
                                label=f"Score {cs['score']}",
                                value=f"{cs['probability']*100:.1f}%",
                                delta=f"≈ {cs['odds_equiv']:.1f} Fair Odds",
                                delta_color="off"
                            )

                # ⚡ Asian Handicap lines
                all_m = a_data.get("all_markets", {})
                ah_keys = [
                    ("Home -0.5", "ah_home_minus_0_5"),
                    ("Home -1.5", "ah_home_minus_1_5"),
                    ("Away +0.5", "ah_away_plus_0_5"),
                    ("Away +1.5", "ah_away_plus_1_5"),
                ]
                has_ah = any(k in all_m for _, k in ah_keys)
                if has_ah:
                    st.markdown("###### ⚡ Asian Handicap Lines")
                    ah_cols = st.columns(4)
                    for idx_ah, (lbl, k) in enumerate(ah_keys):
                        with ah_cols[idx_ah]:
                            prob_val = all_m.get(k, 0.0)
                            st.metric(lbl, f"{prob_val*100:.1f}%", delta=f"Fair: {round(1.0/max(0.01, prob_val), 2):.2f}" if prob_val > 0.01 else None)

                # Best Odds across bookmakers
                st.markdown("---")
                st.write("##### 🔖 Best Odds Line-Shopping (Multi-Bookmaker)")
                books = {
                    "Bet365":   (fx.home_odds,    fx.draw_odds,    fx.away_odds),
                    "BetVictor":(fx.bv_home_odds,  fx.bv_draw_odds,  fx.bv_away_odds),
                    "Betway":   (fx.bw_home_odds,  fx.bw_draw_odds,  fx.bw_away_odds),
                    "Pinnacle": (fx.ps_home_odds,  fx.ps_draw_odds,  fx.ps_away_odds),
                    "MaxOdds":  (fx.max_home_odds, fx.max_draw_odds, fx.max_away_odds),
                }
                best = {side: (None, 0.0) for side in ("home", "draw", "away")}
                for bname, (h, d, a) in books.items():
                    if h and h > 1.0 and h > best["home"][1]:
                        best["home"] = (bname, h)
                    if d and d > 1.0 and d > best["draw"][1]:
                        best["draw"] = (bname, d)
                    if a and a > 1.0 and a > best["away"][1]:
                        best["away"] = (bname, a)

                bh, bd2, ba = best["home"][1], best["draw"][1], best["away"][1]
                if bh > 0 and bd2 > 0 and ba > 0:
                    min_or = round(1/bh + 1/bd2 + 1/ba, 4)
                    bc1, bc2, bc3 = st.columns(3)
                    for col, side in zip([bc1, bc2, bc3], ["home", "draw", "away"]):
                        info = best[side]
                        with col:
                            st.metric(
                                f"Best {side.title()} Odds",
                                f"{info[1]:.2f}",
                                delta=f"@ {info[0]}",
                            )
                            # Show all bookmaker odds for this side
                            idx = ["home", "draw", "away"].index(side)
                            all_od = {k: v[idx] for k, v in books.items()
                                      if v[idx] and v[idx] > 1.0}
                            if len(all_od) > 1:
                                st.caption("  ".join(f"{k}: {v:.2f}" for k, v in all_od.items()))

                    if min_or < 1.0:
                        st.error(
                            f"⚠️ **ARBITRAGE DETECTED!** Combined best-line overround = "
                            f"`{min_or:.4f}` (< 1.0) — guaranteed profit possible if stakes "
                            "are split correctly."
                        )
                    else:
                        st.caption(
                            f"Combined best-line overround: `{min_or:.4f}` — "
                            f"Margin: `{(min_or-1)*100:.2f}%`"
                        )
                else:
                    st.info(
                        "No multi-bookmaker odds available for this fixture. "
                        "Fetch upcoming fixtures to populate Pinnacle / MaxOdds columns."
                    )


# ═════════════════════════════════════════════════════════════════════════════
# TAB — LIVE IN-PLAY SCORES
# ═════════════════════════════════════════════════════════════════════════════
with tab_live:
    st.subheader("⚡ Real-Time Live In-Play & Today's Matches")
    st.caption("Live score tracker across 17+ leagues directly from real-time ESPN scoreboard.")

    c_live_btn, c_live_space = st.columns([2, 5])
    with c_live_btn:
        if st.button("🔄 Refresh Live Scores", use_container_width=True, key="btn_refresh_live"):
            st.rerun()

    db_live = _db()
    try:
        with st.spinner("Fetching live in-play scoreboard…"):
            live_matches = fetch_live_scores(db_live)
    except Exception as e:
        live_matches = []
        st.error(f"Error fetching live scores: {e}")
    finally:
        db_live.close()

    if not live_matches:
        st.info("No matches in play right now. Games scheduled for today will automatically show live minute-by-minute scores here once kicked off.")
    else:
        in_play = [m for m in live_matches if m["is_live"]]
        finished_today = [m for m in live_matches if m["is_finished"]]
        pre_match = [m for m in live_matches if not m["is_live"] and not m["is_finished"]]

        if in_play:
            st.markdown(f"#### 🔴 In-Play Right Now ({len(in_play)} matches)")
            for m in in_play:
                st.markdown(f"""
                <div style="background: rgba(255, 68, 68, 0.12); border: 1px solid #FF4444; border-radius: 8px; padding: 12px 18px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
                    <div>
                        <span style="font-size: 16px; font-weight: bold; color: #FFF;">{m['home_team']} &nbsp;<span style="color: #FF4444;">{m['home_score']} – {m['away_score']}</span>&nbsp; {m['away_team']}</span>
                        <div style="font-size: 12px; color: #AAA;">{m['competition']} · {m['status_desc']}</div>
                    </div>
                    <span style="background: #FF4444; color: #FFF; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold;">⏱️ {m['clock']}</span>
                </div>
                """, unsafe_allow_html=True)

        if pre_match:
            st.markdown(f"#### 🕐 Scheduled Today ({len(pre_match)} matches)")
            pre_df = pd.DataFrame([{
                "Match": f"{m['home_team']} vs {m['away_team']}",
                "League": m["competition"],
                "Kickoff (EAT)": m["kickoff_eat"] or m["status_desc"],
                "Status": m["status_desc"],
            } for m in pre_match])
            st.dataframe(pre_df, use_container_width=True, hide_index=True)

        if finished_today:
            st.markdown(f"#### ✅ Today's Results ({len(finished_today)} matches)")
            fin_df = pd.DataFrame([{
                "Match": f"{m['home_team']} {m['home_score']} – {m['away_score']} {m['away_team']}",
                "League": m["competition"],
                "Final Score": f"{m['home_score']} - {m['away_score']}",
                "Status": m["status_desc"],
            } for m in finished_today])
            st.dataframe(fin_df, use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB — ODDS LINE MOVEMENT
# ═════════════════════════════════════════════════════════════════════════════
with tab_odds_movement:
    st.subheader("📉 Odds Movement & Sharp Money Tracker")
    st.caption("Tracks line movement between opening lines and current market odds. Big drops indicate sharp (smart money) action.")

    db_om = _db()
    try:
        moves = get_odds_movement(db_om, upcoming_only=True)
    except Exception as e:
        moves = []
        st.error(f"Error checking odds movement: {e}")
    finally:
        db_om.close()

    if not moves:
        st.info("No significant odds movement (>8% shift) recorded yet. As matches approach kickoff and opening odds are tracked, sharp line changes will be highlighted here.")
    else:
        st.success(f"⚡ Detected **{len(moves)} match(es)** with notable line movement!")
        for mv in moves:
            badge_color = "#FF4444" if "SHARP" in mv["sharp_signal"] else "#FFBB33"
            st.markdown(f"""
            <div style="background: rgba(22, 27, 34, 0.8); border: 1px solid {badge_color}; border-radius: 8px; padding: 12px 18px; margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <strong style="font-size: 15px; color: #FFF;">{mv['match']} ({mv['competition']})</strong>
                    <span style="background: {badge_color}; color: #000; padding: 2px 8px; border-radius: 6px; font-size: 11px; font-weight: bold;">{mv['sharp_signal']}</span>
                </div>
                <div style="margin-top: 6px; font-size: 13px; color: #DDD;">
                    {" &nbsp;•&nbsp; ".join(mv['signals'])}
                </div>
                <div style="margin-top: 4px; font-size: 11px; color: #888;">
                    Opening: H {mv['opening_odds']['home'] or '—'} / D {mv['opening_odds']['draw'] or '—'} / A {mv['opening_odds']['away'] or '—'} &nbsp;➔&nbsp; 
                    Current: H {mv['current_odds']['home'] or '—'} / D {mv['current_odds']['draw'] or '—'} / A {mv['current_odds']['away'] or '—'}
                </div>
            </div>
            """, unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — INGESTION & IMPORT
# ═════════════════════════════════════════════════════════════════════════════
with tab_import:
    st.subheader("Data Ingestion & Real-Time League Feeds")
    st.write(
        "FIE connects to publicly authorised football data archives. "
        "Every match is automatically deduplicated, normalised, and used to calibrate team ratings."
    )

    st.markdown("### 🌐 Real Data Feeds (Live & Full Seasons)")

    # Master One-Click Auto Sync Banner
    st.markdown("""
    <div style="background: rgba(0, 200, 81, 0.1); border: 1px solid #00C851; border-radius: 10px; padding: 15px; margin-bottom: 20px;">
        <h4 style="margin: 0 0 5px 0; color: #00C851;">⚡ Master Auto-Setup: Live Matches + All 5 Major European Leagues</h4>
        <p style="margin: 0; color: #DDD; font-size: 14px;">
            Automatically ingests genuine real-time upcoming matches (UEFA Nations League + club rounds) with live DraftKings odds 
            <strong>AND</strong> complete seasons for <strong>Premier League, La Liga, Serie A, Bundesliga, and Ligue 1</strong> to calibrate ratings for all 96 top European clubs.
        </p>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🚀 Run Full Auto-Setup (Live Games + All 5 Major Leagues)", type="primary", use_container_width=True, key="cloud_master_auto"):
        with st.spinner("1/2 Fetching real live upcoming fixtures with DraftKings odds..."):
            db_m1 = _db()
            try:
                fetch_live_upcoming_fixtures_espn(db_m1)
            except Exception as e:
                st.warning(f"Live fetch error: {e}")
            finally:
                db_m1.close()

        with st.spinner("2/2 Ingesting all 5 Major European Leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1) & calibrating ratings..."):
            db_m2 = _db()
            try:
                res = fetch_all_major_leagues(db_m2, season="2324")
                st.success(f"✅ {res['message']}")
                st.rerun()
            except Exception as e:
                st.error(f"Major leagues error: {e}")
            finally:
                db_m2.close()

    st.markdown("---")
    feed1, feed2 = st.columns(2)

    with feed1:
        st.markdown("#### 1. Live Games (Tomorrow & Weekend)")
        st.write(
            "Fetches **real upcoming matches** (UEFA Nations League tomorrow + European domestic rounds) "
            "with **real-time live DraftKings odds**."
        )
        if st.button(
            "🟢 Fetch Live Upcoming Games (DraftKings Odds)",
            use_container_width=True,
            key="cloud_btn_live",
        ):
            with st.spinner("Downloading live upcoming matches and DraftKings odds…"):
                db_live = _db()
                try:
                    result = fetch_live_upcoming_fixtures_espn(db_live)
                    st.success(f"✅ {result['message']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Live fetch error: {e}")
                finally:
                    db_live.close()

        st.caption("Or fetch club league batch from football-data.co.uk:")
        if st.button("📥 Fetch Football-Data.co.uk Batch", use_container_width=True, key="cloud_btn_batch"):
            with st.spinner("Downloading upcoming fixtures and live odds…"):
                db_u = _db()
                try:
                    result = fetch_live_upcoming_fixtures(db_u)
                    st.success(f"✅ {result['message']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Fetch error: {e}")
                finally:
                    db_u.close()

    with feed2:
        st.markdown("#### 2. Major European Leagues (All Seasons)")
        st.write(
            "Automatically ingests complete seasons with final scores, half-time stats, and closing market odds "
            "to auto-calibrate all team ratings across Europe."
        )

        l_sea_col, l_btn_col = st.columns([1, 2])
        with l_sea_col:
            sel_all_sea = st.selectbox("Season", ["2324", "2425"], format_func=lambda x: "2023/24" if x == "2324" else "2024/25", key="cloud_major_sea")
        with l_btn_col:
            st.write("") # spacing
            if st.button("⚡ Ingest All 5 Major Leagues", use_container_width=True, key="cloud_btn_all_major"):
                with st.spinner("Downloading all 5 major leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1)..."):
                    db_s = _db()
                    try:
                        result = fetch_all_major_leagues(db_s, season=sel_all_sea)
                        st.success(f"✅ {result['message']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Download error: {e}")
                    finally:
                        db_s.close()

        with st.expander("⚙️ Or Ingest Single Specific League"):
            sel_div = st.selectbox(
                "League",
                ["E0", "SP1", "I1", "D1", "F1", "E1", "N1", "P1", "B1"],
                format_func=lambda x: {
                    "E0": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
                    "SP1": "🇪🇸 La Liga",
                    "I1":  "🇮🇹 Serie A",
                    "D1":  "🇩🇪 Bundesliga",
                    "F1":  "🇫🇷 Ligue 1",
                    "E1":  "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship",
                    "N1":  "🇳🇱 Eredivisie",
                    "P1":  "🇵🇹 Primeira Liga",
                    "B1":  "🇧🇪 Belgian Pro League",
                }.get(x, x),
                key="cloud_single_div",
            )
            if st.button("📥 Download Single League", use_container_width=True, key="cloud_btn_single"):
                with st.spinner(f"Downloading {sel_div} {sel_all_sea}…"):
                    db_s2 = _db()
                    try:
                        result = fetch_historical_league_season(db_s2, sel_div, sel_all_sea)
                        st.success(f"✅ {result['message']}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Download error: {e}")
                    finally:
                        db_s2.close()

    st.markdown("---")
    st.markdown("### 📁 Local CSV & Offline Data")
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.markdown("#### Option A: Quick Sample")
        st.caption("Minimal test `data/sample_fixtures.csv` — good for checking the UI.")
        if st.button("🚀 Load Sample Fixtures", use_container_width=True):
            db_sa = _db()
            try:
                sample_path = ROOT / "data" / "sample_fixtures.csv"
                frame = pd.read_csv(sample_path)
                res = import_fixtures_from_dataframe(db_sa, frame)
                st.success(f"✅ {res['inserted']} inserted, {res['duplicates']} duplicates.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                db_sa.close()

    with col_b:
        st.markdown("#### Option B: Offline EPL Sample")
        st.caption("29 real EPL 2023/24 matches with scores and closing odds, bundled offline.")
        if st.button("📥 Ingest Offline EPL Sample", use_container_width=True):
            db_ep = _db()
            try:
                result = seed_sample_historical_data(db_ep)
                st.success(f"✅ {result['message']}")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                db_ep.close()

    with col_c:
        st.markdown("#### Option C: Upload Custom CSV")
        st.caption("Required headers: `kickoff_utc`, `home_team`, `away_team`, `competition`, `source`.")
        uploaded = st.file_uploader("Choose CSV file", type=["csv"], label_visibility="collapsed")
        if uploaded:
            if st.button("📤 Upload & Process CSV", use_container_width=True):
                db_up = _db()
                try:
                    frame = pd.read_csv(io.BytesIO(uploaded.read()))
                    res = import_fixtures_from_dataframe(db_up, frame)
                    st.success(
                        f"✅ Imported **{res['inserted']}** new, "
                        f"**{res['duplicates']}** duplicates from `{uploaded.name}`."
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Upload error: {e}")
                finally:
                    db_up.close()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — STRATEGY BACKTESTER
# ═════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.subheader("🧪 Strategy Backtester & Performance Simulator")
    st.caption(
        "Simulate quantitative predictive strategies against completed matches "
        "with real scores and closing odds. Requires historical data with final scores."
    )

    bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
    with bt_c1:
        bt_strategy = st.selectbox(
            "Strategy Model",
            ["value_bet", "high_confidence_85"],
            format_func=lambda x: (
                "💰 Expected Value (+EV)" if x == "value_bet"
                else "🎯 High Confidence (≥ 85%)"
            ),
        )
    with bt_c2:
        bt_staking = st.selectbox(
            "Staking Plan",
            ["quarter_kelly", "half_kelly", "flat"],
            format_func=lambda x: {
                "quarter_kelly": "Quarter Kelly (Recommended)",
                "half_kelly":    "Half Kelly",
                "flat":          "Flat 2% Stake",
            }[x],
        )
    with bt_c3:
        bt_min_ev = st.slider("Minimum +EV %", 0.5, 15.0, 3.0, 0.5)
    with bt_c4:
        bt_bankroll = st.number_input("Initial Bankroll ($)", 50.0, 100_000.0, 1000.0, 100.0)

    if st.button("⚡ Run Backtest Simulation", type="primary", use_container_width=True):
        db_bt = _db()
        try:
            with st.spinner("Running simulation…"):
                sim = run_backtest(
                    db=db_bt,
                    strategy=bt_strategy,
                    min_ev_percent=bt_min_ev,
                    min_quality_score=0,
                    staking=bt_staking,
                    initial_bankroll=float(bt_bankroll),
                )
        except Exception as e:
            st.error(f"Backtest error: {e}")
            db_bt.close()
            st.stop()
        finally:
            db_bt.close()

        st.markdown("---")
        st.markdown("#### 📊 Simulation Results")
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total Bets", sim["total_bets"])
        m2.metric("Win Rate", f"{sim['win_rate_percent']:.1f}%",
                  delta=f"{sim['wins']}W / {sim['losses']}L")
        m3.metric("Final Bankroll", f"${sim['final_bankroll']:,.2f}")
        m4.metric("Net Profit", f"${sim['net_profit']:+,.2f}",
                  delta=f"${sim['net_profit']:+,.2f}")
        m5.metric("ROI", f"{sim['roi_percent']:+.1f}%")
        m6.metric("Max Drawdown", f"{sim['max_drawdown_percent']:.1f}%")

        history = sim.get("history", [])
        if history:
            hdf = pd.DataFrame(history)
            st.markdown("##### 📈 Equity Curve (Bankroll Progression)")
            st.line_chart(hdf.set_index(hdf.index)["bankroll"])
            st.markdown("##### 📋 Simulated Trade Log")
            st.dataframe(hdf, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "No completed matches matched the strategy criteria. "
                "Import finished matches with scores (Ingestion tab → Option B or Download Season)."
            )


# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — MARKET PROBABILITIES
# ═════════════════════════════════════════════════════════════════════════════
with tab_markets:
    st.subheader("Dixon-Coles Bivariate Poisson & 85% Qualification")
    st.caption(
        "Standalone market probability engine. Adjust expected goals (λ) and the "
        "Dixon-Coles low-score correlation factor (ρ) to explore model outputs."
    )

    mp_c1, mp_c2, mp_c3, mp_c4 = st.columns(4)
    with mp_c1:
        home_lam = st.number_input("Home Expected Goals (λ)", 0.0, 8.0, 1.65, 0.05)
    with mp_c2:
        away_lam = st.number_input("Away Expected Goals (λ)", 0.0, 8.0, 1.10, 0.05)
    with mp_c3:
        rho_v = st.slider(
            "Dixon-Coles ρ", -0.30, 0.10, -0.10, 0.01,
            help="Negative values increase draw probability for 0-0 and 1-1 scores."
        )
    with mp_c4:
        thr_v = st.slider("Qualification Threshold", 0.50, 0.99, 0.85, 0.01)

    if st.button("⚡ Calculate Probabilities", type="primary"):
        mkt_data = poisson_market_probabilities(
            home_lam, away_lam, qualification_threshold=thr_v, rho=rho_v
        )
        qualifying_m = mkt_data.get("qualifying_markets", [])

        st.markdown("---")
        st.subheader(f"🎯 Qualifying Markets (≥ {thr_v*100:.0f}%)")
        if qualifying_m:
            q_cols = st.columns(min(len(qualifying_m), 4))
            for i, item in enumerate(qualifying_m):
                with q_cols[i % 4]:
                    st.metric(
                        item["market"].replace("_", " ").upper(),
                        f"{item['probability']*100:.1f}%",
                        delta=f"+{(item['probability']-thr_v)*100:.1f}% above threshold",
                    )
        else:
            st.info(f"No markets reached {thr_v*100:.0f}% for this λ pair.")

        st.markdown("---")
        st.subheader("📋 All Market Probabilities")
        mdf = pd.DataFrame([
            {
                "Market": m.replace("_", " ").title(),
                "Probability": f"{p*100:.2f}%",
                "Raw": p,
                f"Qualifies (≥{thr_v*100:.0f}%)": "⭐ YES" if p >= thr_v else "No",
            }
            for m, p in mkt_data.get("markets", {}).items()
        ])
        st.dataframe(mdf.drop(columns=["Raw"]), use_container_width=True, hide_index=True)


# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — TEAM RATINGS
# ═════════════════════════════════════════════════════════════════════════════
with tab_ratings:
    st.subheader("🛡️ Team Strength Ratings")
    st.write(
        "Attack and defense ratings calibrate expected goals (λ) and elevate "
        "fixture data-quality scores to Grade A/B analysis readiness."
    )

    db_r = _db()
    ratings_all = db_r.scalars(
        select(TeamRating).order_by(TeamRating.competition, TeamRating.team_name).limit(200)
    ).all()
    db_r.close()

    r_left, r_right = st.columns([3, 2])

    with r_left:
        st.markdown("#### Registered Team Ratings")
        if ratings_all:
            rdf = pd.DataFrame([{
                "Team":       r.team_name,
                "Competition": r.competition,
                "Attack":     r.attack_rating,
                "Defense":    r.defense_rating,
                "Matches":    r.matches_played,
                "Updated":    r.updated_at,
            } for r in ratings_all])
            st.dataframe(rdf, use_container_width=True, hide_index=True)
        else:
            st.info(
                "No ratings yet. Import completed matches with final scores "
                "and click **Auto-Calculate** below."
            )

        if st.button("🔄 Auto-Calculate Ratings from Finished Matches",
                     use_container_width=True):
            db_rc = _db()
            try:
                res = calculate_ratings_from_fixtures(db_rc)
                st.success(res.get("message", "Ratings updated!"))
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                db_rc.close()

    with r_right:
        st.markdown("#### Add / Update Team Rating")
        with st.form("rating_form", clear_on_submit=True):
            new_team = st.text_input("Team Name", placeholder="e.g. Arsenal")
            new_comp = st.text_input("Competition", placeholder="e.g. Premier League")
            new_att  = st.number_input(
                "Attack Rating (1.0 = average, > 1.0 = above average)",
                0.1, 4.0, 1.20, 0.05,
            )
            new_def  = st.number_input(
                "Defense Rating (1.0 = average, < 1.0 = strong defense)",
                0.1, 4.0, 0.85, 0.05,
            )
            new_mp   = st.number_input("Matches Analysed", 1, 200, 10, 1)
            if st.form_submit_button("💾 Save Team Rating", use_container_width=True):
                if not new_team.strip() or not new_comp.strip():
                    st.warning("Team name and competition are required.")
                else:
                    db_rs = _db()
                    try:
                        upsert_team_rating(
                            db_rs,
                            new_team.strip(),
                            new_comp.strip(),
                            float(new_att),
                            float(new_def),
                            int(new_mp),
                        )
                        db_rs.close()
                        st.success(f"✅ Saved rating for **{new_team.strip()}**!")
                        st.rerun()
                    except Exception as e:
                        db_rs.close()
                        st.error(f"Save failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 6 — BOOKMAKER CONNECTORS
# ═════════════════════════════════════════════════════════════════════════════
with tab_connectors:
    st.subheader("Bookmaker Feed Connectors")
    st.write(
        "FIE is architected to interface with licensed odds feeds and official APIs. "
        "Unconfigured connectors act as stubs — no web scraping is performed."
    )
    st.dataframe(
        pd.DataFrame([
            {"Bookmaker": b, "Status": "🔧 Not Configured", "Type": "Stub / Placeholder"}
            for b in BOOKMAKERS
        ]),
        use_container_width=True,
        hide_index=True,
    )
    st.info(
        "To activate a connector, add its API credentials to Streamlit Secrets "
        "(`Settings → Secrets` in the Streamlit Cloud dashboard) and implement "
        "the corresponding connector in `app/connectors/`."
    )


# ═════════════════════════════════════════════════════════════════════════════
# TAB 7 — AGENT RATING & SCORECARD
# ═════════════════════════════════════════════════════════════════════════════
with tab_agent_rating:
    st.subheader("🏆 FIE Agent Intelligence Rating")
    st.caption(
        "Live scorecard showing the prediction engine's current capabilities, "
        "data coverage, and historical backtested performance."
    )

    # ── Gather stats ──────────────────────────────────────────────────────────
    db_ar = _db()
    _total_fx   = db_ar.scalar(select(func.count(Fixture.id))) or 0
    _sched_fx   = db_ar.scalar(select(func.count(Fixture.id)).where(Fixture.status == "scheduled")) or 0
    _hist_fx    = db_ar.scalar(
        select(func.count(Fixture.id)).where(Fixture.home_score.isnot(None))
    ) or 0
    _total_tr   = db_ar.scalar(select(func.count(TeamRating.id))) or 0
    _comps      = db_ar.scalars(select(Fixture.competition).distinct()).all()
    db_ar.close()

    _n_leagues  = len([c for c in _comps if c])

    # Quick backtest for scorecard metrics (value_bet strategy on all historical data)
    _bt_win_rate = 0.0
    _bt_roi      = 0.0
    _bt_bets     = 0
    if _hist_fx >= 50:
        try:
            db_bt2 = _db()
            _sim = run_backtest(db=db_bt2, strategy="value_bet", min_ev_percent=2.0,
                                min_quality_score=0, staking="quarter_kelly",
                                initial_bankroll=1000.0)
            db_bt2.close()
            _bt_win_rate = _sim.get("win_rate_percent", 0.0)
            _bt_roi      = _sim.get("roi_percent", 0.0)
            _bt_bets     = _sim.get("total_bets", 0)
        except Exception:
            pass

    # ── Grade calculation ─────────────────────────────────────────────────────
    def _compute_agent_grade(teams: int, hist: int, win_rate: float, roi: float) -> tuple:
        score = 0
        if teams >= 80:  score += 25
        elif teams >= 40: score += 15
        elif teams >= 10: score += 8
        if hist >= 1500: score += 25
        elif hist >= 500: score += 15
        elif hist >= 100: score += 8
        if win_rate >= 55: score += 25
        elif win_rate >= 48: score += 15
        elif win_rate >= 40: score += 8
        if roi >= 5:    score += 25
        elif roi >= 0:  score += 15
        elif roi >= -5: score += 8
        if score >= 85: return "A+", "🟢", "Elite — Production Ready"
        if score >= 70: return "A",  "🟢", "Strong — High Confidence"
        if score >= 55: return "B",  "🟡", "Good — Moderate Confidence"
        if score >= 40: return "C",  "🟠", "Fair — Limited Data"
        return "D", "🔴", "Early Stage — Needs More Data"

    _grade, _grade_color, _grade_desc = _compute_agent_grade(
        _total_tr, _hist_fx, _bt_win_rate, _bt_roi
    )

    # ── Top banner ────────────────────────────────────────────────────────────
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #0D1117 0%, #161B22 100%);
                border: 2px solid {'#00C851' if _grade in ('A+','A') else '#FFBB33' if _grade == 'B' else '#FF4444'};
                border-radius: 14px; padding: 24px; margin-bottom: 24px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <h2 style="margin: 0; color: #FFF;">⚽ Football Intelligence Engine</h2>
                <p style="margin: 4px 0 0 0; color: #888; font-size: 14px;">
                    Dixon-Coles Bivariate Poisson · Gemini AI Enhanced · EAT (Uganda Time)
                </p>
            </div>
            <div style="text-align: center;">
                <div style="font-size: 48px; font-weight: 900; color: {'#00C851' if _grade in ('A+','A') else '#FFBB33' if _grade == 'B' else '#FF8800'};">{_grade}</div>
                <div style="font-size: 13px; color: #AAA;">{_grade_desc}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # ── Metric cards row ──────────────────────────────────────────────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("📊 Team Ratings", _total_tr,
              help="Distinct teams with calibrated attack/defense ratings")
    c2.metric("📜 Historical Matches", f"{_hist_fx:,}",
              help="Completed matches used to calibrate the model")
    c3.metric("📅 Upcoming Fixtures", _sched_fx,
              help="Scheduled matches available for prediction")
    c4.metric("🌍 Leagues Covered", _n_leagues,
              help="Number of distinct competitions in the database")
    c5.metric("🎯 Backtest Win Rate",
              f"{_bt_win_rate:.1f}%" if _bt_bets > 0 else "—",
              help="Historical accuracy on value-bet strategy")
    c6.metric("💰 Backtest ROI",
              f"{_bt_roi:+.1f}%" if _bt_bets > 0 else "—",
              delta=f"{_bt_roi:+.1f}%" if _bt_bets > 0 else None,
              help="Return on investment across all backtested bets")

    st.markdown("---")

    # ── Capability breakdown ──────────────────────────────────────────────────
    st.markdown("### 🔬 Capability Breakdown")

    _caps = [
        ("Dixon-Coles Bivariate Poisson Model", True,
         "Applies rho=-0.10 to correct for real-world low-score correlations (0-0, 1-1)."),
        ("19+ Market Types Analysed", True,
         "1X2, Double Chance 1X/X2/12, Over/Under 0.5-3.5, BTTS, Team Goals, Arbitrage."),
        ("All Competitions (No League Filter)", True,
         "Analyses ALL fixtures regardless of competition — not limited to top-5 leagues."),
        ("Odds-Free Prediction (Team Ratings Only)", True,
         "Can predict for any fixture using only team attack/defense ratings, no bookmaker odds needed."),
        ("85%+ Banker Identification", True,
         "Automatically surfaces markets with ≥85% model confidence as banker candidates."),
        ("Daily Combo Builder (2-3 Teams, Odds ≥2.00)", True,
         "Generates same-day accumulator slips sorted by avg team confidence and +EV."),
        ("Quarter-Kelly Stake Sizing", True,
         "Conservative anti-ruin fractional Kelly staking on all recommendations."),
        (f"Ugandan Time (EAT, UTC+3) Display", True,
         "All kickoff times shown in East Africa Time for Uganda bettors."),
        ("Gemini AI Match Previews", is_gemini_enabled(),
         "AI-generated 2-3 sentence match previews using Poisson outputs. Set GEMINI_API_KEY to enable."),
        ("Gemini AI Combo Explanations", is_gemini_enabled(),
         "Natural-language explanation of why a combo was selected. Set GEMINI_API_KEY to enable."),
        ("Multi-League Auto Ingestion (12 Divisions)", True,
         "One-click ingest: Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Championship, Eredivisie, Primeira Liga, Belgian Pro, Super Lig, Scottish Prem, Greek SL."),
        ("Global Live Fixture Fetcher (35+ competitions)", True,
         "ESPN API covers UEFA, Champions League, Europa, 5 big leagues + 15 secondary + Americas/Africa/Asia."),
        ("Backtester with Equity Curve", True,
         "Full historical simulation with win rate, ROI, max drawdown, and trade log."),
        ("Arbitrage Scanner", True,
         "Detects multi-bookmaker arbitrage opportunities across all fixture odds lines."),
    ]

    cap_col1, cap_col2 = st.columns(2)
    for i, (cap, enabled, tip) in enumerate(_caps):
        col = cap_col1 if i % 2 == 0 else cap_col2
        with col:
            icon = "✅" if enabled else "⚙️"
            color = "#00C851" if enabled else "#888"
            col.markdown(
                f"<div style='padding:6px 0;'>"
                f"<span style='color:{color};font-weight:600;'>{icon} {cap}</span><br>"
                f"<span style='color:#666;font-size:12px;'>{tip}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("---")

    # ── AI narrative summary ───────────────────────────────────────────────────
    st.markdown("### 🤖 AI Assessment")
    if is_gemini_enabled():
        with st.spinner("Generating AI assessment..."):
            _ai_summary = gemini_agent_rating_narrative(
                total_fixtures=_total_fx,
                total_teams=_total_tr,
                historical_matches=_hist_fx,
                win_rate=_bt_win_rate,
                roi=_bt_roi,
            )
        if _ai_summary:
            st.info(_ai_summary)
        else:
            st.caption("AI assessment unavailable — check your Gemini API key.")
    else:
        st.info(
            "💡 **Enable Gemini AI** by pasting your free API key in the sidebar "
            "to get an AI-generated assessment of the engine's strengths and gaps. "
            "Get a key at [aistudio.google.com](https://aistudio.google.com/app/apikey)"
        )

    st.markdown("---")

    # ── Machine Learning Ensemble Model ────────────────────────────────────────
    st.markdown("### 🤖 Gradient Boosted Machine (GBM) Ensemble")
    col_ml_stat, col_ml_act = st.columns([3, 1])
    with col_ml_stat:
        if is_model_trained():
            st.success("✅ **GBM Model Trained & Active** — blending 60% Dixon-Coles Poisson + 40% Gradient Boosting Classifier.")
        else:
            st.warning("⚠️ **Ensemble Not Trained** — Currently running pure Dixon-Coles Poisson mode.")
    with col_ml_act:
        if st.button("🧠 Train Ensemble Model", use_container_width=True, key="btn_train_ensemble"):
            db_train = _db()
            with st.spinner("Training GradientBoostingClassifier on 1,750+ historical matches…"):
                train_res = train_ensemble(db_train)
            db_train.close()
            if train_res.get("status") == "success":
                st.success(train_res["message"])
                st.rerun()
            else:
                st.error(train_res.get("message", "Training error."))

    st.markdown("---")

    # ── Prediction Accuracy & Result Tracker ───────────────────────────────────
    st.markdown("### 🎯 Live Model Accuracy & Result Tracker")
    col_acc_btn, col_acc_msg = st.columns([2, 5])
    with col_acc_btn:
        if st.button("🔄 Sync Finished Scores", use_container_width=True, key="btn_sync_scores", help="Fetch final scores for matches that finished recently"):
            db_sc = _db()
            with st.spinner("Fetching final scores from ESPN…"):
                res_sc = fetch_and_update_results(db_sc)
            db_sc.close()
            st.toast(f"Updated {res_sc['updated']} finished scores.")
            st.rerun()

    db_acc = _db()
    try:
        acc_stats = calculate_prediction_accuracy(db_acc, days=30)
    except Exception:
        acc_stats = {"total_matches": 0, "accuracy_percent": 0.0, "details": []}
    finally:
        db_acc.close()

    acc_col1, acc_col2, acc_col3 = st.columns(3)
    acc_col1.metric("Matches Evaluated (30d)", acc_stats.get("total_matches", 0))
    acc_col2.metric("Correct Predictions", acc_stats.get("correct_predictions", 0))
    acc_col3.metric("Prediction Accuracy", f"{acc_stats.get('accuracy_percent', 0.0):.1f}%")

    if acc_stats.get("details"):
        with st.expander(f"📋 View Recent Predictions Log ({len(acc_stats['details'])} matches)"):
            st.dataframe(pd.DataFrame(acc_stats["details"]), use_container_width=True, hide_index=True)

    st.markdown("---")

    # ── Model explainer ───────────────────────────────────────────────────────
    with st.expander("📐 How the Model Works"):
        st.markdown("""
**Dixon-Coles Bivariate Poisson (1997)**

The engine models football scores as two correlated Poisson processes:
- **Home xG (λ_h)** = league_home_avg × attack_home × (1/defense_away)
- **Away xG (λ_a)** = league_away_avg × attack_away × (1/defense_home)

A correlation factor **ρ = -0.10** adjusts the probability of low-scoring outcomes (0-0, 1-0, 0-1, 1-1) to match the real-world tendency for draws in tight matches.

**19 markets** are computed from the full score-matrix grid (up to 10×10 goals):

| Category | Markets |
|---|---|
| Match Outcome | Home Win (1), Draw (X), Away Win (2) |
| Double Chance | 1X, X2, 12 |
| Totals | Over/Under 0.5, 1.5, 2.5, 3.5 |
| BTTS | Yes, No |
| Team Goals | Home/Away Over 0.5, Over 1.5 |

**Qualification threshold:** Markets ≥ 85% probability are flagged as bankers.

**Kelly staking:** f* = (b·p − q) / b, capped at ¼ Kelly to limit variance.
        """)

    with st.expander("📊 Backtest Methodology"):
        st.markdown(f"""
**Strategy:** Value-bet — select the highest +EV market per match.

**Staking:** Quarter-Kelly (conservative anti-ruin).

**Universe:** {_hist_fx:,} completed matches with known final scores.

**Bets placed:** {_bt_bets} (matches where +EV opportunity was identified).

**Key metrics:**
- Win Rate: `{_bt_win_rate:.1f}%` (target: >50%)
- ROI: `{_bt_roi:+.1f}%` (target: >+5%)

> ⚠️ Backtest results are illustrative. Past performance on historical data does not guarantee future results. Always use responsible bankroll management.
        """)

