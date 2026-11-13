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

---

## [LRN-20261112-005] knowledge_gap

**Logged**: 2026-11-12T23:10:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Double chance is not a one-sum outcome set, so de-vigging its three prices as a group halves every fair probability and fabricates huge edges.

### Details
`DEVIG_GROUPS` listed `(double_chance_1x, double_chance_x2, double_chance_12)` and divided each implied probability by their sum. But P(1X)+P(X2)+P(12) = 2, so the normaliser was ~2.09 and a 1.03 X2 price came out as a 46% fair probability. The selector then reported a 50% edge (capped) on a 96% pick, inflated its ranking score, and showed the wrong number in the UI. The bookmaker's own 1X2 group de-vigged correctly, which made the error easy to miss.

### Suggested Action
Never normalise overlapping market sets as if they were exhaustive. De-vig the underlying exclusive group (1X2) and derive DC from it: P(1X) = P(H)+P(D), P(X2) = P(D)+P(A), P(12) = P(H)+P(A). Keep pairs/triples that are genuinely exclusive in the de-vig list.

### Metadata
- Source: error
- Related Files: backend/app/betting/pricing.py, backend/app/betting/selection.py, tests/test_combo_odds_policy.py
- Tags: devig, market-structure, ev-integrity
- Pattern-Key: harden.devig_exclusive_sets
- See Also: LRN-20261112-004

### Resolution
- **Resolved**: 2026-11-12T23:10:00+03:00
- **Notes**: Removed the DC group from `DEVIG_GROUPS` and derive DC fair probabilities from the de-vigged 1X2 group in `devigged_probabilities`. Added `test_devig_derives_double_chance_from_the_1x2_group`; live pick edge dropped from a fake 50% to 0.07% and EV reports −0.76%.

---

## [LRN-20261112-006] best_practice

**Logged**: 2026-11-12T23:55:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Replaying history at the best price across bookmakers manufactures an edge; replay one bookmaker's trio and say so.

### Details
The first combo backtest priced every leg with the `max_*` columns (the highest of Bet365, BetVictor, Betway and Pinnacle). The average overround of that synthetic trio was 1.005 and sometimes below 1.0 — an arbitrage that no single account can actually take. ROI looked positive partly because every leg was bought at the best available price. Single-book Bet365 columns carry a 1.054 overround and are the conservative, reachable view. The same class of error appears in the live engine when the displayed price comes from `max_*` while the attribution shown comes from `market_odds_json`: price and bookmaker must come from the same quote.

### Suggested Action
Default historical replays to one stored bookmaker trio and label the price source. If a best-price mode is offered, call it an upper bound that assumes accounts at every book. In the live path, de-vig and price exactly the attributed trio so the odds shown belong to the bookmaker shown.

### Metadata
- Source: error
- Related Files: backend/app/analytics/combo_backtest.py, backend/app/betting/daily_combos.py
- Tags: backtest, price-attribution, roi-integrity
- Pattern-Key: harden.price_attribution
- See Also: LRN-20261112-005

### Resolution
- **Resolved**: 2026-11-12T23:55:00+03:00
- **Notes**: `price_mode` defaults to `flat`; the `best` mode is documented as an upper bound. `build_daily_combos` de-vigs and prices the attributed trio only, and a test asserts the attributed price beats a higher `max_*` column.

---

## [LRN-20261112-007] correction

**Logged**: 2026-11-12T23:55:00+03:00
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
An "after learning" holdout scored with a calibration fitted on all rows — including the holdout — is in-sample and must not be published.

### Details
`run_combo_backtest` fitted the Platt map on every settled row, then re-selected holdout legs with that map to show how learning changed the result. Because the fit had seen the holdout days, the comparison was not out of sample. Worse, when the training-only fit failed its own validation the fallback silently produced a table identical to the baseline, which reads as "learning changed nothing" rather than "not validated".

### Suggested Action
Fit two maps: one on all history for live use, one on training matchdays only for any holdout comparison. Publish the after-learning table only when the training-only fit passes its own gate; otherwise withhold it and state why in the notes.

