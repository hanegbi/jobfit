"""One endpoint the front end uses to rescue flags out of this browser's
localStorage, if the old page was ever used on this origin."""

import pytest

from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs
from jobfit.store import state as store_state

NOW = "2026-09-30T10:00:00Z"


@pytest.fixture
def seeded(store_conn):
    store_companies.upsert_company(store_conn, "acme", "Acme")
    store_jobs.upsert_scraped(store_conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "u1"},
        {"id": "j2", "title": "Data Scientist", "url": "u2"},
    ], NOW)
    return store_conn


def test_importing_browser_flags_applies_them(client, seeded):
    res = client.post("/api/state/import", json={
        "jobfit_liked": ["j1"], "jobfit_hidden": ["j2"], "jobfit_sent": [], "jobfit_reached": ["j1"],
    })
    assert res.status_code == 200
    assert res.json()["applied"] == {"liked": 1, "hidden": 1, "sent": 0, "reached_out": 1}
    assert store_state.get_state(seeded, "j1") == {
        "liked": True, "hidden": False, "sent": False, "reached_out": True}


def test_flags_for_jobs_that_are_gone_are_counted_not_fatal(client, seeded):
    body = client.post("/api/state/import", json={"jobfit_liked": ["j1", "vanished"]}).json()
    assert body["applied"]["liked"] == 1 and body["unknown_jobs"] == 1


def test_an_empty_payload_is_fine(client, seeded):
    assert client.post("/api/state/import", json={}).json()["unknown_jobs"] == 0


def test_importing_twice_changes_nothing_the_second_time(client, seeded):
    client.post("/api/state/import", json={"jobfit_liked": ["j1"]})
    client.post("/api/state/import", json={"jobfit_liked": ["j1"]})
    assert store_state.ids_with(seeded, "liked") == {"j1"}
