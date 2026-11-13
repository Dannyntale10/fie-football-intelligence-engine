# Continuous learning and in-play prediction — design

Date: 2026-11-13
Status: awaiting operator review
Scope owner: FIE operator
Depends on: `2026-11-13-bot-reliability-and-app-launch-design.md` (live score
persistence and `GET /fixtures/live` already shipped)

## 1. What this builds and why

The engine currently learns nothing between manual refits, and an in-play
match carries a score but no forecast. This design adds, in the order the
operator chose:

1. **Live win probability** for matches in play, derived from the frozen
   pre-match goal expectation and the current score, written to an append-only
   ledger and scored after full time.
2. **A nightly learning pipeline** on GitHub Actions that pulls fresh results
   from keyless sources, refits ratings over a rolling history, and promotes a
   challenger only when a leak-free walk-forward comparison beats the champion
   by a margin.
3. **Live value** is designed as a seam only. No attributed in-play price feed
   exists today, so this phase ships **no live edge and no live bet**, and the
   document states the exact precondition for building it.

Decision log (operator answers): live output = probability first, then
periodic retrain, then live value when a price feed exists; period learning =
automated recurring pulls + refit; promotion = champion/challenger gate;
training host = GitHub Actions nightly; live surface = live board + match
detail on web and iOS; live model = analytical conditional Poisson with a
pluggable intensity multiplier.

## 2. Goals

- Publish a win/draw/away probability for every in-play fixture whose score,
  clock, and pre-match goal expectation are all known.
- Freeze the pre-match prior before kickoff, so a post-kickoff re-quote can
  never rewrite the forecast that was made in play.
- Persist every snapshot, settle it against the full-time score, and report
  whether the conditional forecast actually beats the static pre-match one.
- Refit ratings nightly from fresh results and a rolling multi-season window,
  selecting the recency half-life from data.
- Promote model changes only when a walk-forward comparison wins on the same
  evaluation window as the champion.
- Keep live numbers strictly informational: they never enter the 85% gate,
  a combo, a slip, or a staking recommendation.

## 3. Non-goals and hard rules

- **The 85% qualification gate is not lowered and is not applied to live
  forecasts.** Live probabilities are not selections.
- **No paid APIs.** History comes from `football-data.co.uk`; scores come from
  ESPN; both are keyless and already used.
- **No fabricated data.** A missing score, clock, or goal expectation produces
  no probability, never a guessed one. An unobserved match stays unobserved.
- **No automatic betting.** Every surface is paper-only.
- **Attribution preserved.** Every price keeps its bookmaker, source URL, and
  capture time; the live engine uses prices, never invents them.
- **Append-only history.** Settled snapshots are never rewritten. A corrected
  final score does not mutate prior rows; the correction appears as a new
  result record in the normal results path.
- **No live betting advice without a live price.** Phase 3 ships documentation
  and a provider interface only.
- Single API process. Synthetic ledger rows stay untouched. The hosted
  database's emptiness is a separate data-pipeline task, not solved here.

## 4. What the feeds actually give us

| Input | Source | Status |
| --- | --- | --- |
| Score, clock, state (pre/in/post), half-time | ESPN scoreboard via `backend/app/data_sources/inplay.py` | Present; persisted on the fixture |
| Red cards, lineups, per-minute events | ESPN summary endpoint | **Not fetched; not assumed** |
| Pre-match 1X2 prices per bookmaker | BetPawa, SportPesa, FANSPORT, Betika feeds | Present, attributed, stored in `market_quotes` |
| In-play prices | — | **None. No live edge can be computed.** |
| Historical results + closing 1X2 | `football-data.co.uk` season CSVs | Present locally in `data/historical_cache/` (50 MB, gitignored) |

The live model therefore conditions only on the score and elapsed time. It
does not claim to know about a red card it never saw.

## 5. Phase 1 — Live win probability

### 5.1 Observation time on the fixture

