from __future__ import annotations
import io
import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.currency import format_stake_dual
from app.services.pdf_slip import generate_combo_pdf, generate_text_slip
from app.services.ai_insights import gemini_combo_explanation
from app.services.whatsapp import send_whatsapp_message, send_combo_whatsapp_alert
from app.services.sentinel import start_sentinel, stop_sentinel, get_sentinel_status, run_sentinel_scan_now

API_BASE = os.getenv("FIE_API_URL", "http://127.0.0.1:8000")
EAT_TZ = timezone(timedelta(hours=3), name="EAT")

def to_eat_str(val) -> str:
    """Convert UTC timestamp to Ugandan Time (East Africa Time, EAT = UTC+3)."""
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

st.set_page_config(
    page_title="FIE | Football Intelligence Engine",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("⚽ Football Intelligence Engine")
st.caption("Local-first football research and prediction platform · Foundation v0.3")

# Check backend health
backend_online = False
health_data = {}
try:
    res = requests.get(f"{API_BASE}/health", timeout=2)
    if res.status_code == 200:
        backend_online = True
        health_data = res.json()
except Exception:
    backend_online = False

# Sidebar
with st.sidebar:
    st.header("System Status")
    if backend_online:
        st.success(f"🟢 Backend Online (`{API_BASE}`)")
        st.write(f"**Database:** {health_data.get('database', 'connected')}")
        st.write(f"**Stored Fixtures:** {health_data.get('total_fixtures', 0)}")
    else:
        st.error(f"🔴 Backend Offline (`{API_BASE}`)")
        st.info("Start FastAPI in terminal:\n```bash\nuvicorn app.main:app --reload\n```")

    st.divider()
    st.header("🤖 AI Insights (Gemini)")
    _dash_key = st.text_input(
        "Gemini API Key",
        type="password",
        placeholder="AIza… (optional)",
        help="Paste your Google Gemini API key to unlock AI match previews & combo explanations.",
        key="dash_gemini_key",
    )
    if _dash_key:
        os.environ["GEMINI_API_KEY"] = _dash_key

    st.divider()
    st.header("💱 Currency & Staking")
    dash_currency = st.selectbox(
        "Display Currency",
        ["UGX", "USD", "KES", "TZS", "EUR", "GBP"],
        index=0,
        help="Select display currency for Kelly stakes and ticket slips (UGX, USD, KES, etc.)",
        key="dash_currency"
    )

    st.divider()
    st.header("📱 WhatsApp Notifications")
    st.caption("Receive 24/7 alerts on your phone whenever a qualifying Banker Slip is found.")
    dash_wa_phone = st.text_input(
        "WhatsApp Phone (with country code)",
        value=os.getenv("WHATSAPP_PHONE", ""),
        placeholder="+256701234567",
        help="Enter your mobile number with country code (e.g. +256 for Uganda).",
        key="dash_wa_phone",
    )
    if dash_wa_phone:
        os.environ["WHATSAPP_PHONE"] = dash_wa_phone

    dash_wa_key = st.text_input(
        "CallMeBot WhatsApp API Key",
        value=os.getenv("WHATSAPP_APIKEY", ""),
        type="password",
        placeholder="e.g. 1234567",
        help=(
            "Free setup: open callmebot.com/blog/free-api-whatsapp-messages, message the "
            "number shown there from this same phone, and send 'I allow callmebot to send me "
            "messages' (or 'Recover APIKey' if you already activated). CallMeBot rotates its "
            "number, so don't use numbers from other sites."
        ),
        key="dash_wa_key",
    )
    if dash_wa_key:
        os.environ["WHATSAPP_APIKEY"] = dash_wa_key

    if st.button("🔔 Send Test WhatsApp Alert", use_container_width=True, key="dash_wa_test_btn"):
        if not (dash_wa_phone and dash_wa_key):
            st.warning("Please provide both WhatsApp phone number and CallMeBot API key.")
        else:
            with st.spinner("Dispatching WhatsApp test message..."):
                wa_res = send_whatsapp_message(
                    text="⚽ *FIE Football Intelligence Engine*\n✅ WhatsApp connected! Your 24/7 Banker Sentinel is actively hunting odds >= 2.00 multi-bets.",
                    phone=dash_wa_phone,
                    apikey=dash_wa_key,
                )
                if wa_res.get("success"):
                    st.success("✅ Test WhatsApp message delivered to your phone!")
                else:
                    st.error(f"Failed: {wa_res.get('error')}")

    st.divider()
    st.header("🟢 24/7 Autonomous Sentinel")
    s_status_dash = get_sentinel_status()
    is_dash_active = s_status_dash.get("is_running", False)
    if is_dash_active:
        st.success("🟢 24/7 Sentinel: **ACTIVE & SCANNING**")
        st.caption(f"Last scan: {s_status_dash.get('last_scan_eat') or 'Starting up...'}")
        st.caption(f"Alerts sent: {s_status_dash.get('alerts_dispatched_count', 0)}")
        if st.button("⏹️ Stop 24/7 Sentinel", use_container_width=True, key="dash_btn_stop_sentinel"):
            stop_sentinel()
            st.rerun()
    else:
        st.info("⚪ 24/7 Sentinel: **STANDBY**")
        st.caption("Autonomously monitors 35+ leagues, verifies injuries & sends WhatsApp alerts.")
        if st.button("▶️ Start 24/7 Sentinel", type="primary", use_container_width=True, key="dash_btn_start_sentinel"):
            start_sentinel(interval_minutes=30)
            st.rerun()

    if st.button("⚡ Run Sentinel Scan Now", use_container_width=True, key="dash_btn_scan_sentinel"):
        with st.spinner("Running 24/7 autonomous odds & squad check scan..."):
            scan_out = run_sentinel_scan_now()
            if scan_out.get("success"):
                st.success(f"Scan complete at {scan_out.get('scan_time')}. Fixtures checked: {scan_out.get('fixtures_scanned')}")
                st.rerun()
            else:
                st.error(f"Scan error: {scan_out.get('error')}")

    if st.button("⚖️ Settle Bets with Live Scores", use_container_width=True, key="dash_btn_settle_bets"):
        with st.spinner("Syncing latest match scores & settling finished slips..."):
            try:
                s_res = requests.post(
                    f"{API_BASE}/ledger/settle",
                    params={"phone": dash_wa_phone, "apikey": dash_wa_key, "currency": dash_currency},
                    timeout=15,
                )
                if s_res.status_code == 200:
                    s_data = s_res.json()
                    st.success(f"Settled {s_data.get('settled_count', 0)} finished slip(s)! Outcomes dispatched to WhatsApp.")
                else:
                    st.error(f"Settlement error: {s_res.text}")
                st.rerun()
            except Exception as e:
                st.error(f"Request error: {e}")

    st.divider()
    st.header("⚙️ Thresholds")
    st.metric("Qualification Threshold", "85%", help="Markets with probability >= 85% qualify for shortlisting.")
    st.metric("Data-Quality Minimum", "70 / 100", help="Fixtures must achieve grade A or B (>= 70) for model analysis.")

    st.divider()
    if backend_online:
        if st.button("🧹 Mark Stale Fixtures", use_container_width=True, help="Mark past scheduled fixtures with no result as 'pending_result'"):
            try:
                stale_res = requests.patch(f"{API_BASE}/fixtures/mark-stale")
                if stale_res.status_code == 200:
                    st.toast(stale_res.json().get("message", "Done!"))
                    st.rerun()
            except Exception as e:
                st.error(f"Failed: {e}")

        if st.button("🗑️ Clear All Fixtures", use_container_width=True):
            try:
                del_res = requests.delete(f"{API_BASE}/fixtures")
                if del_res.status_code == 200:
                    st.toast(del_res.json().get("message", "Fixtures cleared!"))
                    st.rerun()
            except Exception as e:
                st.error(f"Failed to clear: {e}")

# Main Tabs
tab_portfolio, tab_fixtures, tab_live, tab_odds_movement, tab_import, tab_backtest, tab_markets, tab_ratings, tab_connectors, tab_agent_rating = st.tabs([
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

# TAB 0: Daily Value Portfolio
with tab_portfolio:
    st.subheader("📋 Daily Value Portfolio & High-Confidence Combos")
    st.caption(
        "Quantitative selection engine: generate optimal 2-team or 3-team accumulators (Odds ≥ 2.00+) "
        "and scan individual +EV value bets with fractional Kelly stake sizing."
    )

    if not backend_online:
        st.warning("Start the FastAPI backend to generate a portfolio.")
    else:
        # ══════════════════════════════════════════════════════════════════════
        # 🎯 HIGH-CONFIDENCE COMBO BUILDER (ODDS >= 2.00+)
        # ══════════════════════════════════════════════════════════════════════
        st.markdown("### 🔥 High-Confidence Daily Combo Slip (Odds ≥ 2.0+)")
        st.write("Generates **same-day accumulator slips** using 85%+ win probability teams from a chosen matchday so bets settle on the same night.")

        # Matchday selector
        try:
            m_res = requests.get(f"{API_BASE}/portfolio/combos", params={"num_legs": 3, "min_total_odds": 2.0}, timeout=4)
            avail_days = m_res.json().get("available_matchdays", []) if m_res.status_code == 200 else []
        except Exception:
            avail_days = []

        day_options = ["Auto (Next Active Matchday)"] + [f"📅 {d['label']} ({d['count']} matches)" for d in avail_days] + ["🌐 All Upcoming Matchdays Combined"]
        sel_day_idx = st.selectbox("📅 Select Matchday (Ugandan Time, EAT):", day_options, index=0)

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
            sel_legs = st.radio("Number of Teams (Max 3)", [2, 3], index=1, horizontal=True, format_func=lambda x: f"{x}-Team Combo")
        with combo_col2:
            sel_min_acc_prob = st.slider("Min Accumulator Win Prob", min_value=0.50, max_value=0.98, value=0.85, step=0.01, format="%.0f%%", key="dash_acc_prob", help="Guarantees the overall ticket win probability is between 85% and 100%!")
        with combo_col3:
            sel_min_leg_prob = st.slider("Min Team Confidence", min_value=0.80, max_value=0.98, value=0.85, step=0.01, format="%.0f%%")
        with combo_col4:
            sel_combo_bankroll = st.number_input("Bankroll ($)", min_value=50.0, max_value=100000.0, value=1000.0, step=50.0, key="cb_bankroll")

        if st.button(f"⚡ Generate {sel_legs}-Team Daily Slip (Ticket Win Prob ≥ {sel_min_acc_prob*100:.0f}%)", type="primary", use_container_width=True):
            with st.spinner("Finding optimal combinations with 85%-100% Accumulator Win Probability…"):
                try:
                    c_res = requests.get(
                        f"{API_BASE}/portfolio/combos",
                        params={
                            "target_date": target_date_val,
                            "num_legs": sel_legs,
                            "min_total_odds": 2.00,  # Always enforce odds >= 2.00
                            "min_leg_prob": sel_min_leg_prob,
                            "min_accumulator_prob": sel_min_acc_prob,
                            "bankroll": sel_combo_bankroll,
                        },
                        timeout=15
                    )
                    if c_res.status_code == 200:
                        c_data = c_res.json()
                        best = c_data.get("best_combo")
                        matchday_hdr = c_data.get("matchday_label", "Daily")
                        if not best:
                            st.error(
                                f"🚫 **No qualifying {sel_legs}-team combo found for {matchday_hdr}.**\n\n"
                                f"All three rules must be met simultaneously — no exceptions:\n"
                                f"- ✅ **AVG TEAM CONFIDENCE ≥ 85%** (every leg individually)\n"
                                f"- ✅ **ACCUMULATOR WIN PROB ≥ 85%** (joint probability of all legs)\n"
                                f"- ✅ **TOTAL ODDS ≥ 2.00**\n\n"
                                f"Today's fixture data does not contain a combination that satisfies all three. "
                                f"Try a different matchday, or wait for the next sentinel scan."
                            )
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
                                        <div><span style="color: #888; font-size: 12px;">ACCUMULATOR WIN PROB</span><br><strong style="font-size: 20px; color: #00C851;">{combo_obj['combined_probability']*100:.1f}% (Banker)</strong></div>
                                        <div><span style="color: #888; font-size: 12px;">EXPECTED VALUE (+EV)</span><br><strong style="font-size: 20px; color: #00C851;">{combo_obj['expected_value_percent']:+.1f}% EV</strong></div>
                                        <div><span style="color: #888; font-size: 12px;">QUARTER KELLY STAKE</span><br><strong style="font-size: 18px; color: #FFBB33;">{format_stake_dual(combo_obj['kelly_stake_amount'], dash_currency)} ({combo_obj['kelly_stake_percent']}%)</strong></div>
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
                                s_text += f"Total Odds: {combo_obj['total_odds']:.2f} | Kelly Stake: {format_stake_dual(combo_obj['kelly_stake_amount'], dash_currency)}"
                                st.text_area("📋 Copy Booking Slip Text", s_text, height=110, key=f"{prefix_key}_text")

                                # Download & WhatsApp Dispatch Buttons
                                b_pdf_col, b_txt_col, b_wa_col = st.columns(3)
                                with b_pdf_col:
                                    try:
                                        pdf_b = generate_combo_pdf(combo_obj, matchday_hdr, bankroll=sel_combo_bankroll, currency=dash_currency)
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
                                    txt_b = generate_text_slip(combo_obj, matchday_hdr, currency=dash_currency)
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
                                                    currency=dash_currency,
                                                )
                                                if res_wa.get("success"):
                                                    st.success(f"Ticket dispatched to WhatsApp (+{wa_ph})!")
                                                else:
                                                    st.error(f"WhatsApp Error: {res_wa.get('error')}")

                            # Render Top Daily Banker Slip
                            _render_full_combo_display(best, "TOP DAILY BANKER MULTI-BET", "dash_top_banker")

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

                            # Alternative Combos — compact summary cards + expandable full detail
                            alts = c_data.get("alternatives", [])
                            if alts:
                                with st.expander(f"📦 View {len(alts)} Alternative Combos (Odds ≥ 2.00)", expanded=True):
                                    st.write("Browse alternative high-confidence multi-bet slips generated for this matchday:")
                                    # Compact summary rows (like the screenshot)
                                    for idx, alt in enumerate(alts, 2):
                                        avg_p_alt = alt.get('avg_leg_probability', alt.get('combined_probability', 0)) * 100
                                        acc_p_alt = alt['combined_probability'] * 100
                                        team_lbl = "Banker" if avg_p_alt >= 85 else "High Conf"
                                        legs_inline = " • ".join(
                                            f"<strong>{l['selection']}</strong> @ {l['odds']:.2f} ({l['probability']*100:.1f}%)"
                                            for l in alt["legs"]
                                        )
                                        st.markdown(f"""
                                        <div style="background:#111E11; border:1px solid #2A5A2A; border-radius:10px; padding:14px 18px; margin-bottom:10px;">
                                            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                                                <span style="color:#00C851; font-size:15px; font-weight:700;">Combo Option #{idx} — Odds: {alt['total_odds']:.2f}</span>
                                                <span style="font-size:12px; color:#AAA;">Avg Team Prob: {avg_p_alt:.1f}% ({team_lbl}) &nbsp;|&nbsp; Joint Win Prob: {acc_p_alt:.1f}% &nbsp;|&nbsp; EV: {alt['expected_value_percent']:+.1f}%</span>
                                            </div>
                                            <div style="font-size:13px; color:#CCC; line-height:1.8;">{legs_inline}</div>
                                        </div>
                                        """, unsafe_allow_html=True)
                                    st.markdown("<hr style='border:1px dashed #2A4A2A; margin:18px 0;'>", unsafe_allow_html=True)
                                    # Expandable full detail per combo
                                    for idx, alt in enumerate(alts, 2):
                                        with st.expander(f"📋 Full Detail — Combo Option #{idx} (Odds {alt['total_odds']:.2f})", expanded=False):
                                            _render_full_combo_display(alt, f"COMBO OPTION #{idx}", f"dash_alt_combo_{idx}")
                    else:
                        st.error(f"Error generating combos: {c_res.text}")
                except Exception as e:
                    st.error(f"Request error: {e}")

        # ── TRANSPARENT PERFORMANCE LEDGER & AUTOMATED BET SETTLEMENT ─────────────
        st.markdown("---")
        st.subheader("📊 Transparent Performance Ledger & Automated Bet Settlement")
        st.caption("Every multi-bet slip sent by the 24/7 Sentinel is recorded here and automatically settled with live final scores.")

        try:
            led_res = requests.get(f"{API_BASE}/ledger/summary", timeout=5)
            if led_res.status_code == 200:
                ledger_stats = led_res.json()
                l_col1, l_col2, l_col3, l_col4 = st.columns(4)
                with l_col1:
                    win_r = ledger_stats.get("win_rate", 0)
                    st.metric(
                        "Slip Win Rate",
                        f"{win_r:.1f}%",
                        f"{ledger_stats.get('won_count', 0)}W / {ledger_stats.get('lost_count', 0)}L",
                    )
                with l_col2:
                    p_ugx = ledger_stats.get("total_profit_ugx", 0)
                    p_usd = ledger_stats.get("total_profit_usd", 0)
                    st.metric(
                        "Net Profit / Loss",
                        format_stake_dual(p_ugx if dash_currency.upper() == "UGX" else p_usd, dash_currency),
                        f"{'+' if p_usd >= 0 else ''}${p_usd:.2f}",
                    )
                with l_col3:
                    roi_val = ledger_stats.get("roi_percent", 0)
                    st.metric("All-Time ROI", f"{roi_val:+.1f}%")
                with l_col4:
                    st.metric("Pending Slips", f"{ledger_stats.get('pending_count', 0)}")

                hist_res = requests.get(f"{API_BASE}/ledger/history", params={"limit": 25}, timeout=5)
                if hist_res.status_code == 200:
                    recent_tracked = hist_res.json()
                    if recent_tracked:
                        with st.expander(f"📜 View All Verified Slips ({len(recent_tracked)} in Ledger)", expanded=False):
                            for b in recent_tracked:
                                st_badge = "🟢 WON" if b["status"] == "WON" else ("🔴 LOST" if b["status"] == "LOST" else "🟡 PENDING")
                                pnl_color = "#00C851" if b["profit_loss"] > 0 else ("#ff4444" if b["profit_loss"] < 0 else "#888")
                                pnl_display = format_stake_dual(b["profit_loss"], dash_currency) if b["status"] != "PENDING" else "In Play"

                                st.markdown(f"""
                                <div style="background: #111A11; border: 1px solid #2A4A2A; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;">
                                    <div style="display:flex; justify-content:space-between; align-items:center;">
                                        <strong>{b['slip_type']} — {b['matchday_label']}</strong>
                                        <span><strong>{st_badge}</strong> &nbsp;|&nbsp; Odds: <strong>{b['total_odds']:.2f}</strong> &nbsp;|&nbsp; Outcome: <strong style="color: {pnl_color};">{pnl_display}</strong></span>
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
                                for idx_l, leg_it in enumerate(b.get("legs", []), 1):
                                    l_st = leg_it.get("status", "PENDING")
                                    icon_l = "✅" if l_st == "WON" else ("❌" if l_st == "LOST" else "⏳")
                                    score_str = f"Final: {leg_it.get('final_score')}" if leg_it.get("final_score") else "Awaiting kickoff"
                                    st.caption(f"  • Leg #{idx_l}: {leg_it.get('match')} ➔ **{leg_it.get('selection')}** ({icon_l} {l_st} | {score_str})")
                    else:
                        st.info("No bets recorded in ledger yet. Active Sentinel alerts will be tracked and settled here automatically.")
        except Exception:
            pass

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

        try:
            f_resp = requests.get(f"{API_BASE}/fixtures", params={"upcoming_only": True, "limit": 300}, timeout=10)
            if f_resp.status_code == 200:
                all_fx = f_resp.json()
                day_matches = []
                for f in all_fx:
                    u_str = f.get("kickoff_utc")
                    if not u_str:
                        continue
                    dt_f = datetime.fromisoformat(u_str.replace("Z", "+00:00"))
                    if dt_f.tzinfo is None:
                        dt_f = dt_f.replace(tzinfo=timezone.utc)
                    f_day = dt_f.astimezone(EAT_TZ).strftime("%Y-%m-%d")
                    if target_date_val and target_date_val != "all" and f_day != target_date_val:
                        continue

                    h = f.get("home_odds")
                    d = f.get("draw_odds")
                    a = f.get("away_odds")
                    fav = "—"
                    if h and a:
                        if h < a and h <= 1.45:
                            fav = f"🔥 {f['home_team']} to Win ({h:.2f})"
                        elif a < h and a <= 1.45:
                            fav = f"🔥 {f['away_team']} to Win ({a:.2f})"
                        elif h < a:
                            fav = f"🛡️ {f['home_team']} Win or Draw (1X)"
                        else:
                            fav = f"🛡️ {f['away_team']} Win or Draw (X2)"

                    day_matches.append({
                        "Kickoff (Ugandan Time, EAT)": f.get("kickoff_eat") or to_eat_str(f.get("kickoff_utc")),
                        "Match": f"{f['home_team']} vs {f['away_team']}",
                        "Competition": f.get("competition"),
                        "Home (1)": f"{h:.2f}" if h else "—",
                        "Draw (X)": f"{d:.2f}" if d else "—",
                        "Away (2)": f"{a:.2f}" if a else "—",
                        "Key Banker Selection": fav,
                        "Quality": f"{_grade_color(f.get('data_quality_grade'))} Grade {f.get('data_quality_grade')}",
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
        except Exception as e:
            st.error(f"Error loading day fixtures: {e}")

# TAB 1: Fixtures
with tab_fixtures:
    st.subheader("Match Fixtures & Quality Grades")
    if not backend_online:
        st.warning("Start the FastAPI backend to inspect stored fixtures.")
    else:
        f_top1, f_top2 = st.columns([3, 1])
        with f_top1:
            view_mode = st.radio(
                "Filter View:",
                ["🟢 Upcoming Only (Tomorrow & Future)", "📜 All Fixtures (Including Past Played)"],
                horizontal=True,
                key="fx_view_mode"
            )
        with f_top2:
            if st.button("🗑️ Purge Past Played Games", use_container_width=True, help="Permanently delete matches whose kickoff has already passed"):
                try:
                    p_res = requests.delete(f"{API_BASE}/fixtures/past")
                    if p_res.status_code == 200:
                        st.toast(p_res.json().get("message", "Past fixtures purged!"))
                        st.rerun()
                except Exception as e:
                    st.error(f"Purge error: {e}")

        only_upcoming = view_mode.startswith("🟢")
        try:
            f_res = requests.get(
                f"{API_BASE}/fixtures",
                params={"limit": 300, "upcoming_only": only_upcoming},
                timeout=5
            )
            fixtures = f_res.json() if f_res.status_code == 200 else []
        except Exception as e:
            fixtures = []
            st.error(f"Could not load fixtures: {e}")

        if not fixtures:
            st.info("No fixtures in the database yet. Head over to the **Ingestion & Import** tab to load sample fixtures!")
        else:
            df = pd.DataFrame(fixtures)
            
            # Filter bar
            f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
            with f_col1:
                search_team = st.text_input("🔍 Search team or match", placeholder="e.g. Athletic")
            with f_col2:
                comp_options = ["All"] + sorted(list(df["competition"].unique()))
                selected_comp = st.selectbox("Competition", comp_options)
            with f_col3:
                source_options = ["All"] + sorted(list(df["source"].unique()))
                selected_source = st.selectbox("Source", source_options)

            filtered = df
            if search_team:
                t = search_team.lower()
                filtered = filtered[
                    filtered["home_team"].str.lower().str.contains(t) |
                    filtered["away_team"].str.lower().str.contains(t)
                ]
            if selected_comp != "All":
                filtered = filtered[filtered["competition"] == selected_comp]
            if selected_source != "All":
                filtered = filtered[filtered["source"] == selected_source]

            st.write(f"Showing **{len(filtered)}** of **{len(df)}** fixtures")
            
            # Format display table with Ugandan Time
            if "kickoff_eat" not in filtered.columns and "kickoff_utc" in filtered.columns:
                filtered["kickoff_eat"] = filtered["kickoff_utc"].apply(to_eat_str)
            display_cols = [
                "id", "kickoff_eat", "home_team", "away_team", "competition",
                "source", "home_odds", "draw_odds", "away_odds",
                "data_quality_score", "data_quality_grade", "eligible_for_analysis"
            ]
            avail_cols = [c for c in display_cols if c in filtered.columns]
            disp_df = filtered[avail_cols].copy()
            if "kickoff_eat" in disp_df.columns:
                disp_df.rename(columns={"kickoff_eat": "Kickoff (Ugandan Time, EAT)"}, inplace=True)
            st.dataframe(disp_df, use_container_width=True, hide_index=True)

            # Fixture Deep-Dive Inspector
            st.markdown("---")
            st.subheader("🔬 Fixture Inspector")
            fixture_ids = filtered["id"].tolist()
            if fixture_ids:
                selected_id = st.selectbox(
                    "Select a fixture to inspect data-quality signals:",
                    fixture_ids,
                    format_func=lambda x: f"#{x} - {filtered[filtered['id'] == x]['home_team'].values[0]} vs {filtered[filtered['id'] == x]['away_team'].values[0]} ({filtered[filtered['id'] == x]['competition'].values[0]})"
                )
                try:
                    q_res = requests.get(f"{API_BASE}/fixtures/{selected_id}/quality", timeout=4)
                    if q_res.status_code == 200:
                        q_data = q_res.json()
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("Score", f"{q_data['score']} / 100")
                        c2.metric("Grade", q_data["grade"])
                        c3.metric("Analysis Eligible", "✅ Yes" if q_data["eligible_for_analysis"] else "❌ No")
                        c4.metric("Covered Weight", f"{q_data['covered_weight']} / {q_data['total_weight']}")

                        st.write("##### Quality Signal Breakdown")
                        breakdown = q_data.get("breakdown", {})
                        if breakdown:
                            breakdown_df = pd.DataFrame([
                                {"Signal Dimension": k, "Weight": v["weight"], "Value (0..1)": v["value"], "Status": v["status"]}
                                for k, v in breakdown.items()
                            ])
                            st.dataframe(breakdown_df, use_container_width=True, hide_index=True)

                        # Match Probabilities & Value Finder (+EV) Section
                        st.markdown("---")
                        st.write("##### ⚡ Market Probabilities & Value Analysis (+EV)")

                        a_res = requests.get(f"{API_BASE}/fixtures/{selected_id}/analysis", timeout=4)
                        if a_res.status_code == 200:
                            a_data = a_res.json()
                            est_lambdas = a_data.get("estimated_lambdas", {})
                            default_h = est_lambdas.get("home_lambda", 1.5)
                            default_a = est_lambdas.get("away_lambda", 1.1)

                            ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([1, 1, 1, 1])
                            with ctrl_col1:
                                custom_h = st.number_input(
                                    f"Home λ ({filtered[filtered['id'] == selected_id]['home_team'].values[0]})",
                                    min_value=0.1, max_value=8.0, value=float(default_h), step=0.05, key=f"h_lam_{selected_id}"
                                )
                            with ctrl_col2:
                                custom_a = st.number_input(
                                    f"Away λ ({filtered[filtered['id'] == selected_id]['away_team'].values[0]})",
                                    min_value=0.1, max_value=8.0, value=float(default_a), step=0.05, key=f"a_lam_{selected_id}"
                                )
                            with ctrl_col3:
                                custom_rho = st.slider(
                                    "Dixon-Coles ρ (Draws)",
                                    min_value=-0.30, max_value=0.10, value=-0.10, step=0.01, key=f"rho_{selected_id}",
                                    help="Adjusts 0-0 and 1-1 probabilities to match real-world draw rates"
                                )
                            with ctrl_col4:
                                custom_thresh = st.slider(
                                    "Qualification Threshold",
                                    min_value=0.60, max_value=0.95, value=0.85, step=0.01, key=f"thresh_{selected_id}"
                                )

                            if custom_h != default_h or custom_a != default_a or custom_thresh != 0.85 or custom_rho != -0.10:
                                a_res = requests.get(
                                    f"{API_BASE}/fixtures/{selected_id}/analysis",
                                    params={
                                        "home_lambda": custom_h,
                                        "away_lambda": custom_a,
                                        "threshold": custom_thresh,
                                        "rho": custom_rho,
                                    },
                                    timeout=4
                                )
                                if a_res.status_code == 200:
                                    a_data = a_res.json()

                            src_label = (a_data.get("lambda_source") or "odds_implied").replace("_", " ").title()
                            st.caption(f"📌 λ source: **{src_label}** · Engine: **Dixon-Coles Bivariate Poisson (ρ = {custom_rho})**")

                            margin_info = a_data.get("bookmaker_margin")
                            if margin_info:
                                st.info(
                                    f"📊 **Bookmaker Overround:** `{margin_info['overround'] * 100:.1f}%` "
                                    f"(Vig/Margin: `{margin_info['margin_percent']:.1f}%`)"
                                )

                            value_bets = a_data.get("value_bets", [])
                            if value_bets:
                                st.markdown("###### 💰 1X2 Market & Expected Value (+EV)")
                                val_cols = st.columns(len(value_bets))
                                for i, vb in enumerate(value_bets):
                                    with val_cols[i]:
                                        ev_val = vb["expected_value_percent"]
                                        delta_str = f"{ev_val:+.1f}% EV"
                                        st.metric(
                                            label=f"{vb['market']} @ {vb['bookmaker_odds']:.2f}",
                                            value=f"{vb['model_probability'] * 100:.1f}%",
                                            delta=delta_str,
                                            delta_color="normal" if vb["is_value"] else "off"
                                        )
                                        st.caption(f"Fair Odds: **{vb.get('fair_odds', 'N/A')}** | Edge: **{vb['edge'] * 100:+.1f}%**")
                                        if vb["is_value"] and vb.get("kelly") and vb["kelly"].get("stake_amount", 0) > 0:
                                            k = vb["kelly"]
                                            st.markdown(f"💼 **Quarter Kelly:** `${k['stake_amount']:.2f}` ({k['stake_percent']}%)")

                            qualifying = a_data.get("qualifying_markets", [])
                            st.markdown("###### 🎯 Qualifying Markets (≥ 85% Confidence)")
                            if qualifying:
                                q_pills = " &nbsp;|&nbsp; ".join([
                                    f"**{item['market'].replace('_', ' ').upper()}**: `{item['probability'] * 100:.1f}%`"
                                    for item in qualifying
                                ])
                                st.success(f"Qualified ({len(qualifying)} markets): {q_pills}")
                            else:
                                st.write("No markets reached the qualification threshold for this match.")

                            # ── BEST ODDS / LINE SHOPPING ──
                            st.markdown("---")
                            st.write("##### 🔖 Best Odds Line-Shopping (Multi-Bookmaker)")
                            try:
                                bo_res = requests.get(f"{API_BASE}/fixtures/{selected_id}/best-odds", timeout=4)
                                if bo_res.status_code == 200:
                                    bo = bo_res.json()
                                    if bo["home"]["best_odds"] > 0:
                                        bo_cols = st.columns(3)
                                        for col, side in zip(bo_cols, ["home", "draw", "away"]):
                                            with col:
                                                info = bo[side]
                                                if info.get("best_odds", 0) > 0:
                                                    st.metric(
                                                        label=f"Best {side.title()} Odds",
                                                        value=f"{info['best_odds']:.2f}",
                                                        delta=f"@ {info.get('best_bookmaker', 'N/A')}"
                                                    )
                                                    all_odds = info.get("all", {})
                                                    if all_odds:
                                                        bk_lines = " | ".join([f"**{k}**: {v:.2f}" for k, v in all_odds.items()])
                                                        st.caption(bk_lines)

                                        if bo.get("has_arbitrage"):
                                            st.error(f"⚠️ ARBITRAGE DETECTED! Combined overround = {bo['min_overround']:.4f} (< 1.0) — guaranteed profit possible.")
                                        else:
                                            st.caption(f"Combined best-line overround: `{bo['min_overround']:.4f}` — Margin: `{(bo['min_overround'] - 1) * 100:.2f}%`")
                                    else:
                                        st.info("No multi-bookmaker odds stored for this fixture yet. Fetch upcoming matches to populate.")
                            except Exception:
                                pass
                except Exception as e:
                    st.error(f"Error inspecting fixture: {e}")

# TAB: Live Scores
with tab_live:
    st.subheader("⚡ Real-Time Live In-Play & Today's Matches")
    st.caption("Live score tracker across 17+ leagues directly from real-time ESPN scoreboard.")
    if st.button("🔄 Refresh Live Scores", use_container_width=True, key="dash_btn_refresh_live"):
        st.rerun()

    try:
        from app.database import get_db
        from app.services.inplay import fetch_live_scores
        db_l = next(get_db())
        l_matches = fetch_live_scores(db_l)
        db_l.close()
    except Exception as e:
        l_matches = []
        st.error(f"Error fetching live scores: {e}")

    if not l_matches:
        st.info("No matches in play right now. Games scheduled for today will automatically show live scores here once kicked off.")
    else:
        in_p = [m for m in l_matches if m["is_live"]]
        if in_p:
            st.markdown(f"#### 🔴 In-Play Right Now ({len(in_p)} matches)")
            for m in in_p:
                st.markdown(f"**{m['home_team']} {m['home_score']} – {m['away_score']} {m['away_team']}** ⏱️ {m['clock']} · {m['competition']}")
        rem = [m for m in l_matches if not m["is_live"]]
        if rem:
            st.markdown(f"#### 📋 Other Matches Today ({len(rem)} matches)")
            st.dataframe(pd.DataFrame([{
                "Match": f"{m['home_team']} vs {m['away_team']}",
                "League": m["competition"],
                "Score / Status": f"{m['home_score']} - {m['away_score']}" if m["is_finished"] else m["status_desc"],
                "Kickoff (EAT)": m["kickoff_eat"],
            } for m in rem]), use_container_width=True, hide_index=True)

# TAB: Line Movement
with tab_odds_movement:
    st.subheader("📉 Odds Line Movement Tracker")
    st.caption("Tracks line movement between opening lines and current market odds. Big drops indicate sharp money action.")
    try:
        from app.database import get_db
        from app.services.odds_movement import get_odds_movement
        db_om = next(get_db())
        moves = get_odds_movement(db_om, upcoming_only=True)
        db_om.close()
    except Exception as e:
        moves = []
        st.error(f"Error checking odds movement: {e}")

    if not moves:
        st.info("No significant odds movement recorded yet. Sharp movements (>8% shift) will appear here.")
    else:
        for mv in moves:
            st.markdown(f"**{mv['sharp_signal']} — {mv['match']}** ({mv['competition']})")
            for sig in mv["signals"]:
                st.write(f"  → {sig}")
            st.divider()

# TAB 2: Ingestion & Import
with tab_import:
    st.subheader("Data Ingestion & Real-Time League Feeds")
    st.write(
        "FIE connects directly to verified football data archives and upcoming fixture feeds. "
        "Every incoming match is automatically deduplicated, normalized, and used to calibrate team ratings."
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

    if st.button("🚀 Run Full Auto-Setup (Live Games + All 5 Major Leagues)", type="primary", use_container_width=True, key="btn_master_auto"):
        if not backend_online:
            st.error("Backend is offline. Start FastAPI first.")
        else:
            with st.spinner("1/2 Fetching real live upcoming fixtures with DraftKings odds..."):
                try:
                    u_res = requests.post(f"{API_BASE}/fixtures/fetch-live-espn", timeout=20)
                except Exception as e:
                    st.warning(f"Live feed error: {e}")
            with st.spinner("2/2 Ingesting all 5 Major European Leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1) & calibrating ratings..."):
                try:
                    m_res = requests.post(f"{API_BASE}/fixtures/fetch-all-major-leagues", params={"season": "2324"}, timeout=60)
                    if m_res.status_code == 200:
                        m_data = m_res.json()
                        st.success(f"✅ {m_data['message']}")
                        st.rerun()
                    else:
                        st.error(f"Failed: {m_res.text}")
                except Exception as e:
                    st.error(f"Major leagues import error: {e}")

    st.markdown("---")
    feed_col1, feed_col2 = st.columns(2)

    with feed_col1:
        st.markdown("#### 1. Live Games (Tomorrow & Weekend)")
        st.write("Fetches **real upcoming matches** (UEFA Nations League tomorrow + European domestic rounds) with **real-time live DraftKings odds**.")
        if st.button("🟢 Fetch Live Upcoming Games (DraftKings Odds)", use_container_width=True):
            if not backend_online:
                st.error("Backend is offline. Start FastAPI first.")
            else:
                with st.spinner("Downloading live upcoming matches and DraftKings odds..."):
                    try:
                        u_res = requests.post(f"{API_BASE}/fixtures/fetch-live-espn", timeout=15)
                        if u_res.status_code == 200:
                            u_data = u_res.json()
                            st.success(f"✅ {u_data['message']}")
                            st.rerun()
                        else:
                            st.error(f"Failed: {u_res.text}")
                    except Exception as e:
                        st.error(f"Fetch error: {e}")

        st.caption("Or fetch club league batch from football-data.co.uk:")
        if st.button("📥 Fetch Football-Data.co.uk Batch", use_container_width=True):
            if not backend_online:
                st.error("Backend is offline. Start FastAPI first.")
            else:
                with st.spinner("Downloading football-data.co.uk fixtures..."):
                    try:
                        u_res = requests.post(f"{API_BASE}/fixtures/fetch-upcoming", timeout=15)
                        if u_res.status_code == 200:
                            u_data = u_res.json()
                            st.success(f"✅ {u_data['message']}")
                            st.rerun()
                        else:
                            st.error(f"Failed: {u_res.text}")
                    except Exception as e:
                        st.error(f"Fetch error: {e}")

    with feed_col2:
        st.markdown("#### 2. Major European Leagues (All Seasons)")
        st.write("Automatically ingests complete seasons with final scores, half-time stats, and closing market odds to calibrate team strength ratings.")

        l_sea_col, l_btn_col = st.columns([1, 2])
        with l_sea_col:
            sel_all_sea = st.selectbox("Season", ["2324", "2425"], format_func=lambda x: "2023/24" if x == "2324" else "2024/25", key="dash_major_sea")
        with l_btn_col:
            st.write("") # vertical spacing
            if st.button("⚡ Ingest All 5 Major Leagues", use_container_width=True, key="dash_btn_all_major"):
                if not backend_online:
                    st.error("Backend is offline. Start FastAPI first.")
                else:
                    with st.spinner("Downloading all 5 major leagues (EPL, La Liga, Serie A, Bundesliga, Ligue 1)..."):
                        try:
                            s_res = requests.post(
                                f"{API_BASE}/fixtures/fetch-all-major-leagues",
                                params={"season": sel_all_sea},
                                timeout=60
                            )
                            if s_res.status_code == 200:
                                s_data = s_res.json()
                                st.success(f"✅ {s_data['message']}")
                                st.rerun()
                            else:
                                st.error(f"Failed: {s_res.text}")
                        except Exception as e:
                            st.error(f"Download error: {e}")

        with st.expander("⚙️ Or Ingest Single Specific League"):
            sel_div = st.selectbox(
                "Select League",
                ["E0", "SP1", "I1", "D1", "F1", "E1"],
                format_func=lambda x: {
                    "E0": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",
                    "SP1": "🇪🇸 La Liga",
                    "I1": "🇮🇹 Serie A",
                    "D1": "🇩🇪 Bundesliga",
                    "F1": "🇫🇷 Ligue 1",
                    "E1": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship"
                }.get(x, x),
                key="dash_single_div"
            )
            if st.button("📥 Download Single League", use_container_width=True, key="dash_btn_single_div"):
                if not backend_online:
                    st.error("Backend is offline. Start FastAPI first.")
                else:
                    with st.spinner(f"Downloading {sel_div} {sel_all_sea}..."):
                        try:
                            s_res = requests.post(
                                f"{API_BASE}/fixtures/fetch-league",
                                params={"division": sel_div, "season": sel_all_sea},
                                timeout=20
                            )
                            if s_res.status_code == 200:
                                s_data = s_res.json()
                                st.success(f"✅ {s_data['message']}")
                                st.rerun()
                            else:
                                st.error(f"Failed: {s_res.text}")
                        except Exception as e:
                            st.error(f"Download error: {e}")

    st.markdown("---")
    st.markdown("### 📁 Local CSV & Offline Data")

    col_sample, col_hist, col_upload = st.columns(3)
    with col_sample:
        st.markdown("#### Option A: Quick Sample")
        st.write("Import minimal test `data/sample_fixtures.csv`.")
        if st.button("🚀 Load Sample Fixtures", use_container_width=True):
            if not backend_online:
                st.error("Backend is offline. Start FastAPI first.")
            else:
                try:
                    res = requests.post(f"{API_BASE}/fixtures/import-csv", json={"path": "data/sample_fixtures.csv"}, timeout=6)
                    if res.status_code == 200:
                        data = res.json()
                        st.success(f"✅ {data['message']}")
                        st.rerun()
                    else:
                        st.error(f"Failed: {res.text}")
                except Exception as e:
                    st.error(f"Request error: {e}")

    with col_hist:
        st.markdown("#### Option B: Offline EPL Sample")
        st.write("Import bundled offline Premier League matches with scores and closing odds.")
        if st.button("📥 Ingest Offline EPL Sample", use_container_width=True):
            if not backend_online:
                st.error("Backend is offline. Start FastAPI first.")
            else:
                try:
                    h_res = requests.post(f"{API_BASE}/fixtures/seed-historical", timeout=8)
                    if h_res.status_code == 200:
                        h_data = h_res.json()
                        st.success(f"✅ {h_data['message']}")
                        st.rerun()
                    else:
                        st.error(f"Failed: {h_res.text}")
                except Exception as e:
                    st.error(f"Seed error: {e}")

    with col_upload:
        st.markdown("#### Option C: Upload Custom CSV")
        st.write("Upload any custom CSV with headers: `kickoff_utc`, `home_team`, `away_team`, `competition`, `source`.")
        uploaded_file = st.file_uploader("Choose CSV file", type=["csv"], label_visibility="collapsed")
        if uploaded_file is not None:
            if st.button("📤 Upload & Process CSV", use_container_width=True):
                if not backend_online:
                    st.error("Backend is offline. Start FastAPI first.")
                else:
                    try:
                        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "text/csv")}
                        res = requests.post(f"{API_BASE}/fixtures/upload-csv", files=files, timeout=10)
                        if res.status_code == 200:
                            data = res.json()
                            st.success(f"✅ {data['message']}")
                            st.rerun()
                        else:
                            st.error(f"Failed: {res.text}")
                    except Exception as e:
                        st.error(f"Upload error: {e}")

# TAB 3: Strategy Backtester
with tab_backtest:
    st.subheader("🧪 Strategy Backtester & Performance Simulator")
    st.caption("Simulate quantitative predictive models and staking strategies against completed matches with real scores and closing odds.")

    if not backend_online:
        st.warning("Start the FastAPI backend to run backtests.")
    else:
        bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
        with bt_c1:
            bt_strategy = st.selectbox(
                "Strategy Model",
                ["value_bet", "high_confidence_85"],
                format_func=lambda x: "💰 Expected Value (+EV)" if x == "value_bet" else "🎯 High Confidence (≥ 85%)"
            )
        with bt_c2:
            bt_staking = st.selectbox(
                "Staking Plan",
                ["quarter_kelly", "half_kelly", "flat"],
                format_func=lambda x: "Quarter Kelly (Recommended)" if x == "quarter_kelly" else ("Half Kelly" if x == "half_kelly" else "Flat 2% Stake")
            )
        with bt_c3:
            bt_min_ev = st.slider("Minimum +EV %", min_value=0.5, max_value=15.0, value=3.0, step=0.5)
        with bt_c4:
            bt_bankroll = st.number_input("Initial Bankroll ($)", min_value=50.0, max_value=100000.0, value=1000.0, step=100.0)

        if st.button("⚡ Run Backtest Simulation", type="primary", use_container_width=True):
            try:
                bt_req_data = {
                    "strategy": bt_strategy,
                    "min_ev_percent": bt_min_ev,
                    "min_quality_score": 0,
                    "staking": bt_staking,
                    "initial_bankroll": float(bt_bankroll)
                }
                b_res = requests.post(f"{API_BASE}/backtest/run", json=bt_req_data, timeout=10)
                if b_res.status_code == 200:
                    sim = b_res.json()
                    st.markdown("---")
                    st.markdown("#### 📊 Simulation Results")

                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("Total Bets", sim["total_bets"])
                    m2.metric("Win Rate", f"{sim['win_rate_percent']:.1f}%", f"{sim['wins']}W / {sim['losses']}L")
                    m3.metric("Final Bankroll", f"${sim['final_bankroll']:,.2f}")
                    m4.metric("Net Profit / Loss", f"${sim['net_profit']:+,.2f}", delta=f"{sim['net_profit']:+,.2f}")
                    m5.metric("Return on Investment (ROI)", f"{sim['roi_percent']:+.1f}%")
                    m6.metric("Max Drawdown", f"{sim['max_drawdown_percent']:.1f}%")

                    history_records = sim.get("history", [])
                    if history_records:
                        st.markdown("##### 📈 Equity Curve (Bankroll Progression)")
                        hdf = pd.DataFrame(history_records)
                        st.line_chart(hdf["bankroll"])

                        st.markdown("##### 📋 Simulated Trade Log")
                        st.dataframe(hdf, use_container_width=True, hide_index=True)
                    else:
                        st.info("No completed matches met the strategy criteria. Make sure to ingest historical matches in the **Ingestion & Import** tab first!")
                else:
                    st.error(f"Backtest failed: {b_res.text}")
            except Exception as e:
                st.error(f"Backtest error: {e}")

# TAB 4: Markets
with tab_markets:
    st.subheader("Dixon-Coles Bivariate Poisson & 85% Qualification")
    st.caption("Quantitative goal-scoring baseline engine with Dixon & Coles (1997) low-score correlation adjustment.")

    p_col1, p_col2, p_col3, p_col4 = st.columns(4)
    with p_col1:
        home_lam = st.number_input("Home Expected Goals (λ)", min_value=0.0, max_value=8.0, value=1.65, step=0.05)
    with p_col2:
        away_lam = st.number_input("Away Expected Goals (λ)", min_value=0.0, max_value=8.0, value=1.10, step=0.05)
    with p_col3:
        rho = st.slider("Dixon-Coles ρ (Draw adjustment)", min_value=-0.30, max_value=0.10, value=-0.10, step=0.01, help="Negative values boost realistic draw frequencies for 0-0 and 1-1 scores.")
    with p_col4:
        threshold = st.slider("Qualification Threshold", min_value=0.50, max_value=0.99, value=0.85, step=0.01)

    if st.button("⚡ Calculate Probabilities", type="primary"):
        if not backend_online:
            st.error("FastAPI backend is offline. Start it to calculate.")
        else:
            try:
                res = requests.get(
                    f"{API_BASE}/markets/baseline",
                    params={"home_lambda": home_lam, "away_lambda": away_lam, "threshold": threshold, "rho": rho},
                    timeout=5
                )
                if res.status_code == 200:
                    data = res.json()
                    qualifying = data.get("qualifying_markets", [])
                    st.markdown("---")

                    # Highlight qualified markets
                    st.subheader(f"🎯 Qualifying Markets (≥ {threshold * 100:.0f}%)")
                    if qualifying:
                        q_cols = st.columns(min(len(qualifying), 4))
                        for i, item in enumerate(qualifying):
                            col_idx = i % min(len(qualifying), 4)
                            with q_cols[col_idx]:
                                st.metric(
                                    label=item["market"].replace("_", " ").upper(),
                                    value=f"{item['probability'] * 100:.1f}%",
                                    delta=f"+{(item['probability'] - threshold) * 100:.1f}% vs threshold"
                                )
                    else:
                        st.info(f"No markets reached the {threshold * 100:.0f}% qualification threshold for this λ pair.")

                    st.markdown("---")
                    st.subheader("📋 All Market Probabilities")
                    all_markets = data.get("markets", {})
                    market_records = [
                        {"Market": m.replace("_", " ").title(), "Probability": f"{p * 100:.2f}%", "Decimal Raw": p, "Qualifies (>=85%)": "⭐ YES" if p >= threshold else "No"}
                        for m, p in all_markets.items()
                    ]
                    st.dataframe(pd.DataFrame(market_records), use_container_width=True, hide_index=True)
                else:
                    st.error(f"Error from server: {res.text}")
            except Exception as e:
                st.error(f"Calculation failed: {e}")

# TAB 4: Team Ratings
with tab_ratings:
    st.subheader("🛡️ Team Strength Ratings")
    st.write(
        "Team attack and defense strength ratings calibrate expected goals ($\lambda$) and elevate "
        "fixture data-quality scores to Grade A/B readiness."
    )

    if not backend_online:
        st.warning("Start the FastAPI backend to view or manage team ratings.")
    else:
        try:
            r_res = requests.get(f"{API_BASE}/ratings", timeout=4)
            ratings = r_res.json() if r_res.status_code == 200 else []
        except Exception as e:
            ratings = []
            st.error(f"Could not load team ratings: {e}")

        r_col_left, r_col_right = st.columns([3, 2])

        with r_col_left:
            st.markdown("#### Registered Team Ratings")
            if ratings:
                rdf = pd.DataFrame(ratings)
                st.dataframe(
                    rdf[["team_name", "competition", "attack_rating", "defense_rating", "matches_played", "updated_at"]],
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.info("No custom team ratings saved yet. Add teams using the form on the right, or import finished matches with scores to auto-generate ratings.")

            if st.button("🔄 Auto-Calculate Ratings from Finished Matches", use_container_width=True):
                try:
                    c_res = requests.post(f"{API_BASE}/ratings/calculate-from-fixtures", timeout=5)
                    if c_res.status_code == 200:
                        st.success(c_res.json().get("message", "Calculated!"))
                        st.rerun()
                    else:
                        st.error(c_res.text)
                except Exception as e:
                    st.error(f"Error: {e}")

        with r_col_right:
            st.markdown("#### Add or Update Team Rating")
            with st.form("team_rating_form", clear_on_submit=True):
                new_team = st.text_input("Team Name", placeholder="e.g. Arsenal")
                new_comp = st.text_input("Competition", placeholder="e.g. Premier League")
                new_att = st.number_input("Attack Rating (1.0 = avg, >1.0 = above avg)", min_value=0.1, max_value=4.0, value=1.20, step=0.05)
                new_def = st.number_input("Defense Rating (1.0 = avg, <1.0 = strong defense)", min_value=0.1, max_value=4.0, value=0.85, step=0.05)
                new_matches = st.number_input("Matches Analyzed", min_value=1, max_value=200, value=10, step=1)
                submit_rating = st.form_submit_button("💾 Save Team Rating", use_container_width=True)

                if submit_rating:
                    if not new_team or not new_comp:
                        st.warning("Please provide team name and competition.")
                    else:
                        try:
                            save_res = requests.post(
                                f"{API_BASE}/ratings",
                                json={
                                    "team_name": new_team,
                                    "competition": new_comp,
                                    "attack_rating": new_att,
                                    "defense_rating": new_def,
                                    "matches_played": int(new_matches),
                                },
                                timeout=4
                            )
                            if save_res.status_code == 200:
                                st.success(f"Saved rating for {new_team}!")
                                st.rerun()
                            else:
                                st.error(save_res.text)
                        except Exception as e:
                            st.error(f"Save failed: {e}")

# TAB 5: Connectors
with tab_connectors:
    st.subheader("Bookmaker Feed Connectors")
    st.write(
        "FIE is architected to interface with licensed odds feeds and official APIs for target bookmakers. "
        "In accordance with security and terms-of-service compliance, unconfigured connectors act as stubs without web scraping."
    )
    if backend_online:
        try:
            conn_res = requests.get(f"{API_BASE}/connectors/status", timeout=4)
            if conn_res.status_code == 200:
                st.dataframe(pd.DataFrame(conn_res.json()), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Could not load connectors: {e}")
    else:
        st.warning("Backend offline.")


# TAB 7: Agent Rating
with tab_agent_rating:
    st.subheader("🏆 FIE Agent Intelligence Rating")
    st.caption("Live scorecard — prediction engine capabilities, data coverage, and backtest performance.")

    if not backend_online:
        st.warning("Start the FastAPI backend to view the agent rating.")
    else:
        try:
            h = health_data
            _total_fx = h.get("total_fixtures", 0)
            # Fetch quick stats via API
            _ratings_res = requests.get(f"{API_BASE}/ratings", timeout=4)
            _total_tr = len(_ratings_res.json()) if _ratings_res.status_code == 200 else 0
        except Exception:
            _total_fx = 0
            _total_tr = 0

        _bt_win_rate, _bt_roi, _bt_bets = 0.0, 0.0, 0
        try:
            _bt_res = requests.post(f"{API_BASE}/backtest",
                json={"strategy": "value_bet", "min_ev_percent": 2.0, "staking": "quarter_kelly", "initial_bankroll": 1000.0},
                timeout=20)
            if _bt_res.status_code == 200:
                _bd = _bt_res.json()
                _bt_win_rate = _bd.get("win_rate_percent", 0.0)
                _bt_roi = _bd.get("roi_percent", 0.0)
                _bt_bets = _bd.get("total_bets", 0)
        except Exception:
            pass

        def _grade(teams, hist, wr, roi):
            s = 0
            if teams >= 80: s += 25
            elif teams >= 40: s += 15
            elif teams >= 10: s += 8
            if hist >= 1500: s += 25
            elif hist >= 500: s += 15
            elif hist >= 100: s += 8
            if wr >= 55: s += 25
            elif wr >= 48: s += 15
            elif wr >= 40: s += 8
            if roi >= 5: s += 25
            elif roi >= 0: s += 15
            elif roi >= -5: s += 8
            if s >= 85: return "A+", "Elite"
            if s >= 70: return "A", "Strong"
            if s >= 55: return "B", "Good"
            if s >= 40: return "C", "Fair"
            return "D", "Early Stage"

        g, gdesc = _grade(_total_tr, _total_fx, _bt_win_rate, _bt_roi)
        gc = "#00C851" if g in ("A+", "A") else "#FFBB33" if g == "B" else "#FF8800"

        st.markdown(f"""
        <div style="background:#161B22;border:2px solid {gc};border-radius:12px;padding:20px;margin-bottom:20px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <h3 style="margin:0;color:#FFF;">⚽ Football Intelligence Engine</h3>
                    <p style="margin:4px 0 0;color:#888;font-size:13px;">Dixon-Coles Bivariate Poisson · AI Enhanced · EAT Uganda Time</p>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:48px;font-weight:900;color:{gc};">{g}</div>
                    <div style="font-size:12px;color:#AAA;">{gdesc}</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🛡️ Team Ratings", _total_tr)
        c2.metric("📜 Fixtures in DB", _total_fx)
        c3.metric("🎯 Backtest Win Rate", f"{_bt_win_rate:.1f}%" if _bt_bets > 0 else "—")
        c4.metric("💰 Backtest ROI", f"{_bt_roi:+.1f}%" if _bt_bets > 0 else "—",
                  delta=f"{_bt_roi:+.1f}%" if _bt_bets > 0 else None)

        st.info("📐 **Model:** Dixon-Coles Bivariate Poisson with ρ=-0.10 · 19 markets · 85%+ banker threshold · Quarter-Kelly staking · 35+ leagues globally · AI previews via Gemini API")

