"""Daily market-probability combos and the historical replay."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analytics import combo_backtest
from app.betting import daily_combos as dc
from app.database import Base
from app.models.fixture import Fixture

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _isolate_learning(monkeypatch, tmp_path):
    """Never read the live trained calibration from data/ during tests."""
    monkeypatch.setattr(dc, "learning_path", lambda: tmp_path / "combo_learning.json")


def _attributed(odds):
    stamp = (NOW - timedelta(minutes=30)).isoformat()
    return json.dumps(
        {
            key: {
                "price": price,
                "bookmaker": "BetPawa",
                "source": "BETPAWA",
                "source_url": "https://www.betpawa.ug/",
                "captured_at": stamp,
            }
            for key, price in odds.items()
        }
    )


def _add(
    db,
    home,
    away,
    kickoff,
    odds,
    *,
    scores=None,
    attributed=True,
    competition="Premier League",
    max_home=None,
):
    fixture = Fixture(
        dedupe_key=f"{home}-{away}-{kickoff.isoformat()}",
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition=competition,
        country="England",
        source="BETPAWA",
        status="finished" if scores else "scheduled",
        home_odds=odds["home_win"],
        draw_odds=odds["draw"],
        away_odds=odds["away_win"],
        max_home_odds=max_home if max_home is not None else odds["home_win"],
        max_draw_odds=odds["draw"],
        max_away_odds=odds["away_win"],
        market_odds_json=_attributed(odds) if attributed else None,
        home_score=scores[0] if scores else None,
        away_score=scores[1] if scores else None,
    )
    db.add(fixture)
    db.commit()
    return fixture


def test_daily_combos_use_attributed_prices_and_pair_independent_matches(db):
    kickoff = NOW + timedelta(hours=11)
    strong_home = {"home_win": 1.22, "draw": 6.5, "away_win": 15.0}
    strong_away = {"home_win": 12.0, "draw": 6.0, "away_win": 1.20}
    _add(db, "Arsenal", "Chelsea", kickoff, strong_home, max_home=1.40)
    _add(db, "Everton", "Liverpool", kickoff, strong_away)
    # Below the 75% tier after de-vigging.
    _add(db, "Fulham", "Brentford", kickoff, {"home_win": 1.30, "draw": 5.5, "away_win": 10.0})
    # Flat prices only: real, but no bookmaker or capture time.
    _add(db, "Leeds", "Derby", kickoff, {"home_win": 1.20, "draw": 6.0, "away_win": 12.0}, attributed=False)
    # A feed placeholder row is not a fixture.
    _add(
        db,
        "Home (Special bets)",
        "Away (Special bets)",
        kickoff,
        {"home_win": 1.10, "draw": 8.0, "away_win": 30.0},
    )

    report = dc.build_daily_combos(db, min_leg_probability=0.75, now=NOW)

    assert report["scope"]["qualified_legs"] == 2
    assert report["scope"]["rejected"]["below_probability_floor"] == 1
    assert report["scope"]["rejected"]["unattributed_price"] == 1
    assert report["scope"]["rejected"]["placeholder_fixture"] == 1
    arsenal = next(leg for leg in report["legs"] if leg["match"] == "Arsenal vs Chelsea")
    # The attributed price wins over the higher max column: the price shown is
    # the price whose bookmaker is shown.
    assert arsenal["odds"] == 1.22
    assert arsenal["bookmaker"] == "BetPawa"
    assert arsenal["source_url"] == "https://www.betpawa.ug/"
    assert 0.75 <= arsenal["probability"] <= 0.82

    two = report["combos"]["2_leg"]
    assert len(two) == 1
    combo = two[0]
    assert combo["size"] == 2
    assert {leg["match"] for leg in combo["legs"]} == {"Arsenal vs Chelsea", "Everton vs Liverpool"}
    assert combo["total_odds"] == pytest.approx(
        round(1.22 * 1.20, 2), abs=0.01
    )
    assert combo["combined_probability"] == pytest.approx(
        arsenal["probability"]
        * next(leg for leg in report["legs"] if leg["match"] == "Everton vs Liverpool")["probability"],
        abs=0.001,
    )
    assert report["combos"]["3_leg"] == []
    assert report["tier"]["below_published_gate"] is True
    assert report["tier"]["published_gate"] == 0.85


def test_daily_combos_collapse_duplicate_feed_rows_and_respect_eat_day(db):
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Czechia", "Spain", kickoff, {"home_win": 12.0, "draw": 6.0, "away_win": 1.20})
    _add(
        db,
        "Czech Republic",
        "Spain",
        kickoff,
        {"home_win": 11.5, "draw": 6.6, "away_win": 1.225},
    )
    # 23:30 UTC is already the next day in EAT and must not enter today's slate.
    _add(
        db,
        "Italy",
        "France",
        NOW + timedelta(hours=17, minutes=30),
        {"home_win": 1.20, "draw": 6.0, "away_win": 12.0},
    )

    report = dc.build_daily_combos(db, min_leg_probability=0.75, now=NOW)

    assert len(report["legs"]) == 1
    assert report["legs"][0]["match"].endswith("vs Spain")
    assert report["scope"]["rejected"]["duplicate_match"] == 1
    assert report["combos"]["2_leg"] == []
    assert report["best"] is None
    assert report["message"] == "NO QUALIFIED SELECTIONS FOUND."


def test_daily_combos_apply_saved_learning_when_present(db, monkeypatch, tmp_path):
    path = tmp_path / "combo_learning.json"
    path.write_text(json.dumps({"applied": True, "a": 1.4, "b": -0.4, "n": 500}))
    monkeypatch.setattr(dc, "learning_path", lambda: path)
    kickoff = NOW + timedelta(hours=11)
    _add(db, "Arsenal", "Chelsea", kickoff, {"home_win": 1.22, "draw": 6.5, "away_win": 15.0})

    report = dc.build_daily_combos(db, min_leg_probability=0.75, now=NOW)

    leg = report["legs"][0]
    assert report["learning"]["applied"] is True
    assert leg["calibrated"] is True
    assert leg["probability"] != leg["market_probability"]


def _settled(db, home, away, kickoff, score, competition="Premier League"):
    _add(
        db,
        home,
        away,
        kickoff,
        {"home_win": 1.22, "draw": 6.5, "away_win": 15.0},
        scores=score,
        competition=competition,
    )


def test_backtest_replays_settles_and_splits_holdout(db):
    day = datetime(2024, 9, 1, 16, 0, tzinfo=timezone.utc)
    teams = [
        ("Arsenal", "Chelsea"),
        ("Liverpool", "Everton"),
        ("Manchester City", "Fulham"),
        ("Brighton", "Brentford"),
    ]
    for index in range(4):
        kickoff = day + timedelta(days=index)
        for offset, (home, away) in enumerate(teams[index % 2 : index % 2 + 2]):
            _settled(
                db,
                f"{home} {index}",
                f"{away} {index}",
                kickoff + timedelta(hours=offset),
                (2, 0),
            )

    report = combo_backtest.run_combo_backtest(db, min_leg_probability=0.75, stake=10.0)

    assert report["status"] == "ok"
    assert report["scope"]["settled_fixtures"] == 8
    assert report["scope"]["matchdays"] == 4
    assert report["combos"]["all"]["2_leg"]["count"] == 4
    assert report["combos"]["holdout"]["2_leg"]["count"] == 2
    assert report["combos"]["all"]["2_leg"]["hit_rate"] == 1.0
    assert report["combos"]["all"]["2_leg"]["roi_percent"] > 0
    assert report["combos"]["all"]["3_leg"]["count"] == 0
    assert report["learning"]["applied"] is False
    assert report["learning"]["reason"] == "insufficient history"
    assert report["window"]["first_matchday"] == "2024-09-01"


def test_backtest_saves_inactive_learning_without_evidence(db, monkeypatch, tmp_path):
    learning_file = tmp_path / "combo_learning.json"
    report_file = tmp_path / "combo_backtest.json"
    monkeypatch.setattr(combo_backtest, "combo_learning_path", lambda: learning_file)
    monkeypatch.setattr(combo_backtest, "backtest_path", lambda: report_file)
    monkeypatch.setattr(dc, "learning_path", lambda: learning_file)

    day = datetime(2024, 9, 1, 16, 0, tzinfo=timezone.utc)
    _settled(db, "Arsenal", "Chelsea", day, (2, 0))
    _settled(db, "Liverpool", "Everton", day, (1, 0))

    report = combo_backtest.run_combo_backtest(db)
    combo_backtest.save_report(report)
    saved = combo_backtest.save_learning(report)

    assert report_file.exists()
    assert saved["applied"] is False
    assert json.loads(learning_file.read_text())["applied"] is False
    assert dc.load_learning() is None
    assert combo_backtest.load_report()["status"] == "ok"
