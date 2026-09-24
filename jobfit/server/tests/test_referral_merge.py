import json

import pytest

from jobfit import config
from jobfit.scripts import update_jobs


@pytest.fixture(autouse=True)
def _isolated_career_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "COMPANIES_CAREER_PAGES_PATH", tmp_path / "companies_career_pages.json")
    monkeypatch.setattr(config, "COMPANY_REVIEW_PATH", tmp_path / "data" / "company_review.json")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})


def _write_company(companies_dir, name, jobs):
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / f"{name}.json").write_text(
        json.dumps({"name": name.title(), "career_url": None, "last_checked": None, "jobs": jobs}),
        encoding="utf-8",
    )


def test_merge_referral_jobs_reads_from_an_explicit_path_not_the_global_default(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    _write_company(companies_dir, "acme", jobs=[])

    upload_path = tmp_path / "my_referral_upload.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe", "requirements": ["python"]}],
        }],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={"default": {"must_have_keywords": []}}, path=upload_path)

    assert stats["added_new_job"] == 1
    saved = json.loads((companies_dir / "acme.json").read_text(encoding="utf-8"))
    assert saved["jobs"][0]["title"] == "Backend Engineer"
    assert saved["jobs"][0]["is_referral"] is True


def test_merge_referral_jobs_dedupes_against_an_existing_similar_title(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    _write_company(companies_dir, "acme", jobs=[{
        "id": "existing1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "x", "last_seen": "x",
    }])

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
    saved = json.loads((companies_dir / "acme.json").read_text(encoding="utf-8"))
    assert len(saved["jobs"]) == 1
    assert saved["jobs"][0]["is_referral"] is True
    assert saved["jobs"][0]["referral_contact"] == "Jane Doe"


def test_merge_referral_jobs_returns_empty_stats_when_the_file_is_missing(tmp_path):
    stats = update_jobs.merge_referral_jobs(profiles={}, path=tmp_path / "does_not_exist.json")
    assert stats == {
        "matched_existing_company": 0, "new_company": 0,
        "merged_into_existing_job": 0, "added_new_job": 0, "added_to_career_pages": 0,
        "scrapable_companies": [],
    }


# --- career-pages bank backfill -------------------------------------------

def test_merge_referral_jobs_adds_a_brand_new_company_to_the_career_pages_bank(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 1
    pages = json.loads(config.COMPANIES_CAREER_PAGES_PATH.read_text(encoding="utf-8"))
    assert pages == {"Acme": None}


def test_merge_referral_jobs_pre_approves_techmap_when_techmap_has_data(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {
        "acme": [{"title": "Backend Engineer", "location": None, "url": "https://x", "company": "Acme"}],
    })

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    review = json.loads(config.COMPANY_REVIEW_PATH.read_text(encoding="utf-8"))
    assert review["Acme"]["decision"] == "techmap"


def test_merge_referral_jobs_leaves_no_techmap_company_pending(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert config.COMPANY_REVIEW_PATH.exists() is False or json.loads(
        config.COMPANY_REVIEW_PATH.read_text(encoding="utf-8")
    ) == {}


def test_merge_referral_jobs_does_not_re_add_a_company_already_in_the_bank(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    config.COMPANIES_CAREER_PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.COMPANIES_CAREER_PAGES_PATH.write_text(json.dumps({"Acme": "https://acme.com/careers"}), encoding="utf-8")

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 0
    pages = json.loads(config.COMPANIES_CAREER_PAGES_PATH.read_text(encoding="utf-8"))
    assert pages == {"Acme": "https://acme.com/careers"}


def test_merge_referral_jobs_reports_techmap_approved_companies_as_scrapable(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {
        "acme": [{"title": "Backend Engineer", "location": None, "url": "https://x", "company": "Acme"}],
    })

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["scrapable_companies"] == ["Acme"]


def test_merge_referral_jobs_reports_no_techmap_companies_as_not_scrapable(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["scrapable_companies"] == []


def test_merge_referral_jobs_backfills_a_company_already_tracked_but_missing_from_the_bank(tmp_path, monkeypatch):
    """The 502-company backlog this was written for: companies/*.json already
    has a file for them (from an earlier referral, before this feature
    existed), but they were never added to companies_career_pages.json."""
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    _write_company(companies_dir, "acme", jobs=[{
        "id": "existing1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "x", "last_seen": "x",
    }])

    upload_path = tmp_path / "referral.json"
    upload_path.write_text(json.dumps({
        "companies": [{"company": "Acme", "jobs": [{"title": "Frontend Engineer", "contact": "Jane Doe"}]}],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_to_career_pages"] == 1
    pages = json.loads(config.COMPANIES_CAREER_PAGES_PATH.read_text(encoding="utf-8"))
    assert pages == {"Acme": None}
