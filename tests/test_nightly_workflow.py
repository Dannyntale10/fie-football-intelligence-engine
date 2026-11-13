"""The nightly workflow trains on real history, stages narrowly, and redeploys."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-learning.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _steps():
    return _workflow()["jobs"]["train"]["steps"]


def _step_named(fragment):
    matches = [step for step in _steps() if fragment in step.get("name", "")]
    assert len(matches) == 1, f"expected one step matching {fragment!r}"
    return matches[0]


def _path_lines(step):
    return [line.strip() for line in step["with"]["path"].splitlines() if line.strip()]


def test_the_workflow_runs_nightly_and_on_demand():
    doc = _workflow()

    # PyYAML reads the bare `on:` key as the boolean True.
    assert doc[True]["schedule"][0]["cron"] == "0 1 * * *"
    assert "workflow_dispatch" in doc[True]
    assert doc["permissions"]["contents"] == "write"
    assert doc["concurrency"]["group"] == "nightly-learning"
    assert doc["concurrency"]["cancel-in-progress"] is False


def test_the_database_and_history_are_restored_for_the_trainer():
    job = _workflow()["jobs"]["train"]
    assert job["env"]["DATABASE_URL"] == "sqlite:///${{ github.workspace }}/fie.db"

    cache = _step_named("Restore the training database")
    assert cache["uses"] == "actions/cache@v4"
    paths = _path_lines(cache)
    assert "fie.db" in paths
    assert "fie.db-wal" in paths
    assert "fie.db-shm" in paths
    assert "data/historical_cache" in paths
    assert cache["with"]["key"] == "fie-db-${{ github.run_id }}"
    assert cache["with"]["restore-keys"].strip() == "fie-db-"


def test_the_schema_exists_before_the_trainer_opens_the_database():
    runs = [step.get("run", "") for step in _steps()]
    schema = [i for i, run in enumerate(runs) if "scripts/init_db.py" in run]
    train = [i for i, run in enumerate(runs) if "scripts/train_nightly.py" in run]

    assert len(schema) == 1
    assert len(train) == 1
    assert schema[0] < train[0]


def test_the_trainer_writes_to_staging_and_is_never_a_dry_run():
    run = _step_named("Train into staging")["run"]

    assert "--out-dir" in run
    assert "$RUNNER_TEMP/learning-out" in run
    assert "--dry-run" not in run


def test_the_commit_step_stages_only_the_promoted_artifacts():
    step = _step_named("Commit promoted artifacts")
    run = step["run"]

    assert step["id"] == "commit"
    assert "git add -A" not in run
    assert "git add data/" not in run
    assert 'git add "data/$name"' in run
    assert "git diff --cached --quiet" in run


def test_the_deploy_runs_only_after_a_promotion_and_retries():
    step = _step_named("Deploy the engine")

    assert step["if"] == "steps.commit.outputs.changed == 'true'"
    assert step["env"]["RENDER_DEPLOY_HOOK"] == "${{ secrets.RENDER_DEPLOY_HOOK }}"
    assert 'curl -fsS --retry 3 --max-time 30 -X POST "$RENDER_DEPLOY_HOOK"' in step["run"]
    assert re.search(r"https?://", WORKFLOW.read_text()) is None


def test_the_report_upload_always_runs_and_no_public_env_leaks():
    step = _step_named("Upload the nightly report")

    assert step["if"] == "always()"
    assert "NEXT_PUBLIC_" not in WORKFLOW.read_text()