`Fixture` gains `live_observed_at: DateTime(timezone=True) | None`. It is set
to `now` every time `inplay._apply` matches a stored fixture to an ESPN event,
even when score and clock are unchanged, and it counts as a change so it
persists. Migration follows the existing `ensure_schema_compatibility` pattern.

This separates "when we last heard from the feed" from "when the score last
changed"; a half-time match whose clock is frozen at `HT` is still fresh.

### 5.2 Elapsed minute

`backend/app/live/clock.py`:

```python
def elapsed_minutes(clock: str | None, kickoff, status: str, now) -> int | None:
```

- `"67:34"` → `67`; `"45+2"` → `45`; `"90+4"` → `90`; `"HT"` or
  `status == "halftime"` → `45`.
- Missing clock, `status == "live"`: derive from `now - kickoff`, subtract 15
  minutes once the raw elapsed passes 45, clamp to `[1, 90]`.
- Anything outside `live`/`halftime`, or underivable, returns `None`; the
  snapshot still records the gap as `no_clock` with no probabilities.

### 5.3 Frozen pre-match prior

`market_reference.resolve(db, fixture, before=None)` gains an optional
`before` datetime. A `MarketQuote` row captured at or after `before` is
ignored. The fixture-column fallback is accepted only when `odds_updated_at`
is known and earlier than `before`; an unknown capture time cannot be proven
pre-kickoff, so it falls through to ratings. Default `None` preserves every
current caller.

`analyze_fixture(..., as_of=None)` passes `as_of` to the resolver. Live code
calls `analyze_fixture(fixture, db=db, as_of=fixture.kickoff_utc)`, so the
prior is built only from evidence captured before kickoff. `as_of=None` keeps
the analysis endpoint unchanged.

From that prior the snapshot keeps:

- `home_lambda`, `away_lambda`, `lambda_source`, `dixon_coles_rho`;
- the blended pre-match 1X2 probabilities (`all_markets` for the three result
  keys), used later to prove (or disprove) that live conditioning helps.

If `lambda_source` is `fallback` or either lambda is missing, the snapshot is
written with `status = "no_lambda"` and **no probabilities**.

### 5.4 Conditional model

`backend/app/live/engine.py`:

```python
REGULATION_MINUTES = 90
MAX_REMAINING_GOALS = 10
INTENSITY_BOUNDS = (0.5, 2.0)

def outcome_probabilities(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_lambda: float,
    remaining_fraction: float,
    intensity: float = 1.0,
) -> dict[str, float]:
```

Remaining goals are independent Poisson with means
`home_lambda * remaining_fraction * intensity` and the same for away. The
final score is the current score plus the remaining goals; the three outcome
probabilities are accumulated over a `(MAX_REMAINING_GOALS + 1)²` grid and
renormalised for the truncated tail. `remaining_fraction = max(0, (90 - minute) / 90)`.

`intensity` is the extensible seam. The default provider returns `1.0` with
`source = "default"`. A fitted provider reads
`data/live_calibration.json` and returns the stored `k`, clamped to
`INTENSITY_BOUNDS`; a missing or rejected artifact yields `1.0`. The interface:

```python
IntensityProvider = Callable[[LiveState], tuple[float, str]]
```

No red-card, lineup, or shot model is implemented; adding one means adding a
provider, not editing the engine.

### 5.5 Snapshot ledger

New model `backend/app/models/live.py::LivePrediction`, table
`live_predictions`, unique `(fixture_id, minute_bucket)`. It is exported from
`backend/app/models/__init__.py` so `Base.metadata.create_all` creates it on
the next start; `live_observed_at` on the fixture uses the existing
`ALTER TABLE` migration path because SQLite cannot add it through
`create_all`.

