# FIE — Global Football Intelligence & Betting Analysis Platform

A local-first research platform for football fixtures, markets and paper bets.
It is built around one rule: **never present an estimate as a fact**. Every
price keeps its provider and timestamp, every analysis reports what it could
not evaluate, and when nothing clears the qualification gate the platform says
**NO QUALIFIED SELECTIONS FOUND.**

- **Primary market:** BetPawa Uganda. Secondary: SportPesa, Betika
  (betika.com/en-ug) and FANSPORT (fsportug.icu), read from the same public
  JSON their websites use.
- **No paid API is required.** The four bookmaker feeds need no key; optional
  keyed providers are shown as unconfigured until a key exists, never as live.
- **No bets are placed automatically.** Predictions are stored for paper
  trading and evaluation.

## Status — read this first

| Area | State |
| --- | --- |
| Fixtures, ratings, 1X2 analysis | Working with local/imported data |
| Universal market discovery | Working for markets present in `market_odds_json` |
| Accumulators, ranking, CLV, calibration | Working; gated on real settled history |
| Top structure selection (≥85%) | Working: one weighted pick per fixture from real prices |
| Model–market blend (1X2) | Fitted and applied; on current data the market carries the weight |
| Bookmaker feeds (BetPawa, SportPesa, Betika, FANSPORT) | Working keyless; refresh on demand or every 30 min |
| Daily 2–3 team combos (75% tier) | Working: attributed prices, explicit exclusions, NO QUALIFIED SELECTIONS when empty |
| Historical combo replay and calibration | Working on 4,109 settled fixtures; holdout ROI is small-sample |
| Free provider adapters | Implemented; keyed providers need your own free keys |
| Telegram bot | Implemented; requires a bot token and an allowlist |
| Learned edge premiums | Need settled real legs before they exist |
| Profit claims | None. Paper trade 4–8 weeks before trusting any ROI |

The bundled `data/learning.json` and historical sample exist so the pipeline
can be exercised offline. They are **not** evidence of live accuracy.

## Architecture

```
backend/app/
  api/            FastAPI routers: fixtures, predictions, markets, performance, sources, system
  analytics/      Dixon-Coles/Poisson, ratings, calibration, backtest, match dossier
  betting/        selection, pricing, ranking, combo, CLV, settlement, portfolio, PDF slips
  data_sources/   free provider adapters, connectors, seeder, live feed
  market_engine/  catalogue → discovery → registry models → analysis → qualification → value
  models/         SQLAlchemy tables (fixtures, markets, predictions, results, audit)
  tasks/          EAT-scheduled automation (APScheduler)
  notifications/  Telegram queue, delivery, allowlist and inbound polling
frontend/         Next.js 14 + TypeScript + Tailwind + Recharts (9 pages)
database/         PostgreSQL schema + migrations
scripts/          setup, init_db, backtest, provider verification, migrations
```

The API is mounted without a prefix; interactive docs are at `/docs`.
CORS accepts the origins listed in `FRONTEND_ORIGINS`.

Live scores: `GET /fixtures/live` refreshes and returns today's in-play fixtures
with stored scores and clock. The endpoint is throttled to one upstream ESPN
refresh per 60 seconds and never creates a fixture.

## Requirements

- macOS (Linux works too)
- Python 3.9+ (3.11 recommended; `runtime.txt` targets 3.11)
- Node.js 20+ for the dashboard
- Optional: Docker Desktop, PostgreSQL 14+

## Quick start (macOS)

```bash
cd "Betting agent/fie_starter"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env          # defaults work; edit only what you need
python scripts/setup.py       # creates every table in ./fie.db
```

Start the API in one terminal:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --app-dir backend
# http://127.0.0.1:8000/health and http://127.0.0.1:8000/docs
```

Start the dashboard in a second terminal:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm run dev
# http://localhost:3000
```

The dashboard is client-rendered: the API must be running before pages load
data. If it is not, each page shows a connection error instead of fake data.

### Optional historical seed

```bash
python scripts/setup.py --seed
```

This loads `data/historical_epl_sample.csv` and fits initial ratings. It is a
sample for pipeline testing, not a live dataset.

## Configuration

