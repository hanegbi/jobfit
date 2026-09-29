"""The one-off import of the JSON company files into SQLite, and the
verification that proves it kept every field."""

import json

from jobfit.scripts import migrate_to_db
from jobfit.store import companies, db, jobs, scores


def _company_file(tmp_path, stem, record):
    (tmp_path / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")


def _record():
    return {
        "name": "Acme Ltd.",
        "career_url": "https://acme.com/careers",
        "last_checked": "2026-09-30T10:00:00Z",
        "jobs": [{
            "id": "j1", "title": "Senior Backend Engineer", "url": "https://acme.com/1",
            "description": "Requirements: Python", "location": "Tel Aviv", "status": "seen",
            "first_seen": "2026-09-01T00:00:00Z", "last_seen": "2026-09-30T00:00:00Z",
            "score_default": 88.0, "coverage_default": 0.8, "confidence_default": "full",
            "matched_default": ["python"], "score_infra": 55.0, "matched_infra": [],
            "_score_cache_keys": {"default": "k1", "infra": "k2"},
        }],
    }


def _fresh():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def test_import_creates_the_company_its_jobs_and_its_scores(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    counts = migrate_to_db.import_all(conn, tmp_path, {"Acme Ltd.": "https://acme.com/careers"}, {}, {})
    assert counts["companies"] == 1 and counts["jobs"] == 1 and counts["scores"] == 2
    job = jobs.get_job(conn, "j1")
    assert job["title"] == "Senior Backend Engineer" and job["company_id"] == "acme_ltd"
    assert job["status"] == "seen" and job["description"] == "Requirements: Python"
    stored = scores.scores_for_job(conn, "j1")
    assert stored["default"]["score"] == 88.0 and stored["default"]["cache_key"] == "k1"
    assert stored["infra"]["score"] == 55.0


def test_a_company_with_a_career_url_but_no_jobs_file_is_still_created(tmp_path):
    """The career-pages map is the scrape list and is far longer than the
    set of companies that have been scraped; losing its tail would silently
    shrink every future run."""
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {"Never Scraped": "https://never.com/jobs"}, {}, {})
    row = companies.get_company(conn, "never_scraped")
    assert row is not None and row["career_url"] == "https://never.com/jobs"


def test_import_is_idempotent(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM job_scores").fetchone()[0] == 2


def test_review_decisions_and_addresses_land_on_the_company(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {"Acme Ltd.": "skip"}, {"acme": "Herzliya"})
    row = companies.get_company(conn, "acme_ltd")
    assert row["review_decision"] == "skip" and row["address_city"] == "Herzliya"


def test_a_null_career_url_survives_as_null(tmp_path):
    """null in the map means "reviewed, not scraped" - not "unknown"."""
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {"Dormant": None}, {"Dormant": "skip"}, {})
    row = companies.get_company(conn, "dormant")
    assert row["career_url"] is None and row["review_decision"] == "skip"


def test_verify_is_silent_on_a_faithful_import(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    assert migrate_to_db.verify(conn, tmp_path, {}, {}) == []


def test_verify_reports_a_changed_field(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    conn.execute("UPDATE jobs SET title = 'Something Else' WHERE id = 'j1'")
    assert any("title" in problem for problem in migrate_to_db.verify(conn, tmp_path))


def test_verify_reports_a_missing_job(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    conn.execute("DELETE FROM job_scores")
    conn.execute("DELETE FROM jobs")
    assert any("count" in problem for problem in migrate_to_db.verify(conn, tmp_path))


def test_verify_is_reproducible(tmp_path):
    """A fixed seed means a failure can be investigated, not re-rolled."""
    for index in range(120):
        record = _record()
        record["jobs"][0]["id"] = f"j{index}"
        _company_file(tmp_path, f"c{index}", {**record, "name": f"Company {index}"})
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    conn.execute("UPDATE jobs SET title = 'Broken'")
    first = migrate_to_db.verify(conn, tmp_path)
    second = migrate_to_db.verify(conn, tmp_path)
    assert first == second and len(first) > 0


def test_import_computes_the_city_and_remote_flag(tmp_path):
    """The aggregate stage used to derive these at the end of every run,
    which is why no company file carries a city. They are columns now, so
    "jobs in Tel Aviv" is a query rather than a scan."""
    record = _record()
    record["jobs"][0]["location"] = "Israel"
    _company_file(tmp_path, "acme", record)
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {"acme": "Herzliya"})
    job = jobs.get_job(conn, "j1")
    assert job["city"] == "Herzliya" and job["location"] == "Herzliya" and job["is_remote"] == 0


def test_a_job_located_abroad_gets_no_city(tmp_path):
    record = _record()
    record["jobs"][0]["location"] = "United States"
    _company_file(tmp_path, "acme", record)
    conn = _fresh()
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {"acme": "Herzliya"})
    job = jobs.get_job(conn, "j1")
    assert job["city"] is None and job["location"] == "United States"
