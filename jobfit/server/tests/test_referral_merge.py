import json

import pytest

from jobfit import config
from jobfit.scripts import update_jobs
from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs


@pytest.fixture(autouse=True)
def _no_techmap(monkeypatch):
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})


def _company(conn, company_id, display_name, jobs=(), career_url=None):
    store_companies.upsert_company(conn, company_id, display_name, career_url=career_url)
    if jobs:
        store_jobs.upsert_scraped(conn, company_id, list(jobs), "2026-09-30T10:00:00Z")


def _jobs(conn, company_id):
    return [dict(row) for row in store_jobs.jobs_for_company(conn, company_id)]


def test_merge_referral_jobs_reads_from_an_explicit_path_not_the_global_default(tmp_path, store_conn):
    _company(store_conn, "acme", "Acme")

    upload_path = tmp_path / "my_referral_upload.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe", "requirements": ["python"]}],
        }],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={"default": {"must_have_keywords": []}}, path=upload_path)

    assert stats["added_new_job"] == 1
    stored = _jobs(store_conn, "acme")
    assert stored[0]["title"] == "Backend Engineer" and stored[0]["is_referral"] == 1


def test_merge_referral_jobs_is_idempotent_for_hebrew_titles(tmp_path, store_conn):
    """A Hebrew-titled referral job merged twice must exist once. The title
    normalizer used to strip every non-ASCII character, so a Hebrew title
    normalized to '' and never matched itself - every full run appended a
    fresh copy of every Hebrew referral job."""
    _company(store_conn, "iai", "IAI")

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "IAI",
            "jobs": [{"title": "מהנדס/ת ייצור מערכות מכניות", "url": "https://www.linkedin.com/jobs/view/4317932362", "contact": "x"}],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    first = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)
    second = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert first["added_new_job"] == 1
    assert second["added_new_job"] == 0
    assert second["merged_into_existing_job"] == 1
    assert len(_jobs(store_conn, "iai")) == 1


def test_merge_referral_jobs_dedupes_against_an_existing_similar_title(tmp_path, store_conn):
    _company(store_conn, "acme", "Acme", jobs=[{"id": "existing1", "title": "Backend Engineer"}])

    upload_path = tmp_path / "my_referral_upload.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}],
        }],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["merged_into_existing_job"] == 1
    assert stats["added_new_job"] == 0
    stored = _jobs(store_conn, "acme")
    assert len(stored) == 1
    assert stored[0]["is_referral"] == 1 and stored[0]["referral_contact"] == "Jane Doe"


def test_merge_referral_jobs_returns_empty_stats_when_the_file_is_missing(tmp_path):
    stats = update_jobs.merge_referral_jobs(profiles={}, path=tmp_path / "does_not_exist.json")
    assert stats == {
        "matched_existing_company": 0, "new_company": 0,
        "merged_into_existing_job": 0, "added_new_job": 0, "added_to_career_pages": 0,
        "scrapable_companies": [],
    }


# --- career-pages bank backfill -------------------------------------------

def test_merge_referral_jobs_adds_a_brand_new_company_to_the_store(tmp_path, store_conn):

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 1
    row = store_companies.get_company(store_conn, "acme")
    assert row["display_name"] == "Acme" and row["career_url"] is None


def test_merge_referral_jobs_pre_approves_techmap_when_techmap_has_data(tmp_path, store_conn, monkeypatch):
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {
        "acme": [{"title": "Backend Engineer", "location": None, "url": "https://x", "company": "Acme"}],
    })

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert store_companies.get_company(store_conn, "acme")["review_decision"] == "techmap"


def test_merge_referral_jobs_leaves_no_techmap_company_pending(tmp_path, store_conn):

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert store_companies.get_company(store_conn, "acme")["review_decision"] is None


def test_merge_referral_jobs_does_not_re_add_a_company_already_in_the_bank(tmp_path, store_conn):
    _company(store_conn, "acme", "Acme", career_url="https://acme.com/careers")

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 0
    assert store_companies.get_company(store_conn, "acme")["career_url"] == "https://acme.com/careers"


def test_merge_referral_jobs_reports_techmap_approved_companies_as_scrapable(tmp_path, store_conn, monkeypatch):
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {
        "acme": [{"title": "Backend Engineer", "location": None, "url": "https://x", "company": "Acme"}],
    })

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["scrapable_companies"] == ["Acme"]


def test_merge_referral_jobs_reports_no_techmap_companies_as_not_scrapable(tmp_path, store_conn):

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["scrapable_companies"] == []


def test_merge_referral_jobs_backfills_a_company_that_is_tracked_but_not_scrapable(tmp_path, store_conn):
    """The 502-company backlog this was written for: the company is already
    known (from an earlier referral, before this feature existed) but has no
    career URL, so no scrape run would ever touch it again."""
    _company(store_conn, "acme", "Acme", jobs=[{"id": "existing1", "title": "Backend Engineer"}])

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Frontend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 1
    row = store_companies.get_company(store_conn, "acme")
    assert row["display_name"] == "Acme" and row["career_url"] is None
