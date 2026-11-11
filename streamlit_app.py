"""
streamlit_app.py – FIE Cloud Entry Point (Streamlit Community Cloud)
======================================================================
Standalone version: calls Python services DIRECTLY (no FastAPI server needed).
The database lives in-process using SQLite in the /tmp directory on Streamlit Cloud.
"""
from __future__ import annotations
import os
import sys
import io

# ── Make sure the app package is importable ──────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Point the DB at a writable location on Streamlit Cloud (/tmp is writable)
# On local dev, this is overridden by the DATABASE_URL env var in .env
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/fie_cloud.db")

import pandas as pd
import streamlit as st

# ── Bootstrap DB tables ───────────────────────────────────────────────────────
from app.database import Base, engine, get_db
from app.models.fixture import Fixture          # noqa: F401 — registers model
from app.models.team_rating import TeamRating   # noqa: F401 — registers model
Base.metadata.create_all(bind=engine)

# ── Service imports ───────────────────────────────────────────────────────────
from sqlalchemy import select, func, delete
from app.services.fixtures import import_fixtures_from_dataframe
from app.services.probabilities import (
    poisson_market_probabilities,
    analyze_fixture,
)
from app.services.quality import evaluate_fixture_signals, grade_data_quality
from app.services.ratings import upsert_team_rating, calculate_ratings_from_fixtures
from app.services.seeder import (
    seed_sample_historical_data,
    fetch_live_upcoming_fixtures,
    fetch_historical_league_season,
)
from app.services.backtest import run_backtest
from app.services.portfolio import build_daily_portfolio

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FIE | Football Intelligence Engine",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("⚽ Football Intelligence Engine")
st.caption("Local-first football research and prediction platform · Cloud Edition v0.3")

# ── Helper: get a DB session ──────────────────────────────────────────────────
def _db():
    return next(get_db())


def _fixture_quality(f: Fixture) -> dict:
    signals = evaluate_fixture_signals(f)
    q = grade_data_quality(signals)
    return {
        "id": f.id,
        "kickoff_utc": f.kickoff_utc,
        "home_team": f.home_team,
        "away_team": f.away_team,
        "competition": f.competition,
        "country": f.country,
        "source": f.source,
        "home_odds": f.home_odds,
        "draw_odds": f.draw_odds,
        "away_odds": f.away_odds,
        "data_quality_score": q["score"],
        "data_quality_grade": q["grade"],
        "eligible_for_analysis": q["eligible_for_analysis"],
    }


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("System Status")
    db_check = _db()
    total_fx = db_check.scalar(select(func.count(Fixture.id))) or 0
    db_check.close()
    st.success("🟢 Engine Online (Standalone Mode)")
    st.write(f"**Stored Fixtures:** {total_fx}")
    st.info("Running directly on Streamlit Cloud — no FastAPI server required.")

    st.divider()
    st.header("⚙️ Thresholds")
    st.metric("Qualification Threshold", "85%")
    st.metric("Data-Quality Minimum", "70 / 100")

    st.divider()
    if st.button("🧹 Mark Stale Fixtures", use_container_width=True):
        from datetime import datetime, timezone
        db2 = _db()
        now = datetime.now(timezone.utc)
        stale = db2.scalars(
            select(Fixture)
            .where(Fixture.status == "scheduled")
            .where(Fixture.kickoff_utc < now)
            .where(Fixture.home_score.is_(None))
        ).all()
        for s in stale:
            s.status = "pending_result"
        db2.commit()
        db2.close()
        st.toast(f"Marked {len(stale)} stale fixtures.")
        st.rerun()

    if st.button("🗑️ Clear All Fixtures", use_container_width=True):
        db3 = _db()
        count = db3.scalar(select(func.count(Fixture.id))) or 0
        db3.execute(delete(Fixture))
        db3.commit()
        db3.close()
        st.toast(f"Cleared {count} fixtures.")
        st.rerun()

# ── Tabs ──────────────────────────────────────────────────────────────────────
(
    tab_portfolio, tab_fixtures, tab_import,
    tab_backtest, tab_markets, tab_ratings, tab_connectors
) = st.tabs([
    "📋 Daily Portfolio",
    "📅 Fixtures & Odds",
    "📥 Ingestion & Import",
    "🧪 Strategy Backtester",
    "📊 Market Probabilities",
    "🛡️ Team Ratings",
    "🔌 Bookmaker Connectors",
])

