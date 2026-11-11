from __future__ import annotations
import io
import os
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import streamlit as st

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
tab_portfolio, tab_fixtures, tab_import, tab_backtest, tab_markets, tab_ratings, tab_connectors = st.tabs([
    "📋 Daily Portfolio",
    "📅 Fixtures & Odds",
    "📥 Ingestion & Import",
    "🧪 Strategy Backtester",
    "📊 Market Probabilities",
    "🛡️ Team Ratings",
    "🔌 Bookmaker Connectors"
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
            sel_legs = st.radio("Number of Teams", [2, 3], index=1, horizontal=True, format_func=lambda x: f"{x}-Team Combo")
        with combo_col2:
            sel_min_odds = st.slider("Target Min Combined Odds", min_value=1.80, max_value=4.00, value=2.00, step=0.05)
        with combo_col3:
            default_prob = 0.72 if sel_legs == 2 else 0.85
            sel_min_leg_prob = st.slider("Min Team Confidence", min_value=0.70, max_value=0.95, value=default_prob, step=0.01, format="%.0f%%")
        with combo_col4:
            sel_combo_bankroll = st.number_input("Bankroll ($)", min_value=50.0, max_value=100000.0, value=1000.0, step=50.0, key="cb_bankroll")

        if st.button(f"⚡ Generate {sel_legs}-Team Daily Slip (Odds ≥ {sel_min_odds:.2f})", type="primary", use_container_width=True):
            with st.spinner("Finding optimal same-day combinations of 85%+ teams…"):
                try:
                    c_res = requests.get(
                        f"{API_BASE}/portfolio/combos",
                        params={
                            "target_date": target_date_val,
                            "num_legs": sel_legs,
                            "min_total_odds": sel_min_odds,
                            "min_leg_prob": sel_min_leg_prob,
                            "bankroll": sel_combo_bankroll,
                        },
                        timeout=15
                    )
                    if c_res.status_code == 200:
                        c_data = c_res.json()
                        best = c_data.get("best_combo")
                        matchday_hdr = c_data.get("matchday_label", "Daily")
                        if not best:
                            st.warning(f"No {sel_legs}-team combos found on {matchday_hdr} with odds ≥ {sel_min_odds:.2f} at {sel_min_leg_prob*100:.0f}% confidence. Try selecting a different matchday or adjusting the Min Team Confidence slider.")
                        else:
                            st.success(f"✅ Found **{c_data['total_combos_generated']}** valid slips for **{matchday_hdr}**!")
                            
                            # Banker Slip Banner Card
                            st.markdown(f"""
                            <div style="background: linear-gradient(135deg, #1A2E1A 0%, #0E1A14 100%); border: 2px solid #00C851; border-radius: 12px; padding: 20px; margin-bottom: 20px;">
                                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #2A4A2A; padding-bottom: 12px; margin-bottom: 15px;">
                                    <div>
                                        <h3 style="margin: 0; color: #00C851;">🏆 TOP DAILY BANKER MULTI-BET ({best['legs_count']} TEAMS)</h3>
                                        <span style="color: #AAA; font-size: 14px;">📅 Matchday: <strong>{matchday_hdr}</strong> (All games finish same day)</span>
                                    </div>
                                    <span style="font-size: 24px; font-weight: bold; background: #00C851; color: #000; padding: 4px 16px; border-radius: 8px;">ODDS {best['total_odds']:.2f}</span>
                                </div>
                                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 15px;">
                                    <div><span style="color: #888; font-size: 12px;">COMBINED WIN PROB</span><br><strong style="font-size: 20px; color: #FFF;">{best['combined_probability']*100:.1f}%</strong></div>
                                    <div><span style="color: #888; font-size: 12px;">EXPECTED VALUE (+EV)</span><br><strong style="font-size: 20px; color: #00C851;">{best['expected_value_percent']:+.1f}% EV</strong></div>
                                    <div><span style="color: #888; font-size: 12px;">VALUE EDGE</span><br><strong style="font-size: 20px; color: #FFF;">+{best['edge_percent']:.1f}%</strong></div>
                                    <div><span style="color: #888; font-size: 12px;">QUARTER KELLY STAKE</span><br><strong style="font-size: 20px; color: #FFBB33;">${best['kelly_stake_amount']:.2f} ({best['kelly_stake_percent']}%)</strong></div>
                                </div>
                            </div>
                            """, unsafe_allow_html=True)

                            # Table of Legs
                            legs_df = pd.DataFrame([{
                                "Leg": f"Leg #{i+1}",
                                "Match": l["match"],
                                "Selection": l["selection"],
                                "Market Type": l["market_type"],
                                "Odds": f"{l['odds']:.2f}",
                                "Team Win/Draw Prob": f"{l['probability']*100:.1f}%",
                                "Kickoff (Ugandan Time, EAT)": l["kickoff_eat"],
                                "League": l["competition"],
                            } for i, l in enumerate(best["legs"])])
                            st.dataframe(legs_df, use_container_width=True, hide_index=True)

                            # Formatted Booking Text to copy
                            slip_text = f"🔥 FIE {sel_legs}-TEAM COMBO SLIP (Odds: {best['total_odds']:.2f} | Win Prob: {best['combined_probability']*100:.1f}%)\n"
                            for i, l in enumerate(best["legs"], 1):
                                slip_text += f"{i}. {l['match']} ➔ {l['selection']} @ {l['odds']:.2f} ({l['kickoff_eat']})\n"
                            slip_text += f"Total Odds: {best['total_odds']:.2f} | Kelly Stake: ${best['kelly_stake_amount']:.2f}"
                            st.text_area("📋 Copy Booking Slip Text", slip_text, height=130)

                            # Alternative Combos
                            alts = c_data.get("alternatives", [])
                            if alts:
                                with st.expander(f"📦 View {len(alts)} Alternative Combos (Odds ≥ {sel_min_odds:.2f})"):
                                    for idx, alt in enumerate(alts, 2):
                                        st.write(f"**Combo Option #{idx}** — Odds: `{alt['total_odds']:.2f}` | Win Prob: `{alt['combined_probability']*100:.1f}%` | EV: `+{alt['expected_value_percent']:.1f}%`")
                                        st.caption(" • ".join([f"{l['selection']} @ {l['odds']:.2f}" for l in alt["legs"]]))
                    else:
                        st.error(f"Error generating combos: {c_res.text}")
                except Exception as e:
                    st.error(f"Request error: {e}")

        st.markdown("---")
        st.markdown("### 📋 Individual Value Bets & Qualification Scan")

        pf_c1, pf_c2, pf_c3, pf_c4 = st.columns(4)
        with pf_c1:
            pf_days = st.slider("Look-ahead (days)", 1, 14, 7)
        with pf_c2:
            pf_bankroll = st.number_input("Bankroll ($)", min_value=50.0, max_value=1_000_000.0, value=1000.0, step=100.0)
        with pf_c3:
            pf_min_ev = st.slider("Min +EV %", 0.0, 15.0, 0.0, 0.5)
        with pf_c4:
            pf_kelly = st.selectbox("Kelly Fraction", [0.25, 0.5, 1.0], format_func=lambda x: {0.25: "¼ Quarter Kelly", 0.5: "½ Half Kelly", 1.0: "Full Kelly"}[x])

        pf_comp = st.text_input("Filter by competition (optional)", placeholder="e.g. Premier League")

        if st.button("🔍 Build Daily Value Portfolio", type="primary", use_container_width=True):
            with st.spinner("Scanning all upcoming fixtures for +EV opportunities…"):
                try:
                    pf_res = requests.get(
                        f"{API_BASE}/portfolio/today",
                        params={
                            "days_ahead": pf_days,
                            "bankroll": pf_bankroll,
                            "min_ev_percent": pf_min_ev,
                            "kelly_fraction": pf_kelly,
                            "competition": pf_comp if pf_comp else None,
                        },
                        timeout=30,
                    )
                    if pf_res.status_code == 200:
                        pf_data = pf_res.json()
                        summary = pf_data.get("summary", {})

                        # Summary metrics
                        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
                        sm1.metric("Fixtures Scanned", summary.get("fixtures_scanned", 0))
                        sm2.metric("Fixtures Analysed", summary.get("fixtures_analysed", 0))
                        sm3.metric("Value Bets Found", summary.get("value_bets_found", 0))
                        sm4.metric("Qualifying Markets", summary.get("qualifying_markets_found", 0))
                        sm5.metric("Total Kelly Exposure", f"${summary.get('total_kelly_exposure', 0):.2f}")

                        st.markdown("---")

                        # ── VALUE BETS SLIP ──
                        value_bets = pf_data.get("value_bets", [])
                        st.subheader(f"💰 +EV Value Bets ({len(value_bets)})")
                        if value_bets:
                            vdf = pd.DataFrame(value_bets)
                            if "kickoff_eat" not in vdf.columns and "kickoff_utc" in vdf.columns:
                                vdf["kickoff_eat"] = vdf["kickoff_utc"].apply(to_eat_str)
                            # Column ordering with Ugandan Time prominent
                            show_cols = [
                                "kickoff_eat", "match", "competition", "market",
                                "model_probability", "bookmaker_odds", "implied_probability",
                                "edge_percent", "ev_percent", "kelly_stake_percent", "kelly_stake_amount",
                                "lambda_source",
                            ]
                            show_cols = [c for c in show_cols if c in vdf.columns]
                            vdf_display = vdf[show_cols].copy()
                            rename_map = {
                                "kickoff_eat": "Kickoff (Ugandan Time, EAT)",
                                "match": "Match",
                                "competition": "League",
                                "market": "Selection",
                                "model_probability": "Model Prob",
                                "bookmaker_odds": "Odds",
                                "implied_probability": "Implied Prob",
                                "edge_percent": "Edge %",
                                "ev_percent": "Expected Value (+EV)",
                                "kelly_stake_percent": "Kelly %",
                                "kelly_stake_amount": "Stake ($)",
                                "lambda_source": "Model Source",
                            }
                            vdf_display.columns = [rename_map.get(c, c.replace("_", " ").title()) for c in vdf_display.columns]
                            st.dataframe(vdf_display, use_container_width=True, hide_index=True)

                            # Export button
                            csv_bytes = vdf[show_cols].to_csv(index=False).encode("utf-8")
                            st.download_button(
                                "📥 Export Value Slip as CSV",
                                data=csv_bytes,
                                file_name="fie_value_slip.csv",
                                mime="text/csv",
                                use_container_width=True,
                            )
                        else:
                            st.info("No +EV bets found with current filters. Try lowering Min +EV % or extending look-ahead days, or ingest upcoming fixtures first.")

                        st.markdown("---")

                        # ── QUALIFYING MARKETS ──
                        qualifying = pf_data.get("qualifying_markets", [])
                        st.subheader(f"🎯 Qualifying Markets ≥85% Confidence ({len(qualifying)})")
                        if qualifying:
                            qdf = pd.DataFrame(qualifying)
                            if "kickoff_eat" not in qdf.columns and "kickoff_utc" in qdf.columns:
                                qdf["kickoff_eat"] = qdf["kickoff_utc"].apply(to_eat_str)
                            q_show = ["kickoff_eat", "match", "competition", "market", "probability", "lambda_source"]
                            q_show = [c for c in q_show if c in qdf.columns]
                            qdf_display = qdf[q_show].copy()
                            q_rename = {
                                "kickoff_eat": "Kickoff (Ugandan Time, EAT)",
                                "match": "Match",
                                "competition": "League",
                                "market": "Market",
                                "probability": "Model Prob",
                                "lambda_source": "Source",
                            }
                            qdf_display.columns = [q_rename.get(c, c.replace("_", " ").title()) for c in qdf_display.columns]
                            st.dataframe(qdf_display, use_container_width=True, hide_index=True)

                            csv_q = qdf[q_show].to_csv(index=False).encode("utf-8")
                            st.download_button(
                                "📥 Export Qualifying Markets as CSV",
                                data=csv_q,
                                file_name="fie_qualifying_markets.csv",
                                mime="text/csv",
                                use_container_width=True,
                            )
                        else:
                            st.info("No markets reached the 85% qualification threshold.")
                    else:
                        st.error(f"Portfolio error: {pf_res.text}")
                except Exception as e:
                    st.error(f"Request error: {e}")

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

# TAB 2: Ingestion & Import
with tab_import:
    st.subheader("Data Ingestion & Real-Time League Feeds")
    st.write(
        "FIE connects directly to verified football data archives and upcoming fixture feeds. "
        "Every incoming match is automatically deduplicated, normalized, and used to calibrate team ratings."
    )

    st.markdown("### 🌐 Real Data Feeds (Live & Full Seasons)")
    feed_col1, feed_col2 = st.columns(2)

    with feed_col1:
        st.markdown("#### 1. Live Games (Tomorrow & Weekend)")
        st.write("Fetches **real upcoming matches** (UEFA Nations League tomorrow + European domestic rounds) with **real-time live DraftKings odds**.")
        if st.button("🟢 Fetch Live Upcoming Games (DraftKings Odds)", type="primary", use_container_width=True):
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
        st.markdown("#### 2. Full League Season (380 Real Matches)")
        st.write("Ingest complete seasons with final scores, half-time stats, and closing market odds to calibrate team strength ratings.")
        l_col_div, l_col_sea = st.columns(2)
        with l_col_div:
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
                }.get(x, x)
            )
        with l_col_sea:
            sel_sea = st.selectbox("Season", ["2324", "2425"], format_func=lambda x: "2023/24" if x == "2324" else "2024/25")

        if st.button("📥 Download & Ingest Season", use_container_width=True):
            if not backend_online:
                st.error("Backend is offline. Start FastAPI first.")
            else:
                with st.spinner(f"Downloading real league season data..."):
                    try:
                        s_res = requests.post(
                            f"{API_BASE}/fixtures/fetch-league",
                            params={"division": sel_div, "season": sel_sea},
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
