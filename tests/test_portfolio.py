"""Tests for the Daily Portfolio endpoint, Best Odds endpoint, and Stale Fixture marking."""
from __future__ import annotations
import io
import csv
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _make_csv_bytes(rows: list[dict]) -> bytes:
    """Build a minimal CSV in the standard FIE import format."""
    buf = io.StringIO()
    fieldnames = ["kickoff_utc", "home_team", "away_team", "competition",
                  "country", "source", "home_odds", "draw_odds", "away_odds"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


# ---------------------------------------------------------------------------
# Portfolio endpoint tests
# ---------------------------------------------------------------------------

def test_portfolio_endpoint_exists():
    """Portfolio endpoint responds without error even on an empty-ish DB."""
    res = client.get("/portfolio/today", params={"days_ahead": 7})
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert "value_bets" in data
    assert "qualifying_markets" in data


def test_portfolio_summary_keys_always_present():
    """Portfolio summary always has all expected keys."""
    res = client.get("/portfolio/today", params={"days_ahead": 1})
    assert res.status_code == 200
    summary = res.json()["summary"]
    for key in (
        "fixtures_scanned",
        "fixtures_analysed",
        "fixtures_skipped_no_odds",
        "value_bets_found",
        "qualifying_markets_found",
        "total_kelly_exposure",
    ):
        assert key in summary, f"Missing summary key: {key}"


def test_portfolio_params_key_returned():
    """Portfolio echoes back input params."""
    res = client.get(
        "/portfolio/today",
        params={"days_ahead": 3, "min_ev_percent": 2.5, "bankroll": 500.0, "kelly_fraction": 0.5},
    )
    assert res.status_code == 200
    params = res.json()["params"]
    assert params["days_ahead"] == 3
    assert params["min_ev_percent"] == 2.5
    assert params["bankroll"] == 500.0
    assert params["kelly_fraction"] == 0.5


def test_portfolio_with_seeded_fixtures():
    """After seeding, portfolio may find upcoming fixtures with odds (non-fatal if 0)."""
    # Seed offline EPL sample (contains historical scores, not upcoming — but validates endpoint pipeline)
    seed_res = client.post("/fixtures/seed-historical")
    assert seed_res.status_code == 200

    res = client.get("/portfolio/today", params={"days_ahead": 7, "bankroll": 1000})
    assert res.status_code == 200
    data = res.json()
    # Even with historical data seeded, upcoming window may have 0 — both are valid
    assert isinstance(data["value_bets"], list)
    assert isinstance(data["qualifying_markets"], list)
    assert data["summary"]["total_kelly_exposure"] >= 0.0


def test_portfolio_min_ev_filter_accepted():
    """min_ev_percent query param is accepted without error."""
    res = client.get("/portfolio/today", params={"min_ev_percent": 5.0})
    assert res.status_code == 200


def test_portfolio_competition_filter_accepted():
    """competition query param is accepted without error."""
    res = client.get("/portfolio/today", params={"competition": "Premier League"})
    assert res.status_code == 200


def test_portfolio_combos_endpoint():
    """Portfolio combos endpoint responds with valid combo structures."""
    res = client.get("/portfolio/combos", params={"num_legs": 3, "min_total_odds": 2.0, "min_leg_prob": 0.80})
    assert res.status_code == 200
    data = res.json()
    assert "total_combos_generated" in data
    assert "best_combo" in data
    assert "alternatives" in data
    if data["best_combo"]:
        bc = data["best_combo"]
        assert bc["total_odds"] >= 2.0
        assert len(bc["legs"]) in [2, 3, 4]
        assert "combined_probability" in bc
        assert "expected_value_percent" in bc



# ---------------------------------------------------------------------------
# Best Odds endpoint tests
# ---------------------------------------------------------------------------

def test_best_odds_404_unknown_fixture():
    """Non-existent fixture returns 404."""
    res = client.get("/fixtures/999999999/best-odds")
    assert res.status_code == 404


def test_best_odds_for_real_fixture():
    """An existing fixture returns a valid best-odds structure."""
    # Import a fixture first
    import_res = client.post("/fixtures/import-csv", json={"path": "data/sample_fixtures.csv"})
    assert import_res.status_code == 200

    # Grab first fixture ID
    list_res = client.get("/fixtures?limit=1")
    assert list_res.status_code == 200
    fixtures = list_res.json()
    if not fixtures:
        pytest.skip("No fixtures in DB — seed first")

    fid = fixtures[0]["id"]
    res = client.get(f"/fixtures/{fid}/best-odds")
    assert res.status_code == 200
    data = res.json()

    # Schema assertions
    assert "fixture_id" in data
    assert "match" in data
    assert "home" in data
    assert "draw" in data
    assert "away" in data
    assert "has_arbitrage" in data
    assert isinstance(data["has_arbitrage"], bool)
    assert "min_overround" in data
    assert data["min_overround"] >= 0.0


# ---------------------------------------------------------------------------
# Mark stale fixtures endpoint tests
# ---------------------------------------------------------------------------

def test_mark_stale_endpoint_exists():
    """Mark-stale endpoint responds with correct schema."""
    res = client.patch("/fixtures/mark-stale")
    assert res.status_code == 200
    data = res.json()
    assert "updated_count" in data
    assert isinstance(data["updated_count"], int)
    assert data["updated_count"] >= 0


def test_mark_stale_message_present():
    """Mark-stale response always has a human-readable message."""
    res = client.patch("/fixtures/mark-stale")
    assert res.status_code == 200
    assert "message" in res.json()
