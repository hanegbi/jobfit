"""Each mutating stage function takes the pipeline lock itself, not just
main() - so a scoped/standalone call (a REPL, a scratch script, another
module) is covered too, which is the whole point: the real incident this
guards against was exactly a standalone recompute_stage() call racing a
CLI run, not two CLI invocations."""

import json

import pytest

from jobfit import config, pipeline_lock
from jobfit.scripts import update_jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    return companies_dir


def test_aggregate_to_jobs_v2_raises_busy_when_lock_already_held(isolated, monkeypatch):
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)
    config.PIPELINE_LOCK_PATH.write_text(json.dumps({
        "pid": 999999, "stage": "recompute", "scope": "all",
        "started_at": "x", "argv": [],
    }), encoding="utf-8")

    with pytest.raises(pipeline_lock.PipelineBusy):
        update_jobs.aggregate_to_jobs_v2()


def test_recompute_stage_holds_the_lock_around_its_own_aggregate_call(isolated, monkeypatch):
    """recompute_stage() calls aggregate_to_jobs_v2() internally - that
    nested call must re-enter the SAME lock, not deadlock or double-acquire."""
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    monkeypatch.setattr(update_jobs, "RECOMPUTE_WORKERS", 1)

    update_jobs.recompute_stage()  # must not raise, must not hang

    assert not config.PIPELINE_LOCK_PATH.exists()  # released on exit


def test_scrape_stage_releases_lock_after_completion(isolated, monkeypatch):
    monkeypatch.setattr(update_jobs.ats_fetchers, "make_session", lambda: None)
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    monkeypatch.setattr(update_jobs, "_process_company", lambda *a, **kw: (a[0], 0, 0, True, None))

    update_jobs.scrape_stage({}, {})

    assert not config.PIPELINE_LOCK_PATH.exists()


def test_main_wait_flag_is_parsed(monkeypatch):
    monkeypatch.setattr(update_jobs, "load_companies_to_scrape", lambda: {})
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    monkeypatch.setattr(update_jobs, "scrape_stage", lambda *a, **kw: update_jobs.RunStats())
    monkeypatch.setattr(update_jobs, "load_meta", lambda: {})
    monkeypatch.setattr(update_jobs, "save_meta", lambda meta: None)
    monkeypatch.setattr("sys.argv", ["update_jobs", "--skip-aggregate", "--wait"])

    update_jobs.main()  # must not raise (argparse must accept --wait)
