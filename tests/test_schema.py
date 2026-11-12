"""Normalized schema coverage."""
from __future__ import annotations

import app.models  # noqa: F401
from app.database import Base


REQUIRED_TABLES = {
    "countries", "leagues", "seasons", "teams", "players", "fixtures",
    "team_form", "match_statistics", "player_statistics", "injuries",
    "suspensions", "lineups", "data_providers", "provider_sync_logs",
    "bookmaker_catalogue", "bookmakers", "market_catalogue", "available_markets",
    "odds_snapshots", "predictions", "model_versions", "selections",
    "combinations", "combination_selections", "match_results",
    "prediction_settlements", "performance_metrics", "notifications",
    "system_settings",
}


def test_every_required_table_is_registered():
    assert REQUIRED_TABLES <= set(Base.metadata.tables)


def test_prediction_history_is_append_only():
    predictions = Base.metadata.tables["predictions"]
    assert not predictions.columns["created_at"].unique
    assert "fixture_id" in predictions.columns
    settlements = Base.metadata.tables["prediction_settlements"]
    unique_sets = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in settlements.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("prediction_id",) in unique_sets