| Column | Type | Purpose |
| --- | --- | --- |
| `id` | Integer PK | |
| `fixture_id` | FK fixtures, indexed | |
| `minute` | Integer | Elapsed minute used |
| `minute_bucket` | Integer | `minute // 5 * 5`, the dedupe key; `-1` for `no_clock` |
| `observed_at` | DateTime | Feed observation time |
| `home_score`, `away_score` | Integer | Score at snapshot |
| `lambda_home`, `lambda_away` | Float | Frozen pre-match means |
| `lambda_source` | String(40) | `market_odds` / `ratings` / `fallback` |
| `remaining_home_lambda`, `remaining_away_lambda` | Float | After fraction × k |
| `intensity` | Float | `k` used |
| `intensity_source` | String(20) | `default` / `fitted` |
| `prob_home`, `prob_draw`, `prob_away` | Float, nullable | Null when suppressed |
| `prematch_home`, `prematch_draw`, `prematch_away` | Float, nullable | For comparison |
| `status` | String(20) | `ok` / `no_score` / `no_lambda` / `no_clock` / `stale` |
| `model_version` | String(40) | `live-dc-1` |
| `result` | String(20), nullable | `home_win` / `draw` / `away_win` |
| `extra_home_goals`, `extra_away_goals` | Integer, nullable | Goals after the snapshot |
| `brier`, `log_loss` | Float, nullable | Settled scores |
| `top_pick_correct` | Boolean, nullable | |
| `settled_at` | DateTime, nullable | |

`record_live_snapshots(db, now=None)`:

1. Query fixtures with `status in ("live", "halftime")` and a kickoff in the
   last 3 hours.
2. Compute the minute; a missing minute writes `no_clock` with
   `minute_bucket = -1`, and a missing score writes `no_score` at its real
   bucket — both with null probabilities, so a gap in the feed is visible
   instead of silent.
3. Skip when a `(fixture_id, minute_bucket)` row already exists — one
   analysis per fixture per five minutes, no matter how many clients poll.
4. Build the prior with `as_of=kickoff`, call the engine, insert the row.
5. Catch `IntegrityError` (scheduler and HTTP racing) and continue.
6. Commit once.

`settle_live_predictions(db)`:

1. For `finished` fixtures with both scores and open snapshots, set `result`,
   `extra_*`, `brier = Σ (p_i − y_i)²`, `log_loss = −ln p_result`,
   `top_pick_correct`, `settled_at`.
2. Leave snapshots of postponed/cancelled/abandoned fixtures open and mark the
   fixture status only through the existing inplay path.
3. Never update a settled row.

### 5.6 When it runs

- `GET /fixtures/live` calls `refresh_live_scores`, then
  `record_live_snapshots`, then `settle_live_predictions`, then serves the
  stored rows. A phone polling every 60 seconds therefore maintains the
  ledger.
- APScheduler job `live_snapshot`, `CronTrigger(minute="*/5")`, runs the same
  three functions so the ledger keeps filling when no client is open. It
  force-refreshes scores; the function no-ops when no fixture is in the live
  window, so no ESPN request is made on a quiet night.
- `update_results` (02:30 EAT) ends with `settle_live_predictions(db)`, so a
  match missed while the process was down still settles.

### 5.7 API

`GET /fixtures/live` adds one key per fixture. All existing keys are
unchanged.

```json
"live_prediction": {
  "status": "ok",
  "observed_at": "2026-11-13T16:05:12+00:00",
  "minute": 67,
  "freshness": "fresh",
  "score": {"home": 1, "away": 0},
  "remaining_minutes": 23,
  "probabilities": {"home_win": 0.812, "draw": 0.131, "away_win": 0.057},
  "prematch_probabilities": {"home_win": 0.548, "draw": 0.262, "away_win": 0.190},
  "lambda": {"home": 1.42, "away": 0.88},
  "remaining_lambda": {"home": 0.36, "away": 0.22},
  "intensity": 1.0,
  "intensity_source": "default",
  "lambda_source": "market_odds",
  "model_version": "live-dc-1"
}
```

