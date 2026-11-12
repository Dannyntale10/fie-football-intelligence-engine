"""Free data-provider adapters with quota, cache, retry and dedupe.

No paid API is required. Every adapter declares its key environment variable
and free-plan limits; an unconfigured provider reports ``configured: false``
instead of pretending to have data. Responses are cached, retried with
backoff, and counted against a persisted quota so a free plan is not exhausted
by accident.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
STATE_PATH = DATA_DIR / "provider_state.json"


class ProviderError(RuntimeError):
    """A provider returned an error or could not be reached."""


class QuotaExceeded(ProviderError):
    """The provider's free-plan quota for the day or minute is exhausted."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    key_env: str
    daily_quota: Optional[int] = None
    requests_per_minute: Optional[int] = None
    cache_ttl_seconds: int = 900
    timeout: int = 20
    free_plan: str = ""
    docs_url: str = ""


@dataclass
class ProviderResult:
    provider: str
    records: List[Dict[str, Any]]
    fetched_at: str
    cached: bool
    status: str
    message: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "records": self.records,
            "count": len(self.records),
            "fetched_at": self.fetched_at,
            "cached": self.cached,
            "status": self.status,
            "message": self.message,
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _utcnow().date().isoformat()


def _minute() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M")


def normalize_team_name(name: Any) -> str:
    text = str(name or "").lower()
    for token in (" fc", " afc", " sc", " cf", " united", " utd"):
        text = text.replace(token, "")
    cleaned = "".join(char if char.isalnum() else " " for char in text)
    return " ".join(cleaned.split())


