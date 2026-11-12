export type Fixture = {
  id: number;
  kickoff_utc: string;
  kickoff_eat: string | null;
  home_team: string;
  away_team: string;
  competition: string;
  country: string;
  source: string;
  external_fixture_id: string | null;
  home_odds: number | null;
  draw_odds: number | null;
  away_odds: number | null;
  status: string | null;
  data_quality_score: number | null;
  data_quality_grade: string | null;
  eligible_for_analysis: boolean | null;
};

export type Health = {
  status: string;
  app: string;
  environment: string;
  database: string;
  total_fixtures: number;
  min_prediction_probability: number;
};

export type PerformanceSummary = {
  scope: string;
  period_days: number | null;
  bets: number;
  settled: number;
  pending: number;
  wins: number;
  losses: number;
  voids: number;
  accuracy: number | null;
  stake: number;
  profit: number;
  roi: number | null;
  yield: number | null;
  by_slip_type: Record<
    string,
    {
      bets: number;
      wins: number;
      losses: number;
      voids: number;
      stake: number;
      profit: number;
      roi: number | null;
      accuracy: number | null;
    }
  >;
  note: string;
};

export type Prediction = {
  id: number;
  fixture_id: number | null;
  family: string;
  selection: string;
  line: number | null;
  probability: number | null;
  confidence: number | null;
  fair_odds: number | null;
  ev: number | null;
  qualified: boolean;
  qualification_reason: string | null;
  created_at: string | null;
};

export type QualifiedPolicy = {
  minimum_probability: number;
  minimum_confidence: number;
  message_when_empty: string;
  probabilities_and_confidence_are_separate: boolean;
  never_lowered_to_fill_a_report: boolean;
};

export type SourceEntry = {
  name: string;
  key_env: string | null;
  free_plan: string;
  daily_quota: number | null;
  configured: boolean;
  status?: string;
  quota_used?: number;
  last_sync_at?: string | null;
  notes?: string;
};

export type SourcesResponse = {
  providers: SourceEntry[];
  configured_count: number;
  note: string;
};

export type SourcesRefreshResult = {
  feeds: Record<
    string,
    {
      provider?: string;
      label?: string;
      status?: string;
      error?: string;
    }
  >;
  totals: Record<string, number>;
  synced_at?: string;
};

export type SyncLog = {
  id: number;
  provider_id: number | null;
  entity: string;
  status: string;
  records_written: number;
  request_count: number;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
};

export type SettingsResponse = {
  settings: Record<string, string>;
  editable: string[];
};

export type NotificationRow = {
  id: number;
  channel: string;
  recipient: string | null;
  subject: string | null;
  body: string;
  status: string;
  sent_at: string | null;
  created_at: string | null;
};

export type MarketFamily = {
  family: string;
  label: string;
  category: string;
  settlement_group: string;
  model: string;
  required_stats: string[];
  notes: string | null;
};

export type MarketCatalogue = {
  engine: string;
  qualification_threshold: number;
  families: MarketFamily[];
  by_category: Record<string, string[]>;
  note: string;
};

export type DiscoveryRow = {
  fixture_id: number | null;
  family: string | null;
  category: string | null;
  label: string;
  settlement_group: string;
  selection: string;
  line: number | null;
  price: number;
  model_key: string | null;
  raw_name: string;
  raw_selection: string;
  bookmaker: string;
  source: string;
  captured_at: string | null;
};

export type DiscoveryResponse = {
  fixture_id: number | null;
  source: string;
  captured_at: string | null;
  data_status: string;
  discovered_count: number;
  market_count: number;
  family_count: number;
  family_counts: Record<string, number>;
  families: string[];
  unmodelled_count: number;
  unmodelled: Array<Record<string, unknown>>;
  markets: DiscoveryRow[];
};

export type AnalysisRow = {
  engine_version: string;
  family: string | null;
  label: string;
  category: string | null;
  settlement_group: string;
  selection: string;
  line: number | null;
  model_key: string | null;
  model: string | null;
  raw_name: string;
  raw_selection: string;
  bookmaker: string;
  source: string;
  captured_at: string | null;
  price: number;
  probability: number | null;
  probability_model: number | null;
  blended: boolean;
  blend_weight: number | null;
  blend_temperature: number | null;
  fair_odds: number | null;
  implied_probability: number | null;
  devigged_probability: number | null;
  group_margin: number | null;
  group_selections: number;
  ev: number | null;
  ev_status: string;
  model_status: string;
  data_status: string;
  confidence: number;
  confidence_reasons: string[];
  qualified: boolean;
  qualification_reason: string | null;
};

