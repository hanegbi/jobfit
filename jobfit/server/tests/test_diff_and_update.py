"""diff_and_update is the core new/seen/closed state machine every scrape run
goes through - central to the whole app's data model.

It persists through jobfit.store now, so these read back what was stored
rather than inspecting a returned record: the store IS the outcome, and a
return value that only tests looked at would be a fiction to maintain.
"""

from jobfit.scripts import update_jobs
from jobfit.store import jobs as store_jobs
from jobfit.store import scores as store_scores

CAREER_URL = "https://acme/careers"


def _stored(conn, company_id="acme"):
    return store_jobs.jobs_for_company(conn, company_id)


def _by_title(conn, company_id="acme"):
    return {row["title"]: row for row in _stored(conn, company_id)}


def test_adds_a_brand_new_job(store_conn):
    profiles = {"default": {"must_have_keywords": ["python"]}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1",
                "description": "python required"}]

    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)

    assert (new_count, closed_count) == (1, 0)
    jobs = _stored(store_conn)
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Backend Engineer"
    assert jobs[0]["status"] == "new"
    assert jobs[0]["first_seen"] == jobs[0]["last_seen"]
    assert store_scores.scores_for_job(store_conn, jobs[0]["id"])["default"]["score"] is not None


def test_records_the_company_and_its_career_url(store_conn):
    from jobfit.store import companies as store_companies

    update_jobs.diff_and_update("Acme", CAREER_URL, [], {})
    row = store_companies.get_company(store_conn, "acme")
    assert row["display_name"] == "Acme" and row["career_url"] == CAREER_URL
    assert row["last_checked"] is not None


def test_stores_years_required_on_a_new_job(store_conn):
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1",
                "description": "Requirements: 5+ years of experience with Python"}]
    update_jobs.diff_and_update("Acme Corp", "https://acme.com/careers", fetched, {})
    assert _stored(store_conn, "acme_corp")[0]["years_required"] == 5


def test_marks_a_missing_job_closed_but_keeps_it(store_conn):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)

    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, [], profiles)

    assert (new_count, closed_count) == (0, 1)
    jobs = _stored(store_conn)
    assert len(jobs) == 1 and jobs[0]["status"] == "closed"


def test_flips_new_to_seen_on_a_second_sighting(store_conn):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)
    assert _stored(store_conn)[0]["status"] == "new"

    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)

    assert (new_count, closed_count) == (0, 0)
    assert _stored(store_conn)[0]["status"] == "seen"


def test_reopens_a_closed_job_that_reappears(store_conn):
    profiles = {"default": {"must_have_keywords": []}}
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)
    update_jobs.diff_and_update("Acme", CAREER_URL, [], profiles)
    assert _stored(store_conn)[0]["status"] == "closed"

    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, profiles)

    assert (new_count, closed_count) == (0, 0)
    assert _stored(store_conn)[0]["status"] == "seen"


def test_skips_jobs_with_no_title(store_conn):
    fetched = [
        {"title": "", "location": None, "url": "https://x/1", "description": ""},
        {"title": None, "location": None, "url": "https://x/2", "description": ""},
    ]
    new_count, _ = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    assert new_count == 0 and _stored(store_conn) == []


def test_drops_a_job_in_a_non_israel_non_remote_location(store_conn):
    """Real case: a company's career page mixes its Israel R&D roles with
    postings from its other offices (a US sales team, a Mexico support
    team) - this job search only wants the Israel-based ones."""
    fetched = [{"title": "Account Executive", "location": "Austin, Texas", "url": "https://x/1", "description": ""}]
    new_count, _ = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    assert new_count == 0 and _stored(store_conn) == []


def test_keeps_a_job_with_an_israeli_location(store_conn):
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    new_count, _ = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    assert new_count == 1
    assert _stored(store_conn)[0]["city"] == "Tel Aviv"


def test_keeps_a_remote_job(store_conn):
    fetched = [{"title": "Backend Engineer", "location": "Remote", "url": "https://x/1", "description": ""}]
    new_count, _ = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    assert new_count == 1
    assert _stored(store_conn)[0]["is_remote"] == 1


def test_keeps_a_job_with_no_location_at_all(store_conn):
    """Empty/unspecified location is kept rather than dropped - many sources
    (plain-HTTP scrapes especially) just don't expose a location at all, and
    that must not be treated as "therefore not Israel"."""
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""}]
    new_count, _ = update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    assert new_count == 1


