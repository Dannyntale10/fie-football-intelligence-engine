"""The nightly run refreshes only the newest seasons it needs."""
from app.data_sources.seeder import seasons_to_refresh


def test_refresh_window_takes_the_newest_seasons():
    seasons = ["2122", "2223", "2324", "2425", "2526"]
    assert seasons_to_refresh(seasons, 2) == {"2425", "2526"}
    assert seasons_to_refresh(seasons, 0) == set()
    assert seasons_to_refresh(seasons, -1) == set()
    assert seasons_to_refresh(seasons, 10) == set(seasons)
