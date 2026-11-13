"""Value tips beyond 1X2: totals, draw-no-bet and Asian handicap.

Every tip must carry a real price, a de-vigged market probability from the
same book and the bookmaker/capture time that price came from. A missing
price or a missing de-vig is not a tip."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (registers every table)
from app.database import Base
from app.models.fixture import Fixture
from app.betting.value_tips import value_tips

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
CAPTURED = "2026-11-13T11:00:00+00:00"


@pytest.fixture()
def mem_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, future=True)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _pin_lambdas(monkeypatch, home: float = 1.55, away: float = 1.20) -> None:
    """Give the model expected goals the market does not already imply.

    With no TeamRating rows the engine back-solves lambdas from the fixture's
    own 1X2, which makes the model and the de-vigged market agree by
    construction. Pinning lambdas keeps discovery, de-vig and value detection
    real while giving the model an independent view to compare against.
    """
    from app.betting import value_tips as tips_module
    from app.market_engine.market_analysis import analyse_fixture_markets as real

    def pinned(fixture, context=None, db=None):
        context = dict(context or {})
        context.setdefault("home_lambda", home)
        context.setdefault("away_lambda", away)
        context.setdefault("lambda_source", "test_pinned")
        return real(fixture, context=context, db=db)

    monkeypatch.setattr(tips_module, "analyse_fixture_markets", pinned)


def _fixture(**overrides) -> Fixture:
    defaults = dict(
        dedupe_key=overrides.get("dedupe_key", "tips-key"),
        kickoff_utc=NOW + timedelta(days=1),
        home_team="Alpha",
        away_team="Beta",
        competition="Premier League",
        country="England",
        source="test",
        home_odds=2.0,
        draw_odds=3.5,
        away_odds=4.0,
        odds_updated_at=NOW - timedelta(hours=1),
        market_odds_json=json.dumps({
            "over_2_5": {"price": 2.6, "bookmaker": "BetPawa", "captured_at": CAPTURED},
            "under_2_5": {"price": 1.5, "bookmaker": "BetPawa", "captured_at": CAPTURED},
        }),
        status="scheduled",
    )
    defaults.update(overrides)
    return Fixture(**defaults)


def test_a_tip_carries_its_bookmaker_and_capture_time(mem_db, monkeypatch):
    _pin_lambdas(monkeypatch)
    mem_db.add(_fixture())
    mem_db.commit()

    result = value_tips(mem_db, days_ahead=3, min_edge_percent=3.0, now=NOW)

    assert result["status"] == "ok"
    assert result["fixtures_scanned"] == 1
    assert result["fixtures_with_markets"] == 1
    assert len(result["tips"]) == 1
    tip = result["tips"][0]
    assert tip["family"] == "total_goals"
    assert tip["selection"] == "OVER"
    assert tip["line"] == 2.5
    assert tip["bookmaker"] == "BetPawa"
    assert tip["captured_at"] == CAPTURED
    assert tip["price"] == pytest.approx(2.6)
    assert tip["market_probability"] == pytest.approx(0.3659, abs=1e-3)
    assert 3.0 <= tip["edge_percent"] <= 20.0
    assert tip["ev_percent"] > 0


def test_only_the_requested_family_and_window_are_scanned(mem_db, monkeypatch):
    _pin_lambdas(monkeypatch)
    mem_db.add_all([
        _fixture(dedupe_key="tips-totals"),
        _fixture(
            dedupe_key="tips-dnb",
            home_team="Gamma",
            away_team="Delta",
            market_odds_json=json.dumps({
                "dnb_home": {"price": 2.1, "bookmaker": "Betika", "captured_at": CAPTURED},
                "dnb_away": {"price": 2.0, "bookmaker": "Betika", "captured_at": CAPTURED},
            }),
        ),
        _fixture(
            dedupe_key="tips-past",
            kickoff_utc=NOW - timedelta(days=1),
            market_odds_json=json.dumps({
                "over_2_5": {"price": 9.0, "bookmaker": "BetPawa", "captured_at": CAPTURED},
                "under_2_5": {"price": 1.1, "bookmaker": "BetPawa", "captured_at": CAPTURED},
            }),
        ),
        _fixture(dedupe_key="tips-live", status="live"),
    ])
    mem_db.commit()

    result = value_tips(mem_db, days_ahead=3, families=["draw_no_bet"], now=NOW)

    assert result["fixtures_scanned"] == 2
    assert [tip["family"] for tip in result["tips"]] == ["draw_no_bet"]
    assert result["tips"][0]["bookmaker"] == "Betika"


def test_a_missing_de_vig_is_not_a_tip(mem_db):
    mem_db.add(_fixture(market_odds_json=json.dumps({
        "over_2_5": {"price": 2.6, "bookmaker": "BetPawa", "captured_at": CAPTURED},
    })))
    mem_db.commit()

    result = value_tips(mem_db, days_ahead=3, now=NOW)

    assert result["status"] == "no_value"
    assert result["message"] == "NO QUALIFIED SELECTIONS FOUND."
    assert result["tips"] == []
    assert result["dropped"]["no_market_probability"] == 1


def test_a_gap_beyond_the_ceiling_is_treated_as_stale(mem_db):
    mem_db.add(_fixture(
        market_odds_json=json.dumps({
            "dnb_home": {"price": 2.4, "bookmaker": "Betika", "captured_at": CAPTURED},
            "dnb_away": {"price": 1.9, "bookmaker": "Betika", "captured_at": CAPTURED},
        }),
    ))
    mem_db.commit()

    result = value_tips(
        mem_db, days_ahead=3, families=["draw_no_bet"],
        min_edge_percent=1.0, max_edge_percent=2.0, now=NOW,
    )

    assert result["tips"] == []
    assert result["dropped"]["above_max_edge"] == 1


def test_an_unpriceable_family_is_refused(mem_db):
    with pytest.raises(ValueError):
        value_tips(mem_db, families=["european_handicap"], now=NOW)


def test_the_endpoint_serves_value_tips():
    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get("/markets/value-tips?days_ahead=1")

    assert response.status_code == 200
    body = response.json()
    assert {"status", "tips", "dropped", "families"} <= set(body)
    assert body["families"] == ["asian_handicap", "draw_no_bet", "total_goals"]