export type MarketAnalysisResponse = {
  engine_version: string;
  fixture_id: number | null;
  source: string;
  captured_at: string | null;
  data_status: string;
  threshold: number;
  models: Record<string, string>;
  model_states: Record<string, number>;
  discovered_count: number;
  unmodelled: Array<Record<string, unknown>>;
  selections: AnalysisRow[];
  qualified: number;
  evaluated: number;
  message: string;
  blend: {
    fitted: boolean;
    applied: boolean;
    weight: number | null;
    temperature?: number;
    weight_source?: string;
    scope: string;
    competition: string | null;
    verdict?: string | null;
    generated_at?: string | null;
    reason?: string;
    selections_blended: number;
    note: string;
  };
  value_detection: {
    value_count: number;
    entries: Array<Record<string, unknown>>;
    note?: string;
  };
};

export type ComboLeg = {
  fixture_id: number;
  match: string;
  competition: string;
  team: string | null;
  selection: string;
  market_key: string;
  market_type: string;
  odds: number;
  probability: number;
  probability_raw?: number;
  calibrated?: boolean;
  implied_probability?: number;
  edge_percent?: number | null;
  expected_value_percent?: number | null;
  required_edge_percent?: number;
  odds_source: "market" | "synthetic" | string;
  model_source?: string;
  kickoff_utc?: string;
  kickoff_eat: string;
  rank?: number;
  score?: number;
  grade?: string;
};

export type Combo = {
  legs_count: number;
  total_odds: number;
  combined_probability: number;
  avg_leg_probability: number;
  min_leg_probability: number;
  implied_probability: number;
  expected_value_percent: number;
  edge_percent: number;
  kelly_stake_amount: number;
  kelly_stake_percent: number;
  legs: ComboLeg[];
  rank?: number;
  score?: number;
  grade?: string;
};

export type RankedComboRow = {
  rank: number | null;
  rank_score: number | null;
  grade: string | null;
  legs_count: number;
  total_odds: number;
  combined_probability: number;
  expected_value_percent: number;
  edge_percent: number;
  legs: string;
};

export type RankedLegRow = {
  rank: number | null;
  rank_score: number | null;
  grade: string | null;
  match: string;
  selection: string;
  market_type?: string | null;
  odds: number;
  probability: number;
  probability_model?: number | null;
  blended?: boolean | null;
  confidence?: number | null;
  edge_percent: number | null;
  expected_value_percent: number | null;
  odds_source: string;
  structures_considered?: number | null;
  runner_ups?: StructureRunnerUp[];
};

export type StructureRunnerUp = {
  market_key: string;
  market_type: string;
  selection: string;
  odds: number;
  probability: number;
  score: number;
  blended: boolean;
};

export type TopPick = {
  rank: number;
  fixture_id: number;
  match: string;
  competition: string;
  team: string;
  market_key: string;
  market_type: string;
  selection: string;
  odds: number;
  odds_source: "market" | "synthetic" | string;
  probability: number;
  probability_model: number;
  blended: boolean;
  blend_weight: number | null;
  implied_probability: number;
  devigged_probability: number;
  edge_percent: number;
  expected_value_percent: number;
  confidence: number;
  confidence_reasons: string[];
  model_source: string | null;
  score: number;
  grade: string;
  grade_label: string;
  structures_considered: number;
  runner_ups: StructureRunnerUp[];
  kickoff_utc: string | null;
  kickoff_eat: string;
  ai_squad?: Record<string, unknown>;
};

export type TopPicksResponse = {
  version: string;
  method: string;
  scope: string;
  threshold: number;
  min_confidence: number;
  evaluated_fixtures: number;
  priced_fixtures: number;
  estimates_skipped: number;
  rejected: Record<string, number>;
  vetoed_fixtures: number;
  picks: TopPick[];
  qualified: number;
  message: string;
  note: string;
  target_date?: string | null;
  matchday_label?: string;
  matchday_matches_count?: number;
  available_matchdays?: Array<{ date: string; label: string; count: number }>;
  returned?: number;
};

