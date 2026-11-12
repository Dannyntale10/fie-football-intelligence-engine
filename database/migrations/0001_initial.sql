-- Global Football Intelligence & Betting Analysis Platform
-- Reference schema for PostgreSQL. Generated from the SQLAlchemy models;
-- run `python scripts/init_db.py` to create the same tables on any backend.

CREATE TABLE bookmaker_catalogue (
	id SERIAL NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	country VARCHAR(80), 
	website VARCHAR(200), 
	api_available BOOLEAN NOT NULL, 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_bookmaker_catalogue_name ON bookmaker_catalogue (name);

CREATE TABLE combinations (
	id SERIAL NOT NULL, 
	target_odds FLOAT, 
	combined_probability FLOAT, 
	combined_odds FLOAT, 
	ev FLOAT, 
	risk_level VARCHAR(20), 
	stake FLOAT, 
	status VARCHAR(40) NOT NULL, 
	version VARCHAR(40), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_combinations_created_at ON combinations (created_at);
CREATE INDEX ix_combinations_status ON combinations (status);

CREATE TABLE countries (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	code VARCHAR(8), 
	confederation VARCHAR(40), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_countries_name ON countries (name);

CREATE TABLE data_providers (
	id SERIAL NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	kind VARCHAR(40) NOT NULL, 
	base_url VARCHAR(300), 
	free_plan BOOLEAN NOT NULL, 
	requests_per_minute INTEGER, 
	daily_quota INTEGER, 
	quota_used INTEGER NOT NULL, 
	quota_reset_at TIMESTAMP WITH TIME ZONE, 
	last_sync_at TIMESTAMP WITH TIME ZONE, 
	status VARCHAR(40) NOT NULL, 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_data_providers_name ON data_providers (name);

CREATE TABLE fixtures (
	id SERIAL NOT NULL, 
	dedupe_key VARCHAR(300) NOT NULL, 
	kickoff_utc TIMESTAMP WITH TIME ZONE NOT NULL, 
	home_team VARCHAR(160) NOT NULL, 
	away_team VARCHAR(160) NOT NULL, 
	competition VARCHAR(160) NOT NULL, 
	country VARCHAR(100) NOT NULL, 
	source VARCHAR(80) NOT NULL, 
	external_fixture_id VARCHAR(160), 
	home_odds FLOAT, 
	draw_odds FLOAT, 
	away_odds FLOAT, 
	bv_home_odds FLOAT, 
	bv_draw_odds FLOAT, 
	bv_away_odds FLOAT, 
	bw_home_odds FLOAT, 
	bw_draw_odds FLOAT, 
	bw_away_odds FLOAT, 
	ps_home_odds FLOAT, 
	ps_draw_odds FLOAT, 
	ps_away_odds FLOAT, 
	max_home_odds FLOAT, 
	max_draw_odds FLOAT, 
	max_away_odds FLOAT, 
	opening_home_odds FLOAT, 
	opening_draw_odds FLOAT, 
	opening_away_odds FLOAT, 
	odds_updated_at TIMESTAMP WITH TIME ZONE, 
	market_odds_json TEXT, 
	home_score INTEGER, 
	away_score INTEGER, 
	status VARCHAR(40) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_fixture_dedupe_key UNIQUE (dedupe_key)
);

CREATE INDEX ix_fixtures_away_team ON fixtures (away_team);
CREATE INDEX ix_fixtures_competition ON fixtures (competition);
CREATE INDEX ix_fixtures_dedupe_key ON fixtures (dedupe_key);
CREATE INDEX ix_fixtures_home_team ON fixtures (home_team);
CREATE INDEX ix_fixtures_id ON fixtures (id);
CREATE INDEX ix_fixtures_kickoff_utc ON fixtures (kickoff_utc);
CREATE INDEX ix_fixtures_source ON fixtures (source);
CREATE INDEX ix_fixtures_status ON fixtures (status);

CREATE TABLE market_catalogue (
	id SERIAL NOT NULL, 
	family VARCHAR(80) NOT NULL, 
	label VARCHAR(120) NOT NULL, 
	category VARCHAR(40) NOT NULL, 
	settlement_group VARCHAR(80) NOT NULL, 
	model VARCHAR(40), 
	required_stats TEXT, 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_market_catalogue_category ON market_catalogue (category);
CREATE UNIQUE INDEX ix_market_catalogue_family ON market_catalogue (family);

CREATE TABLE model_versions (
	id SERIAL NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	version VARCHAR(40) NOT NULL, 
	trained_at TIMESTAMP WITH TIME ZONE, 
	metrics TEXT, 
	artifact_path VARCHAR(300), 
	active BOOLEAN NOT NULL, 
	notes TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_model_name_version UNIQUE (name, version)
);

CREATE INDEX ix_model_versions_active ON model_versions (active);
CREATE INDEX ix_model_versions_name ON model_versions (name);

CREATE TABLE notifications (
	id SERIAL NOT NULL, 
	channel VARCHAR(40) NOT NULL, 
	recipient VARCHAR(120), 
	subject VARCHAR(200), 
	body TEXT NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	related_type VARCHAR(60), 
	related_id INTEGER, 
	sent_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_notifications_channel ON notifications (channel);
CREATE INDEX ix_notifications_status ON notifications (status);

CREATE TABLE performance_metrics (
	id SERIAL NOT NULL, 
	period_start TIMESTAMP WITH TIME ZONE NOT NULL, 
	period_end TIMESTAMP WITH TIME ZONE NOT NULL, 
	scope VARCHAR(40) NOT NULL, 
	scope_value VARCHAR(120), 
	bets INTEGER NOT NULL, 
	wins INTEGER NOT NULL, 
	losses INTEGER NOT NULL, 
	voids INTEGER NOT NULL, 
	accuracy FLOAT, 
	roi FLOAT, 
	yield_pct FLOAT, 
	clv FLOAT, 
	brier FLOAT, 
	log_loss FLOAT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_performance_metrics_period_end ON performance_metrics (period_end);
CREATE INDEX ix_performance_metrics_period_start ON performance_metrics (period_start);
CREATE INDEX ix_performance_metrics_scope ON performance_metrics (scope);
CREATE INDEX ix_performance_metrics_scope_value ON performance_metrics (scope_value);

CREATE TABLE price_snapshots (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	market_key VARCHAR(60) NOT NULL, 
	price FLOAT NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	slip_id INTEGER, 
	source VARCHAR(60) NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE INDEX ix_price_snapshots_captured_at ON price_snapshots (captured_at);
CREATE INDEX ix_price_snapshots_fixture_id ON price_snapshots (fixture_id);
CREATE INDEX ix_price_snapshots_id ON price_snapshots (id);
CREATE INDEX ix_price_snapshots_kind ON price_snapshots (kind);
CREATE INDEX ix_price_snapshots_market_key ON price_snapshots (market_key);
CREATE INDEX ix_price_snapshots_slip_id ON price_snapshots (slip_id);

CREATE TABLE system_settings (
	id SERIAL NOT NULL, 
	key VARCHAR(120) NOT NULL, 
	value TEXT, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_system_settings_key ON system_settings (key);

CREATE TABLE team_ratings (
	id SERIAL NOT NULL, 
	team_name VARCHAR(160) NOT NULL, 
	competition VARCHAR(160) NOT NULL, 
	attack_rating FLOAT NOT NULL, 
	defense_rating FLOAT NOT NULL, 
	matches_played INTEGER NOT NULL, 
	home_attack_boost FLOAT, 
	home_goals_avg FLOAT, 
	away_goals_avg FLOAT, 
	weighted_matches FLOAT, 
	last_match_at TIMESTAMP WITH TIME ZONE, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_team_competition UNIQUE (team_name, competition)
);

CREATE INDEX ix_team_ratings_competition ON team_ratings (competition);
CREATE INDEX ix_team_ratings_id ON team_ratings (id);
CREATE INDEX ix_team_ratings_team_name ON team_ratings (team_name);

CREATE TABLE tracked_bets (
	id SERIAL NOT NULL, 
	slip_type VARCHAR(60) NOT NULL, 
	matchday_label VARCHAR(120) NOT NULL, 
	total_odds FLOAT NOT NULL, 
	avg_confidence FLOAT NOT NULL, 
	accum_probability FLOAT NOT NULL, 
	stake_amount FLOAT NOT NULL, 
	stake_currency VARCHAR(10) NOT NULL, 
	stake_usd FLOAT NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	profit_loss FLOAT NOT NULL, 
	profit_loss_usd FLOAT NOT NULL, 
	legs_json TEXT NOT NULL, 
	placed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	settled_at TIMESTAMP WITH TIME ZONE, 
	whatsapp_alert_sent BOOLEAN NOT NULL, 
	whatsapp_settlement_sent BOOLEAN NOT NULL, 
	model_version VARCHAR(40), 
	ratings_hash VARCHAR(40), 
	selection_mode VARCHAR(20), 
	PRIMARY KEY (id)
);

CREATE INDEX ix_tracked_bets_id ON tracked_bets (id);
CREATE INDEX ix_tracked_bets_matchday_label ON tracked_bets (matchday_label);
CREATE INDEX ix_tracked_bets_placed_at ON tracked_bets (placed_at);
CREATE INDEX ix_tracked_bets_slip_type ON tracked_bets (slip_type);
CREATE INDEX ix_tracked_bets_status ON tracked_bets (status);

CREATE TABLE bookmakers (
	id SERIAL NOT NULL, 
	catalogue_id INTEGER, 
	name VARCHAR(80) NOT NULL, 
	country VARCHAR(80), 
	active BOOLEAN NOT NULL, 
	priority INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(catalogue_id) REFERENCES bookmaker_catalogue (id)
);

CREATE UNIQUE INDEX ix_bookmakers_name ON bookmakers (name);

CREATE TABLE leagues (
	id SERIAL NOT NULL, 
	country_id INTEGER, 
	name VARCHAR(160) NOT NULL, 
	tier INTEGER, 
	is_cup BOOLEAN NOT NULL, 
	active BOOLEAN NOT NULL, 
	external_ids TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_league_country_name UNIQUE (country_id, name), 
	FOREIGN KEY(country_id) REFERENCES countries (id)
);

CREATE INDEX ix_leagues_country_id ON leagues (country_id);
CREATE INDEX ix_leagues_name ON leagues (name);

CREATE TABLE match_results (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	home_score INTEGER, 
	away_score INTEGER, 
	ht_home_score INTEGER, 
	ht_away_score INTEGER, 
	status VARCHAR(40) NOT NULL, 
	source VARCHAR(80), 
	settled_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE UNIQUE INDEX ix_match_results_fixture_id ON match_results (fixture_id);

CREATE TABLE match_statistics (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	home_possession FLOAT, 
	away_possession FLOAT, 
	home_shots INTEGER, 
	away_shots INTEGER, 
	home_shots_on_target INTEGER, 
	away_shots_on_target INTEGER, 
	home_corners INTEGER, 
	away_corners INTEGER, 
	home_cards INTEGER, 
	away_cards INTEGER, 
	home_fouls INTEGER, 
	away_fouls INTEGER, 
	home_offsides INTEGER, 
	away_offsides INTEGER, 
	home_saves INTEGER, 
	away_saves INTEGER, 
	home_xg FLOAT, 
	away_xg FLOAT, 
	source VARCHAR(80) NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE UNIQUE INDEX ix_match_statistics_fixture_id ON match_statistics (fixture_id);
CREATE INDEX ix_match_statistics_source ON match_statistics (source);

CREATE TABLE predictions (
	id SERIAL NOT NULL, 
	fixture_id INTEGER, 
	model_version_id INTEGER, 
	family VARCHAR(80) NOT NULL, 
	selection VARCHAR(80) NOT NULL, 
	line FLOAT, 
	probability FLOAT, 
	confidence FLOAT, 
	fair_odds FLOAT, 
	ev FLOAT, 
	qualified BOOLEAN NOT NULL, 
	qualification_reason VARCHAR(120), 
	data_quality TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id), 
	FOREIGN KEY(model_version_id) REFERENCES model_versions (id)
);

CREATE INDEX ix_predictions_created_at ON predictions (created_at);
CREATE INDEX ix_predictions_family ON predictions (family);
CREATE INDEX ix_predictions_fixture_id ON predictions (fixture_id);
CREATE INDEX ix_predictions_qualified ON predictions (qualified);

CREATE TABLE provider_sync_logs (
	id SERIAL NOT NULL, 
	provider_id INTEGER NOT NULL, 
	entity VARCHAR(60) NOT NULL, 
	status VARCHAR(40) NOT NULL, 
	records_written INTEGER NOT NULL, 
	request_count INTEGER NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	message TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(provider_id) REFERENCES data_providers (id)
);

CREATE INDEX ix_provider_sync_logs_entity ON provider_sync_logs (entity);
CREATE INDEX ix_provider_sync_logs_provider_id ON provider_sync_logs (provider_id);
CREATE INDEX ix_provider_sync_logs_status ON provider_sync_logs (status);

CREATE TABLE teams (
	id SERIAL NOT NULL, 
	country_id INTEGER, 
	name VARCHAR(160) NOT NULL, 
	short_name VARCHAR(60), 
	aliases TEXT, 
	external_ids TEXT, 
	stadium VARCHAR(160), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_team_country_name UNIQUE (country_id, name), 
	FOREIGN KEY(country_id) REFERENCES countries (id)
);

CREATE INDEX ix_teams_country_id ON teams (country_id);
CREATE INDEX ix_teams_name ON teams (name);

CREATE TABLE available_markets (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	bookmaker_id INTEGER, 
	family VARCHAR(80) NOT NULL, 
	selection VARCHAR(80) NOT NULL, 
	line FLOAT, 
	raw_name VARCHAR(200), 
	raw_selection VARCHAR(120), 
	settlement_group VARCHAR(80), 
	source VARCHAR(80) NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_available_market_selection UNIQUE (fixture_id, bookmaker_id, family, selection, line), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id), 
	FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id)
);

CREATE INDEX ix_available_markets_bookmaker_id ON available_markets (bookmaker_id);
CREATE INDEX ix_available_markets_family ON available_markets (family);
CREATE INDEX ix_available_markets_fixture_id ON available_markets (fixture_id);

CREATE TABLE odds_snapshots (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	bookmaker_id INTEGER, 
	family VARCHAR(80) NOT NULL, 
	selection VARCHAR(80) NOT NULL, 
	line FLOAT, 
	price FLOAT NOT NULL, 
	implied_probability FLOAT, 
	source VARCHAR(80) NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id), 
	FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id)
);

CREATE INDEX ix_odds_snapshots_bookmaker_id ON odds_snapshots (bookmaker_id);
CREATE INDEX ix_odds_snapshots_captured_at ON odds_snapshots (captured_at);
CREATE INDEX ix_odds_snapshots_family ON odds_snapshots (family);
CREATE INDEX ix_odds_snapshots_fixture_id ON odds_snapshots (fixture_id);

CREATE TABLE players (
	id SERIAL NOT NULL, 
	team_id INTEGER, 
	name VARCHAR(160) NOT NULL, 
	position VARCHAR(40), 
	nationality VARCHAR(80), 
	date_of_birth DATE, 
	external_ids TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id)
);

CREATE INDEX ix_players_name ON players (name);
CREATE INDEX ix_players_team_id ON players (team_id);

CREATE TABLE seasons (
	id SERIAL NOT NULL, 
	league_id INTEGER NOT NULL, 
	name VARCHAR(40) NOT NULL, 
	start_date DATE, 
	end_date DATE, 
	is_current BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_season_league_name UNIQUE (league_id, name), 
	FOREIGN KEY(league_id) REFERENCES leagues (id)
);

CREATE INDEX ix_seasons_is_current ON seasons (is_current);
CREATE INDEX ix_seasons_league_id ON seasons (league_id);

CREATE TABLE selections (
	id SERIAL NOT NULL, 
	prediction_id INTEGER, 
	fixture_id INTEGER, 
	family VARCHAR(80) NOT NULL, 
	selection VARCHAR(80) NOT NULL, 
	line FLOAT, 
	probability FLOAT NOT NULL, 
	confidence FLOAT, 
	price FLOAT, 
	bookmaker VARCHAR(80), 
	ev FLOAT, 
	qualified BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(prediction_id) REFERENCES predictions (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_selections_created_at ON selections (created_at);
CREATE INDEX ix_selections_family ON selections (family);
CREATE INDEX ix_selections_fixture_id ON selections (fixture_id);
CREATE INDEX ix_selections_prediction_id ON selections (prediction_id);

CREATE TABLE team_form (
	id SERIAL NOT NULL, 
	team_id INTEGER NOT NULL, 
	fixture_id INTEGER, 
	competition VARCHAR(160), 
	played_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	venue VARCHAR(10), 
	goals_for INTEGER, 
	goals_against INTEGER, 
	result VARCHAR(2), 
	xg_for FLOAT, 
	xg_against FLOAT, 
	shots INTEGER, 
	shots_on_target INTEGER, 
	corners INTEGER, 
	cards INTEGER, 
	source VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_team_form_fixture UNIQUE (team_id, fixture_id), 
	FOREIGN KEY(team_id) REFERENCES teams (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_team_form_fixture_id ON team_form (fixture_id);
CREATE INDEX ix_team_form_played_at ON team_form (played_at);
CREATE INDEX ix_team_form_source ON team_form (source);
CREATE INDEX ix_team_form_team_id ON team_form (team_id);

CREATE TABLE combination_selections (
	id SERIAL NOT NULL, 
	combination_id INTEGER NOT NULL, 
	selection_id INTEGER NOT NULL, 
	probability FLOAT, 
	correlation_adjustment FLOAT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_combination_selection UNIQUE (combination_id, selection_id), 
	FOREIGN KEY(combination_id) REFERENCES combinations (id), 
	FOREIGN KEY(selection_id) REFERENCES selections (id)
);

CREATE INDEX ix_combination_selections_combination_id ON combination_selections (combination_id);
CREATE INDEX ix_combination_selections_selection_id ON combination_selections (selection_id);

CREATE TABLE injuries (
	id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	team_id INTEGER, 
	fixture_id INTEGER, 
	status VARCHAR(40), 
	description TEXT, 
	source VARCHAR(80) NOT NULL, 
	reported_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_injuries_fixture_id ON injuries (fixture_id);
CREATE INDEX ix_injuries_player_id ON injuries (player_id);
CREATE INDEX ix_injuries_team_id ON injuries (team_id);

CREATE TABLE lineups (
	id SERIAL NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	team_id INTEGER NOT NULL, 
	player_id INTEGER NOT NULL, 
	is_starting BOOLEAN NOT NULL, 
	position VARCHAR(40), 
	expected_minutes FLOAT, 
	confirmed BOOLEAN NOT NULL, 
	source VARCHAR(80) NOT NULL, 
	captured_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_lineup_fixture_player UNIQUE (fixture_id, player_id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id), 
	FOREIGN KEY(team_id) REFERENCES teams (id), 
	FOREIGN KEY(player_id) REFERENCES players (id)
);

CREATE INDEX ix_lineups_fixture_id ON lineups (fixture_id);
CREATE INDEX ix_lineups_player_id ON lineups (player_id);
CREATE INDEX ix_lineups_team_id ON lineups (team_id);

CREATE TABLE player_statistics (
	id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	fixture_id INTEGER NOT NULL, 
	minutes INTEGER, 
	goals INTEGER, 
	assists INTEGER, 
	shots INTEGER, 
	shots_on_target INTEGER, 
	yellow_cards INTEGER, 
	red_cards INTEGER, 
	saves INTEGER, 
	source VARCHAR(80) NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_player_fixture_stats UNIQUE (player_id, fixture_id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_player_statistics_fixture_id ON player_statistics (fixture_id);
CREATE INDEX ix_player_statistics_player_id ON player_statistics (player_id);
CREATE INDEX ix_player_statistics_source ON player_statistics (source);

CREATE TABLE prediction_settlements (
	id SERIAL NOT NULL, 
	prediction_id INTEGER, 
	selection_id INTEGER, 
	fixture_id INTEGER, 
	outcome VARCHAR(20) NOT NULL, 
	stake FLOAT, 
	odds FLOAT, 
	profit_units FLOAT, 
	notes TEXT, 
	settled_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_settlement_prediction UNIQUE (prediction_id), 
	FOREIGN KEY(prediction_id) REFERENCES predictions (id), 
	FOREIGN KEY(selection_id) REFERENCES selections (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_prediction_settlements_fixture_id ON prediction_settlements (fixture_id);
CREATE INDEX ix_prediction_settlements_outcome ON prediction_settlements (outcome);
CREATE INDEX ix_prediction_settlements_prediction_id ON prediction_settlements (prediction_id);
CREATE INDEX ix_prediction_settlements_selection_id ON prediction_settlements (selection_id);
CREATE INDEX ix_prediction_settlements_settled_at ON prediction_settlements (settled_at);

CREATE TABLE suspensions (
	id SERIAL NOT NULL, 
	player_id INTEGER NOT NULL, 
	fixture_id INTEGER, 
	reason VARCHAR(120), 
	matches INTEGER, 
	source VARCHAR(80) NOT NULL, 
	reported_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(player_id) REFERENCES players (id), 
	FOREIGN KEY(fixture_id) REFERENCES fixtures (id)
);

CREATE INDEX ix_suspensions_fixture_id ON suspensions (fixture_id);
CREATE INDEX ix_suspensions_player_id ON suspensions (player_id);
