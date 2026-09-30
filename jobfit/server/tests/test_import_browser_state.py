"""Rescuing the flags that lived in one browser's localStorage."""

from jobfit.scripts.import_browser_state import import_state
from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs
from jobfit.store import state as store_state

NOW = "2026-09-30T10:00:00Z"


def _seed(conn):
    store_companies.upsert_company(conn, "acme", "Acme")
    store_jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "u1"},
        {"id": "j2", "title": "Data Scientist", "url": "u2"},
    ], NOW)


def test_every_flag_is_applied_to_the_right_job(store_conn):
    _seed(store_conn)
    result = import_state(store_conn, {
        "jobfit_liked": ["j1"], "jobfit_sent": ["j1"], "jobfit_hidden": ["j2"], "jobfit_reached": [],
    })
    assert result["applied"] == {"liked": 1, "hidden": 1, "sent": 1, "reached_out": 0}
    assert store_state.get_state(store_conn, "j1") == {
        "liked": True, "hidden": False, "sent": True, "reached_out": False}
    assert store_state.get_state(store_conn, "j2")["hidden"] is True


def test_a_flag_for_a_job_that_no_longer_exists_is_counted_not_fatal(store_conn):
    """Months of flags will include jobs since deduplicated away; losing the
    import over one of them would be absurd."""
    _seed(store_conn)
    result = import_state(store_conn, {"jobfit_liked": ["j1", "gone-job"]})
    assert result["applied"]["liked"] == 1 and result["unknown_jobs"] == 1


def test_an_export_with_missing_or_null_keys_is_fine(store_conn):
    _seed(store_conn)
    assert import_state(store_conn, {"jobfit_liked": None})["applied"]["liked"] == 0
    assert import_state(store_conn, {})["unknown_jobs"] == 0


def test_importing_twice_is_idempotent(store_conn):
    _seed(store_conn)
    import_state(store_conn, {"jobfit_liked": ["j1"]})
    import_state(store_conn, {"jobfit_liked": ["j1"]})
    assert store_state.ids_with(store_conn, "liked") == {"j1"}
