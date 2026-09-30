"""Guard: no test may touch the real, committed pipeline outputs.

Two tests in this suite once ran the real recompute/referral-merge stages
against the real data directory because a fixture patched some but not
all of the paths they write to. The effect was an empty jobfit.html and
thousands of duplicated referral jobs, once per test run, silently. This
fixture turns that class of leak into an immediate, named failure.

The real paths are captured at import time, before any monkeypatching.
"""

import pytest

from fastapi.testclient import TestClient

from jobfit import config
from jobfit.scripts import update_jobs
from jobfit.server import app as app_module
from jobfit.server import runner

_REAL_OUTPUTS = (
    config.DB_PATH,  # the store: a test that forgets store_conn would scribble on real job data
    config.OUTPUT_HTML,
    config.JOBS_OUTPUT_JSON,
    config.JOBS_OUTPUT_META_JSON,
    update_jobs.META_PATH,
    update_jobs.COMPANIES_DIR,  # directory mtime changes on any create/rename inside it
    config.AGGREGATE_CACHE_DIR,
)


def _stamp():
    return [p.stat().st_mtime_ns if p.exists() else None for p in _REAL_OUTPUTS]


@pytest.fixture(autouse=True)
def _real_pipeline_outputs_untouched():
    before = _stamp()
    yield
    after = _stamp()
    changed = [str(p) for p, a, b in zip(_REAL_OUTPUTS, before, after) if a != b]
    assert not changed, f"test wrote to real pipeline output(s) - patch the path(s) in the test: {changed}"


@pytest.fixture
def store_conn(tmp_path, monkeypatch):
    """A migrated, throwaway database wired in as the process-wide store.

    Any test that exercises a pipeline stage needs this: the stages persist
    through jobfit.store.db.shared(), and without the swap they would write
    the real jobfit.db that the guard above protects.
    """
    from jobfit import config
    from jobfit.store import db

    path = tmp_path / "jobfit.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    # recompute_stage also records the scoring engine for the page footer;
    # without this a stage test would write the real companies/_meta.json.
    monkeypatch.setattr(update_jobs, "META_PATH", tmp_path / "_meta.json")
    conn = db.connect(path)
    db.migrate(conn)
    monkeypatch.setattr(db, "_shared", conn)
    return conn


@pytest.fixture
def client(tmp_path, monkeypatch, store_conn):
    # store_conn: the panel queries the store, so every app test needs its own
    # database rather than the real one.
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_DIR", tmp_path / "cvs")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "REFERRAL_UPLOADS_DIR", tmp_path / "referrals")
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")
    # Module-level constants computed at import time from config.ROOT - patching
    # config.ROOT alone doesn't reach these (see update_jobs.py:42-43).
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path / "companies")
    monkeypatch.setattr(update_jobs, "META_PATH", tmp_path / "companies" / "_meta.json")
    monkeypatch.setattr(app_module, "LOCK_PATH", tmp_path / ".server.lock")
    # merge_referral_jobs() checks techmap availability for any newly-seen
    # company - stub it out so referral-upload tests never hit the real
    # techmap cache/network.
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})

    (tmp_path / "companies").mkdir()
    (tmp_path / "companies_career_pages.json").write_text("{}", encoding="utf-8")

    # Ensure no state leaks in from a previous test via the module-level singleton.
    with runner._lock:
        runner._state.update(running=False, run_id=None, started_at=None, queue=None)

    with TestClient(app_module.app) as test_client:
        yield test_client
