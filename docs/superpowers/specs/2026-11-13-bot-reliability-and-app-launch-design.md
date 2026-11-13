# Bot reliability and app launch experience — design

Date: 2026-11-13
Status: approved for implementation
Scope owner: FIE operator

## 1. Context

The platform has one delivery channel (Telegram), one scheduler, and one
mobile client. Three gaps block daily use:

1. **The bot cannot receive commands.** `poll_once` and `run_polling` exist in
   `backend/app/notifications/telegram.py` but nothing starts them. Only a
   manual `python -m app.notifications.telegram` process can answer `/today`.
2. **Delivery loses state.** Queued notifications are attempted once; a failed
   row is marked `failed` permanently. A non-allowlisted recipient is silently
   redirected to the first allowlisted chat. The 2-minute delivery sweep has no
   dedupe, so a shortlist build followed by a restart can queue the same digest
   twice. The settings key `telegram_allowlist` exists and is documented in the
   dashboard but the bot never reads it.
3. **The legacy WhatsApp sentinel contradicts the published rules.** Its
   docstring requires 85% average confidence, 85% accumulator probability, and
   odds ≥ 2.00, but it calls the combo engine with `min_leg_prob=0.55`,
   `min_total_odds=1.50`, and `min_edge_percent=3.0`. It also persists phone
   numbers and API keys to `sentinel_state.json`, duplicates settlement, and is
   the only caller that records published combos into the tracked-bet ledger.
   Retiring it without moving those responsibilities would silently freeze
   settlement and empty the ledger.

The iOS client has a related gap: `UILaunchScreen` only sets a background
colour, every tab loads its own data on first appearance, and there is no
live-score surface. The backend only finalises scores at 02:30, so an in-play
match shows no score at all.

This design closes all four clusters in two implementation phases. Phase A is
the bot. Phase B is the app launch, preloading, and live-score loop.

## 2. Goals

- Inbound Telegram commands run automatically inside the API process.
- Delivery retries with backoff, dedupes by content, and fails closed.
- The allowlist resolves from settings with an environment override.
- The legacy WhatsApp sentinel and every WhatsApp credential write are removed.
- Settlement and ledger recording continue without the sentinel.
- The app opens on a branded splash, preloads every screen, and refreshes live
  scores and the active screen every 60 seconds.
- No price, score, fixture, or statistic is ever fabricated. Missing feed data
  leaves stored values untouched and is reported as unavailable.

## 3. Non-goals

- Email, WhatsApp, or SMS delivery. Telegram is the only channel after this
  change.
- Placing bets automatically. The bot stays read-only.
- Changing the published 85% gate, the combo tier default, or selection rules.
- Multi-worker deployments. The polling thread assumes one API process.
- Purging synthetic ledger rows. That remains a separate, approval-gated task.

## 4. Phase A — Bot

### A1. Notification delivery state

`Notification` in `backend/app/models/predictions.py` gains four columns:

| Column | Type | Default | Purpose |
| --- | --- | --- | --- |
| `attempts` | Integer | 0 | Delivery attempts made |
| `next_attempt_at` | DateTime nullable | null | Earliest next attempt |
| `last_error` | String(400) nullable | null | Last failure reason |
| `dedupe_key` | String(200) nullable | null | Content identity |

