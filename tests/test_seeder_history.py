from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import delete, select

from app.data_sources import seeder
from app.database import SessionLocal
from app.models.fixture import Fixture

_TEST_PREFIX = "SeedTest"


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.execute(delete(Fixture).where(Fixture.home_team.like(f"{_TEST_PREFIX}%")))
        session.commit()
        session.close()


def _match(home: str, away: str, **odds) -> dict:
    row = {
        "Div": "E0",
        "Date": "14/08/2021",
        "HomeTeam": f"{_TEST_PREFIX} {home}",
        "AwayTeam": f"{_TEST_PREFIX} {away}",
        "FTHG": 1,
        "FTAG": 0,
        "B365H": float("nan"),
        "B365D": float("nan"),
        "B365A": float("nan"),
    }
    row.update(odds)
    return row


def _stored(db, home: str) -> Fixture:
    return db.scalar(
        select(Fixture).where(Fixture.home_team == f"{_TEST_PREFIX} {home}")
    )


def test_import_uses_legacy_bookmaker_when_b365_is_missing(db):
    """2000/01 files carry only WH/LB/SB/IW/GB prices; those must be stored."""
    df = pd.DataFrame([_match("Legacy Alpha", "Legacy Beta", WHH=2.5, WHD=3.4, WHA=2.9)])

    seeder.import_football_data_dataframe(db, df, recalculate_ratings=False)

    result = seeder.import_football_data_dataframe(db, df, recalculate_ratings=False)
    fixture = _stored(db, "Legacy Alpha")
    assert fixture is not None
    assert (fixture.home_odds, fixture.draw_odds, fixture.away_odds) == (2.5, 3.4, 2.9)
    assert result["priced"] == 1


def test_import_prefers_b365_over_other_books(db):
    df = pd.DataFrame([
        _match("Preferred Alpha", "Preferred Beta", B365H=2.0, B365D=3.2, B365A=3.8, WHH=2.4)
    ])

    seeder.import_football_data_dataframe(db, df, recalculate_ratings=False)

    assert _stored(db, "Preferred Alpha").home_odds == 2.0


def test_import_can_defer_the_ratings_rebuild(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        seeder,
        "calculate_ratings_from_fixtures",
        lambda session: calls.append(session) or {"updated_teams": 0},
    )

    seeder.import_football_data_dataframe(
        db, pd.DataFrame([_match("Defer Alpha", "Defer Beta", WHH=2.1)]), recalculate_ratings=False
    )
    assert calls == []

    seeder.import_football_data_dataframe(
        db, pd.DataFrame([_match("Defer Gamma", "Defer Delta", WHH=2.2)]), recalculate_ratings=True
    )
    assert len(calls) == 1


def test_history_season_codes_span_2000_to_present():
    codes = seeder.history_season_codes(2000, 2026)

    assert len(codes) == 27
    assert codes[0] == "0001"
    assert codes[-1] == "2627"
    assert "2324" in codes


def test_historical_divisions_cover_the_source():
    codes = [code for code, _ in seeder.HISTORICAL_DIVISIONS]

    assert len(codes) == 18
    assert len(set(codes)) == 18
    assert {"E0", "E1", "E2", "E3", "SC0", "SP1", "I1", "D1", "F1"} <= set(codes)
    assert all(code in seeder.DIV_MAPPING for code in codes)


def test_seed_backtest_history_sums_loaded_seasons(monkeypatch):
    calls = []

    def fake_fetch(db, division_code, season):
        calls.append((division_code, season))
        return {"inserted": 10, "duplicates": 2}

    monkeypatch.setattr(seeder, "fetch_historical_league_season", fake_fetch)

    res = seeder.seed_backtest_history(None, seasons=(("E0", "2324"), ("SP1", "2425")))

    assert res["source"] == "football-data.co.uk"
    assert res["seasons_loaded"] == 2
    assert res["inserted"] == 20
    assert res["duplicates"] == 4
    assert calls == [("E0", "2324"), ("SP1", "2425")]


def test_reader_keeps_rows_that_carry_only_empty_extra_cells():
    raw = (
        b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,WHH,WHD,WHA\n"
        b"E0,14/08/2021,Tottenham,Chelsea,0,1,2.1,3.2,2.85,,\n"
    )

    frame, dropped = seeder.read_football_data_csv(raw)

    assert dropped == 0
    assert len(frame) == 1
    assert frame.iloc[0]["AwayTeam"] == "Chelsea"
    assert frame.iloc[0]["WHA"] == "2.85"


def test_reader_pads_short_rows_and_drops_rows_with_stray_values():
    raw = (
        b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,WHH,WHD,WHA\n"
        b"E0,14/08/2021,Wolves,Southampton,1,4,2.5,3.4\n"
        b"E0,14/08/2021,Leeds,Everton,1,0,2.5,3.4,2.9,unexpected\n"
    )

    frame, dropped = seeder.read_football_data_csv(raw)

    assert dropped == 1
    assert len(frame) == 1
    assert frame.iloc[0]["HomeTeam"] == "Wolves"
    assert frame.iloc[0]["WHA"] == ""


def test_reader_accepts_the_legacy_greek_team_columns():
    """G1 2000/01-2004/05 name the teams HT/AT instead of HomeTeam/AwayTeam."""
    raw = (
        b"Div,Date,HT,AT,FTHG,FTAG,FTR,GBH,GBD,GBA\n"
        b"G1,16/09/00,Olympiakos,Eth Asteras,6,0,H,1.5,3.5,6.0\n"
    )

    frame, dropped = seeder.read_football_data_csv(raw)

    assert dropped == 0
    assert frame.iloc[0]["HomeTeam"] == "Olympiakos"
    assert frame.iloc[0]["AwayTeam"] == "Eth Asteras"


def test_reader_decodes_latin1_team_names():
    raw = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,14/08/2021,München,Köln,1,0\n".encode("latin-1")

    frame, dropped = seeder.read_football_data_csv(raw)

    assert dropped == 0
    assert frame.iloc[0]["HomeTeam"] == "München"


def test_seed_backtest_history_falls_back_to_bundled_sample(monkeypatch):
    def boom(db, division_code, season):
        raise RuntimeError("network down")

    monkeypatch.setattr(seeder, "fetch_historical_league_season", boom)
    monkeypatch.setattr(
        seeder, "seed_sample_historical_data", lambda db: {"inserted": 29, "duplicates": 0}
    )

    res = seeder.seed_backtest_history(None, seasons=(("E0", "2324"),))

    assert res["source"] == "bundled_sample"
    assert res["inserted"] == 29
    assert res["seasons_loaded"] == 0
    assert "network down" in res["errors"][0]
