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


def test_stores_years_required_on_a_new_job(companies_dir):
    fetched = [{
        "title": "Backend Engineer", "location": "Tel Aviv",
        "description": "Requirements: 5+ years of experience with Python",
        "url": "https://acme.com/careers/1",
    }]
    record, new_count, closed_count = update_jobs.diff_and_update("Acme Corp", "https://acme.com/careers", fetched, {})

    assert new_count == 1
    assert record["jobs"][0]["years_required"] == 5


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


def test_drops_a_job_in_a_non_israel_non_remote_location(companies_dir):
    """Real case: a company's career page mixes its Israel R&D roles with
    postings from its other offices (a US sales team, a Mexico support
    team) - this job search only wants the Israel-based ones."""
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Account Executive", "location": "Austin, Texas", "url": "https://x/1", "description": ""}]

    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 0
    assert record["jobs"] == []


def test_keeps_a_job_with_an_israeli_location(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]

    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 1


def test_keeps_a_remote_job(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": "Remote", "url": "https://x/1", "description": ""}]

    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 1


def test_keeps_a_job_with_no_location_at_all(companies_dir):
    """Empty/unspecified location is kept rather than dropped - many sources
    (plain-HTTP scrapes especially) just don't expose a location at all, and
    that must not be treated as "therefore not Israel"."""
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]

    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    assert new_count == 1


def test_closes_a_previously_israel_job_that_is_now_reported_foreign(companies_dir):
    """If a source corrects/changes a job's location on a re-scrape to
    somewhere non-Israel, it should be closed like any other job that's no
    longer being reported by the fetch, not silently kept forever."""
    profiles = {"default": {"must_have_keywords": []}}
    fetched_first = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched_first, profiles)
    update_jobs.save_company_file("Acme", record)

    fetched_second = [{"title": "Backend Engineer", "location": "Austin, Texas", "url": "https://x/1", "description": ""}]
    record2, new_count, closed_count = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched_second, profiles)

    assert new_count == 0
    assert closed_count == 1
    assert record2["jobs"][0]["status"] == "closed"


def test_new_job_captures_department_and_employment_type(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{
        "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": "",
        "department": "Engineering", "employment_type": "Full-time",
    }]

    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    job = record["jobs"][0]
    assert job["department"] == "Engineering"
    assert job["employment_type"] == "Full-time"


def test_new_job_prefers_the_source_posted_at_over_now(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{
        "title": "Backend Engineer", "location": None, "url": "https://x/1", "description": "",
        "posted_at": "2026-06-15",
    }]

    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    job = record["jobs"][0]
    assert job["first_seen"] == "2026-06-15"
    assert job["last_seen"] != "2026-06-15"  # last_seen is still "now", only first_seen is backdated


def test_new_job_falls_back_to_now_when_no_posted_at(companies_dir):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]

    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme/careers", fetched, profiles)

    job = record["jobs"][0]
    assert job["first_seen"] == job["last_seen"]


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


# --- compute_job_id -----------------------------------------------------

def test_compute_job_id_is_stable_across_whitespace_and_case_changes_in_title():
    id1 = update_jobs.compute_job_id("Acme", "Backend  Engineer", "Tel Aviv", "https://acme.com/careers/x")
    id2 = update_jobs.compute_job_id("Acme", "backend engineer", "Tel Aviv", "https://acme.com/careers/x")
    assert id1 == id2


def test_compute_job_id_is_the_base64url_of_the_normalized_url():
    import base64

    job_id = update_jobs.compute_job_id("Acme Inc", "Backend Engineer", None, "https://boards.greenhouse.io/acme/jobs/1234567")
    assert job_id == base64.urlsafe_b64encode(b"https://boards.greenhouse.io/acme/jobs/1234567").decode().rstrip("=")
    # trailing slash / fragment / whitespace variants are the same posting
    for variant in (" https://boards.greenhouse.io/acme/jobs/1234567/ ", "https://boards.greenhouse.io/acme/jobs/1234567#apply"):
        assert update_jobs.compute_job_id("Acme Inc", "Other Title", "Haifa", variant) == job_id


def test_compute_job_id_ignores_title_and_location_when_a_url_exists():
    a = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", "https://acme.com/careers/x")
    b = update_jobs.compute_job_id("Acme", "Senior Backend Engineer (re-titled)", "Haifa", "https://acme.com/careers/x")
    assert a == b


def test_compute_job_id_differs_for_different_locations_without_an_ats_id():
    id1 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", None)
    id2 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Haifa", None)
    assert id1 != id2


def test_compute_job_id_is_stable_across_reruns_with_identical_inputs():
    id1 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", "https://acme.com/x")
    id2 = update_jobs.compute_job_id("Acme", "Backend Engineer", "Tel Aviv", "https://acme.com/x")
    assert id1 == id2


def test_a_stored_title_is_trimmed_when_the_scrape_now_parses_it_better(tmp_path, monkeypatch):
    """Titles stored before the card parser existed heal on the next scrape -
    but only by trimming, so a scrape can never rename a job."""
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    url = "https://acme.com/careers/1"
    stored, _, _ = update_jobs.diff_and_update(
        "Acme", "https://acme.com/careers",
        [{"title": "Senior MLOps Engineer Full-time Senior Tel Aviv", "url": url}], profiles={},
    )
    update_jobs.atomic_write_json(tmp_path / "acme.json", stored)
    record, _, _ = update_jobs.diff_and_update(
        "Acme", "https://acme.com/careers",
        [{"title": "Senior MLOps Engineer", "url": url, "location": "Tel Aviv"}], profiles={},
    )
    assert record["jobs"][0]["title"] == "Senior MLOps Engineer"
    assert record["jobs"][0]["location"] == "Tel Aviv"


def test_a_stored_title_is_never_replaced_by_a_different_one(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    url = "https://acme.com/careers/2"
    stored, _, _ = update_jobs.diff_and_update("Acme", "https://acme.com/careers",
                                               [{"title": "Senior Backend Engineer", "url": url}], profiles={})
    update_jobs.atomic_write_json(tmp_path / "acme.json", stored)
    record, _, _ = update_jobs.diff_and_update("Acme", "https://acme.com/careers",
                                               [{"title": "Office Manager", "url": url}], profiles={})
    assert record["jobs"][0]["title"] == "Senior Backend Engineer"