The migration goes through `ensure_schema_compatibility` in
`backend/app/database.py`, following the existing
`PRAGMA table_info(...)` + `ALTER TABLE ... ADD COLUMN` pattern. SQLite requires
`NOT NULL` additions to carry a default, so `attempts` is declared
`INTEGER NOT NULL DEFAULT 0`. The helper currently swallows every exception
with `except Exception: pass`; the new blocks log the exception instead, so a
failed migration is visible rather than silently leaving delivery unprotected.
A partial unique index is created after the column:

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_notifications_dedupe_key
ON notifications (dedupe_key) WHERE dedupe_key IS NOT NULL;
```

A single helper in `backend/app/notifications/telegram.py` owns queueing:

```python
def queue_message(
    db: Session,
    subject: str,
    body: str,
    *,
    dedupe_key: str,
    related_type: str | None = None,
    related_id: int | None = None,
    channel: str = "telegram",
) -> int | None:
```

It inserts a `pending` row unless a row with the same `dedupe_key` exists, in
which case it returns `None`. `IntegrityError` from a concurrent insert is
caught and treated as a duplicate. Keys are namespaced by producer:

- shortlist digest: `shortlist:{matchday}:{ticket_signature}`
- settlement: `settle:{tracked_bet_id}`
- daily analytics: `analytics:{matchday}`
- blend refit: `blend:{date}:{weight}`

`ticket_signature` is a short SHA-256 of the sorted
`fixture_id|market_key|odds` tuples, so a rebuilt report with different
selections is not suppressed. An empty shortlist uses
`shortlist:{matchday}:empty`.

`backend/app/tasks/scheduler.py::_notify` becomes a thin wrapper around
`queue_message` and loses its optional-everything signature. `related_id`
is now passed where available.

### A2. Delivery semantics

`deliver_pending(db, transport=None, limit=20)` keeps its public name and
return shape. New behaviour:

1. Select `channel == "telegram"`, `status == "pending"`, and
   `next_attempt_at IS NULL OR next_attempt_at <= now`, ordered by
   `created_at`, limit `limit`.
2. Resolve the allowlist once from `allowed_chat_ids(db)`.
3. For each row, choose the recipient:
   - `row.recipient` if it is allowlisted;
   - otherwise `TELEGRAM_CHAT_ID` if it is allowlisted;
   - otherwise **no recipient**. The row is not redirected. It increments
     `attempts`, records `last_error = "recipient not allowlisted"`, and backs
     off.
4. Split the message into chunks of at most 3,500 characters on line
   boundaries. A single line longer than the limit is hard-split. Every chunk
   must succeed before the row is marked `sent`. The existing `[:4000]`
   truncation in `format_daily_report` and `send_message` is removed so full
   content reaches the chunker; `send_message` sends one already-sized chunk
   unchanged.
5. On failure, increment `attempts` and set
   `next_attempt_at = now + backoff(attempts)` using
   1, 2, 4, 8, 16 minutes. At `MAX_DELIVERY_ATTEMPTS = 5` set
   `status = "failed"` and keep `last_error`.
6. If Telegram answers HTTP 429, honour `parameters.retry_after` (capped at one
   hour) and leave the row `pending` without consuming an attempt.
7. If no token or no allowlist is configured, return
   `{"status": "not_configured", ...}` and touch no rows.

The return keys `status`, `checked`, `sent`, `failed`, and `pending` keep their
current meaning. A `retried` count is added; no existing key is renamed or
removed.

`send_message` returns the Telegram `description` on failure and no longer
swallows it. It still accepts an injectable transport for tests.

### A3. Inbound command polling

`poll_once` gains `long_poll_seconds: int = 0` and persists progress:

- Before sending a reply, process the update, then write
  `telegram_poll_offset = update_id + 1` to `SystemSetting` in the same session.
  An interrupted batch therefore replays at most one update.
- On first run (`telegram_poll_offset` absent), call `getUpdates` with
  `offset = -1` and `timeout = 0`, persist the resulting offset + 1, and do not
  execute any command. This discards backlog instead of replaying day-old
  commands.
- A handler exception logs `logger.exception` and replies
  `Command failed. Reference {token}.` where `token` is the first six hex
  characters of a UUID. The raw exception is never sent to chat.

`start_polling_thread()` starts one daemon thread with a `threading.Event` stop
flag and a 25-second long poll. It refuses to start unless a token and at least
one allowlisted chat are resolved. `TELEGRAM_POLLING=off` disables it
explicitly. `stop_polling_thread()` sets the event and joins with a timeout.

Startup wiring uses a FastAPI lifespan context manager in
`backend/app/main.py`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_polling_thread()
    yield
    stop_polling_thread()
```

