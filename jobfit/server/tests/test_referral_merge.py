import json

from jobfit import config
from jobfit.scripts import update_jobs


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
        "merged_into_existing_job": 0, "added_new_job": 0,
    }
