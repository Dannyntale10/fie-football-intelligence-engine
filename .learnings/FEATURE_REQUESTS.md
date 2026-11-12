# Feature Requests

## [FEAT-20261112-001] per_match_deep_analysis

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: in_progress
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

---

## [FEAT-20261112-002] self_improving_learning_loop

**Logged**: 2026-11-12T18:05:00+03:00
**Priority**: high
**Status**: in_progress
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