export type ComboResponse = {
  target_date: string | null;
  matchday_label: string;
  matchday_matches_count: number;
  available_matchdays: Array<{ date: string; label: string; count: number }>;
  params: Record<string, number | string | boolean>;
  blocked_reason: string | null;
  odds_policy: { require_market_odds: boolean; synthetic_legs_excluded: number; note: string };
  total_candidate_legs: number;
  total_combos_generated: number;
  best_combo: Combo | null;
  alternatives: Combo[];
  top_picks: TopPicksResponse;
  ranking: {
    version: string;
    method: string;
    combos: RankedComboRow[];
    matches: RankedLegRow[];
  };
  diagnostics: Record<string, number | string>;
};

export type Dossier = {
  version: string;
  generated_at: string;
  fixture: {
    id: number;
    match: string;
    home_team: string;
    away_team: string;
    competition: string;
    country: string;
    kickoff_eat: string;
    kickoff_utc: string | null;
    status: string;
    source: string;
  };
  verdict: {
    headline: string;
    confidence: string;
    model_validated: boolean;
    ratings_fresh: boolean;
    best_bet: Record<string, unknown> | null;
  };
  model: {
    lambdas: Record<string, number> | null;
    lambda_source: string | null;
    rating_meta: Record<string, unknown> | null;
    bookmaker_margin: Record<string, unknown> | null;
    data_quality: Record<string, unknown> | null;
    correct_score_top5: Array<{ score: string; probability: number }>;
  };
  markets: Array<{
    market_key: string;
    market_type: string;
    selection: string;
    team: string | null;
    price: number;
    model_probability: number;
    calibrated_probability: number;
    calibration_applied: boolean;
    fair_probability: number;
    fair_odds: number;
    edge_percent: number;
    expected_value_percent: number;
    is_value: boolean;
  }>;
  best_bets: Array<Record<string, unknown>>;
  form: {
    home: TeamForm | null;
    away: TeamForm | null;
  };
  h2h: {
    total: number;
    home_wins: number;
    draws: number;
    away_wins: number;
    avg_total_goals: number;
    avg_home_goals: number;
    avg_away_goals: number;
    last_5_results: Array<Record<string, unknown>>;
    form_string: string;
  };
  team_news: {
    verdict: string;
    source: string;
    advisory: boolean;
    badge: string;
    injuries: string;
    form_summary: string;
    h2h_summary: string;
    risk_note: string;
  };
  player_form: { available: boolean; reason: string };
  risks: Array<{ code: string; severity: string; detail: string }>;
  learning: {
    active: boolean;
    version: string | null;
    applied_markets: string[];
    calibrated_markets_here: string[];
  };
  sources: Record<string, string>;
};

export type TeamForm = {
  team: string;
  played: number;
  record: string;
  form: string;
  points: number;
  points_per_game: number;
  goals_for_per_match: number;
  goals_against_per_match: number;
  clean_sheet_rate: number;
  failed_to_score_rate: number;
  btts_rate: number;
  matches: Array<Record<string, unknown>>;
  home: Record<string, unknown>;
  away: Record<string, unknown>;
};

export type BlendReport = {
  status: string;
  scope: string;
  method: string;
  verdict: string;
  weights: {
    default: number;
    temperature: number;
    per_competition: Record<string, number>;
  };
  train: {
    n_matches: number;
    n_competitions: number;
    weight: number;
    temperature: number;
    log_loss: number;
    from: string | null;
    to: string | null;
  };
  holdout: {
    n_matches: number;
    n_competitions: number;
    from: string | null;
    to: string | null;
    model: BlendMetrics;
    market: BlendMetrics;
    blend: BlendMetrics;
    skill: {
      brier_improvement_over_market: number;
      log_loss_improvement_over_market: number;
      validated: boolean;
    };
  };
  per_competition: Record<
    string,
    {
      n_train: number;
      n_holdout: number;
      raw_weight: number;
      weight: number;
      model_brier: number | null;
      market_brier: number | null;
      blend_brier: number | null;
    }
  >;
  params: Record<string, number | string>;
  message: string;
  generated_at: string;
};

