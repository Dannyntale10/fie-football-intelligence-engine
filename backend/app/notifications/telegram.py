"""Telegram commands and notifications with allowlist authentication.

The bot is read-only: it reports fixtures, markets, predictions and
performance, and never places a bet. Without ``TELEGRAM_ALLOWED_CHAT_IDS``
every command is refused rather than left open.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

EAT_TZ = timezone(timedelta(hours=3), name="EAT")
API_BASE = "https://api.telegram.org"
NO_QUALIFIED = "NO QUALIFIED SELECTIONS FOUND."
HELP_TEXT = (
    "FIE commands:\n"
    "/today - today's fixtures (EAT)\n"
    "/fixtures - next fixtures\n"
    "/singles - markets clearing the 85% gate\n"
    "/combo [2|3] - today's 2- or 3-leg combos\n"
    "/accumulators - today's shortlist\n"
    "/analyse <fixture_id> - match analysis\n"
    "/dossier <fixture_id> - model vs market dossier\n"
    "/value <fixture_id> - best attributed price\n"
    "/markets <fixture_id> - discovered markets\n"
    "/backtest - last combo replay\n"
    "/learning - calibrated parameters\n"
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


def _price_age(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600.0
    if hours < 1:
        return ", <1h old"
    if hours < 48:
        return f", {hours:.0f}h old"
    return f", {hours / 24:.0f}d old"


def format_daily_report(report: Dict[str, Any], size: Optional[int] = None) -> str:
    """The one digest quoted by the bot, the scheduler push and the app.

    ``size`` selects a single ticket size; without it the top 2- and 3-leg
    tickets are shown. The published gate wording is never relaxed here.
    """
    matchday = report.get("matchday_label") or report.get("target_date") or "Today"
    params = report.get("params") or {}
    tier = float(params.get("min_leg_probability") or 0.75)
    tier_block = report.get("tier") or {}
    combos = report.get("combos") or {}
    scope = report.get("scope") or {}

    lines = [f"{matchday} · {tier:.0%} market tier"]
    qualified = scope.get("qualified_legs")
    if qualified is not None:
        lines.append(f"{qualified} qualifying legs from attributed bookmaker prices")
    if tier_block.get("below_published_gate"):
        published = float(tier_block.get("published_gate") or 0.85)
        lines.append(f"Exploratory tier: below the published {published:.0%} gate.")

    keys = [f"{size}_leg"] if size else ["2_leg", "3_leg"]
    tickets = 0
    for key in keys:
        rows = combos.get(key) or []
        if not rows:
            continue
        ticket = rows[0]
        tickets += 1
        lines.append("")
        lines.append(
            f"{ticket['size']}-leg: {ticket['total_odds']:.2f} odds · "
            f"{ticket['combined_probability']:.1%} · "
            f"EV {ticket['expected_value_percent']:+.1f}%"
        )
        for leg in ticket.get("legs", []):
            lines.append(
                f"- {leg['selection']} | {leg['match']} @ {leg['odds']:.2f} "
                f"({leg.get('bookmaker') or 'unattributed'}{_price_age(leg.get('captured_at'))})"
            )

    if not tickets:
        lines.append("")
        lines.append(report.get("message") or NO_QUALIFIED)
    else:
        lines.append("")
        lines.append(
            "Paper analysis only; no bets are placed. Prices are real bookmaker "
            "prices at capture time; the combined probability assumes the legs are independent."
        )
    return "\n".join(lines)[:4000]


def _delivery_recipient(row: Any) -> Optional[str]:
    """Only ever deliver to an explicitly allowlisted chat."""
    allowlist = allowed_chat_ids()
    if not allowlist:
        return None
    candidate = str(getattr(row, "recipient", "") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()
    return candidate if candidate in allowlist else allowlist[0]


def deliver_pending(
    db: Session,
    transport: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """Drain queued Telegram notifications and record each outcome.

    Rows stay ``pending`` when no token or allowlist is configured, so nothing
    is silently dropped. Delivery failures are marked ``failed`` rather than
    retried forever; the notification list exposes both states.
    """
    from app.models.predictions import Notification

    if not _token():
        return {"status": "not_configured", "checked": 0, "sent": 0, "failed": 0, "pending": 0}
    if not allowed_chat_ids():
        return {"status": "no_recipient", "checked": 0, "sent": 0, "failed": 0, "pending": 0}

    rows = db.scalars(
        select(Notification)
        .where(Notification.channel == "telegram", Notification.status == "pending")
        .order_by(Notification.created_at)
        .limit(limit)
    ).all()

    sent = 0
    failed = 0
    pending = 0
    for row in rows:
        recipient = _delivery_recipient(row)
        if recipient is None:
            pending += 1
            continue
        body = f"{row.subject}\n\n{row.body}" if row.subject else row.body
        result = send_message(body, chat_id=recipient, transport=transport)
        status = result.get("status")
        if status == "sent":
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            sent += 1
        elif status == "failed":
            row.status = "failed"
            failed += 1
        else:
            pending += 1
    if sent or failed:
        db.commit()
    return {"status": "ok", "checked": len(rows), "sent": sent, "failed": failed, "pending": pending}


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


def _accumulators_text(db: Session, size: Optional[int] = None) -> str:
    from app.betting.daily_combos import build_daily_combos

    report = build_daily_combos(db)
    if size and not (report.get("combos") or {}).get(f"{size}_leg"):
        return f"No {size}-leg ticket qualifies today. {NO_QUALIFIED}"
    return format_daily_report(report, size=size)


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


def _dossier_text(db: Session, fixture_id: int) -> str:
    from app.analytics.match_analysis import build_match_dossier
    from app.models.fixture import Fixture

    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        return f"Fixture {fixture_id} not found."
    dossier = build_match_dossier(db, fixture)
    verdict = dossier.get("verdict") or {}
    model = dossier.get("model") or {}
    lambdas = model.get("lambdas") or {}

    lines = [
        f"{fixture.home_team} vs {fixture.away_team} · {fixture.competition}",
        f"Verdict ({verdict.get('confidence', 'low')}): {verdict.get('headline', 'No read yet.')}",
    ]
    if lambdas.get("home_lambda") is not None:
        lines.append(
            f"Lambdas {lambdas.get('home_lambda')} / {lambdas.get('away_lambda')} "
            f"({model.get('lambda_source') or 'unknown source'})"
        )
    for row in (dossier.get("markets") or [])[:3]:
        lines.append(
            f"{row['selection']} @ {row['price']:.2f}: model "
            f"{row['calibrated_probability']:.1%} vs market {row['fair_probability']:.1%} "
            f"(edge {row['edge_percent']:+.1f}%)"
        )
    for row in (dossier.get("best_bets") or [])[:2]:
        lines.append(f"Model value: {row['selection']} @ {row['price']:.2f} ({row['edge_percent']:+.1f}%)")
    for risk in (dossier.get("risks") or [])[:3]:
        lines.append(f"Risk ({risk.get('severity')}): {risk.get('detail')}")
    lines.append("Advisory research only; no bets are placed.")
    return "\n".join(lines)[:4000]


def _value_text(db: Session, fixture_id: int) -> str:
    """Best attributed price per 1X2 outcome; unattributed odds are never quoted."""
    from app.models.fixture import Fixture

    fixture = db.get(Fixture, fixture_id)
    if fixture is None:
        return f"Fixture {fixture_id} not found."
    raw = getattr(fixture, "market_odds_json", None)
    try:
        payload = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        payload = {}

    labels = {"home_win": f"{fixture.home_team} to win", "draw": "Draw", "away_win": f"{fixture.away_team} to win"}
    rows = []
    for key in ("home_win", "draw", "away_win"):
        entry = payload.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("price"), (int, float)):
            continue
        rows.append(
            f"{labels[key]}: {float(entry['price']):.2f} "
            f"({entry.get('bookmaker') or 'unattributed'}{_price_age(entry.get('captured_at'))})"
        )
    if not rows:
        return (
            f"No attributed price captured for {fixture.home_team} vs {fixture.away_team}. "
            "Legacy stored odds are not quoted without a bookmaker and capture time."
        )
    lines = [f"{fixture.home_team} vs {fixture.away_team} · attributed 1X2 prices"]
    lines.extend(rows)
    lines.append("Prices move; re-check the book before staking.")
    return "\n".join(lines)


def _backtest_text() -> str:
    from app.analytics.combo_backtest import load_report

    report = load_report()
    if not report:
        return "No combo replay saved yet. Run POST /combos/backtest on the server to build it."
    window = report.get("window") or {}
    params = report.get("params") or {}
    lines = [
        f"Combo replay · status {report.get('status')} · tier "
        f"{float(params.get('min_leg_probability') or 0.75):.0%}",
    ]
    if window:
        lines.append(
            f"{window.get('matchdays', 0)} matchdays ({window.get('first_matchday')} to "
            f"{window.get('last_matchday')}), {window.get('settled_matches', 0)} settled matches"
        )
    holdout = (report.get("combos") or {}).get("holdout") or {}
    for key in ("2_leg", "3_leg"):
        metrics = holdout.get(key) or {}
        if not metrics.get("count"):
            continue
        roi = metrics.get("roi_percent")
        lines.append(
            f"Holdout {metrics.get('count')} {key.replace('_', '-')} tickets: "
            f"hit {metrics.get('hit_rate', 0):.1%} · predicted "
            f"{metrics.get('average_predicted_probability', 0):.1%}"
            + (f" · ROI {roi:+.2f}%" if roi is not None else "")
        )
    learning = report.get("learning") or {}
    if learning:
        lines.append(
            "Calibration: " + ("applied" if learning.get("applied") else "not applied")
            + (f" (improvement {learning['improvement']:+.5f})" if learning.get("improvement") is not None else "")
        )
    try:
        from app.analytics.combo_backtest import load_sweep

        recommendation = (load_sweep() or {}).get("recommendation") or {}
    except Exception:
        recommendation = {}
    tier = recommendation.get("tier")
    if tier:
        holdout = recommendation.get("holdout") or {}
        line = f"Tier sweep: {float(tier):.0%} leads on training"
        if holdout.get("count"):
            line += f" · holdout hit {holdout.get('hit_rate', 0):.1%}"
            if holdout.get("roi_percent") is not None:
                line += f" · ROI {holdout['roi_percent']:+.1f}%"
        line += (
            " · confirmed out of sample"
            if recommendation.get("holdout_confirmed")
            else " · not confirmed; live default unchanged"
        )
        lines.append(line)
        if recommendation.get("guidance"):
            lines.append(str(recommendation["guidance"]))
    lines.append("Replay prices are historical and conservative; negative ROI at book prices is expected.")
    return "\n".join(lines)[:4000]


def _learning_text() -> str:
    from app.analytics.learning import summary

    report = summary()
    if not report.get("trained"):
        return f"No learned parameters yet. {report.get('note', '')}".strip()
    lines = [
        f"Learning {report.get('version')} · trained {report.get('trained_at') or 'unknown'}",
        f"Applied markets: {', '.join(report.get('applied_markets') or []) or 'none'}",
    ]
    premiums = report.get("edge_premiums") or {}
    if premiums:
        parts = []
        for key, value in premiums.items():
            if isinstance(value, dict):
                uplift = (float(value.get("premium") or 1.0) - 1.0) * 100
                parts.append(f"{key} +{uplift:.1f}% (n={value.get('n', 0)})")
            elif isinstance(value, (int, float)):
                parts.append(f"{key} +{(float(value) - 1.0) * 100:.1f}%")
        if parts:
            lines.append("Edge premiums: " + ", ".join(parts))
    sources = report.get("sources") or {}
    if sources:
        lines.append(
            f"Evidence: {sources.get('settled_legs', 0)} settled legs, "
            f"{sources.get('walkforward_matches', 0)} walk-forward matches"
        )
    for note in (report.get("notes") or [])[:2]:
        lines.append(f"Note: {note}")
    return "\n".join(lines)[:4000]


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
    if command == "/combo":
        if argument and argument not in ("2", "3"):
            return "Usage: /combo [2|3]"
        return _accumulators_text(db, size=int(argument) if argument else None)
    if command == "/accumulators":
        return _accumulators_text(db)
    if command == "/backtest":
        return _backtest_text()
    if command == "/learning":
        return _learning_text()
    if command == "/performance":
        return _performance_text(db)
    if command == "/sources":
        return _sources_text()
    if command == "/settings":
        return _settings_text(db)
    if command in ("/analyse", "/analyze", "/markets", "/dossier", "/value"):
        if not argument.isdigit():
            return f"Usage: {command} <fixture_id>"
        fixture_id = int(argument)
        if command == "/markets":
            return _markets_text(db, fixture_id)
        if command == "/dossier":
            return _dossier_text(db, fixture_id)
        if command == "/value":
            return _value_text(db, fixture_id)
        return _analysis_text(db, fixture_id)
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
