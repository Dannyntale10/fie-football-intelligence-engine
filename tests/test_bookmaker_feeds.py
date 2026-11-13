"""Public bookmaker feeds: parsing, best-price merge, insert gating and sync logs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.data_sources import betika_feed as bk
from app.data_sources import bookmaker_sync as sync
from app.data_sources import fansport_feed as fp
from app.data_sources import sportpesa_feed as sp
from app.data_sources.bookmaker_common import (
    BookmakerEvent,
    apply_bookmaker_events,
    competition_label,
    merge_market_prices,
)
from app.data_sources.providers import (
    build_provider,
    is_configured,
    provider_catalogue,
)
from app.database import Base
from app.models.data import DataProvider, ProviderSyncLog
from app.models.fixture import Fixture


def _kickoff(days=2):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )


def _event(source, bookmaker, home, away, competition, country, kickoff, odds):
    return BookmakerEvent(
        source=source,
        bookmaker=bookmaker,
        source_url="https://example.test/feed",
        event_id=f"{source}-{home}",
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition=competition,
        country=country,
        known_competition=competition_label(country, competition),
        market_odds=odds,
    )


def _fixture(home, away, competition, kickoff, source="ESPN_DraftKings"):
    return Fixture(
        dedupe_key=f"{home}-{away}-{kickoff.date()}",
        kickoff_utc=kickoff,
        home_team=home,
        away_team=away,
        competition=competition,
        country="England",
        source=source,
        status="scheduled",
    )


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ── SportPesa ────────────────────────────────────────────────────────────
def _sp_market(market_id, selections, spec=None):
    return {
        "id": market_id,
        "specValue": spec,
        "selections": [{"shortName": name, "odds": price} for name, price in selections],
    }


def _sp_game():
    return {
        "id": 501,
        "date": "2026-11-15T17:00:00+00:00",
        "competitors": [{"name": "Arsenal"}, {"name": "Chelsea"}],
        "competition": {"name": "Premier League"},
        "country": {"name": "England"},
    }


def test_sportpesa_parses_only_model_markets():
    markets = [
        _sp_market(10, [("1", 2.10), ("X", 3.40), ("2", 3.30)]),
        _sp_market(46, [("1X", 1.30), ("12", 1.28), ("X2", 1.62)]),
        _sp_market(43, [("YES", 1.90), ("NO", 1.85)]),
        _sp_market(52, [("OV", 2.05), ("UN", 1.70)], spec="2.5"),
        # Whole-goal total can push, so it must not be stored.
        _sp_market(52, [("OV", 1.80), ("UN", 2.00)], spec="3"),
        _sp_market(47, [("1", 1.70), ("2", 2.15)]),
        # Negative spec = home gives the handicap, the direction the model prices.
        _sp_market(51, [("1", 4.20), ("2", 1.20)], spec="-1.5"),
        # Home receiving is a different market and is skipped.
        _sp_market(51, [("1", 1.20), ("2", 4.20)], spec="1.5"),
    ]

    event = sp.parse_game(_sp_game(), markets)

    assert event is not None
    assert event.source == "SPORTPESA"
    assert event.known_competition == "Premier League"
    assert event.home_team == "Arsenal" and event.away_team == "Chelsea"
    assert event.source_url.endswith("games=501&markets=all")
    odds = event.market_odds
    assert (odds["home_win"], odds["draw"], odds["away_win"]) == (2.10, 3.40, 3.30)
    assert odds["double_chance_1x"] == 1.30
    assert odds["btts_no"] == 1.85
    assert odds["over_2_5"] == 2.05 and odds["under_2_5"] == 1.70
    assert odds["dnb_home"] == 1.70
    assert odds["ah_home_minus_1_5"] == 4.20
    assert odds["ah_away_plus_1_5"] == 1.20
    assert "over_3_0" not in odds and "over_3" not in odds


def test_sportpesa_requires_all_three_match_winner_prices():
    markets = [_sp_market(10, [("1", 2.10), ("X", 3.40)])]
    assert sp.parse_game(_sp_game(), markets) is None


# ── FANSPORT ─────────────────────────────────────────────────────────────
def _fp_entry(type_id, price, parameter=None):
    return {"type": type_id, "cf": price, "parameter": parameter}


def _fp_group(group_id, entries):
    return {"groupId": group_id, "events": [entries]}


def _fp_game():
    return {
        "id": 9001,
        "constId": "const-9001",
        "startTs": int(_kickoff().timestamp()),
        "opponent1": {"fullName": "Liverpool"},
        "opponent2": {"fullName": "Everton"},
        "liga": {"name": "Premier League", "countryName": "England"},
        "eventGroups": [
            _fp_group(1, [_fp_entry(1, 2.00), _fp_entry(2, 3.50), _fp_entry(3, 3.80)]),
            _fp_group(8, [_fp_entry(4, 1.25), _fp_entry(5, 1.35), _fp_entry(6, 1.55)]),
            _fp_group(19, [_fp_entry(180, 1.80), _fp_entry(181, 1.95)]),
            _fp_group(
                17,
                [
                    _fp_entry(9, 2.10, 2.5),
                    _fp_entry(10, 1.75, 2.5),
                    # A 4.0 line is a whole-goal line and can push.
                    _fp_entry(9, 1.30, 4),
                ],
            ),
            _fp_group(
                2,
                [
                    _fp_entry(7, 1.70, None),
                    _fp_entry(8, 2.20, None),
                    _fp_entry(7, 5.00, -1.5),
                    _fp_entry(8, 1.15, 1.5),
                ],
            ),
        ],
    }


def test_fansport_maps_groups_to_model_markets():
    # The list payload only carries eventGroups for the enriched games; start
    # from the full event feed to prove the same parser handles both.
    event = fp.parse_game(_fp_game())

    assert event is not None
    assert event.source == "FANSPORT"
    assert event.event_id == "const-9001"
    assert event.known_competition == "Premier League"
    assert event.source_url.endswith("gameId=9001&marketType=1")
    odds = event.market_odds
    assert (odds["home_win"], odds["draw"], odds["away_win"]) == (2.00, 3.50, 3.80)
    assert odds["double_chance_1x"] == 1.25
    assert odds["double_chance_12"] == 1.35
    assert odds["double_chance_x2"] == 1.55
    assert odds["btts_yes"] == 1.80 and odds["btts_no"] == 1.95
    assert odds["over_2_5"] == 2.10 and odds["under_2_5"] == 1.75
    assert odds["dnb_home"] == 1.70 and odds["dnb_away"] == 2.20
    assert odds["ah_home_minus_1_5"] == 5.00
    assert odds["ah_away_plus_1_5"] == 1.15
    assert "over_4_0" not in odds and "over_4" not in odds


def test_fansport_list_payload_without_markets_is_not_invented():
    game = _fp_game()
    game.pop("eventGroups")
    assert fp.parse_game(game) is None


# ── best-price merge and provenance ──────────────────────────────────────
def test_merge_keeps_best_price_and_winning_bookmaker():
    captured = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    payload, changed = merge_market_prices(
        None,
        {"home_win": 2.10, "draw": 3.40},
        bookmaker="SportPesa",
        source="SPORTPESA",
        source_url="https://www.sportpesa.com/api/games/markets?games=1",
        captured_at=captured,
    )
    assert changed is True

    # A lower price must not replace the stored one or its provenance.
    payload, changed = merge_market_prices(
        payload,
        {"home_win": 2.00},
        bookmaker="BetPawa",
        source="BETPAWA",
        source_url="https://www.betpawa.ug/",
        captured_at=captured,
    )
    assert changed is False
    assert payload["home_win"]["price"] == 2.10
    assert payload["home_win"]["bookmaker"] == "SportPesa"

    # A better price takes over and brings its own attribution.
    payload, changed = merge_market_prices(
        payload,
        {"home_win": 2.25},
        bookmaker="BetPawa",
        source="BETPAWA",
        source_url="https://www.betpawa.ug/",
        captured_at=captured,
    )
    assert changed is True
    assert payload["home_win"]["price"] == 2.25
    assert payload["home_win"]["bookmaker"] == "BetPawa"
    assert payload["home_win"]["source_url"] == "https://www.betpawa.ug/"


def test_merge_follows_a_books_own_lower_requote():
    first = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)
    later = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
    payload, _ = merge_market_prices(
        None,
        {"home_win": 2.75},
        bookmaker="Betika",
        source="BETIKA",
        source_url="https://www.betika.com/en-ug/",
        captured_at=first,
    )
    payload, changed = merge_market_prices(
        payload,
        {"home_win": 1.70},
        bookmaker="Betika",
        source="BETIKA",
        source_url="https://www.betika.com/en-ug/",
        captured_at=later,
    )

    # The old 2.75 is no longer on offer; holding on to it would manufacture
    # value on every later comparison against the current fair price.
    assert changed is True
    assert payload["home_win"]["price"] == 1.70
    assert payload["home_win"]["captured_at"] == later.isoformat()


def test_merge_replaces_legacy_flat_price_with_attributed_one():
    captured = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    legacy = {"home_win": 2.50, "over_2_5": 2.00}
    payload, changed = merge_market_prices(
        legacy,
        {"home_win": 2.10},
        bookmaker="BetPawa",
        source="BETPAWA",
        source_url="https://www.betpawa.ug/",
        captured_at=captured,
    )

    assert changed is True
    # The old number had no verified bookmaker, so it cannot outrank a fresh one.
    assert payload["home_win"]["price"] == 2.10
    assert payload["home_win"]["bookmaker"] == "BetPawa"
    # Keys the feed did not quote stay as they were.
    assert payload["over_2_5"] == 2.00


def test_apply_updates_matched_fixture_and_skips_untracked_competition(db):
    kickoff = _kickoff()
    existing = _fixture("Liverpool", "Everton", "Premier League", kickoff)
    db.add(existing)
    db.commit()

    captured = datetime.now(timezone.utc)
    feed = [
        _event(
            "SPORTPESA", "SportPesa", "Liverpool", "Everton", "Premier League", "England",
            kickoff, {"home_win": 2.10, "draw": 3.40, "away_win": 3.30},
        ),
        # Not tracked yet, so it must not add a new competition to the slate.
        _event(
            "SPORTPESA", "SportPesa", "Some Club", "Other Club", "Club Friendlies", "England",
            _kickoff(3), {"home_win": 1.50, "draw": 4.00, "away_win": 5.00},
        ),
    ]
    for event in feed:
        event.captured_at = captured
    stats = apply_bookmaker_events(db, feed)

    assert stats["matched_existing"] == 1
    assert stats["inserted_new"] == 0
    assert stats["skipped_unknown_competition"] == 1

    db.refresh(existing)
    assert (existing.home_odds, existing.draw_odds, existing.away_odds) == (2.10, 3.40, 3.30)
    assert existing.odds_updated_at is not None
    payload = json.loads(existing.market_odds_json)
    assert payload["home_win"]["bookmaker"] == "SportPesa"
    assert payload["home_win"]["captured_at"] == captured.isoformat()

    # A second feed with worse prices must not overwrite them.
    worse = _event(
        "BETPAWA", "BetPawa", "Liverpool", "Everton", "Premier League", "England",
        kickoff, {"home_win": 2.00, "draw": 3.20, "away_win": 3.10},
    )
    apply_bookmaker_events(db, [worse])
    db.refresh(existing)
    assert existing.home_odds == 2.10
    assert json.loads(existing.market_odds_json)["home_win"]["bookmaker"] == "SportPesa"


# ── sync orchestration ───────────────────────────────────────────────────
@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sync.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def test_sync_feed_records_provider_and_log(monkeypatch, session_factory):
    monkeypatch.setattr(
        sync,
        "_ingest",
        lambda name, db, max_events: {
            "fetched_events": 4,
            "matched_existing": 2,
            "odds_updated": 3,
            "inserted_new": 2,
            "requests": 1,
        },
    )
    feed = {"name": "betpawa", "label": "BetPawa", "base_url": "https://www.betpawa.ug", "notes": "x"}

    result = sync.sync_feed(feed, session_factory=session_factory)

    assert result["status"] == "success"
    db = session_factory()
    try:
        provider = db.query(DataProvider).filter_by(name="betpawa").one()
        assert provider.kind == "odds"
        assert provider.status == "success"
        assert provider.last_sync_at is not None
        log = db.query(ProviderSyncLog).filter_by(provider_id=provider.id).one()
        assert log.status == "success"
        assert log.records_written == 5
        assert log.request_count == 1
    finally:
        db.close()


def test_sync_feed_marks_failure_without_blocking_others(monkeypatch, session_factory):
    monkeypatch.setattr(
        sync,
        "_ingest",
        lambda name, db, max_events: (_ for _ in ()).throw(RuntimeError("feed down")),
    )
    feed = {"name": "sportpesa", "label": "SportPesa", "base_url": "https://www.sportpesa.com", "notes": "x"}

    result = sync.sync_feed(feed, session_factory=session_factory)

    assert result["status"] == "failed"
    assert "feed down" in result["error"]
    db = session_factory()
    try:
        provider = db.query(DataProvider).filter_by(name="sportpesa").one()
        assert provider.status == "failed"
        log = db.query(ProviderSyncLog).filter_by(provider_id=provider.id).one()
        assert log.status == "failed"
        assert "feed down" in log.message
    finally:
        db.close()


def test_sync_feed_failure_detail_is_compact_but_log_keeps_the_error():
    long_url = (
        "HTTPSConnectionPool(host='fsportug.icu', port=443): Max retries exceeded with url: "
        "/service-api/main-line-feed/v3/games1x2?cfView=3&count=40 (Caused by "
        "NameResolutionError(\"Failed to resolve 'fsportug.icu'\"))"
    )
    reason = sync._short_reason(Exception(long_url))

    assert reason == "DNS lookup failed; the feed host did not resolve"
    assert len(sync._short_reason(Exception("feed refused: maintenance"))) < 200
    assert "maintenance" in sync._short_reason(Exception("feed refused: maintenance"))


# ── Betika ────────────────────────────────────────────────────────────────
def _bk_match(**overrides):
    match = {
        "parent_match_id": "76162994",
        "start_time": "2026-11-14 21:00:00",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "competition_name": "Premier League",
        "category": "England",
        "odds": [
            {
                "sub_type_id": "1",
                "name": "1X2",
                "odds": [
                    {"display": "1", "odd_value": "2.10"},
                    {"display": "X", "odd_value": "3.40"},
                    {"display": "2", "odd_value": "3.30"},
                ],
            }
        ],
    }
    match.update(overrides)
    return match


def test_betika_parses_1x2_and_converts_eat_kickoff_to_utc():
    event = bk.parse_match(_bk_match())

    assert event is not None
    assert event["event_id"] == "76162994"
    # 21:00 EAT is 18:00 UTC.
    assert event["kickoff_utc"] == datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc)
    assert event["known_competition"] == "Premier League"
    assert event["market_odds"] == {"home_win": 2.10, "draw": 3.40, "away_win": 3.30}


def test_betika_skips_matches_without_complete_1x2():
    match = _bk_match()
    match["odds"][0]["odds"] = match["odds"][0]["odds"][:2]

    assert bk.parse_match(match) is None


def test_betika_detail_maps_supported_markets_only():
    payload = {
        "data": [
            {
                "sub_type_id": "1",
                "odds": [
                    {"display": "1", "odd_value": "2.10"},
                    {"display": "X", "odd_value": "3.40"},
                    {"display": "2", "odd_value": "3.30"},
                ],
            },
            {
                "sub_type_id": "10",
                "odds": [
                    {"display": "1/X", "odd_value": "1.30"},
                    {"display": "X/2", "odd_value": "1.60"},
                    {"display": "1/2", "odd_value": "1.28"},
                ],
            },
            {
                "sub_type_id": "29",
                "odds": [
                    {"display": "YES", "odd_value": "1.90"},
                    {"display": "NO", "odd_value": "1.85"},
                ],
            },
            {
                "sub_type_id": "18",
                "odds": [
                    {"display": "OVER 2.5", "odd_value": "2.05", "special_bet_value": "total=2.5"},
                    {"display": "UNDER 2.5", "odd_value": "1.70", "special_bet_value": "total=2.5"},
                    # Whole-goal line can push and must be dropped.
                    {"display": "OVER 3", "odd_value": "1.80", "special_bet_value": "total=3"},
                ],
            },
            {
                "sub_type_id": "11",
                "odds": [
                    {"display": "1", "odd_value": "1.70"},
                    {"display": "2", "odd_value": "2.15"},
                ],
            },
            {
                # European handicap is not the model's Asian handicap.
                "sub_type_id": "14",
                "odds": [{"display": "2 (1:0)", "odd_value": "1.33",
                          "special_bet_value": "hcp=1:0"}],
            },
        ]
    }

    odds = bk._detail_markets(payload)

    assert odds["double_chance_1x"] == 1.30
    assert odds["btts_no"] == 1.85
    assert odds["over_2_5"] == 2.05
    assert odds["under_2_5"] == 1.70
    assert "over_3" not in odds
    assert odds["dnb_home"] == 1.70
    assert not any(key.startswith("ah_") for key in odds)


# ── catalogue ────────────────────────────────────────────────────────────
def test_public_feeds_are_catalogue_entries_that_need_no_key():
    catalogue = {row["name"]: row for row in provider_catalogue()}
    for name in ("betpawa", "sportpesa", "fansport", "betika"):
        entry = catalogue[name]
        assert entry["key_env"] == ""
        assert "public" in entry["free_plan"].lower()
        assert is_configured(entry) is True
        assert build_provider(name).configured is True

    # A keyed provider without its key stays honestly unconfigured.
    assert is_configured(catalogue["odds_api"]) is False
