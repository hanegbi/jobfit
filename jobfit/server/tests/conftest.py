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
