"""diff_and_update is the core new/seen/closed state machine every scrape run
goes through - central to the whole app's data model, and previously had zero
direct test coverage (only exercised indirectly via live runs)."""

import pytest

from jobfit.scripts import update_jobs


@pytest.fixture
def companies_dir(tmp_path, monkeypatch):
    d = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", d)
    return d


def test_adds_a_brand_new_job(companies_dir):
    profiles = {"default": {"must_have_keywords": ["python"]}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": "python required"}]

    record, new_count, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 1
    assert closed_count == 0
    assert len(record["jobs"]) == 1
    job = record["jobs"][0]
    assert job["title"] == "Backend Engineer"
    assert job["status"] == "new"
    assert job["first_seen"] == job["last_seen"]
    assert "score_default" in job
    assert record["name"] == "Acme"
    assert record["career_url"] == "https://acme/careers"


def test_marks_a_missing_job_closed_but_keeps_it(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched_first = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched_first, profiles)
    update_jobs.save_company_file("Acme", record)

    record2, new_count, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", [], profiles)

    assert new_count == 0
    assert closed_count == 1
    assert len(record2["jobs"]) == 1  # kept, never deleted
    assert record2["jobs"][0]["status"] == "closed"


def test_flips_new_to_seen_on_a_second_sighting(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)
    assert record["jobs"][0]["status"] == "new"
    update_jobs.save_company_file("Acme", record)

    record2, new_count, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 0
    assert closed_count == 0
    assert record2["jobs"][0]["status"] == "seen"


def test_reopens_a_closed_job_that_reappears(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)
    update_jobs.save_company_file("Acme", record)
    record2, _, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", [], profiles)
    assert closed_count == 1
    update_jobs.save_company_file("Acme", record2)

    record3, new_count, closed_count2 = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 0
    assert closed_count2 == 0
    assert record3["jobs"][0]["status"] == "seen"


def test_skips_jobs_with_no_title(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [
        {"title": "", "location": None, "url": "https://x/1", "description": ""},
        {"title": None, "location": None, "url": "https://x/2", "description": ""},
    ]

    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 0
    assert record["jobs"] == []


def test_does_not_rescore_existing_jobs(companies_dir):
    """recompute_stage() owns rescoring now, not diff_and_update - a changed
    profile registry between two scrape runs must not silently rescore here."""
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": "python"}]
    profiles_v1 = {"default": {"must_have_keywords": ["python"]}}
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles_v1)
    original_score = record["jobs"][0]["score_default"]
    update_jobs.save_company_file("Acme", record)

    profiles_v2 = {"default": {"must_have_keywords": []}}  # would score lower if it re-ran
    record2, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles_v2)

    assert record2["jobs"][0]["score_default"] == original_score


def test_multiple_jobs_new_seen_and_closed_in_one_pass(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    initial = [
        {"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""},
        {"title": "Frontend Engineer", "location": None, "url": "https://x/2", "description": ""},
    ]
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", initial, profiles)
    update_jobs.save_company_file("Acme", record)

    # Frontend Engineer (x/2) disappears, Data Engineer (x/3) is genuinely new.
    second_pass = [
        {"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""},
        {"title": "Data Engineer", "location": None, "url": "https://x/3", "description": ""},
    ]
    record2, new_count, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", second_pass, profiles)

    assert new_count == 1
    assert closed_count == 1
    statuses = {j["title"]: j["status"] for j in record2["jobs"]}
    assert statuses == {"Backend Engineer": "seen", "Frontend Engineer": "closed", "Data Engineer": "new"}


# --- compute_job_id / extract_ats_id ------------------------------------

def test_extract_ats_id_from_greenhouse_url():
    assert update_jobs.extract_ats_id("https://boards.greenhouse.io/acme/jobs/1234567") == "1234567"


def test_extract_ats_id_from_lever_url():
    assert update_jobs.extract_ats_id("https://jobs.lever.co/acme/abcdef12-3456") == "abcdef12-3456"


def test_extract_ats_id_returns_none_for_an_unknown_host():
    assert update_jobs.extract_ats_id("https://acme.com/careers/some-job") is None


def test_extract_ats_id_returns_none_for_no_url():
    assert update_jobs.extract_ats_id(None) is None


def test_compute_job_id_is_stable_across_whitespace_and_case_changes_in_title():
    id1 = update_jobs.compute_job_id("Acme", "Backend  Engineer", "Tel Aviv", "https://acme.com/careers/x")
    id2 = update_jobs.compute_job_id("Acme", "backend engineer", "Tel Aviv", "https://acme.com/careers/x")
    assert id1 == id2


def test_compute_job_id_uses_the_ats_id_when_the_url_reveals_one():
    job_id = update_jobs.compute_job_id(
        "Acme Inc", "Backend Engineer", None, "https://boards.greenhouse.io/acme/jobs/1234567"
    )
    assert job_id == "acme_inc:1234567"


def test_compute_job_id_differs_for_different_locations_without_an_ats_id():
    id1 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", None)
    id2 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Haifa", None)
    assert id1 != id2


def test_compute_job_id_is_stable_across_reruns_with_identical_inputs():
    id1 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", "https://acme.com/x")
    id2 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", "https://acme.com/x")
    assert id1 == id2
