import json
import time

from jobfit import config
from jobfit.server import runner


def test_start_run_marks_running_then_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(runner, "_LOGGER_NAMES", [])

    def _fake_scrape_stage(companies, profiles, force=False):
        from jobfit.scripts.update_jobs import RunStats
        return RunStats(companies_checked=1, companies_skipped=0, new_jobs=2, closed_jobs=0, failures=[])

    monkeypatch.setattr("jobfit.scripts.update_jobs.scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr("jobfit.scripts.update_jobs.recompute_stage", lambda: None)
    monkeypatch.setattr("jobfit.scripts.update_jobs.cv.load_profiles", lambda: {})
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "companies_career_pages.json").write_text("{}", encoding="utf-8")

    run_id = runner.start_run(force=False)
    assert runner.status()["running"] is True
    assert runner.status()["run_id"] == run_id

    for _ in range(50):
        if not runner.is_running():
            break
        time.sleep(0.05)
    assert runner.is_running() is False

    history = runner.get_history()
    assert history[-1]["id"] == run_id
    assert history[-1]["new_jobs"] == 2
    assert history[-1]["finished_at"] is not None


def test_start_run_rejects_a_second_concurrent_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    with runner._lock:
        runner._state.update(running=True, run_id="already-running", started_at="x", queue=None)
    try:
        try:
            runner.start_run(force=False)
            assert False, "expected RuntimeError"
        except RuntimeError as error:
            assert "already-running" in str(error)
    finally:
        with runner._lock:
            runner._state.update(running=False, run_id=None, started_at=None, queue=None)


def test_mark_orphaned_runs_crashed_flags_unfinished_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    config.RUN_HISTORY_PATH.write_text(json.dumps([
        {"id": "a", "started_at": "t1", "finished_at": None, "crashed": False},
        {"id": "b", "started_at": "t2", "finished_at": "t3", "crashed": False},
    ]), encoding="utf-8")

    runner.mark_orphaned_runs_crashed()

    history = runner.get_history()
    assert history[0]["crashed"] is True
    assert history[0]["finished_at"] == "t1"
    assert history[1]["crashed"] is False