- `freshness` is computed at read time from `observed_at`: `fresh` under 180
  seconds, `stale` from 180 to 480, `expired` above 480. A `stale` response
  keeps the numbers and flags them; an `expired` response returns
  `probabilities: null` and overrides `status` to `"stale"`, because an old
  score is worse than no number. The override is presentation-only; the stored
  row keeps the status it was written with.
- Suppressed snapshots return the same object with `probabilities: null` and
  `status` set to `no_score`, `no_lambda`, or `no_clock`.

`GET /fixtures/{fixture_id}/live` returns the latest snapshot plus every
snapshot in the last 24 hours for that fixture, in bucket order. 404 when the
fixture does not exist; an empty history is a valid response for a scheduled
match.

`GET /live/calibration?days=90` aggregates settled snapshots:

```json
{
  "generated_at": "...",
  "sample": {"snapshots": 1284, "matches": 71},
  "verdict": "conditional_beats_prematch",
  "overall": {"n": 1284, "brier_live": 0.412, "brier_prematch": 0.503,
              "log_loss_live": 0.688, "log_loss_prematch": 0.791,
              "top_pick_accuracy": 0.744},
  "buckets": [{"bucket": "0-14", "n": 220, "...": "same keys"}],
  "intensity": {"k": 1.0, "source": "default"}
}
```

Buckets are `0-14`, `15-29`, `30-44`, `45-59`, `60-74`, `75-90`. The verdict
is `insufficient_sample` below 200 snapshots, otherwise
`conditional_beats_prematch` when Brier improves by at least 0.002,
`prematch_better` when it regresses by at least 0.002, and `tie` in between.

`GET /live/export?days=90` (API-key protected, not exposed through the
dashboard proxy) returns settled snapshots with `competition`, `kickoff_utc`,
`minute`, lambdas, extra goals, and result. It is the training input for the
intensity fit.

### 5.8 Surfaces

Web:

- New `/live` page: in-play cards with score, clock, three probability bars,
  "as of" time, freshness badge, and the line "Model estimate, not a price.
  Informational only."
- New `/fixtures/[id]` page: fixture header, live panel when in play, the
  bucket history table, and a link to the existing analysis.
- Nav gains "Live" after Overview; `frontend/lib/proxyAllowlist.ts` gains an
  exact read path set containing `/live/calibration` (not a prefix, so
  `/live/export` stays server-only).
- Performance page gains a live calibration panel from `/live/calibration`.

iOS:

- New Live tab (`dot.radiowaves.left.and.right`) listing in-play matches with
  the same probabilities and freshness label, refreshed every 30 seconds
  through `EngineStore`.
- `FixtureDetailView` gains the live panel and bucket history.
- `Models.swift` adds `LivePrediction`; `LiveFixture` decodes the new key
  optionally so an older engine response still decodes.

### 5.9 Guardrails

- Live fixtures are excluded from every selection path. `build_daily_combos`
  and `build_high_odds` already filter `status == "scheduled"` and
  `kickoff_utc > now`; a regression test asserts that a live fixture with a
  huge edge never appears in top picks, high-odds slips, or combos.
- Live probabilities are never written to `predictions`, `selections`,
  `combinations`, `tracked_bets`, or notifications.
- No notification is generated from `live_predictions`.

## 6. Phase 2 — Nightly learning pipeline

### 6.1 Data across periods

The trainer pulls `football-data.co.uk` CSVs (keyless) for the last **10
seasons** (~70k matches, 18 divisions, 12 countries), re-downloading the
current and previous season on every run and reusing the cache for older
ones. Ten seasons is already generous: with a 150-day half-life a match from
four seasons ago carries under 0.1% of a current match's weight, so the older
files add runtime, not information. The full 27-season cache stays available
for research scripts.

The only temporal knob that is learned is `half_life_days`, selected from
`(90, 120, 150, 200)` by the leak-free comparison in 6.3. Everything else
(prior strength, Dixon-Coles rho, iterations) stays fixed, because tuning many
knobs on a few hundred held-out matches is how a model learns noise.