The module-level `start_scheduler()` call stays where it is. Tests never set a
token, so the thread does not start under pytest.

### A4. Allowlist resolution

`allowed_chat_ids(db=None)` resolves in this order:

1. `TELEGRAM_ALLOWED_CHAT_IDS` when non-empty (explicit override);
2. `SystemSetting` key `telegram_allowlist`;
3. empty list.

`is_authorized(chat_id, db=None)` uses the same resolver. `deliver_pending`
passes its session. `/status` reports which source supplied the list.
An empty list still refuses every command and every delivery.

### A5. Operator commands

`HELP_TEXT` and `handle_command` gain:

- `/status` — engine health, scheduler running state and next run per job,
  poller running state, allowlist source, and queue counts (pending, failed,
  sent today).
- `/pending` — up to ten pending or failed rows with id, subject, status,
  attempts, and a truncated `last_error`.
- `/retry <notification_id>` — resets a failed row to `pending` with
  `attempts = 0`, clears `last_error` and `next_attempt_at`, and confirms the
  id. Unknown ids answer `Notification {id} not found.`
- `/sweep` — the tier-sweep recommendation (tier, holdout confirmation,
  guidance), reusing the existing sweep report loader.

`/today` appends the score and clock to fixtures that are in play, for example
`Arsenal vs Chelsea · LIVE 2-1 67'`.
`/help` lists the new commands.

`scheduler_status()` is added to `backend/app/tasks/scheduler.py` and returns
`{"running": bool, "jobs": [{"id": str, "next_run": iso | None}]}` from the
retained scheduler instance. The scheduler module keeps a module-level
reference to the `BackgroundScheduler` it starts.

### A6. Retire the WhatsApp sentinel

Delete:

- `backend/app/tasks/sentinel.py`
- `backend/app/notifications/whatsapp.py`
- `GET /sentinel/status`, `POST /sentinel/start`, `POST /sentinel/stop`,
  `POST /sentinel/scan`, `POST /whatsapp/test` in `backend/app/main.py`

**Streamlit is the deployed service.** `render.yaml` starts
`streamlit run streamlit_app.py`, and both `streamlit_app.py` and the
unreferenced `dashboard.py` import `app.notifications.whatsapp` and
`app.tasks.sentinel` at module level. Deleting those modules without editing
the dashboards would break the Render deployment on the next build. Phase A
therefore also:

- removes the WhatsApp credential inputs, test-send button, and
  `send_combo_whatsapp_alert` call from `streamlit_app.py` and `dashboard.py`;
- removes the sentinel start/stop/scan controls and sentinel chip, replacing
  the chip with a Telegram queue chip (`queued N · failed M` from the
  `notifications` table);
- keeps the existing `fetch_live_scores` live-score table in Streamlit, which
  Phase B preserves through a compatibility wrapper;
- removes the `WHATSAPP_*` and `TWILIO_*` variables from `render.yaml`,
  `.env.example`, and the README environment table, and updates the
  `notifications/` line in the README architecture block.

The on-disk `sentinel_state.json` is left in place and unreferenced; it
contains credentials and is the operator's to delete.

**Bot host.** The polling thread runs inside the FastAPI process. The Render
service runs Streamlit only, so it is not a bot host. Running the bot requires
the FastAPI process (today: local `uvicorn app.main:app --app-dir backend`).
Deploying FastAPI as a second Render web service is out of scope for this
change and is recorded here so the requirement is not silently lost.

Three responsibilities move before the deletion:

**A6.1 Shortlist records the ledger.** `build_shortlist()` records each ticket
it publishes. For each size present in the report, the top ticket is converted
to the shape `record_tracked_bet` expects:

- `legs_count = size`
- `avg_leg_probability = mean(leg probabilities)`
- `kelly_stake_amount = float(os.getenv("FIE_SHORTLIST_STAKE", "10"))`
- `kelly_stake_percent = 0`