class BaseProvider:
    config: ProviderConfig

    def __init__(
        self,
        config: ProviderConfig,
        cache_dir: Path = CACHE_DIR,
        state_path: Path = STATE_PATH,
        transport: Optional[Callable[[str, Dict[str, Any], Dict[str, str]], Tuple[int, Any]]] = None,
        api_key: Optional[str] = None,
    ):
        self.config = config
        self.cache_dir = Path(cache_dir) / config.name
        self.state_path = Path(state_path)
        self.transport = transport or self._http_get
        self.api_key = api_key if api_key is not None else os.getenv(config.key_env, "")

    # ── configuration and quota ──────────────────────────────────────────
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> Dict[str, Any]:
        state = self._load_state()
        provider_state = state.get("providers", {}).get(self.config.name, {})
        used = provider_state.get("daily", {}).get(_today(), 0)
        return {
            "provider": self.config.name,
            "configured": self.configured,
            "key_env": self.config.key_env,
            "free_plan": self.config.free_plan,
            "daily_quota": self.config.daily_quota,
            "used_today": used,
            "requests_per_minute": self.config.requests_per_minute,
            "docs_url": self.config.docs_url,
        }

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state))
        os.replace(temporary, self.state_path)

    def _enforce_quota(self, state: Dict[str, Any]) -> None:
        providers = state.setdefault("providers", {}).setdefault(self.config.name, {})
        daily = providers.setdefault("daily", {})
        used = int(daily.get(_today(), 0))
        if self.config.daily_quota and used >= self.config.daily_quota:
            raise QuotaExceeded(
                f"{self.config.name} daily quota of {self.config.daily_quota} requests is exhausted"
            )

        minute_count = 0
        minute_key = _minute()
        if providers.get("minute") == minute_key:
            minute_count = int(providers.get("minute_count", 0))
        if self.config.requests_per_minute and minute_count >= self.config.requests_per_minute:
            raise QuotaExceeded(
                f"{self.config.name} limit of {self.config.requests_per_minute} requests/minute reached"
            )

        daily[_today()] = used + 1
        providers["minute"] = minute_key
        providers["minute_count"] = minute_count + 1
        self._save_state(state)

    # ── transport, cache and retry ───────────────────────────────────────
    def _http_get(self, url: str, params: Dict[str, Any], headers: Dict[str, str]) -> Tuple[int, Any]:
        response = requests.get(url, params=params, headers=headers, timeout=self.config.timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:500]}
        return response.status_code, payload

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str) -> Optional[Any]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.config.cache_ttl_seconds:
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return None

    def _write_cache(self, key: str, payload: Any) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(key).write_text(json.dumps(payload))

    def request_json(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        attempts: int = 3,
    ) -> Tuple[Any, bool]:
        """Return ``(payload, cached)``; raises ProviderError on failure."""
        if not self.configured and self.config.key_env:
            raise ProviderError(f"{self.config.name} is not configured (missing {self.config.key_env})")

        params = dict(params or {})
        headers = dict(headers or {})
        url = f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"
        cache_key = hashlib.sha1(
            json.dumps([url, params], sort_keys=True).encode()
        ).hexdigest()
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached, True

        state = self._load_state()
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                self._enforce_quota(state)
                status_code, payload = self.transport(url, params, headers)
                if status_code == 429:
                    raise ProviderError("rate limited by provider (HTTP 429)")
                if status_code >= 400:
                    raise ProviderError(f"provider returned HTTP {status_code}")
                self._write_cache(cache_key, payload)
                return payload, False
            except QuotaExceeded:
                raise
            except ProviderError as error:
                last_error = error
            except requests.RequestException as error:
                last_error = error
            if attempt < attempts - 1:
                time.sleep(min(4.0, 0.5 * (2 ** attempt)))
        raise ProviderError(str(last_error) if last_error else "provider request failed")

    def fetch_fixtures(self, target_date: date, league: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError

    def _result(
        self,
        records: List[Dict[str, Any]],
        cached: bool,
        message: Optional[str] = None,
    ) -> ProviderResult:
        return ProviderResult(
            provider=self.config.name,
            records=records,
            fetched_at=_utcnow().isoformat(),
            cached=cached,
            status="ok" if records else "empty",
            message=message,
        )


class ApiFootballProvider(BaseProvider):
    """API-Football (api-sports.io). Free plan: 100 requests/day."""

    config = ProviderConfig(
        name="api_football",
        base_url="https://v3.football.api-sports.io",
        key_env="API_FOOTBALL_KEY",
        daily_quota=100,
        requests_per_minute=10,
        free_plan="100 requests/day",
        docs_url="https://www.api-football.com/documentation-v3",
    )

    def fetch_fixtures(self, target_date: date, league: Optional[str] = None) -> ProviderResult:
        params: Dict[str, Any] = {"date": target_date.isoformat(), "timezone": "Africa/Kampala"}
        if league:
            params["league"] = league
        payload, cached = self.request_json(
            "fixtures",
            params,
            headers={"x-apisports-key": self.api_key},
        )
        records: List[Dict[str, Any]] = []
        for item in (payload or {}).get("response", []) or []:
            fixture = item.get("fixture") or {}
            teams = item.get("teams") or {}
            league_info = item.get("league") or {}
            records.append(
                {
                    "external_fixture_id": str(fixture.get("id")),
                    "kickoff_utc": fixture.get("date"),
                    "home_team": (teams.get("home") or {}).get("name"),
                    "away_team": (teams.get("away") or {}).get("name"),
                    "competition": league_info.get("name"),
                    "country": league_info.get("country"),
                    "status": "scheduled",
                    "source": self.config.name,
                }
            )
        return self._result(records, cached)


class FootballDataOrgProvider(BaseProvider):
    """football-data.org v4. Free tier: 10 requests/minute."""

    config = ProviderConfig(
        name="football_data_org",
        base_url="https://api.football-data.org/v4",
        key_env="FOOTBALL_DATA_ORG_KEY",
        requests_per_minute=10,
        cache_ttl_seconds=1800,
        free_plan="10 requests/minute (free tier)",
        docs_url="https://www.football-data.org/documentation/quickstart",
    )

    def fetch_fixtures(self, target_date: date, league: Optional[str] = None) -> ProviderResult:
        payload, cached = self.request_json(
            "matches",
            {"dateFrom": target_date.isoformat(), "dateTo": target_date.isoformat()},
            headers={"X-Auth-Token": self.api_key},
        )
        records = []
        for item in (payload or {}).get("matches", []) or []:
            competition = item.get("competition") or {}
            area = item.get("area") or {}
            records.append(
                {
                    "external_fixture_id": str(item.get("id")),
                    "kickoff_utc": item.get("utcDate"),
                    "home_team": (item.get("homeTeam") or {}).get("name"),
                    "away_team": (item.get("awayTeam") or {}).get("name"),
                    "competition": competition.get("name"),
                    "country": area.get("name"),
                    "status": str(item.get("status", "SCHEDULED")).lower(),
                    "source": self.config.name,
                }
            )
        return self._result(records, cached)


class EspnProvider(BaseProvider):
    """ESPN's public scoreboard endpoint. No key; undocumented quota."""

    config = ProviderConfig(
        name="espn",
        base_url="https://site.api.espn.com/apis/site/v2/sports/soccer",
        key_env="",
        cache_ttl_seconds=900,
        free_plan="no key, no published quota; use conservatively",
        docs_url="https://github.com/pseudo-r/Public-ESPN-API",
    )

    LEAGUES = ("eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "uefa.champions")

    def fetch_fixtures(self, target_date: date, league: Optional[str] = None) -> ProviderResult:
        leagues = [league] if league else list(self.LEAGUES)
        records: List[Dict[str, Any]] = []
        cached_any = True
        for slug in leagues:
            payload, cached = self.request_json(
                f"{slug}/scoreboard",
                {"dates": target_date.strftime("%Y%m%d")},
            )
            cached_any = cached_any and cached
            for event in (payload or {}).get("events", []) or []:
                competition = (event.get("competitions") or [{}])[0]
                competitors = competition.get("competitors") or []
                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})
                records.append(
                    {
                        "external_fixture_id": str(event.get("id")),
                        "kickoff_utc": event.get("date"),
                        "home_team": (home.get("team") or {}).get("displayName"),
                        "away_team": (away.get("team") or {}).get("displayName"),
                        "competition": slug,
                        "country": None,
                        "status": "scheduled",
                        "source": self.config.name,
                    }
                )
        return self._result(records, cached_any)

    @property
    def configured(self) -> bool:
        return True