### 6.2 Artifacts

| File | Producer | Consumed by | Committed |
| --- | --- | --- | --- |
| `data/ratings.json` | nightly fit | startup loader → `TeamRating` | yes |
| `data/blending.json` | nightly fit | `blending.blend_status` | yes (existing) |
| `data/learning.json` | settled-leg report | combo and analysis calibration | yes (existing) |
| `data/live_calibration.json` | live snapshot fit | intensity provider | yes, once a fit is accepted |
| `data/model_champion.json` | gate | next night's gate | yes |
| `nightly_report.json` | trainer | Actions artifact only | no (written to the output dir) |

`ratings.json` is pruned to teams with `weighted_matches >= 1.5`, the same
`MIN_WEIGHTED_MATCHES` threshold below which the engine refuses to rate a
team, which keeps the file near 300 KB instead of several megabytes.

```json
{
  "artifact_version": 1,
  "content_hash": "sha256:...",
  "generated_at": "...",
  "trained_through": "2026-11-12T23:00:00+00:00",
  "half_life_days": 150.0,
  "competitions": {
    "Premier League": {
      "league_avg_home": 1.51, "league_avg_away": 1.14,
      "teams": {"arsenal": {"attack": 1.21, "defence": 0.88,
                            "weighted_matches": 12.4, "matches_played": 13,
                            "home_attack_boost": 0.04,
                            "home_goals_avg": 1.9, "away_goals_avg": 1.2,
                            "last_match_at": "2026-11-09T15:00:00+00:00"}}
    }
  }
}
```

`content_hash` is the SHA-256 of the document with that field blanked, so it
identifies the exact ratings without being self-referential.

`model_champion.json` records the configuration, not just a snapshot:

```json
{
  "artifact_version": 1,
  "config": {"half_life_days": 150.0},
  "promoted_at": "...",
  "trained_through": "...",
  "training_seasons": 10,
  "gate": {"champion_brier": 0.601, "challenger_brier": 0.598,
           "holdout_matches": 8120, "window": "2024-11-13..2026-11-12",
           "per_competition": {"Premier League": {"champion": 0.598,
                                                  "challenger": 0.596,
                                                  "n": 760}},
           "reason": "brier_improved"},
  "artifacts": {"ratings.json": "sha256:...", "blending.json": "sha256:..."},
  "git_sha": "..."
}
```

### 6.3 Champion/challenger gate

`backend/app/analytics/model_gate.py`:

```python
def evaluate_config(
    db: Session,
    *,
    half_life_days: float,
    window_days: int = 730,
    refit_days: int = 14,
) -> dict
```

One pass over the evaluation window. Every 14 days the ratings are fit on all
matches strictly before the window, then the window's matches are predicted
with the Dixon-Coles grid. Every grid half-life is evaluated on **the same
windows**, so neither gets fresher data than the other. That is at most four
vectorised fits per window over two years, which fits the 120-minute job
budget with room to spare. Predictions use the de-vigged market only for
comparison, never in the model forecast.

The challenger's half-life is chosen from the grid by the lowest Brier over
the same evaluation, then compared with the champion. Taking the best of four
on the comparison window is a small selection bias; the 0.002 margin and the
per-competition tolerance exist to absorb it.

```
promote iff
  brier_challenger <= brier_champion - 0.002
  and logloss_challenger <= logloss_champion
  and for every competition with n >= 150:
        brier_challenger[comp] <= brier_champion[comp] + 0.005
  and config differs from the champion config
```

Otherwise the champion stays and the report states the failing condition. A
missing `model_champion.json` bootstraps the champion with
`half_life_days = 150` and `reason = "bootstrap"` without a comparison.

The gate compares **configurations**, not stale snapshots: `champion_brier`
is the walk-forward score of the champion's half-life, refitted on the same
schedule as the challenger. The committed `ratings.json` is tied to the
winning configuration by the hashes in the manifest.

