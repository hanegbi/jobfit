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
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "AGGREGATE_CACHE_DIR", tmp_path / "cache" / "aggregate")
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "TECHMAP_CACHE_DIR", tmp_path / "cache" / "techmap")
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


def test_second_run_is_a_cache_hit_and_does_not_reflatten(isolated, monkeypatch):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    def _boom(*a, **kw):
        raise AssertionError("should not re-flatten on a cache hit")
    monkeypatch.setattr(update_jobs, "_flatten_company", _boom)

    count = update_jobs.aggregate_to_jobs_v2()
    assert count == 1


def test_changing_a_company_file_invalidates_only_its_own_cache_entry(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    _write_company(isolated, "wiz", jobs=[{
        "id": "j2", "title": "Frontend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "closed",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    acme_job = next(j for j in dataset if j["id"] == "j1")
    assert acme_job["status"] == "closed"


def test_force_true_ignores_the_cache(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    calls = {"n": 0}
    real_flatten = update_jobs._flatten_company

    def counting_flatten(*args, **kwargs):
        calls["n"] += 1
        return real_flatten(*args, **kwargs)

    import jobfit.scripts.update_jobs as uj_module
    uj_module._flatten_company = counting_flatten
    try:
        update_jobs.aggregate_to_jobs_v2(force=True)
    finally:
        uj_module._flatten_company = real_flatten

    assert calls["n"] == 1


def test_orphaned_cache_entries_for_deleted_companies_are_removed(isolated):
    _write_company(isolated, "acme", jobs=[])
    update_jobs.aggregate_to_jobs_v2()
    assert (config.AGGREGATE_CACHE_DIR / "acme.json").exists()

    (isolated / "acme.json").unlink()
    update_jobs.aggregate_to_jobs_v2()

    assert not (config.AGGREGATE_CACHE_DIR / "acme.json").exists()


def test_jobs_v2_json_has_no_indentation(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    raw = config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8")
    assert "\n" not in raw  # indent=None -> single line
    assert json.loads(raw)  # still valid JSON


def test_jobs_v2_meta_json_written(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    meta = json.loads(config.JOBS_OUTPUT_META_JSON.read_text(encoding="utf-8"))
    assert meta["job_count"] == 1
    assert meta["company_count"] == 1
    assert "scoring_engine" in meta