def test_closes_a_previously_israel_job_that_is_now_reported_foreign(store_conn):
    """If a source corrects a job's location on a re-scrape to somewhere
    non-Israel, it is closed like any other job the fetch no longer reports,
    not silently kept forever."""
    profiles = {"default": {"must_have_keywords": []}}
    update_jobs.diff_and_update("Acme", CAREER_URL, [
        {"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}], profiles)

    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, [
        {"title": "Backend Engineer", "location": "Austin, Texas", "url": "https://x/1", "description": ""}], profiles)

    assert (new_count, closed_count) == (0, 1)
    assert _stored(store_conn)[0]["status"] == "closed"


def test_new_job_captures_department_and_employment_type(store_conn):
    fetched = [{
        "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": "",
        "department": "Engineering", "employment_type": "Full-time",
    }]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    job = _stored(store_conn)[0]
    # "Engineering" is stored as the canonical "Software Engineering"; the
    # ATS's own spelling never reaches the column (see jobfit/departments.py).
    assert job["department"] == "Software Engineering" and job["employment_type"] == "Full-time"


def test_new_job_prefers_the_source_posted_at_over_now(store_conn):
    """An ATS states when a job was posted; that beats the moment we looked,
    so a company scraped for the first time isn't all "brand new"."""
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1",
                "description": "", "posted_at": "2026-06-01"}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    job = _stored(store_conn)[0]
    assert job["first_seen"] == "2026-06-01" and job["last_seen"] != "2026-06-01"


def test_new_job_falls_back_to_now_when_no_posted_at(store_conn):
    fetched = [{"title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": ""}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {})
    job = _stored(store_conn)[0]
    assert job["first_seen"] == job["last_seen"]


def test_does_not_rescore_existing_jobs(store_conn):
    """recompute_stage owns rescoring - a changed profile registry between
    two scrape runs must not silently rescore here."""
    fetched = [{"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": "python"}]
    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {"default": {"must_have_keywords": ["python"]}})
    job_id = _stored(store_conn)[0]["id"]
    original = store_scores.scores_for_job(store_conn, job_id)["default"]["score"]

    update_jobs.diff_and_update("Acme", CAREER_URL, fetched, {"default": {"must_have_keywords": []}})

    assert store_scores.scores_for_job(store_conn, job_id)["default"]["score"] == original


def test_multiple_jobs_new_seen_and_closed_in_one_pass(store_conn):
    profiles = {"default": {"must_have_keywords": []}}
    update_jobs.diff_and_update("Acme", CAREER_URL, [
        {"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""},
        {"title": "Frontend Engineer", "location": None, "url": "https://x/2", "description": ""},
    ], profiles)

    # Frontend Engineer (x/2) disappears, Data Engineer (x/3) is genuinely new.
    new_count, closed_count = update_jobs.diff_and_update("Acme", CAREER_URL, [
        {"title": "Backend Engineer", "location": None, "url": "https://x/1", "description": ""},
        {"title": "Data Engineer", "location": None, "url": "https://x/3", "description": ""},
    ], profiles)

    assert (new_count, closed_count) == (1, 1)
    assert {title: row["status"] for title, row in _by_title(store_conn).items()} == {
        "Backend Engineer": "seen", "Frontend Engineer": "closed", "Data Engineer": "new",
    }


def test_a_stored_title_is_trimmed_when_the_scrape_now_parses_it_better(store_conn):
    """Titles stored before the card parser existed heal on the next scrape -
    but only by trimming, so a scrape can never rename a job."""
    url = "https://acme.com/careers/1"
    update_jobs.diff_and_update("Acme", CAREER_URL,
                                [{"title": "Senior MLOps Engineer Full-time Senior Tel Aviv", "url": url}], {})
    update_jobs.diff_and_update("Acme", CAREER_URL,
                                [{"title": "Senior MLOps Engineer", "url": url, "location": "Tel Aviv"}], {})
    job = _stored(store_conn)[0]
    assert job["title"] == "Senior MLOps Engineer" and job["location"] == "Tel Aviv"


def test_a_stored_title_is_never_replaced_by_a_different_one(store_conn):
    url = "https://acme.com/careers/2"
    update_jobs.diff_and_update("Acme", CAREER_URL, [{"title": "Senior Backend Engineer", "url": url}], {})
    update_jobs.diff_and_update("Acme", CAREER_URL, [{"title": "Office Manager", "url": url}], {})
    assert _stored(store_conn)[0]["title"] == "Senior Backend Engineer"


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
