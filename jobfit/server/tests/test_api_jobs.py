"""The API a front end reads: search, detail, and the user's own flags."""

import pytest

from jobfit.store import companies as store_companies
from jobfit.store import jobs as store_jobs
from jobfit.store import scores as store_scores

NOW = "2026-09-30T10:00:00Z"


@pytest.fixture
def seeded(store_conn):
    store_companies.upsert_company(store_conn, "acme", "Acme", career_url="https://acme.com/careers")
    store_companies.upsert_company(store_conn, "beta", "Beta")
    store_jobs.upsert_scraped(store_conn, "acme", [
        {"id": "j1", "title": "Senior Backend Engineer", "url": "u1", "city": "Tel Aviv",
         "description": "Requirements: Python and Kubernetes"},
        {"id": "j2", "title": "Data Scientist", "url": "u2", "city": "Haifa", "description": "Requirements: pandas"},
    ], NOW)
    store_jobs.upsert_scraped(store_conn, "beta", [
        {"id": "j3", "title": "Platform Engineer", "url": "u3", "city": "Tel Aviv", "description": "Kubernetes"},
    ], NOW)
    store_scores.write_scores(store_conn, "j1", {"default": {"score": 90, "matched": ["python"], "cache_key": "a"}})
    store_scores.write_scores(store_conn, "j2", {"default": {"score": 40, "cache_key": "b"}})
    store_scores.write_scores(store_conn, "j3", {"default": {"score": 70, "cache_key": "c"}})
    return store_conn


def test_jobs_returns_a_page_with_a_total(client, seeded):
    body = client.get("/api/jobs").json()
    assert body["total"] == 3 and body["page"] == 1
    assert [j["id"] for j in body["jobs"]] == ["j1", "j3", "j2"]


def test_jobs_never_returns_descriptions(client, seeded):
    """The rule that keeps a page of results kilobytes rather than megabytes."""
    assert "description" not in client.get("/api/jobs").json()["jobs"][0]


def test_jobs_filters_and_searches(client, seeded):
    assert {j["id"] for j in client.get("/api/jobs?q=kubernetes").json()["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in client.get("/api/jobs?city=Tel+Aviv&min_score=80").json()["jobs"]} == {"j1"}
    assert client.get("/api/jobs?company=beta").json()["total"] == 1


def test_jobs_paginates(client, seeded):
    body = client.get("/api/jobs?page=2&size=2").json()
    assert body["total"] == 3 and len(body["jobs"]) == 1


def test_jobs_caps_the_page_size(client, seeded):
    """An unbounded size would let one request pull the whole dataset, which
    is the thing this API exists to avoid."""
    assert client.get("/api/jobs?size=100000").json()["size"] <= 500


def test_a_nonsense_query_is_an_empty_result_not_a_500(client, seeded):
    for query in ("C%2B%2B", "%22", "AND", "*"):
        assert client.get(f"/api/jobs?q={query}").status_code == 200


def test_a_bad_page_number_is_a_422_not_a_crash(client, seeded):
    assert client.get("/api/jobs?page=abc").status_code == 422


def test_job_detail_carries_the_description_and_scores(client, seeded):
    body = client.get("/api/jobs/j1").json()
    assert body["description"].startswith("Requirements: Python")
    assert body["company"] == "Acme"
    assert body["scores"]["default"]["score"] == 90
    assert body["scores"]["default"]["matched"] == ["python"]


def test_job_detail_404s_for_an_unknown_id(client, seeded):
    assert client.get("/api/jobs/nope").status_code == 404


def test_patching_state_persists_and_is_visible_in_search(client, seeded):
    res = client.patch("/api/jobs/j1/state", json={"liked": True})
    assert res.status_code == 200 and res.json()["liked"] is True

    assert client.get("/api/jobs/j1").json()["state"]["liked"] is True
    assert [j["id"] for j in client.get("/api/jobs?liked=true").json()["jobs"]] == ["j1"]


def test_patching_one_flag_leaves_the_others(client, seeded):
    client.patch("/api/jobs/j1/state", json={"liked": True})
    body = client.patch("/api/jobs/j1/state", json={"sent": True}).json()
    assert body == {"liked": True, "hidden": False, "sent": True, "reached_out": False}


def test_patching_an_unknown_job_is_404_and_an_unknown_flag_is_400(client, seeded):
    assert client.patch("/api/jobs/nope/state", json={"liked": True}).status_code == 404
    assert client.patch("/api/jobs/j1/state", json={"favourite": True}).status_code == 400


def test_facets_count_by_company_city_and_status(client, seeded):
    body = client.get("/api/facets").json()
    assert {c["name"]: c["n"] for c in body["companies"]} == {"Acme": 2, "Beta": 1}
    assert body["statuses"] == {"new": 3}


def test_facets_follow_the_filter(client, seeded):
    body = client.get("/api/facets?city=Tel+Aviv").json()
    assert {c["name"]: c["n"] for c in body["companies"]} == {"Acme": 1, "Beta": 1}


def test_companies_lists_every_tracked_company(client, seeded):
    listing = {c["id"]: c for c in client.get("/api/companies").json()}
    assert listing["acme"]["open_jobs"] == 2
    assert listing["beta"]["open_jobs"] == 1
    assert listing["acme"]["career_url"] == "https://acme.com/careers"


def test_the_control_panel_routes_still_work(client, seeded):
    """This phase is additive; the panel must not regress."""
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/profiles").status_code == 200
    assert client.get("/api/run/status").status_code == 200


def test_jobs_can_be_filtered_by_connection(client, seeded):
    from jobfit.store import companies as store_companies

    store_companies.refresh_connection_counts(seeded, {"acme": ["Jane"]})
    body = client.get("/api/jobs?has_connection=true").json()
    assert {j["id"] for j in body["jobs"]} == {"j1", "j2"}
    assert body["jobs"][0]["connection_count"] == 1


def test_every_old_page_filter_is_reachable_over_http(client, seeded):
    """The static page had these; losing them in the port would be a
    regression the user notices before any test does."""
    for query in ("scope=title&q=engineer", "exclude=scientist", "department=R%26D",
                  "industry=Software", "language=he", "max_years=5", "posted_after=2026-01-01",
                  "referral=false", "has_description=true", "reached_out=false",
                  "company=acme,beta", "city=Tel+Aviv,Haifa", "sort=title"):
        res = client.get(f"/api/jobs?{query}")
        assert res.status_code == 200, query


def test_facets_cover_every_sidebar_dimension(client, seeded):
    body = client.get("/api/facets").json()
    assert set(body) == {"companies", "cities", "statuses", "departments", "industries",
                         "languages", "years"}


def test_scored_profiles_lists_what_can_be_ranked_by(client, seeded):
    assert client.get("/api/profiles/scored").json() == ["default"]
