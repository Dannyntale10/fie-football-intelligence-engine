# Learnings

## [LRN-20261112-001] best_practice

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
Tests that use `SessionLocal` write into the real `./fie.db`; the app then displayed synthetic slips as a real 17W/0L ledger.

### Details
`tests/test_services.py`, `test_api.py` and `test_new_features.py` instantiate `app.database.SessionLocal`, which points at `sqlite:///./fie.db`. Every run left fixtures in "Test League", team ratings and tracked bets behind. The dashboard's "Transparent Performance Ledger" then reported a 100% win rate and +114.6% ROI from those generated rows. The accumulation was invisible because each test only asserts on its own row.

### Suggested Action
Force tests onto a throwaway database before any app module is imported (`tests/conftest.py` sets `DATABASE_URL` then calls `create_all`). Never assert ledger totals from a shared database.

### Metadata
- Source: error
- Related Files: tests/conftest.py, app/database.py, app/core/config.py
- Tags: test-isolation, sqlite, ledger
- Pattern-Key: harden.test_isolation

### Resolution
- **Resolved**: 2026-11-12T17:50:00+03:00
- **Notes**: Added `tests/conftest.py` that redirects `DATABASE_URL` to a temp dir before importing `app.*`, then creates the schema. Verified `./fie.db` row counts and mtime unchanged across a full run.

---

## [LRN-20261112-002] best_practice

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Generated/demo data must be excluded from headline performance metrics, not just labelled.

### Details
Even after test isolation, existing synthetic slips remained in the database. Summing `tracked_bets` without provenance produced a false proof of profitability. The same failure mode applies to any future demo or sandbox rows.

### Suggested Action
Filter synthetic records out of every aggregate (win rate, ROI, CLV) and surface the excluded count so the exclusion is visible rather than silent. Reuse one shared notion of "ignored competition" instead of inventing per-view heuristics.

### Metadata
- Source: error
- Related Files: app/services/settlement.py, app/services/clv.py, app/services/ratings.py
- Tags: metrics-integrity, demo-data
- Pattern-Key: harden.metric_data_integrity
- See Also: LRN-20261112-001

### Resolution
- **Resolved**: 2026-11-12T18:00:00+03:00
- **Notes**: `_is_synthetic_bet` excludes test-league slips from ledger stats and CLV; UI reports how many were excluded.

---

## [LRN-20261112-003] best_practice

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
AI-generated match commentary is unverified and must never act as a hard filter.

### Details
`check_squad_and_injuries` asks Gemini for injuries/form from memory. There is no lineup feed, so the answer can be confidently wrong. Selection gates must rely only on measured quantities (price, model probability, calibration evidence).

### Suggested Action
Keep `advisory: True` and `source: ai_unverified|heuristic` on every AI result, never VETO on them, and label them in the UI. Deep-dive views should say explicitly which parts are data-backed and which are advisory.

### Metadata
- Source: conversation
- Related Files: app/services/ai_squad_checker.py, app/services/combo.py
- Tags: ai-honesty, selection-gates
- Pattern-Key: harden.ai_advisory_only

### Resolution
- **Resolved**: 2026-11-12T19:40:00+03:00
- **Notes**: The per-match dossier keeps `advisory: True` and `source` on every AI result, labels the Team news panel as advisory, and states that it is never a hard filter. `player_form` returns `available: False` with an explicit reason instead of naming players without a feed.

---

## [LRN-20261112-004] best_practice

**Logged**: 2026-11-12T19:40:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Calibrating complementary markets independently breaks the probability simplex; renormalize each group after adjusting it.

### Details
The learning loop fits a Platt map per market key. Applied naively, `home_win`, `draw` and `away_win` each moved by their own factor, so the trio stopped summing to 1 — the same for BTTS, over/under pairs, double chance and draw-no-bet. Downstream EV and "fair price" comparisons then mixed incoherent probabilities, which can manufacture or erase edges purely as an artefact of calibration.

### Suggested Action
Calibrate per market, then renormalize each member of a complementary group to the group total, clamping to [0.005, 0.995]. Route every consumer through one shared helper so combo selection and the dossier cannot drift apart.

### Metadata
- Source: error
- Related Files: app/services/learning.py, app/services/combo.py, app/services/match_analysis.py, tests/test_learning.py
- Tags: calibration, probability-coherence, ev-integrity
- Pattern-Key: harden.probability_coherence
- See Also: FEAT-20261112-002

### Resolution
- **Resolved**: 2026-11-12T19:20:00+03:00
- **Notes**: Added `CALIBRATION_GROUPS` and `calibrate_markets()` in `app/services/learning.py`; `combo.py` and `match_analysis.py` both call it. `tests/test_learning.py` asserts group totals land within 1e-3 of 1.0 and that ungrouped markets are left untouched.
