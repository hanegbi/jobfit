"""Jobs that no company scrape re-verifies (LinkedIn matches, referrals) age
out through close_jobs_by_url, fed by the LinkedIn banner check and the
URL audit."""

from jobfit import config
from jobfit.scripts import update_jobs
from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs

NOW = "2026-09-30T10:00:00Z"


def _company(conn, company_id, display_name, jobs):
    store_companies.upsert_company(conn, company_id, display_name)
    store_jobs.upsert_scraped(conn, company_id, jobs, NOW)


def test_close_jobs_by_url_closes_matching_open_jobs_with_a_reason(store_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    _company(store_conn, "acme", "Acme", [
        {"id": "a", "title": "Backend", "url": "https://www.linkedin.com/jobs/view/1/"},
        {"id": "b", "title": "Frontend", "url": "https://www.linkedin.com/jobs/view/2"},
        {"id": "c", "title": "Old", "url": "https://www.linkedin.com/jobs/view/3"},
    ])
    _company(store_conn, "beta", "Beta", [{"id": "d", "title": "QA", "url": "https://beta.com/jobs/9"}])
    # "c" is already closed before the run, so it must be reported as such.
    store_conn.execute("UPDATE jobs SET status = 'closed' WHERE id = 'c'")

    stats = update_jobs.close_jobs_by_url({
        # fragment and trailing-slash variants are the same posting
        "https://www.linkedin.com/jobs/view/1#apply": "linkedin: no longer accepting applications",
        "https://www.linkedin.com/jobs/view/3": "linkedin: no longer accepting applications",
        "https://nowhere.example/x": "url: http 404",
    })

    assert stats == {"jobs_closed": 1, "companies_touched": 1, "already_closed": 1}
    stored = {row["id"]: row for row in store_jobs.jobs_for_company(store_conn, "acme")}
    assert stored["a"]["status"] == "closed"
    assert stored["a"]["closed_reason"].startswith("linkedin") and stored["a"]["closed_at"]
    assert stored["b"]["status"] == "new"
    assert store_jobs.jobs_for_company(store_conn, "beta")[0]["status"] == "new"


def test_close_jobs_by_url_with_nothing_to_close_changes_nothing(store_conn, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    _company(store_conn, "acme", "Acme", [{"id": "a", "title": "Backend", "url": "https://x/1"}])

    assert update_jobs.close_jobs_by_url({}) == {"jobs_closed": 0, "companies_touched": 0, "already_closed": 0}
    assert update_jobs.close_jobs_by_url({"https://x/2": "url: http 404"})["jobs_closed"] == 0
    assert store_jobs.get_job(store_conn, "a")["status"] == "new"