On promotion the trainer refits the winning configuration on all data through
yesterday, writes `ratings.json` and `blending.json`, and writes
`model_champion.json` with the gate numbers. `blending.json` is replaced
whenever `fit_report` returns `status: "fitted"`, as it is today; a
`market_beats_blend` verdict is not a failure, it shrinks the model weight
toward zero and the report says so. `learning.json` is only replaced when
`learning.build_report` returns a fitted report; an empty ledger never erases
a valid calibration.

### 6.4 Live intensity fit

`backend/app/live/fit.py` consumes `/live/export` rows:

- Aggregate fitted `k = Σ extra_goals / Σ remaining_lambda` over snapshots,
  clamped to `INTENSITY_BOUNDS`.
- Validate on the later 30% of snapshots: per-snapshot Poisson log loss with
  the fitted `k` must beat `k = 1.0` by at least 0.002.
- Promote `k` only when there are at least 500 settled snapshots, that
  threshold is met, and `abs(k - 1) >= 0.05`; otherwise store
  `status = "insufficient_sample"` or `"rejected"` with `k = 1.0`.
- A failed or empty export leaves the committed file untouched.

This is the honest version of "learn from live matches": one global intensity
parameter, justified only by settled snapshots of this engine's own forecasts,
not by data we do not have.

### 6.5 Workflow

`.github/workflows/nightly-learning.yml`, cron `0 1 * * *` UTC (04:00 EAT,
after the 02:30 results job), plus `workflow_dispatch`. One job on
`ubuntu-latest` with `timeout-minutes: 120`.

1. Checkout, Python 3.11, `pip install -r backend/requirements.txt`.
2. Restore `data/historical_cache` from an Actions cache keyed by season code;
   the trainer force-refreshes the current and previous season.
3. Run `scripts/train_nightly.py --history-seasons 10 --eval-days 730
   --out-dir "$RUNNER_TEMP/learning-out"`. The script sets `DATABASE_URL` to a
   temporary SQLite file before importing app modules, downloads CSVs,
   ingests, fits, evaluates, and writes **candidate** artifacts to the output
   directory. The checkout's `data/` stays untouched, so the test run sees
   exactly what is committed.
4. Optionally download `/live/export` with `FIE_ENGINE_URL` + `FIE_API_KEY`.
   When either secret is absent, the step logs `live export skipped` and the
   run continues.
5. Run the full pytest suite. A failing test blocks promotion.
6. Upload `nightly_report.json` from the output directory as an artifact
   (always).
7. If the trainer promoted a challenger and tests passed: copy the candidate
   artifacts into `data/`, commit on `main` as
   `chore(learning): promote challenger <date> (Brier <x>)`, and push.
   Otherwise the job ends green with the rejection reason in the report.
8. If a commit was pushed: `POST` `RENDER_DEPLOY_HOOK` and fail the job when
   the hook does not accept.

Secrets: `RENDER_DEPLOY_HOOK` (required for deploy), `FIE_ENGINE_URL` and
`FIE_API_KEY` (optional, live export). No secret is written to the repo.
Permissions: `contents: write`, `concurrency: nightly-learning`.

### 6.6 Artifact loading in the engine

`backend/app/analytics/artifacts.py`:

- `load_ratings_artifact(db)` runs at startup. When `ratings.json` exists and
  its `content_hash` differs from `SystemSetting["ratings_artifact_version"]`,
  it replaces `TeamRating` rows for the listed competitions and stores the
  hash.
- A local fit from a full results table still outranks the artifact: the 07:00
  `run_models` job refits from the DB when finished matches exist. On the
  hosted engine, whose results table is empty, the artifact is the prior and
  survives restarts and deploys.
- `live_intensity()` reads `data/live_calibration.json` on each snapshot and
  returns `(k, source)`, defaulting to `(1.0, "default")` on any problem.

## 7. Phase 3 — Live value, deferred