All settings live in `.env` (copy from `.env.example`). The essentials:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | `sqlite:///./fie.db` by default; PostgreSQL supported |
| `FRONTEND_ORIGINS` | Comma-separated browser origins for CORS |
| `MIN_PREDICTION_PROBABILITY` | Qualification floor, default `0.85` |
| `MIN_DATA_QUALITY_SCORE` | Minimum fixture data grade, default `70` |
| `GEMINI_API_KEY` | Optional narrative layer; deterministic fallback without it |
| `API_FOOTBALL_KEY` | Optional free provider |
| `FOOTBALL_DATA_ORG_KEY` | Optional free provider |
| `APIFOOTBALL_COM_KEY` | Optional free provider |
| `ODDS_API_KEY` | Optional free odds provider |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Comma-separated allowlist; empty = bot refuses to start |
| `TELEGRAM_CHAT_ID` | Default destination for scheduler-queued alerts |

Alerts are queued by the scheduler, delivered by the bot with retry/backoff,
and the same account drives inbound commands through the API lifespan poller.
The allowlist can also be stored in `SystemSetting("telegram_allowlist")`;
the env var wins when both are set. With no allowlist the bot stays closed.

The 85% floor and `auto_place_bets` are locked in the Settings page: the API
rejects changes to them. Lowering the gate to fill a report is not supported.

## Database

SQLite needs no setup:

```bash
python scripts/init_db.py
```

PostgreSQL:

```bash
createdb fie
export DATABASE_URL="postgresql+psycopg2://user:pass@localhost:5432/fie"
python scripts/init_db.py          # creates tables from the models
# or, for the reference DDL:
psql "$DATABASE_URL" -f database/schema.sql
python scripts/apply_migrations.py # records/applies database/migrations/*
```

`fie.db` in this repository contains historical local data, including
synthetic paper-trade rows used to exercise the settlement path. Those rows
are excluded from performance reporting and must not be deleted casually.

## Scheduled automation (East Africa Time)

`backend/app/tasks/scheduler.py` starts with the API process and runs:

| Time (EAT) | Job |
| --- | --- |
| 06:00 | Fixture scan |
| 06:30 | Statistics sync |
| 07:00 | Model run |
| 07:30 | Shortlist |
| Every 30 min, 06–23 | Odds refresh |
| Every 15 min, 05–23 | Lineup watch (60–90 min before kickoff) |
| 02:30 | Results and settlement |
| 23:00 | Daily analytics |
| Sunday 23:30 | Blend refit (model–market weights) |

Jobs fail soft: a missing provider logs a skip instead of inventing data.
Because the scheduler is in-process, run a single API worker locally. For
multi-worker deployments, disable the scheduler or move it to a separate
process.

## Telegram bot

```bash
# .env
TELEGRAM_BOT_TOKEN=123456:abc...
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321

source .venv/bin/activate
python -m app.notifications.telegram
```

Commands: `/today`, `/fixtures`, `/singles`, `/accumulators`, `/analyse <id>`,
`/markets <id>`, `/performance`, `/sources`, `/settings`.

Only allowlisted chat IDs are answered. Keys are never echoed back.

## Data providers and price attribution

BetPawa, SportPesa, Betika and FANSPORT are read from their own public JSON
feeds, with no key and no account. Pull them on demand from the Data Sources
page or the API, or let the 30-minute scheduler do it:

```bash
curl -X POST "http://localhost:8000/sources/refresh"
curl -X POST "http://localhost:8000/sources/refresh?max_events=20"   # bounded probe
```

Each run records a `DataProvider` row and a `ProviderSyncLog` entry per feed, so
coverage and failures stay visible. New fixtures are only added for
competitions already tracked in the database; every other match can still
refresh prices on an existing fixture. Configure any free keys and inspect
readiness:

```bash
python scripts/verify_providers.py          # env-only check, no requests
python scripts/verify_providers.py --live   # real request per configured provider
```

Feed limits are honest and bounded: SportPesa exposes a 30-game window,
FANSPORT a 40-game window, and betPawa is paginated. A feed returning nothing
leaves existing prices untouched — it never creates placeholder odds.

Rules enforced by the engine:

- A price carries `bookmaker`, `source` and `observed_at`. Anything estimated
  from de-vigged probabilities is labelled **estimated**, never BetPawa.
- Markets are discovered from the feed actually on offer. The catalogue is a
  naming/normalization layer, not a whitelist of "supported markets".
- A market with no model is reported as unanalysed. It is never described as
  analysed.
- Missing lineups, injuries or xG are reported as data gaps. They are never
  filled with plausible-looking numbers.

## Qualification and accumulators

- A single selection must reach the configured probability floor (85%) and a
  confidence floor; otherwise it is not published.
- Probability and confidence are separate fields. High probability does not
  imply high confidence.
- EV uses de-vigged market prices. No reliable price means no EV claim.
- Accumulators report the joint probability, the worst-leg probability and
  correlation/independence assumptions. Target odds (2.00, 2.50, 3.00) are
  goals, not guarantees.

## Choosing the market structure (≥85%)

`backend/app/betting/selection.py` decides *which* structure to publish for a
fixture, rather than assuming 1X2. Every market the model can price is
considered, but a structure is only eligible when a real bookmaker price
exists for it (`market_odds_json` or the 1X2 columns). Model-derived prices are
labelled `synthetic` and excluded unless the caller opts in.

Two floors are hard-coded and cannot be lowered by an API parameter:

- **probability ≥ 85%** — the published qualification gate;
- **data confidence ≥ 0.60** — ratings-driven or current, cross-verified prices.

Eligible structures are weighted with a probability-first composite: win
probability 60 points (scaled 80%→100%), price edge 15 (capped at 10%), EV 10
(uncapped edge is not rewarded), and data evidence 15. The highest-scoring
structure per fixture is the pick; the next two are kept as `runner_ups` so the
choice can be audited. A verified squad `VETO` falls through to the next
eligible structure; an advisory reading never blocks a priced pick.

De-vigging is per settlement group. Double chance is derived from the de-vigged
1X2 group (`P(1X) = P(H) + P(D)`) instead of being normalised on its own,
because its three outcomes overlap and are not a one-sum set.

The 85% joint-probability accumulator rule and the 2.00 minimum total odds are
rarely satisfied at fair prices. Rather than relax either, the engine publishes
`top_picks` separately and reports `NO QUALIFIED SELECTIONS FOUND.` when no
accumulator survives.

```bash
curl "http://127.0.0.1:8000/portfolio/top-picks"                    # next matchday
curl "http://127.0.0.1:8000/portfolio/top-picks?target_date=all&limit=25"
curl "http://127.0.0.1:8000/portfolio/combos?selection_mode=certainty"
```

The Accumulators page renders the top-picks table above the slip result: rank,
match, structure, selection and runner-ups, price with its source, win
probability with the 85% marker, data confidence, and the weighted score/grade.

## Model–market blending (1X2)

The model is not allowed to overrule the market by default. For the 1X2
settlement group, `backend/app/analytics/blending.py` pools the model and the
de-vigged price in log-odds space:

```
logit(blend) = w · logit(model) / τ + (1 − w) · logit(market)
```

then renormalises the three outcomes. `τ` is a temperature fitted on a
chronological 70/30 train/holdout split, `w` is chosen by log-loss grid search
on the training split, and per-competition weights are shrunk toward the global
weight (`k = 150`). Nothing about the fit looks at the holdout.

- Only 1X2 is blended. Other market families remain pure model output, and a
  price estimated from the model is never blended — only a real de-vigged
  bookmaker price.
- The market prior dominates because the model does not beat it: on the current
  local history the fitted global weight is **0.003** with `τ = 0.50`, and the
  holdout Brier is 0.568666 blended versus 0.568637 for the market alone
  (model alone 0.597938). The report verdict is `blend_matches_market`, which
  is not a win.
- Because the blend does not beat the market out of sample, EV slips stay gated
  off. The gate is not lowered to make the blend look useful.

Refit and inspect:

```bash
source .venv/bin/activate
python scripts/fit_blend.py              # fit, save data/blending.json, print JSON
python scripts/fit_blend.py --no-save    # dry run
curl http://127.0.0.1:8000/evaluation/blend          # saved report
curl -X POST http://127.0.0.1:8000/evaluation/blend  # refit now
```