# ══════════════════════════════════════════════════════════════════════════════
# TAB 0 — DAILY PORTFOLIO
# ══════════════════════════════════════════════════════════════════════════════
with tab_portfolio:
    st.subheader("📋 Daily Value Portfolio")
    st.caption("Scans upcoming scheduled fixtures and surfaces +EV bets and ≥85% qualifying markets.")

    pf_c1, pf_c2, pf_c3, pf_c4 = st.columns(4)
    with pf_c1:
        pf_days = st.slider("Look-ahead (days)", 1, 14, 7)
    with pf_c2:
        pf_bankroll = st.number_input("Bankroll ($)", 50.0, 1_000_000.0, 1000.0, 100.0)
    with pf_c3:
        pf_min_ev = st.slider("Min +EV %", 0.0, 15.0, 0.0, 0.5)
    with pf_c4:
        pf_kelly = st.selectbox(
            "Kelly Fraction", [0.25, 0.5, 1.0],
            format_func=lambda x: {0.25: "¼ Quarter Kelly", 0.5: "½ Half Kelly", 1.0: "Full Kelly"}[x]
        )
    pf_comp = st.text_input("Filter by competition (optional)", placeholder="e.g. Premier League")

    if st.button("🔍 Build Daily Value Portfolio", type="primary", use_container_width=True):
        with st.spinner("Scanning all upcoming fixtures for +EV opportunities…"):
            db_pf = _db()
            try:
                pf_data = build_daily_portfolio(
                    db=db_pf,
                    days_ahead=pf_days,
                    min_ev_percent=pf_min_ev,
                    bankroll=pf_bankroll,
                    kelly_fraction=pf_kelly,
                    competition=pf_comp if pf_comp else None,
                )
            finally:
                db_pf.close()

        summary = pf_data["summary"]
        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("Fixtures Scanned", summary["fixtures_scanned"])
        sm2.metric("Analysed", summary["fixtures_analysed"])
        sm3.metric("Value Bets", summary["value_bets_found"])
        sm4.metric("Qualifying Mkts", summary["qualifying_markets_found"])
        sm5.metric("Kelly Exposure", f"${summary['total_kelly_exposure']:.2f}")

        st.markdown("---")
        value_bets = pf_data.get("value_bets", [])
        st.subheader(f"💰 +EV Value Bets ({len(value_bets)})")
        if value_bets:
            vdf = pd.DataFrame(value_bets)
            show_cols = ["kickoff_utc", "match", "competition", "market",
                         "model_probability", "bookmaker_odds", "edge_percent",
                         "ev_percent", "kelly_stake_percent", "kelly_stake_amount"]
            show_cols = [c for c in show_cols if c in vdf.columns]
            vdf_d = vdf[show_cols].copy()
            vdf_d.columns = [c.replace("_", " ").title() for c in vdf_d.columns]
            st.dataframe(vdf_d, use_container_width=True, hide_index=True)
            st.download_button("📥 Export Value Slip as CSV",
                               data=vdf[show_cols].to_csv(index=False).encode(),
                               file_name="fie_value_slip.csv", mime="text/csv",
                               use_container_width=True)
        else:
            st.info("No +EV bets found. Fetch upcoming fixtures first (Ingestion tab).")

        qualifying = pf_data.get("qualifying_markets", [])
        st.markdown("---")
        st.subheader(f"🎯 Qualifying Markets ≥85% ({len(qualifying)})")
        if qualifying:
            qdf = pd.DataFrame(qualifying)
            q_show = ["kickoff_utc", "match", "competition", "market", "probability"]
            q_show = [c for c in q_show if c in qdf.columns]
            qdf_d = qdf[q_show].copy()
            qdf_d.columns = [c.replace("_", " ").title() for c in qdf_d.columns]
            st.dataframe(qdf_d, use_container_width=True, hide_index=True)
            st.download_button("📥 Export Qualifying Markets as CSV",
                               data=qdf[q_show].to_csv(index=False).encode(),
                               file_name="fie_qualifying.csv", mime="text/csv",
                               use_container_width=True)
        else:
            st.info("No markets reached the 85% threshold.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — FIXTURES
# ══════════════════════════════════════════════════════════════════════════════
with tab_fixtures:
    st.subheader("Match Fixtures & Quality Grades")
    db_fx = _db()
    fixtures_raw = db_fx.scalars(select(Fixture).order_by(Fixture.kickoff_utc).limit(300)).all()
    db_fx.close()

    if not fixtures_raw:
        st.info("No fixtures yet. Go to **Ingestion & Import** to load data.")
    else:
        fixtures = [_fixture_quality(f) for f in fixtures_raw]
        df = pd.DataFrame(fixtures)

        f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
        with f_col1:
            search_team = st.text_input("🔍 Search team", placeholder="e.g. Arsenal")
        with f_col2:
            comp_opts = ["All"] + sorted(df["competition"].unique().tolist())
            sel_comp = st.selectbox("Competition", comp_opts)
        with f_col3:
            src_opts = ["All"] + sorted(df["source"].unique().tolist())
            sel_src = st.selectbox("Source", src_opts)

        filtered = df.copy()
        if search_team:
            t = search_team.lower()
            filtered = filtered[
                filtered["home_team"].str.lower().str.contains(t) |
                filtered["away_team"].str.lower().str.contains(t)
            ]
        if sel_comp != "All":
            filtered = filtered[filtered["competition"] == sel_comp]
        if sel_src != "All":
            filtered = filtered[filtered["source"] == sel_src]

        st.write(f"Showing **{len(filtered)}** of **{len(df)}** fixtures")
        display_cols = ["id", "kickoff_utc", "home_team", "away_team", "competition",
                        "source", "home_odds", "draw_odds", "away_odds",
                        "data_quality_score", "data_quality_grade", "eligible_for_analysis"]
        avail = [c for c in display_cols if c in filtered.columns]
        st.dataframe(filtered[avail], use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("🔬 Fixture Inspector")
        fixture_ids = filtered["id"].tolist()
        if fixture_ids:
            selected_id = st.selectbox(
                "Select fixture:",
                fixture_ids,
                format_func=lambda x: (
                    f"#{x} – "
                    f"{filtered[filtered['id']==x]['home_team'].values[0]} vs "
                    f"{filtered[filtered['id']==x]['away_team'].values[0]} "
                    f"({filtered[filtered['id']==x]['competition'].values[0]})"
                )
            )
            db_insp = _db()
            fx = db_insp.get(Fixture, selected_id)
            db_insp.close()
            if fx:
                sigs = evaluate_fixture_signals(fx)
                grade = grade_data_quality(sigs)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Score", f"{grade['score']} / 100")
                c2.metric("Grade", grade["grade"])
                c3.metric("Eligible", "✅ Yes" if grade["eligible_for_analysis"] else "❌ No")
                c4.metric("Covered Weight", f"{grade['covered_weight']} / {grade['total_weight']}")

                st.write("##### Quality Signal Breakdown")
                bd = grade.get("breakdown", {})
                if bd:
                    st.dataframe(pd.DataFrame([
                        {"Signal": k, "Weight": v["weight"], "Value": v["value"], "Status": v["status"]}
                        for k, v in bd.items()
                    ]), use_container_width=True, hide_index=True)

                st.markdown("---")
                st.write("##### ⚡ Market Probabilities & +EV Analysis")
                db_an = _db()
                a_data = analyze_fixture(fx, db=db_an)
                db_an.close()

                est = a_data.get("estimated_lambdas", {})
                ctrl1, ctrl2, ctrl3, ctrl4 = st.columns(4)
                with ctrl1:
                    custom_h = st.number_input(f"Home λ ({fx.home_team})", 0.1, 8.0,
                                               float(est.get("home_lambda", 1.5)), 0.05, key=f"h{selected_id}")
                with ctrl2:
                    custom_a = st.number_input(f"Away λ ({fx.away_team})", 0.1, 8.0,
                                               float(est.get("away_lambda", 1.1)), 0.05, key=f"a{selected_id}")
                with ctrl3:
                    custom_rho = st.slider("Dixon-Coles ρ", -0.30, 0.10, -0.10, 0.01, key=f"rho{selected_id}")
                with ctrl4:
                    custom_thr = st.slider("Qualification Threshold", 0.60, 0.95, 0.85, 0.01, key=f"thr{selected_id}")

                db_an2 = _db()
                a_data = analyze_fixture(
                    fx, home_lambda=custom_h, away_lambda=custom_a,
                    qualification_threshold=custom_thr, rho=custom_rho, db=db_an2
                )
                db_an2.close()

                src = (a_data.get("lambda_source") or "odds_implied").replace("_", " ").title()
                st.caption(f"📌 λ source: **{src}** · Engine: Dixon-Coles Bivariate Poisson (ρ = {custom_rho})")

                margin = a_data.get("bookmaker_margin")
                if margin:
                    st.info(f"📊 **Bookmaker Overround:** `{margin['overround']*100:.1f}%` "
                            f"(Margin: `{margin['margin_percent']:.1f}%`)")

                vbs = a_data.get("value_bets", [])
                if vbs:
                    st.markdown("###### 💰 1X2 Market & Expected Value")
                    vcols = st.columns(len(vbs))
                    for i, vb in enumerate(vbs):
                        with vcols[i]:
                            ev = vb["expected_value_percent"]
                            st.metric(f"{vb['market']} @ {vb['bookmaker_odds']:.2f}",
                                      f"{vb['model_probability']*100:.1f}%",
                                      delta=f"{ev:+.1f}% EV",
                                      delta_color="normal" if vb["is_value"] else "off")
                            st.caption(f"Fair Odds: **{vb.get('fair_odds', 'N/A')}** | Edge: **{vb['edge']*100:+.1f}%**")
                            if vb["is_value"] and vb.get("kelly", {}).get("stake_amount", 0) > 0:
                                k = vb["kelly"]
                                st.markdown(f"💼 **Quarter Kelly:** `${k['stake_amount']:.2f}` ({k['stake_percent']}%)")

                qualifying_mkts = a_data.get("qualifying_markets", [])
                st.markdown("###### 🎯 Qualifying Markets (≥ 85% Confidence)")
                if qualifying_mkts:
                    pills = " &nbsp;|&nbsp; ".join([
                        f"**{m['market'].replace('_', ' ').upper()}**: `{m['probability']*100:.1f}%`"
                        for m in qualifying_mkts
                    ])
                    st.success(f"Qualified ({len(qualifying_mkts)} markets): {pills}")
                else:
                    st.write("No markets reached the qualification threshold.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — INGESTION
# ══════════════════════════════════════════════════════════════════════════════
with tab_import:
    st.subheader("Data Ingestion & Real-Time League Feeds")

    st.markdown("### 🌐 Real Data Feeds (Live & Full Seasons)")
    feed1, feed2 = st.columns(2)

    with feed1:
        st.markdown("#### 1. Upcoming Matches & Live Odds")
        st.write("Fetches **160+ real upcoming European matches** with live multi-bookmaker odds.")
        if st.button("⚡ Fetch Real Upcoming Matches (160+ Games)", type="primary", use_container_width=True):
            with st.spinner("Downloading upcoming fixtures…"):
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
        st.markdown("#### 2. Full League Season (380 Matches)")
        l_div, l_sea = st.columns(2)
        with l_div:
            sel_div = st.selectbox("Select League", ["E0", "SP1", "I1", "D1", "F1", "E1"],
                                   format_func=lambda x: {
                                       "E0": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League", "SP1": "🇪🇸 La Liga",
                                       "I1": "🇮🇹 Serie A", "D1": "🇩🇪 Bundesliga",
                                       "F1": "🇫🇷 Ligue 1", "E1": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship"
                                   }.get(x, x))
        with l_sea:
            sel_sea = st.selectbox("Season", ["2324", "2425"],
                                   format_func=lambda x: "2023/24" if x == "2324" else "2024/25")
        if st.button("📥 Download & Ingest Season", use_container_width=True):
            with st.spinner("Downloading season data…"):
                db_s = _db()
                try:
                    result = fetch_historical_league_season(db_s, division_code=sel_div, season=sel_sea)
                    st.success(f"✅ {result['message']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Download error: {e}")
                finally:
                    db_s.close()

    st.markdown("---")
    st.markdown("### 📁 Local CSV & Offline Data")
    col_a, col_b, col_c = st.columns(3)

    with col_a:
        st.markdown("#### Option A: Quick Sample")
        if st.button("🚀 Load Sample Fixtures", use_container_width=True):
            db_sa = _db()
            try:
                import pandas as _pd
                from pathlib import Path as _P
                p = _P(__file__).parent / "data" / "sample_fixtures.csv"
                frame = _pd.read_csv(p)
                res = import_fixtures_from_dataframe(db_sa, frame)
                st.success(f"✅ {res['inserted']} inserted, {res['duplicates']} duplicates.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                db_sa.close()

    with col_b:
        st.markdown("#### Option B: Offline EPL Sample")
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
        uploaded = st.file_uploader("Choose CSV", type=["csv"], label_visibility="collapsed")
        if uploaded and st.button("📤 Upload & Process CSV", use_container_width=True):
            db_up = _db()
            try:
                frame = pd.read_csv(io.BytesIO(uploaded.read()))
                res = import_fixtures_from_dataframe(db_up, frame)
                st.success(f"✅ {res['inserted']} inserted, {res['duplicates']} duplicates.")
                st.rerun()
            except Exception as e:
                st.error(f"Upload error: {e}")
            finally:
                db_up.close()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — BACKTESTER
# ══════════════════════════════════════════════════════════════════════════════
with tab_backtest:
    st.subheader("🧪 Strategy Backtester & Performance Simulator")
    bt_c1, bt_c2, bt_c3, bt_c4 = st.columns(4)
    with bt_c1:
        bt_strategy = st.selectbox("Strategy", ["value_bet", "high_confidence_85"],
                                   format_func=lambda x: "💰 +EV" if x == "value_bet" else "🎯 High Confidence 85%")
    with bt_c2:
        bt_staking = st.selectbox("Staking", ["quarter_kelly", "half_kelly", "flat"],
                                  format_func=lambda x: "Quarter Kelly" if x == "quarter_kelly" else ("Half Kelly" if x == "half_kelly" else "Flat 2%"))
    with bt_c3:
        bt_min_ev = st.slider("Min +EV %", 0.5, 15.0, 3.0, 0.5)
    with bt_c4:
        bt_bankroll = st.number_input("Initial Bankroll ($)", 50.0, 100_000.0, 1000.0, 100.0)

    if st.button("⚡ Run Backtest Simulation", type="primary", use_container_width=True):
        db_bt = _db()
        try:
            sim = run_backtest(db=db_bt, strategy=bt_strategy, min_ev_percent=bt_min_ev,
                               min_quality_score=0, staking=bt_staking, initial_bankroll=float(bt_bankroll))
            st.markdown("---")
            st.markdown("#### 📊 Simulation Results")
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Total Bets", sim["total_bets"])
            m2.metric("Win Rate", f"{sim['win_rate_percent']:.1f}%", f"{sim['wins']}W / {sim['losses']}L")
            m3.metric("Final Bankroll", f"${sim['final_bankroll']:,.2f}")
            m4.metric("Net Profit", f"${sim['net_profit']:+,.2f}", delta=f"${sim['net_profit']:+,.2f}")
            m5.metric("ROI", f"{sim['roi_percent']:+.1f}%")
            m6.metric("Max Drawdown", f"{sim['max_drawdown_percent']:.1f}%")
            if sim.get("history"):
                hdf = pd.DataFrame(sim["history"])
                st.markdown("##### 📈 Equity Curve")
                st.line_chart(hdf["bankroll"])
                st.markdown("##### 📋 Trade Log")
                st.dataframe(hdf, use_container_width=True, hide_index=True)
            else:
                st.info("No completed matches met criteria. Seed historical data first.")
        except Exception as e:
            st.error(f"Backtest error: {e}")
        finally:
            db_bt.close()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — MARKET PROBABILITIES
# ══════════════════════════════════════════════════════════════════════════════
with tab_markets:
    st.subheader("Dixon-Coles Bivariate Poisson & 85% Qualification")
    p_c1, p_c2, p_c3, p_c4 = st.columns(4)
    with p_c1: home_lam = st.number_input("Home λ", 0.0, 8.0, 1.65, 0.05)
    with p_c2: away_lam = st.number_input("Away λ", 0.0, 8.0, 1.10, 0.05)
    with p_c3: rho_val = st.slider("Dixon-Coles ρ", -0.30, 0.10, -0.10, 0.01)
    with p_c4: threshold_val = st.slider("Qualification Threshold", 0.50, 0.99, 0.85, 0.01)

    if st.button("⚡ Calculate Probabilities", type="primary"):
        data = poisson_market_probabilities(home_lam, away_lam, qualification_threshold=threshold_val, rho=rho_val)
        qualifying = data.get("qualifying_markets", [])
        st.markdown("---")
        st.subheader(f"🎯 Qualifying Markets (≥ {threshold_val*100:.0f}%)")
        if qualifying:
            q_cols = st.columns(min(len(qualifying), 4))
            for i, item in enumerate(qualifying):
                with q_cols[i % 4]:
                    st.metric(item["market"].replace("_", " ").upper(),
                              f"{item['probability']*100:.1f}%",
                              delta=f"+{(item['probability']-threshold_val)*100:.1f}% vs threshold")
        else:
            st.info(f"No markets reached {threshold_val*100:.0f}% threshold.")
        st.markdown("---")
        st.subheader("📋 All Market Probabilities")
        mdf = pd.DataFrame([
            {"Market": m.replace("_", " ").title(), "Probability": f"{p*100:.2f}%",
             "Qualifies": "⭐ YES" if p >= threshold_val else "No"}
            for m, p in data.get("markets", {}).items()
        ])
        st.dataframe(mdf, use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — TEAM RATINGS
# ══════════════════════════════════════════════════════════════════════════════
with tab_ratings:
    st.subheader("🛡️ Team Strength Ratings")
    db_r = _db()
    ratings = db_r.scalars(select(TeamRating).order_by(TeamRating.team_name).limit(100)).all()
    db_r.close()

    r_left, r_right = st.columns([3, 2])
    with r_left:
        st.markdown("#### Registered Ratings")
        if ratings:
            rdf = pd.DataFrame([{
                "Team": r.team_name, "Competition": r.competition,
                "Attack": r.attack_rating, "Defense": r.defense_rating,
                "Matches": r.matches_played, "Updated": r.updated_at
            } for r in ratings])
            st.dataframe(rdf, use_container_width=True, hide_index=True)
        else:
            st.info("No ratings yet. Import finished matches with scores to auto-generate.")

        if st.button("🔄 Auto-Calculate Ratings from Finished Matches", use_container_width=True):
            db_rc = _db()
            try:
                res = calculate_ratings_from_fixtures(db_rc)
                st.success(res.get("message", "Done!"))
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                db_rc.close()

    with r_right:
        st.markdown("#### Add / Update Rating")
        with st.form("rating_form", clear_on_submit=True):
            new_team = st.text_input("Team Name", placeholder="Arsenal")
            new_comp = st.text_input("Competition", placeholder="Premier League")
            new_att = st.number_input("Attack Rating", 0.1, 4.0, 1.20, 0.05)
            new_def = st.number_input("Defense Rating", 0.1, 4.0, 0.85, 0.05)
            new_mp = st.number_input("Matches Analyzed", 1, 200, 10, 1)
            if st.form_submit_button("💾 Save Rating", use_container_width=True):
                if not new_team or not new_comp:
                    st.warning("Team name and competition are required.")
                else:
                    db_rs = _db()
                    try:
                        upsert_team_rating(db_rs, new_team, new_comp, new_att, new_def, int(new_mp))
                        st.success(f"Saved rating for {new_team}!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save failed: {e}")
                    finally:
                        db_rs.close()

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — CONNECTORS
# ══════════════════════════════════════════════════════════════════════════════
with tab_connectors:
    st.subheader("Bookmaker Feed Connectors")
    st.write(
        "FIE is architected to interface with licensed odds feeds and official APIs. "
        "Unconfigured connectors act as stubs (no scraping)."
    )
    from app.connectors.bookmakers import BOOKMAKERS
    st.dataframe(pd.DataFrame([{"bookmaker": b, "status": "not_configured"} for b in BOOKMAKERS]),
                 use_container_width=True, hide_index=True)
