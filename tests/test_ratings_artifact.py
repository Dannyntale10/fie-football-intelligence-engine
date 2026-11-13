"""A committed ratings artifact can seed and refresh a hosted database."""
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.analytics.artifacts import load_ratings, write_ratings
from app.database import Base
from app.models.predictions import SystemSetting
from app.models.team_rating import TeamRating


def _private_db(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_source(factory, attack=1.25):
    db = factory()
    try:
        db.add(TeamRating(
            team_name="artifact home",
            competition="Artifact League A",
            attack_rating=attack,
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
    finally:
        db.close()


def _write(path, factory):
    db = factory()
    try:
        return write_ratings(
            path,
            db,
            half_life_days=150.0,
            trained_through="2026-11-12T23:00:00+00:00",
            league_averages={"Artifact League A": (1.51, 1.14)},
        )
    finally:
        db.close()


def test_artifact_round_trip(tmp_path):
    path = tmp_path / "ratings.json"
    source_factory = _private_db(tmp_path, "source.db")
    target_factory = _private_db(tmp_path, "artifact.db")

    # Both halves of the round trip use private databases. The suite shares a
    # single throwaway SQLite file, so exporting the artifact from the shared
    # session would also carry other modules' ratings and the loader count
    # would depend on which tests happened to run first.
    _seed_source(source_factory)
    _write(path, source_factory)

    payload = json.loads(path.read_text())
    assert payload["content_hash"].startswith("sha256:")
    assert payload["half_life_days"] == 150.0
    assert "artifact home" in payload["competitions"]["Artifact League A"]["teams"]
    assert "thin sample" not in payload["competitions"].get("Artifact League B", {}).get("teams", {})

    assert load_ratings(path, session_factory=target_factory) == 1

    target = target_factory()
    try:
        row = target.query(TeamRating).filter(
            TeamRating.competition == "artifact league a",
            TeamRating.team_name == "artifact home",
        ).one()
        assert row.attack_rating == 1.25
        assert row.weighted_matches == 12.5

        stored = target.scalar(
            select(SystemSetting).where(
                SystemSetting.key == "ratings_artifact_version"
            )
        )
        assert stored.value == payload["content_hash"]
    finally:
        target.close()


def test_an_unchanged_artifact_is_not_reloaded(tmp_path):
    path = tmp_path / "ratings.json"
    source_factory = _private_db(tmp_path, "source.db")
    target_factory = _private_db(tmp_path, "artifact.db")

    _seed_source(source_factory)
    _write(path, source_factory)

    assert load_ratings(path, session_factory=target_factory) == 1
    assert load_ratings(path, session_factory=target_factory) == 0

    target = target_factory()
    try:
        row = target.query(TeamRating).filter(
            TeamRating.competition == "artifact league a",
            TeamRating.team_name == "artifact home",
        ).one()
        assert row.attack_rating == 1.25
    finally:
        target.close()


def test_a_new_hash_replaces_the_stored_ratings(tmp_path):
    path = tmp_path / "ratings.json"
    source_factory = _private_db(tmp_path, "source.db")
    target_factory = _private_db(tmp_path, "artifact.db")

    _seed_source(source_factory)
    _write(path, source_factory)
    assert load_ratings(path, session_factory=target_factory) == 1

    source = source_factory()
    try:
        source.query(TeamRating).filter(
            TeamRating.competition == "Artifact League A"
        ).update({"attack_rating": 1.30})
        source.commit()
    finally:
        source.close()

    _write(path, source_factory)
    assert load_ratings(path, session_factory=target_factory) == 1

    target = target_factory()
    try:
        row = target.query(TeamRating).filter(
            TeamRating.competition == "artifact league a",
            TeamRating.team_name == "artifact home",
        ).one()
        assert row.attack_rating == 1.30
    finally:
        target.close()


def test_a_local_refit_outranks_the_artifact(tmp_path):
    path = tmp_path / "ratings.json"
    source_factory = _private_db(tmp_path, "source.db")
    target_factory = _private_db(tmp_path, "artifact.db")

    _seed_source(source_factory)
    _write(path, source_factory)

    target = target_factory()
    try:
        target.add(SystemSetting(key="ratings_artifact_version", value="local"))
        target.add(TeamRating(
            team_name="locally fitted",
            competition="artifact league a",
            attack_rating=9.99,
            defense_rating=1.00,
            matches_played=20,
            weighted_matches=12.5,
        ))
        target.commit()
    finally:
        target.close()

    assert load_ratings(path, session_factory=target_factory) == 0

    target = target_factory()
    try:
        row = target.query(TeamRating).filter(
            TeamRating.competition == "artifact league a",
            TeamRating.team_name == "locally fitted",
        ).one()
        assert row.attack_rating == 9.99
    finally:
        target.close()