The scheduler refits weekly (Sunday 23:30 EAT). The Performance page shows the
report, including the near-zero weight, so the result is visible rather than
implied.

## Daily 2–3 team combos and the historical replay

The Overview page has an **Analyse today's slate** button. It builds 2- and
3-team combos from the matches on today's EAT matchday:

- one selection per fixture (the highest de-vigged 1X2 outcome);
- only legs whose de-vigged market probability is **≥ 75%**, an exploratory
  tier that sits *below* the unchanged 85% published gate;
- each leg priced by the exact attributed bookmaker quote, so the price shown is
  the price whose bookmaker, source and capture time are shown;
- feed placeholder rows (`Home (Special bets)`), stale prices, unattributed
  flat columns and near-duplicate fixtures collapsed to a single leg;
- when nothing qualifies the response is `NO QUALIFIED SELECTIONS FOUND.`

The report lists the top combo, the full leg pool, the learning note, and the
rejection counts, so an empty slate explains itself. A feed refresh runs after
the report renders: an unreachable bookmaker slows the refresh, never the
report, and the refresh line reports exactly which feeds responded.

**Test on history** replays the same rule on every settled fixture with stored
prices (single-book `flat` prices by default; `price_mode=best` is also
available). The latest 30% of matchdays is a holdout. A Platt calibration
(`logit p → a·logit p + b`, L2-shrunk) is fitted on a chronological 70/30 split
and stored in `data/combo_learning.json` only when it reduces held-out log
loss; the live engine applies it to every leg. The after-learning holdout is
computed with a calibration fitted on training matchdays only, and is withheld
entirely when that training-only fit does not clear its own bar — so a
published improvement is never in-sample.

```bash
curl "http://127.0.0.1:8000/combos/daily"                    # today's slate, EAT
curl "http://127.0.0.1:8000/combos/daily?min_leg_probability=0.8&competition=UEFA"
curl -X POST http://127.0.0.1:8000/combos/backtest \
     -H 'Content-Type: application/json' \
     -d '{"price_mode": "flat", "limit_per_size": 1}'
curl http://127.0.0.1:8000/combos/backtest                   # last saved report
```

The replay is honest about its limits: one top ticket per size per matchday
(overlapping tickets would double-count legs), historical prices are one
bookmaker trio rather than per-feed attribution, and the holdout is only a few
dozen tickets. On the current 4,109-match history the holdout hit rate is
82.9% over 35 tickets at 63.3% predicted, and all-history ROI is +4.42% — small
samples, not a profit claim.

## Tests and backtests

```bash
source .venv/bin/activate
python -m pytest                 # 196 tests
python scripts/backtest.py --strategy walkforward
python scripts/backtest.py --strategy legacy   # explicitly lookahead-biased
```

The walk-forward backtest refits ratings only on matches played before each
prediction and is the honest estimate. The legacy path is kept for comparison
and is labelled optimistic in its output.

Frontend checks:

```bash
cd frontend
npm run build
npm run start
```

## Docker

```bash
cp .env.example .env
docker compose up --build
# API:       http://localhost:8000/docs
# Dashboard: http://localhost:3000
```

Compose runs PostgreSQL 16, the FastAPI backend and the Next.js frontend. The
data volume `fie_pgdata` persists the database. Telegram polling is not a
separate compose service; run it with `python -m app.notifications.telegram`
where the bot should live.

## Cloud deployment

- **Dashboard:** Vercel (root `frontend/`, set `NEXT_PUBLIC_API_BASE_URL`).
- **API:** any Python host. Build `pip install -r requirements.txt`, start
  `uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port $PORT`, set
  `DATABASE_URL`, `FIE_API_KEY` and `FRONTEND_ORIGINS`. `/health` and the docs
  pages (`/docs`, `/redoc`, `/openapi.json`) are open; every data route expects
  the `X-API-Key` header. Open `/docs`, click **Authorize** and paste the key to
  call the API from a browser.
