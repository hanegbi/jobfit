"""aggregate_to_jobs_v2's posted_at field drives the "Most recently posted"
sort and the job card's date display in jobfit.html - it must reflect when a
job was first discovered, not when it was last re-confirmed still open
(that would reset to "now" on every single run, which isn't what "posted"
means)."""

import json

import pytest

from jobfit import config
from jobfit.scripts import update_jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    return companies_dir


def _write_company(companies_dir, name, jobs):
    (companies_dir / f"{name}.json").write_text(
        json.dumps({"name": name, "career_url": None, "last_checked": None, "jobs": jobs}),
        encoding="utf-8",
    )


def test_posted_at_uses_first_seen_not_last_seen(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    assert dataset[0]["posted_at"] == "2026-06-15T10:00:00Z"