The precondition is an in-play price feed that returns a bookmaker, a market
key, a price, and a capture time for a match currently in play. None of the
current keyless feeds does.

When one exists, the seam is:

```python
class LivePriceProvider(Protocol):
    def quotes(self, fixture) -> list[LiveQuote]: ...
```

`LiveQuote` carries `bookmaker`, `market_key`, `price`, `captured_at`,
`source_url`. Live value is `p_live × price − 1` with `p_live` from the
conditional engine, requires `overround > 1.02` from a single book exactly as
the pre-match path does, and is labelled `live`, `informational`, and
`not_eligible_for_slips`. Until then no route, page, or number is shipped, so
nothing can look like a live tip.

## 8. Failure behaviour

| Failure | Behaviour |
| --- | --- |
| ESPN unreachable | Existing warning, scores untouched, no snapshot, freshness ages to `expired`, probability suppressed |
| Match has no stored score | `no_score`, no probability |
| No coherent pre-match market and no ratings | `no_lambda`, no probability |
| Pre-match prices captured after kickoff | Excluded from the prior; ratings λ used if available |
| Scheduler and HTTP write the same bucket | Unique constraint, one row wins, other insert ignored |
| Match abandoned | Snapshots stay open, excluded from calibration and export |
| Nightly download fails | Run fails; no artifacts change, report uploaded, previous artifacts stay |
| Gate rejects challenger | Champion unchanged, report states the failing condition |
| Live export empty | Intensity stays at 1.0, file unchanged |
| Deploy hook fails | Job fails, code remains committed and deployable manually |

## 9. Testing

New: `tests/test_live_clock.py`, `test_live_engine.py`,
`test_live_snapshots.py`, `test_live_calibration.py`,
`test_model_gate.py`, `test_ratings_artifact.py`, `test_live_guardrails.py`.
Covered behaviours:

- Minute parsing (`67:34`, `45+2`, `90+4`, `HT`, derived, clamped).
- Probabilities sum to 1; 0-0 at minute 0 reproduces the pre-match 1X2 within
  0.01; 2-0 at minute 85 gives home ≥ 0.98; intensity 0.5 halves remaining
  λ; truncation tail is renormalised.
- `as_of=kickoff` ignores post-kickoff quotes; default resolver behaviour is
  unchanged.
- Bucket dedupe, race-safe insert, settle-once, Brier and log-loss arithmetic,
  abandoned exclusion, export shape.
- Calibration buckets and verdict thresholds.
- Gate: identical config, improving challenger, regressing bucket, short
  window, bootstrap, half-life grid selection on synthetic data.
- Artifact round-trip, idempotent load, version mismatch reload,
  prune-below-threshold, empty file does not wipe ratings.
- Live fixtures never appear in top picks, high-odds, combos, or
  notifications.
- The existing suite stays green.

Network calls are never made in tests; ESPN and the CSV source are injected or
monkeypatched, following `tests/test_inplay.py`.

## 10. Rollout

1. Phase 1 behind the existing routes; nothing changes for scheduled fixtures.
2. Verify locally: refresh live feeds, open `/live`, check a snapshot row is
   written once per bucket, finish a match and confirm settlement.
3. Deploy; verify `/fixtures/live` carries `live_prediction` and
   `/live/calibration` reports `insufficient_sample` rather than a number.
4. Phase 2: run `train_nightly.py --dry-run` locally, compare the report with
   the currently committed artifacts, then enable the workflow.
5. First live workflow run must either bootstrap the champion or report why
   not; a rejected challenger is a successful run.
6. iOS: build in the simulator after the web surface is verified.

## 11. Explicitly untouched

- `QUALIFICATION_THRESHOLD = 0.85`, `MIN_DATA_CONFIDENCE`, combo tiers,
  `MIN_MARKET_OVERROUND`, settlement, CLV, and every existing selection rule.
- The Telegram bot's commands and delivery.
- The synthetic ledger rows and the separate purge task.
- Bankroll staking math.
