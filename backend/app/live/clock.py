"""Turn a feed clock into an elapsed regulation minute.

ESPN supplies a display clock only while the ball is in play. When it is
missing, the wall clock since kickoff is the only evidence available, and the
15-minute interval is removed once the raw elapsed time passes 45 minutes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

REGULATION_MINUTES = 90
HALF_TIME_MINUTES = 45
HALF_TIME_BREAK = 15


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _from_clock(clock: str) -> Optional[int]:
    text = clock.strip()
    if not text:
        return None
    if text.upper() in {"HT", "HALF TIME"}:
        return HALF_TIME_MINUTES
    head = text.split("+", 1)[0].strip().rstrip("'").strip()
    head = head.split(":", 1)[0].strip()
    try:
        minute = int(head)
    except ValueError:
        return None
    return max(1, min(REGULATION_MINUTES, minute))


def elapsed_minutes(
    clock: Optional[str],
    kickoff: Optional[datetime],
    status: Optional[str],
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Elapsed regulation minute, or ``None`` when it cannot be established."""
    status = (status or "").strip().lower()
    if status not in {"live", "halftime"}:
        return None
    if status == "halftime":
        return HALF_TIME_MINUTES
    if clock:
        parsed = _from_clock(clock)
        if parsed is not None:
            return parsed
    if kickoff is None or now is None:
        return None
    raw = int((_aware(now) - _aware(kickoff)).total_seconds() // 60)
    if raw > HALF_TIME_MINUTES:
        raw -= HALF_TIME_BREAK
    return max(1, min(REGULATION_MINUTES, raw))
