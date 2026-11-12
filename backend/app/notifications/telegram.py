"""Telegram commands and notifications with allowlist authentication.

The bot is read-only: it reports fixtures, markets, predictions and
performance, and never places a bet. Without ``TELEGRAM_ALLOWED_CHAT_IDS``
every command is refused rather than left open.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

EAT_TZ = timezone(timedelta(hours=3), name="EAT")
API_BASE = "https://api.telegram.org"
HELP_TEXT = (
    "FIE commands:\n"
    "/today - today's fixtures (EAT)\n"
    "/fixtures - next fixtures\n"
    "/singles - markets clearing the 85% gate\n"
    "/accumulators - today's shortlist\n"
    "/analyse <fixture_id> - match analysis\n"
    "/markets <fixture_id> - discovered markets\n"
    "/performance - real-slip record\n"
    "/sources - data providers\n"
    "/settings - platform settings"
)


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def allowed_chat_ids() -> List[str]:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def is_authorized(chat_id: Any) -> bool:
    allowlist = allowed_chat_ids()
    return bool(allowlist) and str(chat_id) in allowlist


def send_message(
    text: str,
    chat_id: Optional[Any] = None,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    recipient = str(chat_id or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    token = _token()
    if not token:
        return {"status": "not_configured", "detail": "TELEGRAM_BOT_TOKEN is not set"}
    if not recipient:
        return {"status": "no_recipient", "detail": "No chat_id supplied"}
    transport = transport or _post
    try:
        response = transport(
            f"{API_BASE}/bot{token}/sendMessage",
            {"chat_id": recipient, "text": text[:4000], "disable_web_page_preview": True},
        )
        return {"status": "sent" if response.get("ok") else "failed", "response": response}
    except Exception as error:
        return {"status": "failed", "detail": str(error)}


def _post(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = requests.post(url, json=payload, timeout=20)
    return response.json()


def _eat(value: Optional[datetime]) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(EAT_TZ).strftime("%a %d %b %H:%M")


def _today_bounds() -> Tuple[datetime, datetime]:
    now_eat = datetime.now(EAT_TZ)
    start = now_eat.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _fixtures_text(db: Session, today_only: bool, limit: int = 10) -> str:
    from app.models.fixture import Fixture

    if today_only:
        start, end = _today_bounds()
        statement = select(Fixture).where(Fixture.kickoff_utc >= start, Fixture.kickoff_utc < end)
    else:
        statement = select(Fixture).where(Fixture.kickoff_utc >= datetime.now(timezone.utc))
    rows = db.scalars(statement.order_by(Fixture.kickoff_utc).limit(limit)).all()
    if not rows:
        return "No fixtures found for that window."
    lines = [
        f"{_eat(row.kickoff_utc)} EAT | {row.home_team} vs {row.away_team} | {row.competition} [{row.id}]"
        for row in rows
    ]
    return "\n".join(lines)


def _singles_text(db: Session, limit: int = 10) -> str:
    from app.analytics.probabilities import analyze_fixture
    from app.models.fixture import Fixture

    start, end = _today_bounds()
    fixtures = db.scalars(
        select(Fixture)
        .where(Fixture.kickoff_utc >= start, Fixture.kickoff_utc < end)
        .where(Fixture.home_odds.isnot(None))
        .order_by(Fixture.kickoff_utc)
        .limit(20)
    ).all()
    lines: List[str] = []
    for fixture in fixtures:
        try:
            analysis = analyze_fixture(fixture, db=db)
        except Exception:
            continue
        for market in analysis.get("qualifying_markets", []):
            lines.append(
                f"{fixture.home_team} vs {fixture.away_team} | {market['market']} "
                f"{market['probability'] * 100:.1f}% [{fixture.id}]"
            )
            if len(lines) >= limit:
                break
        if len(lines) >= limit:
            break
    if not lines:
        return "NO QUALIFIED SELECTIONS FOUND."
    return "\n".join(lines)


def _accumulators_text(db: Session) -> str:
    from app.betting.combo import generate_high_confidence_combos

    result = generate_high_confidence_combos(db)
    combo = result.get("best_combo")
    if not combo:
        return "NO QUALIFIED SELECTIONS FOUND."
    lines = [
        f"{result.get('matchday_label', 'Matchday')}: {combo.get('total_odds')} odds | "
        f"joint probability {combo.get('accum_probability', 0) * 100:.1f}%"
    ]
    for leg in combo.get("legs", [])[:6]:
        lines.append(
            f"- {leg.get('match', '')}: {leg.get('market', '')} "
            f"@ {leg.get('odds', '-')} ({leg.get('probability', 0) * 100:.1f}%)"
        )
    lines.append("Paper analysis only; no guarantee of profit.")
    return "\n".join(lines)


def _analysis_text(db: Session, fixture_id: int) -> str:
    from app.analytics.probabilities import analyze_fixture
    from app.models.fixture import Fixture

    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        return f"Fixture {fixture_id} not found."
    analysis = analyze_fixture(fixture, db=db)
    lambdas = analysis.get("estimated_lambdas", {})
    lines = [
        f"{fixture.home_team} vs {fixture.away_team}",
        f"Lambdas: {lambdas.get('home_lambda')} / {lambdas.get('away_lambda')} "
        f"({analysis.get('lambda_source')})",
        f"1X2: {analysis['all_markets']['home_win'] * 100:.1f}% / "
        f"{analysis['all_markets']['draw'] * 100:.1f}% / "
        f"{analysis['all_markets']['away_win'] * 100:.1f}%",
    ]
    for market in analysis.get("qualifying_markets", [])[:8]:
        lines.append(f"85%+ candidate: {market['market']} {market['probability'] * 100:.1f}%")
    if not analysis.get("qualifying_markets"):
        lines.append("No market clears the 85% gate.")
    lines.append("Advisory research only.")
    return "\n".join(lines)


def _markets_text(db: Session, fixture_id: int) -> str:
    from app.market_engine import analyse_fixture
    from app.models.fixture import Fixture

    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        return f"Fixture {fixture_id} not found."
    analysis = analyse_fixture(fixture, db=db)
    lines = [
        f"Discovered {analysis['discovered_count']} priced selections across "
        f"{len(analysis.get('model_states', {}))} states.",
        f"Qualified: {analysis['qualified']} | Message: {analysis['message']}",
    ]
    if analysis.get("unmodelled"):
        lines.append(f"Unmodelled raw markets: {len(analysis['unmodelled'])} (reported, not analysed)")
    return "\n".join(lines)


def _performance_text(db: Session) -> str:
    from app.api.performance import performance_summary

    summary = performance_summary(days=None, db=db)
    return (
        f"Real slips only ({summary['settled']} settled): "
        f"{summary['wins']}W/{summary['losses']}L/{summary['voids']}V | "
        f"ROI {summary['roi']} | stake {summary['stake']}"
    )


def _sources_text() -> str:
    from app.data_sources.providers import is_configured, provider_catalogue

    lines = []
    for entry in provider_catalogue():
        configured = is_configured(entry)
        lines.append(
            f"{entry['name']}: {'configured' if configured else 'not configured'} "
            f"({entry['free_plan'] or 'no key required'})"
        )
    return "\n".join(lines)


def _settings_text(db: Session) -> str:
    from app.api.system import get_settings

    settings = get_settings(db=db)["settings"]
    return (
        f"Timezone: {settings['timezone']} | gate: {settings['qualification_threshold']} | "
        f"paper trading: {settings['paper_trading']} | auto-place: {settings['auto_place_bets']}"
    )


def handle_command(text: str, db: Session) -> str:
    parts = (text or "").strip().split()
    if not parts:
        return HELP_TEXT
    command = parts[0].split("@")[0].lower()
    argument = parts[1] if len(parts) > 1 else ""

    if command in ("/start", "/help"):
        return HELP_TEXT
    if command == "/today":
        return _fixtures_text(db, today_only=True)
    if command == "/fixtures":
        return _fixtures_text(db, today_only=False)
    if command == "/singles":
        return _singles_text(db)
    if command == "/accumulators":
        return _accumulators_text(db)
    if command == "/performance":
        return _performance_text(db)
    if command == "/sources":
        return _sources_text()
    if command == "/settings":
        return _settings_text(db)
    if command in ("/analyse", "/analyze", "/markets"):
        if not argument.isdigit():
            return f"Usage: {command} <fixture_id>"
        return _analysis_text(db, int(argument)) if command != "/markets" else _markets_text(db, int(argument))
    return f"Unknown command.\n{HELP_TEXT}"


def poll_once(
    db: Session,
    offset: Optional[int] = None,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
) -> Optional[int]:
    """Process one batch of updates; returns the next offset."""
    token = _token()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    transport = transport or _post
    payload = {"timeout": 0}
    if offset is not None:
        payload["offset"] = offset
    update = transport(f"{API_BASE}/bot{token}/getUpdates", payload)
    next_offset = offset
    for item in update.get("result", []) or []:
        next_offset = item.get("update_id", 0) + 1
        message = item.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text") or ""
        if not text:
            continue
        if not is_authorized(chat_id):
            continue
        try:
            reply = handle_command(text, db)
        except Exception as error:
            reply = f"Command failed: {error}"
        send_message(reply, chat_id=chat_id, transport=transport)
    return next_offset


def run_polling(interval: float = 2.0) -> None:
    from app.database import get_db

    if not _token():
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")
    if not allowed_chat_ids():
        raise SystemExit("TELEGRAM_ALLOWED_CHAT_IDS is empty; refusing to run an open bot")
    offset = None
    while True:
        db = next(get_db())
        try:
            offset = poll_once(db, offset)
        finally:
            db.close()
        time.sleep(interval)


if __name__ == "__main__":
    run_polling()