and is recorded with
`dedupe_key = "shortlist:{matchday}:{size}:{ticket_signature}"`.
`TrackedBet` gains nullable `dedupe_key` and `tier` columns plus a partial
unique index on `dedupe_key`. When the key already exists,
`record_tracked_bet` returns the existing row and writes no snapshots. `tier`
holds the report's `min_leg_probability`, so exploratory 75% slips are never
silently mixed with the published 85% gate in performance reporting.

`build_shortlist` order is: build report, record tickets, queue digest with
`dedupe_key = "shortlist:{matchday}:{digest_signature}"`, then deliver. A
second run on the same matchday is a no-op for both ledger and queue.

**A6.2 Settlement is a scheduler job.** `settle_pending_bets(db)` loses the
`phone`, `apikey`, `provider`, and `currency` parameters. For each bet that
transitions to `WON` or `LOST` and whose `settlement_notified` flag is false,
it calls `queue_message` with
`dedupe_key = "settle:{bet.id}"`,
`subject = "Settlement · {slip_type}"`, the formatted settlement text, and
`related_type = "tracked_bet"`. It then sets `settlement_notified = True`.
`format_settlement_whatsapp_message` is renamed `format_settlement_message`
with unchanged content; currency comes from `bet.stake_currency`, falling back
to `FIE_CURRENCY` (default `UGX`).

`TrackedBet.settlement_notified` is added as a non-null boolean default false
and backfilled once during migration from `whatsapp_settlement_sent`. The old
`whatsapp_*` columns remain in the table, unused, because SQLite cannot drop
them in place. `record_tracked_bet` stops writing them.

`settle_pending_bets` leaves `settled_at`, `status`, `profit_loss`, and
`profit_loss_usd` untouched — it only adds notification behaviour.

**A6.3 Scheduler jobs.** `start_scheduler()` registers
`settle_and_capture` on `CronTrigger(minute="*/30")`. It calls
`capture_closing_prices(db)` then `settle_pending_bets(db)`, closes the
session, and logs counts. Failures are logged, never raised. The 02:30
`update_results` job keeps finalising fixture scores first, so the following
settlement run sees them. `settle_pending_bets` already calls
`sync_finished_match_scores_from_espn`, so the 30-minute job also finalises
scores between the 02:30 runs.

`POST /ledger/settle` keeps working with no query parameters. Delivery is the
2-minute queue drain that already exists.

### A7. Dashboard alignment

- `frontend/app/settings/page.tsx`: the "Queue a notification" channel select
  offers only `telegram`. The hint text already names `telegram_allowlist`; it
  gains one sentence that an environment value overrides the setting.
- `frontend/app/settings/page.tsx` no longer mentions WhatsApp anywhere.
- The dashboard does not get a new page. Queue visibility stays on the existing
  notifications surface and the new `/pending` command.

### A8. Logging

- Delivery failures log row id, attempt number, and Telegram's description.
- Polling handler failures log the full traceback with the reference token.
- Sentinel retirement removes the only path that wrote credentials to disk.

## 5. Phase B — App launch, preload, and live updates

### B1. Backend live scores

`Fixture` gains:

| Column | Type | Purpose |
| --- | --- | --- |
| `live_clock` | String(20) nullable | ESPN display clock, e.g. `67'`, `HT` |
| `espn_league_code` | String(30) nullable | League code used for score polling |

Both are added through `ensure_schema_compatibility`. `live_feed.py` writes
`espn_league_code` on every ingested fixture, including fixtures that already
exist and are only being refreshed for odds.

**Reuse `inplay.py`.** The repo already has
`backend/app/data_sources/inplay.py`, which fetches live ESPN scores but never
persists them, polls a fixed 17-league list, and has no throttle. That module
is rewritten rather than duplicated. It keeps a compatibility function with its
current signature so the Streamlit live table continues to work:

```python
def fetch_live_scores(db: Session) -> List[Dict[str, Any]]:
    # refresh if stale, then return the same dict shape Streamlit expects
```

