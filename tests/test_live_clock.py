"""Elapsed-minute parsing never guesses a clock it cannot read."""
from datetime import datetime, timedelta, timezone

import pytest

from app.live.clock import elapsed_minutes

NOW = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)


def _kickoff(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


@pytest.mark.parametrize(
    ("clock", "status", "expected"),
    [
        ("67:34", "live", 67),
        ("45+2", "live", 45),
        ("90+4", "live", 90),
        ("HT", "live", 45),
        (None, "halftime", 45),
        ("12'", "live", 12),
    ],
)
def test_clock_strings(clock, status, expected):
    assert elapsed_minutes(clock, _kickoff(70), status, NOW) == expected


def test_missing_clock_derives_from_kickoff_and_removes_the_break():
    assert elapsed_minutes(None, _kickoff(30), "live", NOW) == 30
    assert elapsed_minutes(None, _kickoff(70), "live", NOW) == 55


def test_the_derived_minute_never_walks_backwards_across_the_break():
    minutes = [44, 45, 46, 50, 59, 60, 61, 70]

    derived = [elapsed_minutes(None, _kickoff(m), "live", NOW) for m in minutes]

    assert derived == sorted(derived)
    assert derived[:5] == [44, 45, 45, 45, 45]
    assert derived[5:] == [45, 46, 55]


def test_derived_minute_is_clamped():
    assert elapsed_minutes(None, _kickoff(-5), "live", NOW) == 1
    assert elapsed_minutes(None, _kickoff(600), "live", NOW) == 90


def test_non_live_status_and_underivable_inputs_return_none():
    assert elapsed_minutes("67:34", _kickoff(70), "finished", NOW) is None
    assert elapsed_minutes("67:34", _kickoff(70), "scheduled", NOW) is None
    assert elapsed_minutes("garbage", None, "live", NOW) is None
