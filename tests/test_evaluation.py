"""Tests for the measurement layer: decay, aliasing, market-implied goals,
walk-forward prediction, calibration metrics and closing-line value."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.fixture import Fixture
from app.models.price_snapshot import PriceSnapshot  # noqa: F401  (registers the table)
from app.analytics.ratings import (
    MIN_MATCH_WEIGHT,
    RATING_HALF_LIFE_DAYS,
    data_age_days,
    decay_weight,
    rating_key,
)
from app.analytics.probabilities import (
    estimate_lambdas_from_market,
    poisson_market_probabilities,
)
from app.analytics.calibration import (
    _fair_1x2,
    brier_score,
    expected_calibration_error,
    iter_walkforward_predictions,
    log_loss,
)
from app.betting.clv import clv_summary, record_selection_prices


@pytest.fixture()
def mem_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _fixture(**overrides) -> Fixture:
    defaults = dict(
        dedupe_key=overrides.get("dedupe_key", "key"),
        kickoff_utc=datetime(2024, 6, 1, 15, 0, tzinfo=timezone.utc),
        home_team="Alpha",
        away_team="Beta",
        competition="Synthetic League",
        country="Testland",
        source="test",
        home_odds=2.0,
        draw_odds=3.3,
        away_odds=3.8,
        status="finished",
    )
    defaults.update(overrides)
    return Fixture(**defaults)


# ── Ratings: recency decay and alias resolution ──────────────────────────────

def test_decay_weight_halves_each_half_life():
    ref = datetime(2024, 6, 1, tzinfo=timezone.utc)
    one_half_life = decay_weight(ref - timedelta(days=RATING_HALF_LIFE_DAYS), ref)
    two_half_lives = decay_weight(ref - timedelta(days=2 * RATING_HALF_LIFE_DAYS), ref)

    assert one_half_life == pytest.approx(0.5, abs=1e-6)
    assert two_half_lives == pytest.approx(0.25, abs=1e-6)
    assert decay_weight(ref - timedelta(days=30), ref) > one_half_life
    # A match in the "future" relative to the reference must not exceed full weight.
    assert decay_weight(ref + timedelta(days=5), ref) == 1.0
    # Ancient matches never decay to zero, they bottom out at the floor.
    assert decay_weight(ref - timedelta(days=5000), ref) == MIN_MATCH_WEIGHT


def test_data_age_days_reports_staleness():
    ref = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert data_age_days(None, ref) is None
    assert data_age_days(datetime(2025, 12, 2, tzinfo=timezone.utc), ref) == 30.0
    # Naive datetimes (SQLite) are treated as UTC.
    assert data_age_days(datetime(2025, 12, 2), ref) == 30.0


def test_rating_key_resolves_feed_aliases_and_club_prefixes():
    assert rating_key("AFC Bournemouth") == rating_key("Bournemouth") == "bournemouth"
    assert rating_key("Nott'm Forest") == rating_key("Nottingham Forest") == "nottingham forest"
    assert rating_key("Inter Milan") == rating_key("Internazionale") == "internazionale"
    assert rating_key("FC Porto") == rating_key("Porto") == "fc porto"
    assert rating_key("Sheffield Weds") == "sheffield wednesday"


# ── Market-implied expected goals ────────────────────────────────────────────

class _FakeFixture:
    def __init__(self, home, draw, away, market=None):
        self.home_odds = home
        self.draw_odds = draw
        self.away_odds = away
        self.market_odds_json = json.dumps(market) if market else None


def test_market_implied_lambdas_match_the_over_under_line():
    fixture = _FakeFixture(
        2.00, 3.40, 3.80,
        market={"over_2_5": 2.00, "under_2_5": 1.85},
    )
    home_lam, away_lam, source = estimate_lambdas_from_market(fixture)

    assert source == "market_totals"
    assert home_lam > 0.15 and away_lam > 0.15

    raw_over, raw_under = 1.0 / 2.00, 1.0 / 1.85
    fair_over = raw_over / (raw_over + raw_under)
    model = poisson_market_probabilities(home_lam, away_lam, rho=0.0)["markets"]
    assert model["over_2_5"] == pytest.approx(fair_over, abs=0.02)


def test_market_implied_lambdas_put_more_goals_on_the_favourite():
    favourite = _FakeFixture(1.45, 4.20, 6.50, market={"over_2_5": 1.90, "under_2_5": 1.95})
    home_lam, away_lam, _ = estimate_lambdas_from_market(favourite)
    assert home_lam > away_lam


def test_market_implied_lambdas_fall_back_when_prices_missing():
    home_lam, away_lam, source = estimate_lambdas_from_market(_FakeFixture(None, None, None))
    assert source == "fallback"
    assert home_lam == pytest.approx(1.45)
    assert away_lam == pytest.approx(1.15)


# ── Calibration metrics ──────────────────────────────────────────────────────

def test_calibration_metrics_reward_confident_correct_forecasts():
    good = [{"home_win": 0.90, "draw": 0.05, "away_win": 0.05}]
    bad = [{"home_win": 0.05, "draw": 0.05, "away_win": 0.90}]

    assert brier_score(good, ["home_win"]) < brier_score(bad, ["home_win"])
    assert log_loss(good, ["home_win"]) < log_loss(bad, ["home_win"])

    fair = _fair_1x2(2.0, 4.0, 4.0)
    assert sum(fair.values()) == pytest.approx(1.0)

    overconfident = [(0.9, 0)] * 10
    assert expected_calibration_error(overconfident) == pytest.approx(0.9, abs=1e-9)


# ── Walk-forward predictions never see the future ────────────────────────────

def _seed_league(db, matches: int = 40, spacing_days: int = 3):
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    start = datetime(2024, 1, 1, 15, 0, tzinfo=timezone.utc)
    kickoffs = []
    for i in range(matches):
        kickoff = start + timedelta(days=i * spacing_days)
        kickoffs.append(kickoff)
        db.add(Fixture(
            dedupe_key=f"wf-{i}",
            kickoff_utc=kickoff,
            home_team=teams[i % 4],
            away_team=teams[(i + 1) % 4],
            competition="Synthetic League",
            country="Testland",
            source="test",
            home_odds=2.0,
            draw_odds=3.3,
            away_odds=3.8,
            home_score=1 + (i % 3),
            away_score=(i + 1) % 3,
            status="finished",
        ))
    db.commit()
    return kickoffs


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def test_walkforward_predictions_use_only_past_matches(mem_db):
    kickoffs = _seed_league(mem_db, matches=40)

    predictions = list(iter_walkforward_predictions(mem_db, refit_days=14))
    assert predictions, "expected some out-of-sample predictions from 40 matches"

    all_kickoffs = sorted(kickoffs)
    for item in predictions:
        kickoff = _aware(item["fixture"].kickoff_utc)
        # At least 30 matches must have finished before this one, or the model
        # had no history to learn from and should not have predicted it.
        assert sum(1 for k in all_kickoffs if k < kickoff) >= 30

        # Probabilities are rounded to four decimals before being returned.
        assert sum(item["model"].values()) == pytest.approx(1.0, abs=1e-3)
        assert sum(item["market"].values()) == pytest.approx(1.0, abs=1e-6)
        assert item["label"] in item["model"]

    earliest_predicted = min(_aware(p["fixture"].kickoff_utc) for p in predictions)
    assert earliest_predicted >= all_kickoffs[30]


def test_walkforward_ignores_ignored_competitions(mem_db):
    _seed_league(mem_db, matches=40)
    for fixture in mem_db.query(Fixture).all():
        fixture.competition = "Test League"
    mem_db.commit()

    assert list(iter_walkforward_predictions(mem_db, refit_days=14)) == []


# ── Closing-line value ───────────────────────────────────────────────────────

def test_clv_measures_selection_against_closing_price(mem_db):
    fixture = _fixture(dedupe_key="clv-1")
    mem_db.add(fixture)
    mem_db.commit()

    recorded = record_selection_prices(
        mem_db, slip_id=1,
        legs=[{"fixture_id": fixture.id, "market_key": "home_win", "odds": 2.20}],
    )
    assert recorded == 1

    # Recording twice must not duplicate the selection snapshot.
    assert record_selection_prices(
        mem_db, slip_id=1,
        legs=[{"fixture_id": fixture.id, "market_key": "home_win", "odds": 2.20}],
    ) == 0

    mem_db.add(PriceSnapshot(
        fixture_id=fixture.id, market_key="home_win", price=2.00, kind="close",
    ))
    mem_db.commit()

    summary = clv_summary(mem_db)
    assert summary["n_selections"] == 1
    assert summary["mean_clv_percent"] == pytest.approx(10.0, abs=0.01)
    assert summary["beat_close_percent"] == pytest.approx(100.0)


def test_clv_summary_is_empty_without_closing_prices(mem_db):
    fixture = _fixture(dedupe_key="clv-2")
    mem_db.add(fixture)
    mem_db.commit()
    record_selection_prices(
        mem_db, slip_id=2,
        legs=[{"fixture_id": fixture.id, "market_key": "draw", "odds": 3.5}],
    )
    summary = clv_summary(mem_db)
    assert summary["n_selections"] == 0
    assert summary["mean_clv_percent"] is None


# ── Ledger integrity: synthetic test slips must not prove performance ────────

def _bet(matchday_label, competition, status="WON"):
    from app.models.bet import TrackedBet
    return TrackedBet(
        slip_type="2-Team Combo",
        matchday_label=matchday_label,
        total_odds=2.15,
        avg_confidence=88.0,
        accum_probability=86.0,
        stake_amount=100000.0,
        stake_currency="UGX",
        stake_usd=27.0,
        status=status,
        profit_loss=114600.0,
        profit_loss_usd=31.0,
        legs_json=json.dumps([
            {"match": "A vs B", "competition": competition, "odds": 1.45},
            {"match": "C vs D", "competition": competition, "odds": 1.48},
        ]),
    )


def test_ledger_excludes_test_league_slips(mem_db):
    from app.models.bet import TrackedBet
    from app.betting.settlement import get_performance_ledger_summary

    mem_db.add_all([
        _bet("Test Matchday", "Test League"),
        _bet("Sat 14 Nov", "Test League"),
        _bet("Sat 14 Nov", "Premier League"),
    ])
    mem_db.commit()

    stats = get_performance_ledger_summary(mem_db)

    assert stats["excluded_synthetic_count"] == 2
    assert stats["settled_count"] == 1
    assert stats["win_rate"] == pytest.approx(100.0)
    assert [b.matchday_label for b in stats["recent_bets"]] == ["Sat 14 Nov"]
    assert all(b.matchday_label != "Test Matchday" for b in stats["recent_bets"])