and gains the persisting entry point:

```python
def refresh_live_scores(db: Session, *, force: bool = False) -> dict:
```

- Selects fixtures whose kickoff is between now − 4 hours and now + 15 minutes
  and whose status is `scheduled`, `live`, or `halftime`.
- Resolves a league code per fixture from `espn_league_code`, falling back to a
  `display name → code` map built from `COMPETITION_TARGETS`. Fixtures with no
  resolvable code are skipped and counted in `skipped`.
- Fetches `https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard`
  concurrently (max 8 workers, 6-second timeout) for the distinct codes only.
- Throttles to at most one refresh per 60 seconds unless `force=True`, using a
  module-level monotonic timestamp.
- Matches events by `external_fixture_id`, then by exact home/away team names
  inside the same league code. It **never creates a fixture**.
- Maps ESPN state: `pre → scheduled`, `in → live` (or `halftime` when the type
  name contains `HALFTIME`; then `live_clock` is `HT`), `post → finished`.
  Non-final post states set `postponed` or `cancelled` only when ESPN says so.
- Copies scores (ESPN returns them as strings) with an `int(value)` parse inside
  a `try`; a missing or non-numeric score leaves the stored value untouched.
  Scores are only copied for `in` and final `post` states, never for
  postponed or cancelled matches.
- Commits once and returns
  `{"refreshed": bool, "updated": int, "skipped": int, "warning": str | None}`.
- A feed error returns `refreshed=False` plus `warning`; it never raises and
  never invents a score.

`fetch_live_scores` calls `refresh_live_scores` (throttled) and then maps the
stored live, halftime, and finished-today rows to the dict keys Streamlit
already consumes (`home_team`, `away_team`, `home_score`, `away_score`,
`state`, `clock`, `fixture_id`, `is_live`, `is_finished`, `kickoff_eat`).

New endpoint in `backend/app/main.py`:

```
GET /fixtures/live
  -> 200 LiveFixturesOut
```

`LiveFixturesOut = {as_of, refreshed, warning?, fixtures: [FixtureOut]}`.
It calls `refresh_live_scores(db)`, then returns fixtures with status `live` or
`halftime` ordered by kickoff. `FixtureOut` gains `home_score`, `away_score`,
and `live_clock`. A feed outage still returns 200 with stored live rows and a
`warning`.

The route is declared **before** `GET /fixtures/{fixture_id}` in
`backend/app/main.py`. Otherwise FastAPI matches `live` as a `fixture_id` and
returns 422.

No scheduler job is added: the endpoint refreshes on demand and is throttled,
which keeps a 60-second app poll at one upstream refresh per minute.

### B2. Splash and preload

**Branding assets.** A `BrandLogo.imageset` is added, built from the existing
`AppIcon.png` (dark background, green football/graph mark). `Info.plist`
`UILaunchScreen` gains `UIImageName = BrandLogo`, so the OS launch screen
already shows the mark instead of a blank dark rectangle.

**Splash view.** New `ios/FIE/Design/SplashView.swift`:

- `BrandLogo` at 120 pt, the wordmark `FIE`, and the subtitle
  `Football Intelligence Engine`.
- A subtle opacity/scale entrance and a bottom label showing
  `Loading · N / 11 ready`, incremented as each bootstrap section finishes. It
  reports real completion, not invented stages.
- Displayed for at least 1.0 second and until `EngineStore.isBootstrapped` is
  true, with a hard 6-second cap so an unreachable engine cannot trap the app.
  Exiting the splash does **not** cancel bootstrap; slow sections keep loading
  and report their own state.

**EngineStore.** New `ios/FIE/Core/EngineStore.swift`, a `@MainActor
ObservableObject` created once in `FIEApp` and injected with
`.environmentObject`.

Published state:

