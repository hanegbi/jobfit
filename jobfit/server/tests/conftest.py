"""Guard: no test may touch the real, committed pipeline outputs.

Two tests in this suite once ran the real recompute/referral-merge stages
against the real data directory because a fixture patched some but not
all of the paths they write to. The effect was an empty jobfit.html and
thousands of duplicated referral jobs, once per test run, silently. This
fixture turns that class of leak into an immediate, named failure.

The real paths are captured at import time, before any monkeypatching.
"""

import pytest

from jobfit import config
from jobfit.scripts import update_jobs

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
    conn = db.connect(path)
    db.migrate(conn)
    monkeypatch.setattr(db, "_shared", conn)
    return conn