### Metadata
- Source: error
- Related Files: backend/app/analytics/combo_backtest.py, frontend/app/page.tsx
- Tags: leakage, holdout, calibration
- Pattern-Key: harden.holdout_leakage
- See Also: LRN-20261112-006

### Resolution
- **Resolved**: 2026-11-12T23:55:00+03:00
- **Notes**: The holdout comparison now uses `train_fit`; `holdout_after_learning` is `null` when that fit does not improve held-out log loss by ≥0.002, and a report note explains the omission. The UI hides the table and prints the reason.

---

## [LRN-20261112-008] best_practice

**Logged**: 2026-11-12T23:55:00+03:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
Tests must not depend on trained artefacts in `data/`; seen live, a saved calibration flipped a combo test from 2 to 3 qualified legs.

### Details
`build_daily_combos` loads `data/combo_learning.json`. Once a real backtest wrote a calibration with `a=1.239`, a marginal leg crossed the 0.75 tier inside the test fixture and an assertion expecting two qualified legs failed — the code was correct and the test environment was not isolated. The failure appeared only after the live artefact existed, so it would have passed in CI and broken on a developer machine.

### Suggested Action
Autouse-fixture every module that reads persisted learning and point the path at `tmp_path`. Keep one explicit test that writes a learning file to verify application.

### Metadata
- Source: error
- Related Files: tests/test_daily_combos.py, backend/app/betting/daily_combos.py
- Tags: tests, isolation, calibration
- Pattern-Key: harden.test_isolation
- See Also: LRN-20261112-007

### Resolution
- **Resolved**: 2026-11-12T23:55:00+03:00
- **Notes**: Added an autouse `_isolate_learning` fixture in `tests/test_daily_combos.py`; the explicit saved-learning test overrides the same path with its own file.

---

## [LRN-20261112-009] best_practice

**Logged**: 2026-11-12T23:55:00+03:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
Feed rows need three guards before they represent a fixture: placeholder names, near-duplicate matches and naive timestamps.

### Details
Public feeds returned `Home (Special bets)` vs `Away (Special bets)` as a placeholder row, the same Spain match twice as "Czechia" and "Czech Republic" from different feeds, and kickoff timestamps without a timezone. Each distorts a combo differently: a placeholder invents a fixture, a duplicate offers the same match as two independent legs in one accumulator, and a naive timestamp shifts a 23:30 UTC kickoff into the wrong EAT matchday.

### Suggested Action
Reject names that reduce to home/away or start with `home (`/`away (`; collapse matches that share a normalised team key and kick off within three hours; treat stored naive timestamps as UTC before converting to EAT. Report each exclusion in the response so the empty result is explainable.

### Metadata
- Source: error
- Related Files: backend/app/betting/daily_combos.py, backend/app/analytics/combo_backtest.py
- Tags: data-quality, dedupe, timezone
- Pattern-Key: harden.feed_row_validation

### Resolution
- **Resolved**: 2026-11-12T23:55:00+03:00
- **Notes**: `is_placeholder_team`, `same_match` (normalised team overlap + `SAME_MATCH_WINDOW = 3h`) and `as_utc` enforce the three guards; `scope.rejected` reports `placeholder_fixture` and `duplicate_match`, and the live response shows one leg per match.

## [LRN-20261113-001] knowledge_gap

**Logged**: 2026-11-13T11:20:00+03:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Xcode 27's simulator runs headless behind "DeviceHub": there is no on-screen Simulator window, so AppleScript/CGEvent input injection is unavailable while `simctl` screenshots still work.

### Details
`osascript ... process "Simulator"` fails with -1728 and `process "DeviceHub"` fails with "not allowed assistive access". `CGWindowListCopyWindowInfo` on the active desktop only listed Qoder windows, so the device framebuffer has no window to scroll or click. Visual verification therefore has to be driven entirely from `xcrun simctl` plus persisted app state, not from GUI automation.

### Suggested Action
Verify UI through: (1) `defaults write <bundle> <key>` for `@AppStorage` state, then terminate/relaunch; (2) `simctl io … screenshot`; (3) a tall device (iPad, `TARGETED_DEVICE_FAMILY` includes 2) when content is below the fold. Do not spend time on osascript/cliclick/hardware-key injection on this setup.

