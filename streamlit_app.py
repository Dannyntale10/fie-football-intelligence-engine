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
)
from app.services.backtest import run_backtest          # noqa: E402
from app.services.portfolio import build_daily_portfolio  # noqa: E402
from app.connectors.bookmakers import BOOKMAKERS       # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _db():
    """Return a fresh SQLAlchemy session (caller must close it)."""
    return next(get_db())


def _build_fixture_row(f: Fixture) -> dict:
    sigs = evaluate_fixture_signals(f)
    q = grade_data_quality(sigs)
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
    tab_portfolio, tab_fixtures, tab_import,
    tab_backtest, tab_markets, tab_ratings, tab_connectors,
) = st.tabs([
    "📋 Daily Portfolio",
    "📅 Fixtures & Odds",
    "📥 Ingestion & Import",
    "🧪 Strategy Backtester",
    "📊 Market Probabilities",
    "🛡️ Team Ratings",
    "🔌 Bookmaker Connectors",
])


# ═════════════════════════════════════════════════════════════════════════════
# TAB 0 — DAILY VALUE PORTFOLIO
# ═════════════════════════════════════════════════════════════════════════════
with tab_portfolio:
    st.subheader("📋 Daily Value Portfolio")
    st.caption(
        "Scans all upcoming scheduled fixtures within the look-ahead window, "
        "runs the Dixon-Coles Poisson model on each, and surfaces +EV bets and "
        "≥85% qualifying markets as an exportable shortlist."
    )

    pf_c1, pf_c2, pf_c3, pf_c4 = st.columns(4)
    with pf_c1:
        pf_days = st.slider("Look-ahead window (days)", 1, 14, 7)
    with pf_c2:
        pf_bankroll = st.number_input("Bankroll ($)", 50.0, 1_000_000.0, 1000.0, 50.0)
    with pf_c3:
        pf_min_ev = st.slider("Min +EV %", 0.0, 15.0, 0.0, 0.5,
                              help="Only show bets where model EV exceeds this threshold")
    with pf_c4:
        pf_kelly = st.selectbox(
            "Kelly Fraction",
            [0.25, 0.5, 1.0],
            format_func=lambda x: {
                0.25: "¼ Quarter Kelly (Recommended)",
                0.5:  "½ Half Kelly",
                1.0:  "Full Kelly (Aggressive)",
            }[x],
        )

    pf_comp = st.text_input(
        "Filter by competition (optional)",
        placeholder="e.g. Premier League, La Liga, Bundesliga…"
    )

    if st.button("🔍 Build Daily Value Portfolio", type="primary", use_container_width=True):
        with st.spinner("Scanning upcoming fixtures for +EV opportunities…"):
            db_pf = _db()
            try:
                pf_data = build_daily_portfolio(
                    db=db_pf,
                    days_ahead=pf_days,
                    min_ev_percent=pf_min_ev,
                    bankroll=pf_bankroll,
                    kelly_fraction=pf_kelly,
                    competition=pf_comp.strip() if pf_comp.strip() else None,
                )
            except Exception as e:
                st.error(f"Portfolio error: {e}")
                db_pf.close()
                st.stop()
            finally:
                db_pf.close()

        summary = pf_data["summary"]

        # ── Summary banner ──────────────────────────────────────────────────
        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("Fixtures Scanned", summary["fixtures_scanned"])
        sm2.metric("Analysed", summary["fixtures_analysed"])
        sm3.metric("Value Bets Found", summary["value_bets_found"])
        sm4.metric("Qualifying Markets", summary["qualifying_markets_found"])
        sm5.metric("Total Kelly Exposure", f"${summary['total_kelly_exposure']:.2f}")

        if summary["fixtures_scanned"] == 0:
            st.warning(
                "⚠️ No upcoming fixtures found in the database.\n\n"
                "Go to the **📥 Ingestion & Import** tab and click "
                "**⚡ Fetch Real Upcoming Matches** to load 160+ live fixtures."
            )
        else:
            st.markdown("---")

            # ── Value Bets ──────────────────────────────────────────────────
            value_bets = pf_data.get("value_bets", [])
            st.subheader(f"💰 +EV Value Bets  ({len(value_bets)})")
            if value_bets:
                vdf = pd.DataFrame(value_bets)
                show = ["kickoff_utc", "match", "competition", "market",
                        "model_probability", "bookmaker_odds", "implied_probability",
                        "edge_percent", "ev_percent",
                        "kelly_stake_percent", "kelly_stake_amount", "lambda_source"]
                show = [c for c in show if c in vdf.columns]
                vdf_disp = vdf[show].copy()
                vdf_disp.columns = [c.replace("_", " ").title() for c in vdf_disp.columns]
                st.dataframe(
                    vdf_disp.style.background_gradient(
                        subset=["Ev Percent"] if "Ev Percent" in vdf_disp.columns else [],
                        cmap="Greens"
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                csv_vb = vdf[show].to_csv(index=False).encode()
                st.download_button(
                    "📥 Export Value Slip as CSV",
                    data=csv_vb,
                    file_name=f"fie_value_slip_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.info(
                    "No +EV bets found with current filters. "
                    "Try lowering the Min +EV % slider or extending the look-ahead window."
                )

            # ── Qualifying Markets ──────────────────────────────────────────
            st.markdown("---")
            qualifying = pf_data.get("qualifying_markets", [])
            st.subheader(f"🎯 Qualifying Markets ≥ 85% Confidence  ({len(qualifying)})")
            if qualifying:
                qdf = pd.DataFrame(qualifying)
                q_show = ["kickoff_utc", "match", "competition", "market",
                          "probability", "lambda_source"]
                q_show = [c for c in q_show if c in qdf.columns]
                qdf_disp = qdf[q_show].copy()
                qdf_disp.columns = [c.replace("_", " ").title() for c in qdf_disp.columns]
                st.dataframe(qdf_disp, use_container_width=True, hide_index=True)
                csv_qm = qdf[q_show].to_csv(index=False).encode()
                st.download_button(
                    "📥 Export Qualifying Markets as CSV",
                    data=csv_qm,
                    file_name=f"fie_qualifying_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.info("No markets reached the 85% confidence threshold.")


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — FIXTURES & ODDS
# ═════════════════════════════════════════════════════════════════════════════
with tab_fixtures:
    st.subheader("Match Fixtures & Quality Grades")

    db_fx = _db()
    fixtures_raw = db_fx.scalars(
        select(Fixture).order_by(Fixture.kickoff_utc).limit(500)
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
            "id", "kickoff_utc", "home_team", "away_team", "competition", "country",
            "source", "status", "home_odds", "draw_odds", "away_odds",
            "data_quality_score", "data_quality_grade", "eligible_for_analysis",
        ]
        avail = [c for c in display_cols if c in filtered.columns]
        st.dataframe(filtered[avail], use_container_width=True, hide_index=True)

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
# TAB 2 — INGESTION & IMPORT
# ═════════════════════════════════════════════════════════════════════════════
with tab_import:
    st.subheader("Data Ingestion & Real-Time League Feeds")
    st.write(
        "FIE connects to publicly authorised football data archives. "
        "Every match is automatically deduplicated, normalised, and used to calibrate team ratings."
    )

    st.markdown("### 🌐 Real Data Feeds (Live & Full Seasons)")
    feed1, feed2 = st.columns(2)

    with feed1:
        st.markdown("#### 1. Upcoming Matches & Live Odds")
        st.write(
            "Fetches **160+ real upcoming European matches** across England, Spain, Germany, "
            "Italy, France, and more — with multi-bookmaker odds from football-data.co.uk."
        )
        if st.button(
            "⚡ Fetch Real Upcoming Matches (160+ Games)",
            type="primary", use_container_width=True,
        ):
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
        st.markdown("#### 2. Full League Season (380 Matches)")
        st.write(
            "Ingest a complete historical season with final scores and closing odds "
            "to auto-calibrate all 20 team ratings."
        )
        l_div, l_sea = st.columns(2)
        with l_div:
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
            )
        with l_sea:
            sel_sea = st.selectbox(
                "Season",
                ["2425", "2324", "2223"],
                format_func=lambda x: {
                    "2425": "2024/25",
                    "2324": "2023/24",
                    "2223": "2022/23",
                }.get(x, x),
            )
        if st.button("📥 Download & Ingest Season", use_container_width=True):
            with st.spinner(f"Downloading {sel_div} {sel_sea}…"):
                db_s = _db()
                try:
                    result = fetch_historical_league_season(db_s, sel_div, sel_sea)
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
