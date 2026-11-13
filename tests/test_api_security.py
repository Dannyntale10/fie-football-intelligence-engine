"""The engine fails closed, throttles bursts and caps bodies outside development."""
import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.core.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _restore_settings():
    original = {
        "environment": settings.environment,
        "fie_api_key": settings.fie_api_key,
        "rate_limit_read": settings.rate_limit_read,
        "rate_limit_write": settings.rate_limit_write,
        "rate_limit_expensive": settings.rate_limit_expensive,
        "max_body_bytes": settings.max_body_bytes,
    }
    security.limiter.reset()
    yield
    for name, value in original.items():
        setattr(settings, name, value)
    security.limiter.reset()


# --- production posture -----------------------------------------------------


def test_production_without_a_key_refuses_to_serve_data():
    settings.environment = "production"
    settings.fie_api_key = ""

    response = client.get("/fixtures/live")

    assert response.status_code == 503
    assert response.json()["detail"] == "Server API key is not configured"
    assert client.get("/health").status_code == 200


def test_production_with_a_key_requires_it():
    settings.environment = "production"
    settings.fie_api_key = "prod-key"

    assert client.get("/fixtures/live").status_code == 401
    assert (
        client.get("/fixtures/live", headers={"X-API-Key": "prod-key"}).status_code
        == 200
    )


def test_docs_are_not_served_outside_development():
    settings.environment = "production"
    settings.fie_api_key = "prod-key"

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/").json()["status"] == "ok"


def test_a_non_ascii_key_is_rejected_instead_of_crashing():
    settings.fie_api_key = "kéy"

    response = client.get("/fixtures/live", headers={"X-API-Key": b"n\xf1ot-the-key"})

    assert response.status_code == 401


def test_health_reports_the_security_posture():
    settings.environment = "production"
    settings.fie_api_key = ""

    body = client.get("/health").json()["security"]

    assert body["auth_required"] is True
    assert body["api_key_configured"] is False
    assert body["docs_public"] is False


def test_health_does_not_leak_the_key():
    settings.environment = "production"
    settings.fie_api_key = "super-secret-value"

    text = client.get("/health").text

    assert "super-secret-value" not in text


# --- rate limits ------------------------------------------------------------


def test_read_burst_is_throttled_with_retry_after():
    settings.rate_limit_read = 2

    assert client.get("/fixtures/live").status_code == 200
    assert client.get("/fixtures/live").status_code == 200
    third = client.get("/fixtures/live")

    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1


def test_expensive_routes_have_their_own_tighter_budget():
    settings.rate_limit_read = 100
    settings.rate_limit_expensive = 1

    assert client.get("/live/export", params={"days": 1}).status_code in (200, 404)
    assert client.get("/live/export", params={"days": 1}).status_code == 429


def test_clients_behind_the_proxy_get_separate_budgets():
    settings.rate_limit_read = 1

    assert (
        client.get(
            "/fixtures/live", headers={"X-Forwarded-For": "203.0.113.7"}
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/fixtures/live", headers={"X-Forwarded-For": "203.0.113.7"}
        ).status_code
        == 429
    )
    assert (
        client.get(
            "/fixtures/live", headers={"X-Forwarded-For": "198.51.100.4"}
        ).status_code
        == 200
    )


def test_health_is_never_throttled():
    settings.rate_limit_read = 0

    assert all(client.get("/health").status_code == 200 for _ in range(5))


# --- body cap and headers ---------------------------------------------------


def test_oversized_json_bodies_are_rejected_before_parsing():
    settings.max_body_bytes = 64

    response = client.post(
        "/fixtures/import-csv", content=b"x" * 200, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large"


def test_uploads_get_the_larger_cap():
    settings.max_body_bytes = 16

    response = client.post(
        "/fixtures/upload-csv",
        content=b"x" * 64,
        headers={"Content-Type": "text/csv"},
    )

    assert response.status_code != 413


def test_responses_carry_hardening_headers():
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_is_added_only_behind_https():
    plain = client.get("/health")
    secure = client.get("/health", headers={"X-Forwarded-Proto": "https"})

    assert "Strict-Transport-Security" not in plain.headers
    assert "Strict-Transport-Security" in secure.headers


# --- helpers ----------------------------------------------------------------


def test_proxy_identity_uses_the_rightmost_forwarded_address():
    assert security.client_identity({"x-forwarded-for": "1.1.1.1, 2.2.2.2"}, "9.9.9.9") == "2.2.2.2"
    assert security.client_identity({}, "127.0.0.1") == "127.0.0.1"
    assert security.client_identity({"x-forwarded-for": " , "}, "127.0.0.1") == "127.0.0.1"


def test_request_classes_match_their_cost():
    assert security.request_class("GET", "/combos/backtest") == "expensive"
    assert security.request_class("POST", "/fixtures/upload-csv") == "write"
    assert security.request_class("GET", "/fixtures") == "read"


def test_limiter_window_expires():
    bucket = security.RateLimiter(window_seconds=60)

    assert bucket.check("k", 1, now=1000.0) == (True, 0)
    allowed, retry = bucket.check("k", 1, now=1001.0)
    assert allowed is False and retry == 59
    assert bucket.check("k", 1, now=1061.0) == (True, 0)


def test_docs_are_public_in_development_only():
    assert security.is_public_path("/docs", "development") is True
    assert security.is_public_path("/docs", "production") is False
    assert security.is_public_path("/health", "production") is True