### Metadata
- Source: error
- Related Files: ios/FIE/FIEApp.swift, ios/FIE.xcodeproj/project.pbxproj
- Tags: ios, simulator, verification
- Pattern-Key: verify.ios_visual
- See Also: LRN-20261113-002

---

## [LRN-20261113-002] best_practice

**Logged**: 2026-11-13T11:20:00+03:00
**Status**: resolved
**Area**: frontend

### Summary
To verify a card that is below the fold without touch input, temporarily root the app at that screen and shrink the data above it with a truncating proxy, then revert and grep for the hook.

### Details
The discovered-markets card sits under a long priced-markets list, so no screenshot could reach it. Rooting the app via an env var (`FIE_FIXTURE_ID`) into `FixtureDetailView` and pointing `fie.serverURL` at a local stub that truncated the dossier payload to two markets exposed the card. The screen then showed 22 priced / 6 families, matching the live discovery endpoint — proving both decode and render.

### Suggested Action
When a surface is unreachable: add a temporary env-var root in the app entry point, run a tiny local proxy that trims unrelated payload, screenshot, then delete the hook, `grep` for it, rebuild, and reset the simulator defaults you touched. Keep the stub outside the repo.

### Metadata
- Source: conversation
- Related Files: ios/FIE/FIEApp.swift, ios/FIE/Features/FixtureDetailView.swift
- Tags: ios, verification, test-scaffolding
- Pattern-Key: verify.ios_below_fold
- See Also: LRN-20261113-001

---

## [LRN-20261113-003] best_practice

**Logged**: 2026-11-13T11:20:00+03:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
`xcodebuild -quiet` prints nothing on success, so judge the build by exit code, not by log text.

### Details
`xcodebuild ... -quiet build 2>&1 | tail -5` produced no output for a successful build. Piping through `tail` also masks the exit status unless `$?` is captured deliberately.

### Suggested Action
Run `xcodebuild ... -quiet build` and assert `exit=0`; print `** BUILD SUCCEEDED **` from a non-quiet run only when a human needs the banner.

### Metadata
- Source: conversation
- Related Files: ios/FIE.xcodeproj
- Tags: xcodebuild, verification
- Pattern-Key: verify.build_exit_code

---

## [LRN-20261113-004] knowledge_gap

**Logged**: 2026-11-13T11:20:00+03:00
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
Project memory claiming "no Xcode on the dev Mac" was stale: Xcode 27.2 is installed and `xcodebuild` builds the app.

### Details
An earlier session recorded that SwiftUI could not be compiled locally. On 2026-11-13 `xcodebuild -version` reported Xcode 27.2 (27B52) and simulator builds succeeded twice. Acting on the stale note would have skipped type-checking and runtime verification entirely.

### Suggested Action
Before trusting any environment claim from memory, run the one command that proves it (`xcodebuild -version`, `xcrun simctl list devices booted`). Correct or delete the memory entry once contradicted.

### Metadata
- Source: conversation
- Related Files: ios/FIE.xcodeproj
- Tags: environment, memory-staleness
- Pattern-Key: verify.environment_claims
- See Also: LRN-20261113-001

---

## [LRN-20261113-005] knowledge_gap

**Logged**: 2026-11-13T11:45:00+03:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
A booted iOS 26 simulator ignores `simctl spawn defaults write com.<bundle> …` for the app's own `@AppStorage` keys.

### Details
Setting `fie.selectedTab` through the device-wide defaults domain, then through the app data container plist, both failed: `@AppStorage` kept reading the previous value (cfprefsd cache and sandbox separation). Only a fresh install reset it. For one-off verification, an env-var override read at `onAppear` selects the tab reliably; anything written that way must be removed and rebuilt afterwards.

### Suggested Action
For simulator verification of a specific tab, either uninstall/reinstall to reset preferences, or add a temporary env-var override, screenshot, then revert and rebuild. Do not trust `simctl spawn defaults` for app-scoped keys.

### Metadata
- Source: conversation
- Related Files: ios/FIE/FIEApp.swift
- Tags: simulator, appstorage, verification
- Pattern-Key: verify.ios_visual
- See Also: LRN-20261113-001, LRN-20261113-002