- **Storage:** the engine uses SQLite. Mount a persistent disk and point
  `DATABASE_URL` at it (`sqlite:////var/data/fie.db`); without a disk each
  redeploy starts from an empty database. `render.yaml` already describes this
  service, the disk and the environment variables.
- **Historical corpus:** the 26-season training set (see `scripts/ingest_history.py`)
  stays in the local database. The trained artifacts it produces —
  `data/calibration.json`, `data/blending.json`, `data/combo_learning.json` —
  are committed, so a fresh host serves the same calibrated model without
  re-importing anything.
- **Legacy Streamlit UI:** `streamlit_app.py` still runs
  (`streamlit run streamlit_app.py`) for local inspection. It is not the
  production service; the Next.js dashboard and the iOS app are.

Never commit `.env`, tokens or keys. Use the host's secret manager.

## iOS app

A native SwiftUI client lives in `ios/` and talks to the same FastAPI backend.

- Open `ios/FIE.xcodeproj` in Xcode 15+ (iOS 17 deployment target) and run.
- It ships five tabs: **Today** (the 2–3 team combos with the Analyse button and
  the two-phase stored-prices → feed-refresh flow), **Fixtures** (with the
  per-match dossier), **Performance**, **Sources** (provider readiness and feed
  refresh) and **Settings**.
- On launch the app shows the FIE splash while eleven sections preload
  concurrently, then refreshes live scores every 60 seconds while it is in the
  foreground. Fixtures pins a Live now card, Today shows a compact live strip,
  and the match detail header shows the score and clock only when the engine
  has them.
- The server address is editable in Settings. The default is the Mac's LAN
  address (`http://192.168.1.165:8001`), which the Simulator and a physical
  iPhone on the same Wi-Fi can both reach, with the backend bound to `0.0.0.0`:
  `.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8001`
  If the Mac's IP changes, update it in Settings (Reset restores the default).
- The app only reads the API. It stores no picks and never places a bet. The
  85% gate, attribution and paper-only rules are identical to the web UI.

## Legacy Streamlit entrypoint

`streamlit_app.py`/`dashboard.py` are the original single-file UI. They import
the same backend packages and remain useful for quick inspection. They are not
required for the platform and can be retired once the Next.js dashboard is the
only interface you use.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `address already in use` on 8000 | Stop the old process or run `--port 8001` and point `NEXT_PUBLIC_API_BASE_URL` at it |
| Dashboard shows "Cannot reach the API" | Start the backend and confirm `/health` in a browser |
| CORS error in the browser console | Add the dashboard origin to `FRONTEND_ORIGINS` and restart the API |
| `No module named app` | Run uvicorn with `--app-dir backend`, or set `PYTHONPATH=backend` |
| Empty pages | Expected with an empty database: import fixtures or configure a provider |
| Telegram bot exits immediately | `TELEGRAM_BOT_TOKEN` missing or `TELEGRAM_ALLOWED_CHAT_IDS` empty |
| `psycopg2` missing | `pip install psycopg2-binary` and use a `postgresql+psycopg2://` URL |

## Limitations

- Free providers have quotas, delays and gaps. Verify current limits yourself.
- Historical prices cover 1X2; other markets may be unbacktestable, so the
  model–market blend is fitted for 1X2 only.
- The fitted 1X2 blend gives the model almost no weight (0.003) because the
  model does not beat the de-vigged market out of sample. That is the honest
  output, and it keeps EV slips switched off.
- The learning loop only learns from settled real legs. With none settled,
  there is no learned edge — by design.
- A top pick means "the highest weighted structure that cleared 85%", not a
  guaranteed winner. At fair prices such picks usually carry negative EV once
  the bookmaker margin is included; the EV column reports that honestly.
- Requiring every leg ≥85%, an 85% joint probability and total odds ≥2.00 makes
  accumulators mathematically rare. The engine therefore shows the qualified
  individual picks instead of loosening any rule.
- The 75% combo replay is measured on a small holdout (tens of tickets). Its ROI
  is an observation about the stored prices, not an expected return.
- A bookmaker host that stops responding slows a feed refresh (the sync retries
  per request) but never blocks the combo report, which renders from stored
  prices first.
- This is research and paper trading. It is not betting advice, and it does not
  guarantee profit.
