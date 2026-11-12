"""Platform routers: markets, predictions, performance, sources, settings."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_market_catalogue_and_policy():
    response = client.get("/markets/catalogue")
    assert response.status_code == 200
    payload = response.json()
    assert payload["qualification_threshold"] == 0.85
    families = {row["family"] for row in payload["families"]}
    assert {"match_result", "total_corners", "total_cards", "player_goalscorer"} <= families

    policy = client.get("/markets/qualification-policy").json()
    assert policy["minimum_probability"] == 0.85
    assert policy["message_when_empty"] == "NO QUALIFIED SELECTIONS FOUND."


def test_market_analysis_requires_fixture():
    assert client.get("/markets/fixtures/999999/discovery").status_code == 404
    assert client.get("/markets/fixtures/999999/analysis").status_code == 404


def test_prediction_history_is_recorded():
    created = client.post(
        "/predictions/record",
        json={
            "fixture_id": None,
            "family": "match_result",
            "selection": "1",
            "probability": 0.91,
            "confidence": 0.8,
            "qualified": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["qualified"] is True

    low = client.post(
        "/predictions/record",
        json={
            "family": "match_result",
            "selection": "1",
            "probability": 0.70,
            "confidence": 0.9,
            "qualified": True,
        },
    )
    assert low.json()["qualified"] is False

    listing = client.get("/predictions?qualified_only=true").json()
    assert listing["count"] >= 1


def test_performance_summary_excludes_synthetic_scope():
    response = client.get("/performance/summary")
    assert response.status_code == 200
    payload = response.json()
    assert "synthetic" in payload["scope"]
    assert {"bets", "settled", "wins", "losses", "voids", "roi", "yield"} <= set(payload)


def test_sources_report_configuration_without_faking_data():
    payload = client.get("/sources").json()
    names = {row["name"] for row in payload["providers"]}
    assert {"api_football", "football_data_org", "espn", "odds_api", "manual_csv"} <= names
    assert all("configured" in row for row in payload["providers"])
    assert client.get("/sources/nope/status").status_code == 404


def test_settings_enforce_policy():
    settings = client.get("/settings").json()["settings"]
    assert settings["auto_place_bets"] == "false"
    assert settings["qualification_threshold"] == "0.85"

    blocked = client.put("/settings/qualification_threshold", json={"value": "0.80"})
    assert blocked.status_code == 400
    blocked_auto = client.put("/settings/auto_place_bets", json={"value": "true"})
    assert blocked_auto.status_code == 400

    updated = client.put("/settings/timezone", json={"value": "Africa/Kampala"})
    assert updated.status_code == 200


def test_notifications_queue():
    created = client.post(
        "/notifications",
        json={"channel": "telegram", "body": "Daily shortlist ready", "subject": "FIE"},
    )
    assert created.status_code == 200
    notification_id = created.json()["id"]

    pending = client.get("/notifications?status=pending").json()
    assert any(row["id"] == notification_id for row in pending)

    sent = client.post(f"/notifications/{notification_id}/sent")
    assert sent.json()["status"] == "sent"
