"""A de-vig needs one bookmaker's complete 1X2, not the best price per outcome."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.analytics.market_reference import resolve
from app.analytics.probabilities import analyze_fixture
from app.data_sources.bookmaker_common import BookmakerEvent, apply_bookmaker_events
from app.database import Base
from app.models.fixture import Fixture
from app.models.market_quote import MarketQuote

NOW = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
# Ingestion only considers fixtures that have not kicked off, so the test
# fixture has to sit in the real future, not just after a frozen NOW.
KICKOFF = datetime.now(timezone.utc) + timedelta(hours=11)


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


def _fixture(db, *, home=2.75, draw=3.35, away=6.65, kickoff=None):
    fixture = Fixture(
        dedupe_key=f"andorra-gibraltar-{kickoff or KICKOFF}",
        kickoff_utc=kickoff or KICKOFF,
        home_team="Andorra",
        away_team="Gibraltar",
        competition="Premier League",
        country="England",
        source="BETPAWA",
        status="scheduled",
        home_odds=home,
        draw_odds=draw,
        away_odds=away,
        max_home_odds=home,
        max_draw_odds=draw,
        max_away_odds=away,
        market_odds_json=json.dumps(
            {
                "home_win": {"price": home, "bookmaker": "Betika", "captured_at": NOW.isoformat()},
                "draw": {"price": draw, "bookmaker": "Betika", "captured_at": NOW.isoformat()},
                "away_win": {"price": away, "bookmaker": "FANSPORT", "captured_at": NOW.isoformat()},
            }
        ),
    )
    db.add(fixture)
    db.commit()
    return fixture


def _quote(db, fixture, bookmaker, home, draw, away, captured_at=NOW):
    for key, price in (("home_win", home), ("draw", draw), ("away_win", away)):
        db.add(
            MarketQuote(
                fixture_id=fixture.id,
                bookmaker=bookmaker,
                market_key=key,
                price=price,
                source=bookmaker,
                source_url="https://example.test/",
                captured_at=captured_at,
            )
        )
    db.commit()


def _event(bookmaker, home, draw, away, captured_at=NOW):
    return BookmakerEvent(
        source=bookmaker,
        bookmaker=bookmaker,
        source_url="https://example.test/",
        event_id=f"{bookmaker}-1",
        kickoff_utc=KICKOFF,
        home_team="Andorra",
        away_team="Gibraltar",
        competition="UEFA Nations League",
        country="International",
        known_competition="Premier League",
        market_odds={"home_win": home, "draw": draw, "away_win": away},
        captured_at=captured_at,
    )


def test_ingest_keeps_each_books_own_trio(db):
    _fixture(db, home=2.00, draw=3.20, away=4.50)

    apply_bookmaker_events(db, [_event("BetPawa", 2.10, 3.40, 4.00)])
    apply_bookmaker_events(db, [_event("Betika", 2.75, 3.35, 5.20)])

    stored = {
        (quote.bookmaker, quote.market_key): quote.price
        for quote in db.scalars(select(MarketQuote)).all()
    }
    assert stored[("BetPawa", "home_win")] == pytest.approx(2.10)
    assert stored[("BetPawa", "away_win")] == pytest.approx(4.00)
    assert stored[("Betika", "home_win")] == pytest.approx(2.75)
    # The fixture columns still keep the best price per outcome.
    fixture = db.scalar(select(Fixture))
    assert fixture.home_odds == pytest.approx(2.75)
    assert fixture.away_odds == pytest.approx(5.20)


def test_two_feed_rows_for_one_book_in_a_batch_do_not_collide(db):
    _fixture(db)
    first = _event("BetPawa", 2.10, 3.40, 4.00)
    second = _event("BetPawa", 2.10, 3.40, 4.00)
    second.event_id = "BetPawa-2"

    apply_bookmaker_events(db, [first, second])

    count = db.scalar(select(func.count()).select_from(MarketQuote))
    assert count == 3


def test_resolver_prefers_one_book_over_mixed_best_prices(db):
    fixture = _fixture(db)
    _quote(db, fixture, "BetPawa", 2.10, 3.40, 4.00)

    reference = resolve(db, fixture)

    assert reference is not None
    assert reference.origin == "bookmaker"
    assert reference.bookmaker == "BetPawa"
    assert reference.home == pytest.approx(2.10)
    assert reference.overround == pytest.approx(1.0203, abs=0.001)


def test_resolver_refuses_mixed_prices_without_a_complete_book(db):
    fixture = _fixture(db)

    assert resolve(db, fixture) is None
    assert resolve(None, fixture) is None


def test_resolver_falls_back_to_coherent_fixture_prices(db):
    fixture = _fixture(db, home=1.8, draw=3.3, away=5.4)

    reference = resolve(db, fixture)

    assert reference is not None
    assert reference.origin == "fixture"
    assert reference.bookmaker is None
    assert reference.overround == pytest.approx(1.0438, abs=0.001)


def test_resolver_skips_incomplete_books_and_prefers_the_fresher_one(db):
    fixture = _fixture(db)
    # Incomplete: cannot be de-vigged.
    db.add(
        MarketQuote(
            fixture_id=fixture.id,
            bookmaker="FANSPORT",
            market_key="home_win",
            price=2.40,
            source="FANSPORT",
            captured_at=NOW,
        )
    )
    db.commit()
    _quote(db, fixture, "Betika", 2.60, 3.30, 4.60, captured_at=NOW - timedelta(hours=3))
    _quote(db, fixture, "BetPawa", 2.10, 3.40, 4.00, captured_at=NOW)

    reference = resolve(db, fixture)

    assert reference is not None
    assert reference.bookmaker == "BetPawa"


def test_analysis_devigs_the_resolved_book_not_the_mixed_prices(db):
    fixture = _fixture(db)
    _quote(db, fixture, "BetPawa", 2.10, 3.40, 4.00)

    analysis = analyze_fixture(fixture, db=db)

    assert analysis["market_reference"]["origin"] == "bookmaker"
    assert analysis["market_reference"]["bookmaker"] == "BetPawa"
    assert analysis["bookmaker_margin"]["overround"] == pytest.approx(1.0203, abs=0.001)
    assert analysis["bookmaker_margin"]["fair_implied"]["home"] == pytest.approx(
        0.4667, abs=0.001
    )


def test_analysis_refuses_a_mixed_market_instead_of_inventing_one(db):
    fixture = _fixture(db)

    analysis = analyze_fixture(fixture, db=db)

    assert analysis["market_reference"] is None
    assert analysis["bookmaker_margin"] is None
    assert analysis["probability_blend"]["applied"] is False
    assert analysis["probability_blend"]["reason"] == "no_coherent_market"
    assert analysis["value_bets"] == []
