"""A committed ratings artifact can seed an empty hosted database."""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analytics.artifacts import load_ratings, write_ratings
from app.database import Base
from app.models.team_rating import TeamRating


def test_artifact_round_trip(tmp_path):
    path = tmp_path / "ratings.json"

    # Both halves of the round trip use private databases. The suite shares a
    # single throwaway SQLite file, so exporting the artifact from the shared
    # session would also carry other modules' ratings and the loader count
    # would depend on which tests happened to run first.
    source_engine = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(source_engine)
    source_factory = sessionmaker(bind=source_engine)

    db = source_factory()
    try:
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

        # The loader only seeds an empty table. Point it at its own empty
        # database rather than depending on the shared test database, where
        # other modules legitimately hold ratings.
        engine = create_engine(f"sqlite:///{tmp_path / 'artifact.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        assert load_ratings(path, session_factory=factory) == 1

        seeded = factory()
        try:
            row = seeded.query(TeamRating).filter(
                TeamRating.competition == "artifact league a",
                TeamRating.team_name == "artifact home",
            ).one()
            assert row.attack_rating == 1.25
            assert row.weighted_matches == 12.5
        finally:
            seeded.close()
    finally:
        db.close()
