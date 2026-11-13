"""The nightly workflow trains on real history, stages narrowly, and redeploys."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "nightly-learning.yml"

# $RUNNER_TEMP is the shell mirror of the `runner.temp` context.
STAGE = "${{ runner.temp }}/learning-out"

# The only artifacts a nightly promotion may publish. Cache, backtests and
# synthetic reports stay out.
ARTIFACTS = (
    "ratings.json",
    "blending.json",
    "learning.json",
    "live_calibration.json",
    "model_champion.json",
)


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


def _index_of(predicate):
    return [i for i, step in enumerate(_steps()) if predicate(step)]


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


def test_the_cache_is_restored_before_the_trainer_opens_the_database():
    cache = _index_of(lambda step: step.get("name") == "Restore the training database")
    train = _index_of(lambda step: "scripts/train_nightly.py" in step.get("run", ""))

    assert len(cache) == 1
    assert len(train) == 1
    assert cache[0] < train[0]


def test_the_schema_exists_before_the_trainer_opens_the_database():
    schema = _index_of(lambda step: "scripts/init_db.py" in step.get("run", ""))
    train = _index_of(lambda step: "scripts/train_nightly.py" in step.get("run", ""))

    assert len(schema) == 1
    assert len(train) == 1
    assert schema[0] < train[0]


def test_the_trainer_writes_to_staging_and_is_never_a_dry_run():
    run = _step_named("Train into staging")["run"]

    assert '--out-dir "$RUNNER_TEMP/learning-out"' in run
    assert "--dry-run" not in run


def test_staging_is_one_directory_from_training_to_upload():
    trainer = _step_named("Train into staging")
    commit = _step_named("Commit promoted artifacts")
    upload = _step_named("Upload the nightly report")

    assert '--out-dir "$RUNNER_TEMP/learning-out"' in trainer["run"]
    assert commit["env"]["STAGE"] == STAGE
    assert upload["with"]["path"] == STAGE + "/nightly_report.json"


def test_the_commit_step_stages_only_the_promoted_artifacts():
    step = _step_named("Commit promoted artifacts")
    run = step["run"]

    assert step["id"] == "commit"
    assert "git add -A" not in run
    assert "git add data/" not in run
    assert "for name in " + " ".join(ARTIFACTS) + "; do" in run

    # The add sits inside the existence guard: a missing artifact can neither
    # stage a stale file nor abort the step under `bash -e`.
    guard = run.index('if [ -f "$STAGE/$name" ]')
    add = run.index('git add "data/$name"')
    done = run.index("done")
    assert guard < add < done
    assert run.count("git add") == 1
    assert "git diff --cached --quiet" in run


def test_a_promotion_is_pushed_and_survives_a_moved_branch():
    run = _step_named("Commit promoted artifacts")["run"]

    assert "git commit" in run
    assert "git pull --rebase --autostash" in run
    assert "git push" in run
    assert run.index("git commit") < run.index("git pull --rebase") < run.index("git push")


def test_the_deploy_runs_only_after_a_promotion_and_retries():
    step = _step_named("Deploy the engine")
    run = step["run"]

    assert step["if"] == "steps.commit.outputs.changed == 'true'"
    assert step["env"]["RENDER_DEPLOY_HOOK"] == "${{ secrets.RENDER_DEPLOY_HOOK }}"
    assert 'curl -fsS --retry 3 --max-time 30 -X POST "$RENDER_DEPLOY_HOOK"' in run
    assert re.search(r"https?://", run) is None


def test_no_deploy_hook_is_committed_and_no_public_env_leaks():
    text = WORKFLOW.read_text()

    # A hook URL is a credential: it may only arrive through the secret.
    assert re.search(r"https?://\S*(?:render\.com|hooks\.)", text) is None
    assert "NEXT_PUBLIC_" not in text


def test_the_report_upload_always_runs():
    step = _step_named("Upload the nightly report")

    assert step["if"] == "always()"
    assert step["uses"] == "actions/upload-artifact@v4"
