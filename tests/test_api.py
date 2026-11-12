from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "total_fixtures" in data
    assert data["min_prediction_probability"] == 0.85

def test_bookmakers_endpoint():
    response = client.get("/bookmakers")
    assert response.status_code == 200
    names = [b["name"] for b in response.json()]
    assert "BetPawa" in names
    assert "Betika" in names
    assert "1xBet" in names

def test_connectors_status():
    response = client.get("/connectors/status")
    assert response.status_code == 200
    assert len(response.json()) == 5

def test_import_and_list_fixtures():
    # Import sample fixtures
    import_res = client.post("/fixtures/import-csv", json={"path": "data/sample_fixtures.csv"})
    assert import_res.status_code == 200
    data = import_res.json()
    assert data["inserted"] >= 1 or data["duplicates"] >= 1

    # List fixtures
    list_res = client.get("/fixtures")
    assert list_res.status_code == 200
    fixtures = list_res.json()
    assert len(fixtures) > 0

    first = fixtures[0]
    assert "home_team" in first
    assert "away_team" in first
    assert "home_odds" in first
    assert "data_quality_score" in first

    # Query single fixture
    single_res = client.get(f"/fixtures/{first['id']}")
    assert single_res.status_code == 200
    assert single_res.json()["id"] == first["id"]

    # Check fixture quality
    qual_res = client.get(f"/fixtures/{first['id']}/quality")
    assert qual_res.status_code == 200
    q_data = qual_res.json()
    assert "signals" in q_data
    assert "breakdown" in q_data

    # Check fixture analysis (+EV and market probabilities)
    analysis_res = client.get(f"/fixtures/{first['id']}/analysis")
    assert analysis_res.status_code == 200
    a_data = analysis_res.json()
    assert "estimated_lambdas" in a_data
    assert "all_markets" in a_data
    assert "qualifying_markets" in a_data
    assert "value_bets" in a_data

def test_upload_csv_endpoint():
    csv_content = (
        "kickoff_utc,home_team,away_team,competition,country,source,home_odds,draw_odds,away_odds\n"
        "2026-11-25T19:00:00Z,Arsenal,Chelsea,Premier League,England,TestBookie,2.05,3.40,3.60\n"
    )
    response = client.post(
        "/fixtures/upload-csv",
        files={"file": ("custom_fixtures.csv", csv_content, "text/csv")}
    )
    assert response.status_code == 200
    assert response.json()["inserted"] >= 1 or response.json()["duplicates"] >= 1

def test_markets_baseline_endpoint():
    response = client.get("/markets/baseline?home_lambda=1.8&away_lambda=1.2&threshold=0.85&rho=-0.12")
    assert response.status_code == 200
    data = response.json()
    assert "qualifying_markets" in data
    assert "markets" in data
    assert "double_chance_1x" in data["markets"]

def test_team_ratings_api():
    # Create / update rating
    post_res = client.post(
        "/ratings",
        json={
            "team_name": "Liverpool",
            "competition": "Premier League",
            "attack_rating": 1.45,
            "defense_rating": 0.75,
            "matches_played": 18
        }
    )
    assert post_res.status_code == 200
    r_data = post_res.json()
    assert r_data["team_name"] == "liverpool"
    assert r_data["attack_rating"] == 1.45

    # List ratings
    list_res = client.get("/ratings?competition=Premier")
    assert list_res.status_code == 200
    teams = [t["team_name"] for t in list_res.json()]
    assert "liverpool" in teams

    # Calculate from fixtures
    calc_res = client.post("/ratings/calculate-from-fixtures")
    assert calc_res.status_code == 200

def test_seed_and_backtest_api():
    # Seed historical
    seed_res = client.post("/fixtures/seed-historical")
    assert seed_res.status_code == 200
    assert "teams_rated" in seed_res.json()

    # Run backtest
    bt_res = client.post(
        "/backtest/run",
        json={
            "strategy": "value_bet",
            "min_ev_percent": 1.0,
            "min_quality_score": 0,
            "staking": "quarter_kelly",
            "initial_bankroll": 1000.0,
        }
    )
    assert bt_res.status_code == 200
    bt_data = bt_res.json()
    assert "win_rate_percent" in bt_data
    assert "net_profit" in bt_data


def test_evaluation_endpoints():
    clv = client.get("/evaluation/clv")
    assert clv.status_code == 200
    clv_data = clv.json()
    assert "n_selections" in clv_data
    assert "beat_close_percent" in clv_data

    calibration = client.get("/evaluation/calibration")
    assert calibration.status_code in (200, 404)
    if calibration.status_code == 200:
        assert "verdict" in calibration.json()


def test_deep_analysis_and_learning_endpoints():
    fixtures = client.get("/fixtures").json()
    assert fixtures, "fixture list should not be empty"

    deep = client.get(f"/fixtures/{fixtures[0]['id']}/deep-analysis")
    assert deep.status_code == 200
    data = deep.json()
    assert {"verdict", "markets", "form", "h2h", "team_news", "player_form", "risks"} <= set(data)
    assert data["player_form"]["available"] is False

    missing = client.get("/fixtures/99999999/deep-analysis")
    assert missing.status_code == 404

    summary = client.get("/learning/summary")
    assert summary.status_code == 200
    assert "trained" in summary.json()


def test_ratings_freshness_endpoint():
    response = client.get("/ratings/freshness")
    assert response.status_code == 200
    data = response.json()
    assert "snapshot_hash" in data
    assert data["stale_after_days"] > 0
    assert isinstance(data["competitions"], list)
