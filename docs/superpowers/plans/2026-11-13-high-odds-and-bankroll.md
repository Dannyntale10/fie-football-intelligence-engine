# Phase A + C: High-odds 6.0 slips and bankroll planning

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans
> (or subagent-driven-development) to implement this plan task-by-task.
> Steps use checkbox syntax for tracking.

**Goal:** Add a 6.00-total-odds slip mode that only publishes when every leg
is +EV against the blended probability, and a paper-only bankroll calculator
that states the strike rate a target requires.

**Architecture:** Two pure backend modules — `high_odds.py` (reuses the
attributed 1X2 prices, `analyze_fixture` blended probabilities and
`daily_combos` helpers) and `bankroll.py` (pure arithmetic, no DB). Routers
expose `GET /markets/high-odds` and `GET /bankroll/plan|presets`. The
dashboard gains `/high-odds` and `/bankroll` pages plus two nav entries.

**Tech stack:** FastAPI, SQLAlchemy, pytest, Next.js 14 App Router.

**Spec:** this file, approved in conversation on 2026-11-13.

## Global Constraints

- The published 85% gate is never lowered; the high-odds mode has its own
  `min_leg_ev` rule and does not touch `QUALIFICATION_THRESHOLD`.
- Every leg price must keep its bookmaker, source and capture time; an
  unattributed price is excluded, never estimated.
- `NO QUALIFIED SELECTIONS FOUND.` is the exact empty-state message.
- Paper only; no bet placement, no profit claim.
- Value claims require a fitted blend (`probability_blend.applied`); without
  it the mode reports `no_fitted_blend` rather than publishing pure-model EV.
- `data/combo_backtest.json` and `data/combo_sweep.json` stay unstaged.

---

### Task 1: Bankroll math module

**Files:**
- Create: `backend/app/betting/bankroll.py`
- Test: `tests/test_bankroll.py`

- [ ] Write tests for `plan()` (50 @ 1.20 → 60 return / 10 profit / 83.33%
      strike; 10 @ 6.00 → 60 / 50 / 16.67%), `target_profit` sizing, cap
      warning, and `ValueError` on odds ≤ 1 or stake ≤ 0.
- [ ] Write tests for `daily_exposure()` (within cap, above cap).
- [ ] Implement `required_strike_rate`, `plan`, `daily_exposure`,
      `preset_plans`.
- [ ] Run `PYTHONPATH=backend .venv/bin/pytest tests/test_bankroll.py -q`.

### Task 2: High-odds builder

**Files:**
- Create: `backend/app/betting/high_odds.py`
- Test: `tests/test_high_odds.py`

- [ ] Write tests with an injected `analyse` stub: three +EV legs at 1.80
      produce one 3-leg slip at 5.83; negative-EV slate returns the exact
      empty message with `best_leg_ev < 0`; unattributed prices and a missing
      blend are bucketed; duplicate feed rows collapse.
- [ ] Implement `build_high_odds` with `ODDS_BAND = (5.5, 6.5)`, 3–5 legs,
      `MIN_LEG_EV = 0.0`, `MIN_DATA_CONFIDENCE`, one leg per match, disjoint
      slip selection via `select_best_combos`, and `closest_legs` diagnostics.
- [ ] Run `PYTHONPATH=backend .venv/bin/pytest tests/test_high_odds.py -q`.

### Task 3: API routes and proxy allowlist

**Files:**
- Create: `backend/app/api/bankroll.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/api/markets.py`
- Modify: `frontend/lib/proxyAllowlist.ts`
- Test: `tests/test_bankroll.py`, `tests/test_high_odds.py` (route cases)

- [ ] Add `GET /markets/high-odds` with a capped `limit`.
- [ ] Add `GET /bankroll/plan` and `GET /bankroll/presets`.
- [ ] Register the router and allow `/bankroll` in the GET proxy prefixes.
- [ ] Run both test files plus the node allowlist tests.

### Task 4: Dashboard pages

**Files:**
- Modify: `frontend/lib/types.ts`
- Create: `frontend/app/high-odds/page.tsx`
- Create: `frontend/app/bankroll/page.tsx`
- Modify: `frontend/components/Nav.tsx`

- [ ] Add `HighOddsReport`/`HighOddsSlip` and `BankrollPlan` types.
- [ ] Build both pages with the exact empty-state copy and diagnostics.
- [ ] Add nav entries `High Odds 6.0` (H6) and `Bankroll` (BK).
- [ ] Run `npx tsc --noEmit` and `npm run build`.

### Task 5: Verify and commit

- [ ] Full `PYTHONPATH=backend .venv/bin/pytest -q` and node allowlist tests.
- [ ] Restart the single uvicorn, query both endpoints, load both pages.
- [ ] Commit only feature files; leave the two data snapshots unstaged.
