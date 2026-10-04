import sqlite3

import pytest

from jobfit_agent.agent.tools import jobfit_store


def test_select_jobs_orders_by_score_and_respects_top_n(store):
    picked = jobfit_store.select_jobs(store, "default", 2)
    assert [j["id"] for j in picked] == ["j1", "j3"]
    assert picked[0]["company"] == "Acme" and picked[0]["best_score"] == 90


def test_job_with_context_adds_the_company_contacts(store):
    job = jobfit_store.job_with_context(store, "j1")
    assert job["title"] == "Senior Backend Engineer"
    assert job["scores"]["default"]["score"] == 90
    assert [c["name"] for c in job["contacts"]] == ["Jane"]


def test_the_agent_connection_cannot_write(store):
    jobfit_store.read_only(store)
    with pytest.raises(sqlite3.OperationalError):
        store.execute("DELETE FROM jobs")


def test_salary_snippets_find_sentences_with_an_amount(store):
    snippets = jobfit_store.salary_snippets(store, "acme")
    assert snippets == {"https://acme.test/1": "Salary $150,000 - $180,000 per year"}


def test_missing_cv_profile_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(jobfit_store.cv, "load_registry", lambda: {})
    with pytest.raises(KeyError, match="nope"):
        jobfit_store.load_cv_text("nope")


def test_a_profile_without_scores_selects_nothing(store):
    assert jobfit_store.select_jobs(store, "no-such-profile", 5) == []


def test_job_id_for_url_matches_the_stored_id():
    url = "https://job-boards.eu.greenhouse.io/conifersaicareers/jobs/4793505101"
    assert jobfit_store.job_id_for_url(url) == (
        "aHR0cHM6Ly9qb2ItYm9hcmRzLmV1LmdyZWVuaG91c2UuaW8vY29uaWZlcnNhaWNhcmVlcnMvam9icy80NzkzNTA1MTAx")


def test_select_by_url_finds_the_one_job(store):
    from jobfit_agent.tests.conftest import URL_JOB_ID, URL_JOB_URL

    assert jobfit_store.select_by_url(store, URL_JOB_URL) == [
        {"id": URL_JOB_ID, "company_id": "beta", "company": "Beta", "title": "SRE", "best_score": 0}]


def test_select_by_url_is_empty_for_an_unknown_url(store):
    assert jobfit_store.select_by_url(store, "https://nobody.test/x") == []