- `report: DailyComboReport?`
- `fixtures: [Fixture]`
- `gate: GateReport?`
- `health: Health?`
- `summary: PerformanceSummary?`
- `clv: ClvSummary?`
- `learning: LearningSummary?`
- `sweep: ComboSweep?`
- `sources: SourcesResponse?`
- `backtest: ComboBacktestReport?`
- `live: [Fixture]`
- `errors: [Section: String]`
- `isBootstrapped`, `isRefreshing`, `lastUpdated`

`bootstrap()` runs every existing view request concurrently in one task group,
using the same paths the views use today:
`/health`, `/combos/daily`, `/fixtures?upcoming_only=true&limit=120`,
`/markets/qualifying`, `/performance/summary`,
`/evaluation/clv?days=90`, `/learning/summary`,
`/combos/backtest/sweep`, `/combos/backtest`, `/sources`, `/fixtures/live`.
A failing section records an `errors` entry and does not cancel the others.
The daily report is written to `ReportCache` on success.

Views change from owning network state to reading the store:

- `TodayView`, `FixturesView`, `PerformanceView`, `SourcesView`, `HistoryView`
  keep their filters, expansion, and animation state, but read data from
  `@EnvironmentObject var store`.
- Each keeps `.refreshable { await store.refresh(section:) }`.
- Explicit actions (analyse slate, run replay, rebuild) call the store and keep
  their existing progress and error copy.
- `SettingsView` calls `store.reset()` when the server URL changes.

`RootView` shows the splash over the tab view until bootstrap finishes, then
fades it out. The persisted `fie.selectedTab` behaviour is unchanged.

### B3. 60-second refresh

`EngineStore.startAutoRefresh()` runs one loop:

```
while active:
  sleep 60
  await refreshLive()            // GET /fixtures/live
  await refresh(section: active) // current tab's data
```

- `refreshLive()` updates `live` and `lastUpdated`; errors keep the last data
  and set a `livePaused` flag shown as a one-line note in Fixtures.
- `refresh(section:)` refreshes the active tab only. Report updates apply
  without animation during automatic ticks; manual refresh keeps animation.
- `scenePhase` integration: entering `background` cancels the loop; entering
  `active` performs an immediate `refreshLive()` then restarts the loop.
- The ticker is owned by `RootView` (`.task` / `onChange(of: scenePhase)`), not
  by individual tabs, so it survives tab switches.
- A manual pull-to-refresh still calls `refreshAll()`.

### B4. Live presentation

- `FixturesView` gains a pinned "Live now" section above the list when
  `store.live` is non-empty: score in `2 – 1`, clock badge (`67'`, `HT`), and a
  pulsing live dot. Matches are tappable into the existing detail view.
- `TodayView` gains a compact live strip above the report when live fixtures
  exist; it shows up to three matches and a count.
- `FixtureDetailView` shows the score and clock in its header when available.
- If a fixture has no score, nothing is rendered — no placeholder zeros.

## 6. Data model summary

| Table | Change | Migration |
| --- | --- | --- |
| `notifications` | +`attempts`, `next_attempt_at`, `last_error`, `dedupe_key` | ALTER + partial unique index |
| `tracked_bets` | +`dedupe_key`, `tier`, `settlement_notified` | ALTER + partial unique index; backfill `settlement_notified` from `whatsapp_settlement_sent` |
| `fixtures` | +`live_clock`, `espn_league_code` | ALTER |

All migrations are additive and run from `ensure_schema_compatibility` at
startup. No table is rebuilt or dropped.

## 7. API summary

| Endpoint | Change |
| --- | --- |
| `GET /fixtures/live` | New |
| `GET /fixtures`, `GET /fixtures/{id}` | Response gains score and clock fields |
| `POST /ledger/settle` | No WhatsApp parameters; queues Telegram notifications |
| `GET/POST /sentinel/*` | Removed |
| `POST /whatsapp/test` | Removed |

## 8. Test plan

Backend (`pytest`):

