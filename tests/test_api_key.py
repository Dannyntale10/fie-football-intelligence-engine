"""A configured FIE_API_KEY closes every route except /health."""
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

client = TestClient(app)

KEY = "test-key-1234"


@pytest.fixture(autouse=True)
def _restore_key():
    original = settings.fie_api_key
    yield
    settings.fie_api_key = original


def test_routes_stay_open_when_no_key_is_configured():
    settings.fie_api_key = ""

    assert client.get("/fixtures/live").status_code == 200


def test_missing_header_is_rejected():
    settings.fie_api_key = KEY

    response = client.get("/fixtures/live")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid API key"


def test_wrong_header_is_rejected():
    settings.fie_api_key = KEY

    response = client.get("/fixtures/live", headers={"X-API-Key": "not-the-key"})

    assert response.status_code == 401


def test_correct_header_is_accepted():
    settings.fie_api_key = KEY

    response = client.get("/fixtures/live", headers={"X-API-Key": KEY})

    assert response.status_code == 200


def test_health_stays_open_so_the_host_can_probe_it():
    settings.fie_api_key = KEY

    assert client.get("/health").status_code == 200


def test_writes_are_gated_too():
    settings.fie_api_key = KEY

    response = client.post("/combos/backtest")

    assert response.status_code == 401


def test_docs_load_in_a_browser_while_data_routes_stay_gated():
    settings.fie_api_key = KEY

    assert client.get("/").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/fixtures/live").status_code == 401


def test_openapi_declares_the_header_so_authorize_works():
    settings.fie_api_key = KEY

    schema = client.get("/openapi.json").json()
    scheme = schema["components"]["securitySchemes"]["FieApiKey"]

    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert schema["security"] == [{"FieApiKey": []}]