class ApifootballComProvider(BaseProvider):
    """APIFootball.com. Free plan limits vary; verify before relying on it."""

    config = ProviderConfig(
        name="apifootball_com",
        base_url="https://apiv3.apifootball.com",
        key_env="APIFOOTBALL_COM_KEY",
        cache_ttl_seconds=1800,
        free_plan="key required; free-plan limits vary",
        docs_url="https://apifootball.com/documentation/",
    )

    def fetch_fixtures(self, target_date: date, league: Optional[str] = None) -> ProviderResult:
        payload, cached = self.request_json(
            "/",
            {
                "action": "get_events",
                "from": target_date.isoformat(),
                "to": target_date.isoformat(),
                "APIkey": self.api_key,
            },
        )
        records = []
        for item in payload if isinstance(payload, list) else []:
            records.append(
                {
                    "external_fixture_id": str(item.get("match_id")),
                    "kickoff_utc": f"{item.get('match_date', '')} {item.get('match_time', '')}".strip(),
                    "home_team": item.get("match_home_team"),
                    "away_team": item.get("match_away_team"),
                    "competition": item.get("league_name"),
                    "country": item.get("country_name"),
                    "status": str(item.get("match_status", "scheduled")).lower(),
                    "source": self.config.name,
                }
            )
        return self._result(records, cached)