export type BlendMetrics = {
  n_matches: number;
  brier: number | null;
  log_loss: number | null;
  accuracy_percent: number | null;
};

export type CalibrationReport = {
  verdict: string;
  n_matches: number;
  n_competitions: number;
  model: { brier: number; log_loss: number; ece: number; accuracy_percent: number };
  market: { brier: number; log_loss: number; accuracy_percent: number };
  skill: {
    brier_improvement: number;
    log_loss_improvement: number;
    validated: boolean;
  };
  reliability: Array<{
    bin: string;
    predictions: number;
    mean_confidence: number;
    observed_rate: number;
    gap: number;
  }>;
  per_competition: Record<
    string,
    { n_matches: number; verdict: string; model_brier: number; market_brier: number }
  >;
  generated_at: string;
};

export type DailyComboLeg = {
  fixture_id: number;
  match: string;
  competition: string;
  kickoff_utc: string | null;
  kickoff_eat: string;
  market_key: string;
  market_type: string;
  selection: string;
  odds: number;
  probability: number;
  market_probability: number;
  calibrated: boolean;
  bookmaker: string | null;
  source: string | null;
  source_url: string | null;
  captured_at: string | null;
  price_age_hours: number | null;
};

export type DailyCombo = {
  size: number;
  legs: DailyComboLeg[];
  combined_probability: number;
  total_odds: number;
  implied_probability: number | null;
  expected_value_percent: number | null;
  min_leg_probability: number;
};

export type DailyComboReport = {
  target_date: string;
  matchday_label: string;
  generated_at: string;
  params: {
    min_leg_probability: number;
    combo_sizes: number[];
    max_price_age_hours: number | null;
    competition: string | null;
  };
  tier: {
    label: string;
    probability_source: string;
    published_gate: number;
    below_published_gate: boolean;
    note: string;
  };
  scope: {
    fixtures_on_slate: number;
    fixtures_priced: number;
    qualified_legs: number;
    candidate_legs: number;
    combos_considered: number;
    rejected: Record<string, number>;
  };
  learning: {
    applied: boolean;
    a?: number;
    b?: number;
    n?: number;
    source?: string | null;
  };
  legs: DailyComboLeg[];
  combos: Record<string, DailyCombo[]>;
  best: DailyCombo | null;
  stale_legs: Array<{ match: string; price_age_hours: number }>;
  message: string;
  note: string;
};

export type ComboMetrics = {
  count: number;
  wins?: number;
  losses?: number;
  hit_rate?: number;
  average_predicted_probability?: number;
  calibration_gap?: number;
  brier?: number;
  log_loss?: number;
  total_staked?: number;
  total_returned?: number;
  profit?: number;
  roi_percent?: number | null;
};

export type ComboBacktestReport = {
  version: string;
  generated_at: string;
  status: string;
  message?: string;
  window?: {
    first_matchday: string | null;
    last_matchday: string | null;
    matchdays: number;
    settled_matches: number;
  };
  params: {
    min_leg_probability: number;
    combo_sizes: number[];
    stake: number;
    price_mode: string;
    combos_per_size: number;
    holdout_share: number;
  };
  scope: {
    settled_fixtures: number;
    qualified_legs?: number;
    combos?: number;
    skipped: Record<string, number>;
  };
  legs?: {
    all: ComboMetrics & { buckets?: Array<Record<string, number | string>>; ece?: number };
    holdout: ComboMetrics & { buckets?: Array<Record<string, number | string>>; ece?: number };
  };
  combos?: {
    all: Record<string, ComboMetrics>;
    train: Record<string, ComboMetrics>;
    holdout: Record<string, ComboMetrics>;
  };
  learning: {
    applied: boolean;
    a: number;
    b: number;
    n: number;
    n_train?: number;
    n_validation?: number;
    logloss_before?: number;
    logloss_after?: number;
    improvement?: number;
    reason?: string;
  };
  holdout_after_learning?: Record<string, ComboMetrics> | null;
  holdout_fit?: {
    applied: boolean;
    a: number | null;
    b: number | null;
    n: number | null;
    improvement?: number | null;
    reason?: string;
  };
  notes: string[];
  learning_saved?: Record<string, unknown>;
};
