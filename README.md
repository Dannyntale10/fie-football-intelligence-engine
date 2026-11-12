# FIE — Global Football Intelligence & Betting Analysis Platform

A local-first research platform for football fixtures, markets and paper bets.
It is built around one rule: **never present an estimate as a fact**. Every
price keeps its provider and timestamp, every analysis reports what it could
not evaluate, and when nothing clears the qualification gate the platform says
**NO QUALIFIED SELECTIONS FOUND.**

- **Primary market:** BetPawa Uganda. Secondary: SportPesa, Betika, Funspot.
- **No paid API is required.** Free provider adapters are optional; an
  unconfigured provider is shown as unconfigured, never as live.
- **No bets are placed automatically.** Predictions are stored for paper
  trading and evaluation.

## Status — read this first

| Area | State |
| --- | --- |
| Fixtures, ratings, 1X2 analysis | Working with local/imported data |
| Universal market discovery | Working for markets present in `market_odds_json` |
| Accumulators, ranking, CLV, calibration | Working; gated on real settled history |
| Free provider adapters | Implemented; require your own free keys |
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
  betting/        combo, ranking, CLV, settlement, portfolio, PDF slips
  data_sources/   free provider adapters, connectors, seeder, live feed
  market_engine/  catalogue → discovery → registry models → analysis → qualification → value
  models/         SQLAlchemy tables (fixtures, markets, predictions, results, audit)
  tasks/          EAT-scheduled automation (APScheduler)
  notifications/  Telegram polling + WhatsApp alerts
frontend/         Next.js 14 + TypeScript + Tailwind + Recharts (9 pages)
database/         PostgreSQL schema + migrations
scripts/          setup, init_db, backtest, provider verification, migrations
```

The API is mounted without a prefix; interactive docs are at `/docs`.
CORS accepts the origins listed in `FRONTEND_ORIGINS`.

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
| `WHATSAPP_*`, `TWILIO_*` | Optional alert transport |

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

Configure any free keys and inspect readiness:

```bash
python scripts/verify_providers.py          # env-only check, no requests
python scripts/verify_providers.py --live   # real request per configured provider
```

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

## Tests and backtests

```bash
source .venv/bin/activate
python -m pytest                 # 157 tests
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
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, set `DATABASE_URL` and
  `FRONTEND_ORIGINS`.
- **Legacy Streamlit UI:** `streamlit_app.py` remains deployable
  (`streamlit run streamlit_app.py`) and is still referenced by `render.yaml`.
  The Next.js dashboard is the primary interface.

Never commit `.env`, tokens or keys. Use the host's secret manager.

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
- Historical prices cover 1X2; other markets may be unbacktestable.
- The learning loop only learns from settled real legs. With none settled,
  there is no learned edge — by design.
- This is research and paper trading. It is not betting advice, and it does not
  guarantee profit.
