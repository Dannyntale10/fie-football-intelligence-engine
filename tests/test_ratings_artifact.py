"""A committed ratings artifact can seed an empty hosted database."""
import json

from app.analytics.artifacts import RATINGS_PATH, load_ratings, write_ratings
from app.database import SessionLocal
from app.models.team_rating import TeamRating


def _cleanup(db):
    # ``upsert_team_rating`` normalises competition names, so the loaded rows
    # are lower-case even though the artifact keeps the training spelling.
    db.query(TeamRating).filter(
        TeamRating.competition.ilike("Artifact League%")
    ).delete(synchronize_session=False)
    db.commit()


def test_artifact_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "ratings.json"
    db = SessionLocal()
    try:
        _cleanup(db)
        db.add(TeamRating(
            team_name="artifact home",
            competition="Artifact League A",
            attack_rating=1.25,
            defense_rating=0.85,
            matches_played=20,
            weighted_matches=12.5,
            home_attack_boost=0.04,
            home_goals_avg=1.9,
            away_goals_avg=1.2,
        ))
        db.add(TeamRating(
            team_name="thin sample",
            competition="Artifact League B",
            attack_rating=1.10,
            defense_rating=1.00,
            matches_played=1,
            weighted_matches=0.4,
        ))
        db.commit()

        meta = write_ratings(
            path,
            db,
            half_life_days=150.0,
            trained_through="2026-11-12T23:00:00+00:00",
            league_averages={"Artifact League A": (1.51, 1.14)},
        )

        payload = json.loads(path.read_text())
        assert meta["content_hash"].startswith("sha256:")
        assert payload["half_life_days"] == 150.0
        assert "artifact home" in payload["competitions"]["Artifact League A"]["teams"]
        assert "thin sample" not in payload["competitions"].get("Artifact League B", {}).get("teams", {})

        _cleanup(db)
        baseline = db.query(TeamRating).count()
        assert baseline == 0, (
            "another test leaked TeamRating rows into the shared database"
        )
        loaded = load_ratings(path)
        assert loaded >= 1
        row = db.query(TeamRating).filter(
            TeamRating.competition == "artifact league a",
            TeamRating.team_name == "artifact home",
        ).one()
        assert row.attack_rating == 1.25
        assert row.weighted_matches == 12.5
    finally:
        _cleanup(db)
        db.close()
