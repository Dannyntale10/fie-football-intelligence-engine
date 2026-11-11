# FIE — Football Intelligence Engine (Starter)

A local-first foundation for a football research and prediction platform.

## What is included
- FastAPI backend with health, fixture, bookmaker and data-quality endpoints.
- Streamlit dashboard for inspecting fixtures and data-quality grades.
- SQLite by default; PostgreSQL can be configured later.
- Five bookmaker connector interfaces (BetPawa, Betika, Fans Sport, SportPesa, 1xBet).
- A fixture normalization layer to deduplicate the same match across sources.
- A transparent data-quality scoring module.
- A baseline Poisson market-probability module for later model development.
- CSV import for fixtures so you can test the system without paid APIs.

## Important limitations
This is the **foundation**, not a live betting-data product. The bookmaker connectors are adapters/stubs: they do not scrape bookmaker websites or bypass access controls. Live integrations require an authorized API/feed or a permitted aggregator. No live fixtures, injuries, odds, lineups or predictions are supplied by default.

The 85% threshold is a qualification rule, not a guarantee. The app does not claim a prediction is reliable until it has been calibrated and validated on unseen historical data.

## Requirements
- macOS
- Python 3.11 or newer recommended
- Internet connection only if installing packages or later connecting data providers

## 1. Unzip and open the project
Unzip `fie_starter.zip`, then open Terminal and move into the project folder:

```bash
cd ~/Downloads/fie_starter
```

If you extracted it somewhere else, adjust the path.

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure environment

```bash
cp .env.example .env
```

The starter runs with SQLite and does not require API keys.

## 4. Start the backend

In Terminal window 1:

```bash
uvicorn app.main:app --reload
```

Open:
- API: http://127.0.0.1:8000
- Interactive API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## 5. Start the dashboard

Open a second Terminal window, enter the same project directory, activate the environment and run:

```bash
cd ~/Downloads/fie_starter
source .venv/bin/activate
streamlit run dashboard.py
```

Streamlit will show a local URL, usually http://localhost:8501.

## 6. Import sample fixtures
The project includes `data/sample_fixtures.csv`. Use the dashboard's **Import CSV** section to load it, or call the API:

```bash
curl -X POST "http://127.0.0.1:8000/fixtures/import-csv" \
  -H "Content-Type: application/json" \
  -d '{"path":"data/sample_fixtures.csv"}'
```

The sample data is fictional test data, not real fixtures.

## 7. CSV format
Required columns:
- `kickoff_utc` — ISO 8601 timestamp, preferably UTC (example `2026-11-20T15:00:00Z`)
- `home_team`
- `away_team`
- `competition`
- `country`
- `source` — bookmaker or data provider identifier

Optional:
- `external_fixture_id`
- `home_odds`
- `draw_odds`
- `away_odds`

## API endpoints
- `GET /health`
- `GET /bookmakers`
- `GET /fixtures`
- `POST /fixtures/import-csv`
- `GET /fixtures/{fixture_id}/quality`
- `GET /markets/baseline?home_lambda=1.6&away_lambda=1.1`

## Project layout

```text
fie_starter/
├── app/
│   ├── connectors/       # Authorized source adapters
│   ├── core/             # Settings
│   ├── models/           # Database models
│   ├── services/         # Fixture, quality and probability logic
│   ├── database.py
│   └── main.py
├── data/
│   └── sample_fixtures.csv
├── tests/
├── dashboard.py
├── requirements.txt
└── .env.example
```

## Next development milestones
1. Verify this foundation runs locally.
2. Select and test a football data provider against representative leagues, including African and lower-division coverage.
3. Add provider-specific fixture and historical-data ingestion.
4. Add player availability, news and lineup feeds.
5. Build historical backtesting and probability calibration before enabling the 85% shortlist.
6. Add licensed bookmaker odds integrations and automated refresh jobs.

## Security
Never commit `.env` or API keys. Do not paste bookmaker passwords into this application. Use authorized feeds and respect provider terms, rate limits and regional restrictions.
