"""Free provider adapters: quota, cache, retry, dedupe and verification."""
from __future__ import annotations

from datetime import date

import pytest
import requests

from app.data_sources import providers
from app.data_sources.providers import (
    ApiFootballProvider,
    ProviderConfig,
    ProviderError,
    QuotaExceeded,
    build_provider,
    cross_verify_fixtures,
    dedupe_fixtures,
    detect_missing_data,
    provider_catalogue,
)


class Harness(ApiFootballProvider):
    config = ProviderConfig(
        name="harness",
        base_url="https://example.test",
        key_env="HARNESS_KEY",
        daily_quota=100,
        requests_per_minute=10,
    )


@pytest.fixture()
def harness(tmp_path):
    calls = {"count": 0}
    payload = {
        "response": [
            {
                "fixture": {"id": 1, "date": "2026-11-12T14:00:00+00:00"},
                "league": {"name": "Uganda Premier League", "country": "Uganda"},
                "teams": {"home": {"name": "KCCA FC"}, "away": {"name": "Vipers SC"}},
            }
        ]
    }

    def transport(url, params, headers):
        calls["count"] += 1
        return 200, payload

    provider = Harness(
        Harness.config,
        cache_dir=tmp_path / "cache",
        state_path=tmp_path / "state.json",
        transport=transport,
        api_key="test-key",
    )
    provider.calls = calls
    return provider


def test_unconfigured_provider_reports_instead_of_calling(harness):
    provider = Harness(
        Harness.config,
        cache_dir=harness.cache_dir,
        state_path=harness.state_path,
        api_key="",
    )
    assert provider.status()["configured"] is False
    assert provider.status()["free_plan"] == ""
    with pytest.raises(ProviderError):
        provider.fetch_fixtures(date(2026, 11, 12))


def test_fetch_normalizes_and_caches(harness):
    target = date(2026, 11, 12)
    first = harness.fetch_fixtures(target)
    second = harness.fetch_fixtures(target)
    assert first.status == "ok"
    assert first.cached is False
    assert second.cached is True
    assert harness.calls["count"] == 1
    record = first.records[0]
    assert record["home_team"] == "KCCA FC"
    assert record["competition"] == "Uganda Premier League"
    assert record["external_fixture_id"] == "1"


def test_daily_quota_is_enforced(tmp_path):
    config = ProviderConfig(
        name="tiny",
        base_url="https://example.test",
        key_env="TINY_KEY",
        daily_quota=1,
    )
    provider = ApiFootballProvider(
        config,
        cache_dir=tmp_path / "cache",
        state_path=tmp_path / "state.json",
        transport=lambda url, params, headers: (200, {"response": []}),
        api_key="key",
    )
    provider.fetch_fixtures(date(2026, 11, 12))
    with pytest.raises(QuotaExceeded):
        provider.fetch_fixtures(date(2026, 11, 13))
    assert provider.status()["used_today"] == 1


def test_retry_recovers_without_sleeping(monkeypatch, tmp_path):
    monkeypatch.setattr(providers.time, "sleep", lambda _: None)
    attempts = {"count": 0}

    def flaky(url, params, headers):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise requests.ConnectionError("boom")
        return 200, {"response": []}

    provider = ApiFootballProvider(
        ApiFootballProvider.config,
        cache_dir=tmp_path / "cache",
        state_path=tmp_path / "state.json",
        transport=flaky,
        api_key="key",
    )
    result = provider.fetch_fixtures(date(2026, 11, 12))
    assert result.status == "empty"
    assert attempts["count"] == 3


def test_dedupe_and_cross_verification():
    records = [
        {"home_team": "KCCA FC", "away_team": "Vipers SC", "kickoff_utc": "2026-11-12T14:00:00Z",
         "competition": "UPL", "source": "api_football"},
        {"home_team": "Kcca", "away_team": "vipers", "kickoff_utc": "2026-11-12T14:00:00Z",
         "competition": "Uganda Premier League", "source": "football_data_org"},
        {"home_team": "Express", "away_team": "SC Villa", "kickoff_utc": "2026-11-12T16:00:00Z",
         "competition": "UPL", "source": "espn"},
    ]
    deduped = dedupe_fixtures(records)
    assert len(deduped) == 2
    verified = cross_verify_fixtures(records)
    assert len(verified["verified"]) == 1
    assert verified["verified"][0]["sources"] == ["api_football", "football_data_org"]
    assert len(verified["unverified"]) == 1


def test_missing_data_detection():
    report = detect_missing_data(
        [
            {"home_team": "A", "away_team": "B", "kickoff_utc": "2026-11-12T14:00:00Z", "competition": "C"},
            {"home_team": "A", "away_team": None, "competition": "C"},
        ]
    )
    assert len(report) == 1
    assert set(report[0]["missing"]) == {"away_team", "kickoff_utc"}


def test_provider_catalogue_lists_free_limits():
    catalogue = {row["name"]: row for row in provider_catalogue()}
    assert catalogue["api_football"]["daily_quota"] == 100
    assert catalogue["football_data_org"]["requests_per_minute"] == 10
    assert "500" in catalogue["odds_api"]["free_plan"]
    assert catalogue["espn"]["key_env"] == ""


def test_unknown_provider_is_rejected():
    with pytest.raises(ProviderError):
        build_provider("does_not_exist")