- `tests/test_telegram.py`
  - replace `test_deliver_pending_ignores_unlisted_recipient` with a test that
    asserts the row stays pending, records `recipient not allowlisted`, and
    sends nothing;
  - retry backoff and the five-attempt cap;
  - 429 `retry_after` handling;
  - chunk splitting sends every part and marks sent only when all succeed;
  - dedupe: queueing the same key twice inserts one row;
  - settings allowlist with and without the environment override;
  - poll offset persistence and backlog bootstrap;
  - sanitized error reply contains a reference and not the exception text;
  - `/status`, `/pending`, `/retry`, `/sweep`, and `/today` live suffix.

- `tests/test_inplay.py` (new)
  - stubbed ESPN payload maps in-play state, scores, and clock;
  - string scores are parsed, non-numeric scores leave stored values alone;
  - postponed matches keep their status and do not receive scores;
  - unmatched events do not create fixtures;
  - a feed outage returns `refreshed=False` and leaves stored scores unchanged;
  - throttle prevents a second upstream call inside 60 seconds;
  - `fetch_live_scores` still returns the Streamlit dict shape.

- `tests/test_new_features.py`
  - delete the WhatsApp formatting and sentinel endpoint tests;
  - keep a settlement-text test against `format_settlement_message`.

- `tests/test_daily_combos.py` or a new `tests/test_shortlist.py`
  - `build_shortlist` records one ledger row per size with the report tier;
  - a second run records nothing new and queues no second digest;
  - settlement queues exactly one notification per bet.

Manual API checks against the running backend: `/health`, `/fixtures/live`,
`/combos/daily`, `/notifications`, and the queue counters after a forced
delivery with a stub token.

iOS:

- Swift parse check of every edited file.
- `xcodebuild build` must exit 0.
- Simulator: screenshot the splash, today's live strip with a seeded live
  fixture, and the Fixtures "Live now" section; verify `lastUpdated` advances
  after 60 seconds.

Frontend: `tsc --noEmit` and `next build` after the settings-page edit.
Streamlit: `python -m compileall streamlit_app.py dashboard.py` and a start-up
smoke check that the module no longer imports WhatsApp or sentinel. A grep for
`whatsapp|sentinel|WHATSAPP` must return only historical documentation and the
unused `sentinel_state.json` filename.

Full suite must report zero failures before the implementation commit.

## 9. Risks and mitigations

- **ESPN league mapping misses a legacy fixture.** Those fixtures are skipped,
  counted, and shown as unavailable; no score is invented. New ingestion fills
  the code.
- **60-second report rebuild is expensive.** Measured on the running backend:
  `/combos/daily` 0.02 s, `/markets/qualifying` 0.44 s, `/health` 0.02 s. Only
  the active tab refreshes, and failure keeps the previous payload.
- **Deleting the modules breaks the deployed Streamlit app.** Phase A edits both
  dashboards and removes the WhatsApp env vars before the modules are deleted,
  and verifies with `compileall` plus an import grep.
- **The Render service cannot host the bot.** It runs Streamlit, not FastAPI, so
  inbound polling runs only where the API process runs. Documented; deploying
  FastAPI as a separate web service is out of scope.
- **Duplicate polling across processes.** Documented single-process
  requirement; `TELEGRAM_POLLING=off` provides an explicit kill switch.
- **Migration backfill.** `settlement_notified` is only backfilled when the
  column is first added, detected before the ALTER via `PRAGMA table_info`.
- **Splash masks a dead engine.** The 6-second cap guarantees entry, and each
  section reports its own error.
- **Ledger now records 75% exploratory slips.** The new `tier` column keeps them
  attributable, and performance queries can filter by tier later. The live
  default tier is unchanged.

## 10. Delivery order

1. Phase A migrations, queue helper, delivery semantics, polling, commands.
2. Phase A sentinel retirement, ledger/settlement moves, scheduler job,
   Streamlit and dashboard cleanup, deployment env removal, dashboard copy.
3. Phase B backend live scores and endpoint.
4. Phase B store, splash, view wiring, refresh loop, live presentation.
5. Verification, then one implementation commit and push.
