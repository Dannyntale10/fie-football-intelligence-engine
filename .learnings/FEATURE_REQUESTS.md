# Feature Requests

## [FEAT-20261112-001] per_match_deep_analysis

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Requested Capability
Analyse a single match on its own, with a background check on team news, form, injuries, in-form players and other relevant details — separate from the accumulator builder.

### User Context
Slips are built by screening many fixtures at once. The user also wants to inspect one fixture in depth before committing, including the qualitative context that the probability model cannot see.

### Complexity Estimate
medium

### Suggested Implementation
`app/services/match_analysis.py` returns one dossier: model probabilities per market, de-vigged fair prices and edges, recent form and home/away splits, H2H, squad/news status with an explicit advisory flag, rating freshness, and a ranked verdict. Expose it at `GET /fixtures/{id}/deep-analysis` and render it from the Fixtures tab. Player-level form needs a paid feed; until one exists the field must report "unavailable" rather than invent names.

### Metadata
- Frequency: first_time
- Related Features: combo engine, ai_squad_checker, h2h

### Resolution
- **Resolved**: 2026-11-12T19:40:00+03:00
- **Notes**: `app/services/match_analysis.py` assembles the dossier (market edges, form and venue splits, H2H, advisory team news, explicit risks, ranked best bets) and `GET /fixtures/{id}/deep-analysis` exposes it. New "🧠 Match Deep Dive" tool tab renders it with nested Market edges / Form / H2H / Team news / Risks panels. Player-level form reports `available: False` with the reason instead of inventing names. Seeded λ inputs equal to the model baseline are no longer treated as manual overrides, so provenance stays visible.

---

## [FEAT-20261112-002] self_improving_learning_loop

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Requested Capability
The agent should learn from the losses it makes and become more accurate over time, instead of repeating the same mispriced selections.

### User Context
Walk-forward measurement showed the model losing to the market (Brier 0.5995 vs 0.5663) and a −10.8% ROI over 779 bets. The user wants the engine to improve itself automatically from results.

### Complexity Estimate
complex

### Suggested Implementation
A learning service that (1) harvests leak-free predictions plus settled real legs, (2) fits per-market probability calibration with shrinkage toward the identity, (3) validates on a held-out time slice and only applies an adjustment that measurably reduces log loss, (4) learns per-market edge premiums from realised results, and (5) records everything in a versioned `data/learning.json` that selection reads. Never learn from in-sample fits or from synthetic slips; require minimum sample sizes and report them.

### Metadata
- Frequency: first_time
- Related Features: calibration, backtest, combo gate

### Resolution
- **Resolved**: 2026-11-12T19:40:00+03:00
- **Notes**: `app/services/learning.py` fits per-market Platt scaling on leak-free walk-forward pairs plus settled real legs (synthetic slips excluded), shrinks toward the identity, and applies a market only when held-out log loss improves by ≥0.002. Per-market edge premiums need ≥25 settled legs and are capped at 2.0. `combo.py` applies calibration and premiums in EV mode and reports the learning block; `GET /learning/summary` and `POST /learning/train` expose the loop, with a retrain button in the Engine Scorecard. `data/learning.json` is versioned (`learn-1`) from 3576 walk-forward matches; home_win and away_win calibrated, draw rejected by the validation gate, no edge premiums yet because there are no settled real slips.

---

## [FEAT-20261112-003] daily_betika_combos_with_history_learning

**Logged**: 2026-11-12T23:55:00+03:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Requested Capability
A one-click home-page button that searches the day's bookmaker prices (including Betika), builds 2- and 3-team combos from selections with at least a 75% win probability, explains the report, and replays the same rule on historical data so the bot can learn from the result and improve itself.

### User Context
The user wants a practical daily ticket from real, attributed prices rather than a research table, plus evidence of how the rule has performed and a learning step that is not a claim.

### Complexity Estimate
complex

### Suggested Implementation
Keyless Betika adapter alongside the other public feeds; a daily combo engine that de-vigs attributed 1X2 prices, enforces the 75% per-leg tier, collapses duplicate fixtures and placeholder rows, and returns NO QUALIFIED SELECTIONS FOUND when empty; a chronological historical replay with a holdout; Platt calibration stored only when held-out log loss improves; home-page card with Analyse and Test on history actions.

### Metadata
- Frequency: first_time
- Related Features: bookmaker feeds, calibration, qualification gate

### Resolution
- **Resolved**: 2026-11-12T23:55:00+03:00
- **Notes**: `betika_feed.py` reads Betika's public match JSON. `GET /combos/daily` and `POST /combos/backtest` back the Overview card. The 75% tier is labelled as below the untouched 85% gate. The replay uses single-book prices, one top ticket per size per matchday, and a 30% matchday holdout. The live calibration is fitted on 4,109 settled fixtures and stored in `data/combo_learning.json` (a=1.239, b=0.0589, holdout log loss 0.6428→0.6398). A training-only fit failed its own bar, so the after-learning holdout comparison is withheld rather than shown in-sample.