class OddsApiProvider(BaseProvider):
    """The Odds API. Free plan: 500 credits/month."""

    config = ProviderConfig(
        name="odds_api",
        base_url="https://api.the-odds-api.com/v4",
        key_env="ODDS_API_KEY",
        daily_quota=None,
        cache_ttl_seconds=1800,
        free_plan="500 credits/month",
        docs_url="https://the-odds-api.com/",
    )

    def fetch_odds(self, sport: str = "soccer", target_date: Optional[date] = None) -> ProviderResult:
        payload, cached = self.request_json(
            f"sports/{sport}/odds",
            {"apiKey": self.api_key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"},
        )
        captured_at = _utcnow().isoformat()
        records = []
        for event in payload if isinstance(payload, list) else []:
            if target_date and str(event.get("commence_time", ""))[:10] != target_date.isoformat():
                continue
            for bookmaker in event.get("bookmakers") or []:
                for market in bookmaker.get("markets") or []:
                    for outcome in market.get("outcomes") or []:
                        price = outcome.get("price")
                        if not isinstance(price, (int, float)) or price <= 1.0:
                            continue
                        records.append(
                            {
                                "fixture_key": f"{normalize_team_name(event.get('home_team'))}|"
                                               f"{normalize_team_name(event.get('away_team'))}",
                                "home_team": event.get("home_team"),
                                "away_team": event.get("away_team"),
                                "kickoff_utc": event.get("commence_time"),
                                "market": "match_result" if market.get("key") == "h2h" else market.get("key"),
                                "selection": outcome.get("name"),
                                "line": outcome.get("point"),
                                "price": float(price),
                                "bookmaker": bookmaker.get("title"),
                                "captured_at": bookmaker.get("last_update") or captured_at,
                                "source": self.config.name,
                            }
                        )
        return self._result(records, cached)


class ManualOddsProvider(BaseProvider):
    """CSV odds import: bookmaker,home_team,away_team,kickoff_utc,market,selection,line,price."""

    config = ProviderConfig(
        name="manual_csv",
        base_url="",
        key_env="",
        free_plan="operator-supplied file; no external calls",
        docs_url="",
    )

    def __init__(self, csv_path: Path, **kwargs: Any):
        super().__init__(self.config, **kwargs)
        self.csv_path = Path(csv_path)

    @property
    def configured(self) -> bool:
        return self.csv_path.exists()

    def fetch_odds(self) -> ProviderResult:
        if not self.configured:
            raise ProviderError(f"manual odds file not found: {self.csv_path}")
        with self.csv_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        records = []
        for row in rows:
            try:
                price = float(row.get("price") or 0)
            except ValueError:
                continue
            if price <= 1.0:
                continue
            records.append(
                {
                    "fixture_key": f"{normalize_team_name(row.get('home_team'))}|"
                                   f"{normalize_team_name(row.get('away_team'))}",
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "kickoff_utc": row.get("kickoff_utc"),
                    "market": row.get("market"),
                    "selection": row.get("selection"),
                    "line": row.get("line") or None,
                    "price": price,
                    "bookmaker": row.get("bookmaker"),
                    "captured_at": row.get("captured_at") or _utcnow().isoformat(),
                    "source": "manual_csv",
                }
            )
        return self._result(records, False)


def provider_catalogue() -> List[Dict[str, Any]]:
    return [
        {
            "name": cls.config.name,
            "key_env": cls.config.key_env,
            "free_plan": cls.config.free_plan,
            "daily_quota": cls.config.daily_quota,
            "requests_per_minute": cls.config.requests_per_minute,
            "docs_url": cls.config.docs_url,
        }
        for cls in (
            ApiFootballProvider,
            FootballDataOrgProvider,
            EspnProvider,
            ApifootballComProvider,
            OddsApiProvider,
            ManualOddsProvider,
        )
    ]


def is_configured(entry: Dict[str, Any]) -> bool:
    """Whether a catalogue entry can talk to its source right now.

    ESPN needs no key. The manual CSV adapter stays inactive here because the
    file path is supplied by a caller, not by the environment.
    """
    key_env = entry.get("key_env") or ""
    if key_env:
        return bool(os.getenv(key_env))
    return entry.get("name") != "manual_csv"


def build_provider(name: str, **kwargs: Any) -> BaseProvider:
    providers = {
        "api_football": ApiFootballProvider,
        "football_data_org": FootballDataOrgProvider,
        "espn": EspnProvider,
        "apifootball_com": ApifootballComProvider,
        "odds_api": OddsApiProvider,
    }
    if name == "manual_csv":
        return ManualOddsProvider(Path(kwargs.pop("csv_path")), **kwargs)
    if name not in providers:
        raise ProviderError(f"unknown provider: {name}")
    return providers[name](**kwargs)


# ── dedupe, cross-verification and missing-data checks ───────────────────
def fixture_key(record: Dict[str, Any]) -> str:
    kickoff = str(record.get("kickoff_utc") or "")[:10]
    return "|".join(
        (
            kickoff,
            normalize_team_name(record.get("home_team")),
            normalize_team_name(record.get("away_team")),
        )
    )


def dedupe_fixtures(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for record in records:
        key = fixture_key(record)
        if key not in unique:
            unique[key] = {**record, "sources": [record.get("source")]}
            continue
        merged = unique[key]
        source = record.get("source")
        if source and source not in merged["sources"]:
            merged["sources"].append(source)
    return list(unique.values())


def cross_verify_fixtures(
    records: Iterable[Dict[str, Any]],
    minimum_sources: int = 2,
) -> Dict[str, List[Dict[str, Any]]]:
    deduped = dedupe_fixtures(records)
    verified = [row for row in deduped if len(row["sources"]) >= minimum_sources]
    unverified = [row for row in deduped if len(row["sources"]) < minimum_sources]
    return {"verified": verified, "unverified": unverified}


REQUIRED_FIXTURE_FIELDS = ("home_team", "away_team", "kickoff_utc", "competition")


def detect_missing_data(fixtures: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    report = []
    for fixture in fixtures:
        missing = [field for field in REQUIRED_FIXTURE_FIELDS if not fixture.get(field)]
        if missing:
            report.append({"fixture": fixture_key(fixture), "missing": missing})
    return report
