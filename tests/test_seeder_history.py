from app.data_sources import seeder


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
