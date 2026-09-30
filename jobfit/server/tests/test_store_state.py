"""What the user thinks of each job. The only table a scrape never writes,
and the first thing in this project that is the user's own data rather than
something scraped."""

import pytest

from jobfit.store import companies, db, jobs, state

NOW = "2026-09-30T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Dev", "url": "u1"}], NOW)
    return conn


def test_a_job_with_no_state_row_reads_as_all_false():
    conn = _conn()
    assert state.get_state(conn, "j1") == {"liked": False, "hidden": False, "sent": False, "reached_out": False}


def test_setting_one_flag_leaves_the_others_alone():
    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    state.set_state(conn, "j1", sent=True)
    assert state.get_state(conn, "j1") == {"liked": True, "hidden": False, "sent": True, "reached_out": False}


def test_a_flag_can_be_turned_back_off():
    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    assert state.set_state(conn, "j1", liked=False)["liked"] is False


def test_setting_state_records_when_it_changed():
    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    assert conn.execute("SELECT updated_at FROM job_state WHERE job_id = 'j1'").fetchone()[0] is not None


def test_an_unknown_flag_is_refused():
    conn = _conn()
    with pytest.raises(ValueError):
        state.set_state(conn, "j1", favourite=True)


def test_state_for_a_job_that_does_not_exist_is_refused():
    """A typo'd id must not create an orphan row the UI can never reach."""
    conn = _conn()
    with pytest.raises(KeyError):
        state.set_state(conn, "nope", liked=True)


def test_state_survives_a_rescrape_of_the_job():
    """The user's own data is the one thing a scrape must never touch."""
    conn = _conn()
    state.set_state(conn, "j1", liked=True, sent=True)
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Dev", "url": "u1"}], "2026-10-05T10:00:00Z")
    assert state.get_state(conn, "j1") == {"liked": True, "hidden": False, "sent": True, "reached_out": False}


def test_state_survives_the_job_being_closed():
    """A hidden job that closes stays hidden - the flag is about the user's
    intent, not the listing's status."""
    conn = _conn()
    state.set_state(conn, "j1", hidden=True)
    jobs.upsert_scraped(conn, "acme", [], "2026-10-05T10:00:00Z")
    assert jobs.get_job(conn, "j1")["status"] == "closed"
    assert state.get_state(conn, "j1")["hidden"] is True


def test_ids_with_state_lists_only_flagged_jobs():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j2", "title": "Other", "url": "u2"}], NOW)
    state.set_state(conn, "j1", liked=True)
    assert state.ids_with(conn, "liked") == {"j1"}
    assert state.ids_with(conn, "hidden") == set()
