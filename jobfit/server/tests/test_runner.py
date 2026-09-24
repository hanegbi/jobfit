import json
import threading
import time

from jobfit import config
from jobfit.server import runner


def test_start_run_marks_running_then_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(runner, "_LOGGER_NAMES", [])

    seen_companies = {}

    def _fake_scrape_stage(companies, profiles, force=False, cancel_event=None):
        from jobfit.scripts.update_jobs import RunStats
        seen_companies.update(companies)
        return RunStats(companies_checked=1, companies_skipped=0, new_jobs=2, closed_jobs=0, failures=[])

    monkeypatch.setattr("jobfit.scripts.update_jobs.scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr("jobfit.scripts.update_jobs.recompute_stage", lambda: None)
    monkeypatch.setattr("jobfit.scripts.update_jobs.cv.load_profiles", lambda: {})
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")
    (tmp_path / "companies_career_pages.json").write_text(
        json.dumps({"Acme": "https://acme.com/careers"}), encoding="utf-8"
    )

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
    assert seen_companies == {"Acme": "https://acme.com/careers"}


def test_start_run_scopes_scrape_to_the_given_companies(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(runner, "_LOGGER_NAMES", [])

    seen_companies = {}

    def _fake_scrape_stage(companies, profiles, force=False, cancel_event=None):
        from jobfit.scripts.update_jobs import RunStats
        seen_companies.update(companies)
        return RunStats(companies_checked=len(companies), companies_skipped=0, new_jobs=0, closed_jobs=0, failures=[])

    monkeypatch.setattr("jobfit.scripts.update_jobs.scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr("jobfit.scripts.update_jobs.recompute_stage", lambda: None)
    monkeypatch.setattr("jobfit.scripts.update_jobs.cv.load_profiles", lambda: {})
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")
    (tmp_path / "companies_career_pages.json").write_text(json.dumps({
        "Acme": "https://acme.com/careers",
        "Beta": "https://beta.com/careers",
    }), encoding="utf-8")

    run_id = runner.start_run(force=False, companies=["Beta"])

    for _ in range(50):
        if not runner.is_running():
            break
        time.sleep(0.05)

    assert seen_companies == {"Beta": "https://beta.com/careers"}
    history = runner.get_history()
    assert history[-1]["id"] == run_id
    assert history[-1]["companies"] == ["Beta"]


def test_stop_run_returns_false_when_nothing_is_running():
    assert runner.stop_run() is False


def test_stop_run_sets_the_cancel_event_for_the_active_run():
    event = threading.Event()
    with runner._lock:
        runner._state.update(running=True, run_id="r1", started_at="x", queue=None, cancel_event=event)
    try:
        assert runner.stop_run() is True
        assert event.is_set() is True
        assert runner.status()["stop_requested"] is True
    finally:
        with runner._lock:
            runner._state.update(running=False, run_id=None, started_at=None, queue=None, cancel_event=None)


def test_scrape_stage_receives_the_cancel_event_and_marks_the_run_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(runner, "_LOGGER_NAMES", [])

    received_event = {}

    def _fake_scrape_stage(companies, profiles, force=False, cancel_event=None):
        from jobfit.scripts.update_jobs import RunStats
        received_event["event"] = cancel_event
        cancel_event.set()  # simulate a stop request arriving mid-scrape
        return RunStats(companies_checked=0, companies_skipped=0, new_jobs=0, closed_jobs=0, failures=[])

    monkeypatch.setattr("jobfit.scripts.update_jobs.scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr("jobfit.scripts.update_jobs.recompute_stage", lambda: None)
    monkeypatch.setattr("jobfit.scripts.update_jobs.cv.load_profiles", lambda: {})
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")
    (tmp_path / "companies_career_pages.json").write_text(json.dumps({"Acme": "https://acme.com/careers"}), encoding="utf-8")

    run_id = runner.start_run(force=False)
    for _ in range(50):
        if not runner.is_running():
            break
        time.sleep(0.05)

    assert received_event["event"] is not None
    history = runner.get_history()
    assert history[-1]["id"] == run_id
    assert history[-1]["stopped"] is True


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
