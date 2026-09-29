"""Jobs that no company scrape re-verifies (LinkedIn matches, referrals) age
out through close_jobs_by_url, fed by the LinkedIn banner check and the
URL audit."""

import json

from jobfit import config
from jobfit.scripts import update_jobs


def _write(companies_dir, name, jobs):
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / f"{name}.json").write_text(json.dumps({"name": name.title(), "career_url": None, "last_checked": None, "jobs": jobs}), encoding="utf-8")


def test_close_jobs_by_url_closes_matching_open_jobs_with_a_reason(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    _write(companies_dir, "acme", [
        {"id": "a", "title": "Backend", "url": "https://www.linkedin.com/jobs/view/1/", "status": "seen"},
        {"id": "b", "title": "Frontend", "url": "https://www.linkedin.com/jobs/view/2", "status": "new"},
        {"id": "c", "title": "Old", "url": "https://www.linkedin.com/jobs/view/3", "status": "closed"},
    ])
    _write(companies_dir, "beta", [{"id": "d", "title": "QA", "url": "https://beta.com/jobs/9", "status": "seen"}])

    stats = update_jobs.close_jobs_by_url({
        "https://www.linkedin.com/jobs/view/1#apply": "linkedin: no longer accepting applications",  # fragment/slash variants match
        "https://www.linkedin.com/jobs/view/3": "linkedin: no longer accepting applications",
        "https://nowhere.example/x": "url: http 404",
    })

    assert stats == {"jobs_closed": 1, "companies_touched": 1, "already_closed": 1}
    acme = {j["id"]: j for j in json.loads((companies_dir / "acme.json").read_text(encoding="utf-8"))["jobs"]}
    assert acme["a"]["status"] == "closed" and acme["a"]["closed_reason"].startswith("linkedin") and acme["a"]["closed_at"]
    assert acme["b"]["status"] == "new"
    beta = json.loads((companies_dir / "beta.json").read_text(encoding="utf-8"))["jobs"][0]
    assert beta["status"] == "seen"


def test_close_jobs_by_url_with_nothing_to_close_touches_no_file(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    _write(companies_dir, "acme", [{"id": "a", "title": "Backend", "url": "https://x/1", "status": "seen"}])
    before = (companies_dir / "acme.json").stat().st_mtime_ns
    assert update_jobs.close_jobs_by_url({}) == {"jobs_closed": 0, "companies_touched": 0, "already_closed": 0}
    assert update_jobs.close_jobs_by_url({"https://x/2": "url: http 404"})["jobs_closed"] == 0
    assert (companies_dir / "acme.json").stat().st_mtime_ns == before
