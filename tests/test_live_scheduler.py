"""The live job refreshes, records and settles, and is scheduled every five minutes."""
from app.tasks import scheduler


class _FakeSession:
    def close(self):
        return None


def test_live_snapshot_runs_all_three_steps(monkeypatch):
    calls = []

    monkeypatch.setattr(scheduler, "_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.data_sources.inplay.refresh_live_scores",
        lambda db, force=False: calls.append(("refresh", force))
        or {"refreshed": True, "updated": 1},
    )
    monkeypatch.setattr(
        "app.live.ledger.record_live_snapshots",
        lambda db: calls.append(("record", None)) or {"written": 1},
    )
    monkeypatch.setattr(
        "app.live.ledger.settle_live_predictions",
        lambda db: calls.append(("settle", None)) or {"settled": 1},
    )

    result = scheduler.live_snapshot()

    assert [name for name, _ in calls] == ["refresh", "record", "settle"]
    assert calls[0][1] is True
    assert result["snapshots"] == {"written": 1}


def test_schedule_declares_five_minute_live_job():
    assert scheduler.SCHEDULE["live_minutes"] == 5
