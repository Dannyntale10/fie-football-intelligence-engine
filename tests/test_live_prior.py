"""The live prior may only use prices provably captured before kickoff."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.analytics.market_reference import resolve
from app.analytics.probabilities import analyze_fixture
from app.database import SessionLocal
from app.models.market_quote import MarketQuote

KICKOFF = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)


def _fixture(fixture_id=990001):
    return SimpleNamespace(
        id=fixture_id,
        kickoff_utc=KICKOFF,
        home_team="Prior Home",
        away_team="Prior Away",
        competition="Prior League",
        country="Test",
        source="Test",
        home_odds=1.90,
        draw_odds=3.40,
        away_odds=4.20,
        odds_updated_at=KICKOFF - timedelta(hours=2),
    )


def _add_quote(db, fixture_id, bookmaker, home, draw, away, captured):
    for key, price in (("home_win", home), ("draw", draw), ("away_win", away)):
        db.add(
            MarketQuote(
                fixture_id=fixture_id,
                bookmaker=bookmaker,
                market_key=key,
                price=price,
                source="Test",
                captured_at=captured,
            )
        )


def _cleanup(db):
    db.query(MarketQuote).filter(MarketQuote.fixture_id == 990001).delete(synchronize_session=False)
    db.commit()


def test_before_ignores_a_quote_captured_after_kickoff():
    db = SessionLocal()
    try:
        _cleanup(db)
        _add_quote(db, 990001, "EarlyBook", 1.80, 3.60, 4.50, KICKOFF - timedelta(hours=3))
        _add_quote(db, 990001, "LateBook", 1.50, 4.00, 6.00, KICKOFF + timedelta(minutes=30))
        db.commit()
        fixture = _fixture()

        live_prior = resolve(db, fixture, before=KICKOFF)
        unrestrained = resolve(db, fixture)

        assert live_prior is not None
        assert live_prior.bookmaker == "EarlyBook"
        assert unrestrained is not None
        assert unrestrained.bookmaker == "LateBook"
    finally:
        _cleanup(db)
        db.close()


def test_fixture_fallback_needs_a_provable_capture_time():
    db = SessionLocal()
    try:
        _cleanup(db)
        db.commit()
        fixture = _fixture()
        fixture.odds_updated_at = KICKOFF + timedelta(minutes=5)

        assert resolve(db, fixture, before=KICKOFF) is None
        assert resolve(db, fixture) is not None
    finally:
        _cleanup(db)
        db.close()


def test_analysis_passes_as_of_to_the_prior():
    db = SessionLocal()
    try:
        _cleanup(db)
        _add_quote(db, 990001, "EarlyBook", 1.80, 3.60, 4.50, KICKOFF - timedelta(hours=3))
        _add_quote(db, 990001, "LateBook", 1.50, 4.00, 6.00, KICKOFF + timedelta(minutes=30))
        db.commit()

        frozen = analyze_fixture(_fixture(), db=db, as_of=KICKOFF)
        current = analyze_fixture(_fixture(), db=db)

        assert frozen["market_reference"]["bookmaker"] == "EarlyBook"
        assert current["market_reference"]["bookmaker"] == "LateBook"
    finally:
        _cleanup(db)
        db.close()
