"""The read the application is built on: filter, sort, paginate, count -
one query, so the client never holds the dataset."""

from jobfit.store import companies, db, jobs, scores, search

NOW = "2026-09-30T10:00:00Z"
LATER = "2026-10-01T10:00:00Z"


def _contact(name, position="Engineer"):
    """One entry of connections.load_connections_index's output."""
    return {"name": name, "position": position, "url": f"https://linkedin.com/in/{name.lower()}"}


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Senior Backend Engineer", "url": "u1", "city": "Tel Aviv",
         "description": "Requirements: Python and Kubernetes"},
        {"id": "j2", "title": "Data Scientist", "url": "u2", "city": "Haifa",
         "description": "Requirements: pandas"},
    ], NOW)
    jobs.upsert_scraped(conn, "beta", [
        {"id": "j3", "title": "Platform Engineer", "url": "u3", "city": "Tel Aviv", "is_remote": True,
         "description": "Requirements: Kubernetes"},
    ], NOW)
    scores.write_scores(conn, "j1", {"default": {"score": 90, "cache_key": "a"}})
    scores.write_scores(conn, "j2", {"default": {"score": 40, "cache_key": "b"}})
    scores.write_scores(conn, "j3", {"default": {"score": 70, "cache_key": "c"}})
    return conn


def test_no_filters_returns_every_job_best_score_first():
    result = search.search_jobs(_conn())
    assert result["total"] == 3
    assert [j["id"] for j in result["jobs"]] == ["j1", "j3", "j2"]


def test_list_rows_never_carry_the_description():
    """Descriptions were most of the 77MB the old page shipped; the list
    view must not reintroduce that."""
    result = search.search_jobs(_conn())
    assert "description" not in result["jobs"][0]


def test_a_row_carries_a_bounded_snippet_not_the_description():
    """A card shows a few lines. The cap is what keeps that from becoming
    the whole description again by another name."""
    conn = _conn()
    long_text = "Kubernetes. " * 500
    jobs.upsert_scraped(conn, "acme", [{"id": "j9", "title": "SRE", "url": "u9", "description": long_text}], NOW)
    row = next(j for j in search.search_jobs(conn, company_id="acme")["jobs"] if j["id"] == "j9")
    assert len(row["snippet"]) == search.SNIPPET_CHARS
    assert row["description_length"] == len(long_text)


def test_every_row_carries_its_company_name():
    result = search.search_jobs(_conn(), company_id="beta")
    assert result["jobs"][0]["company"] == "Beta"


def test_full_text_search_matches_title_and_description():
    conn = _conn()
    assert {j["id"] for j in search.search_jobs(conn, q="kubernetes")["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, q="scientist")["jobs"]} == {"j2"}


def test_filters_narrow_and_combine():
    conn = _conn()
    assert {j["id"] for j in search.search_jobs(conn, city="Tel Aviv")["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, company_id="beta")["jobs"]} == {"j3"}
    assert {j["id"] for j in search.search_jobs(conn, city="Tel Aviv", min_score=80)["jobs"]} == {"j1"}
    assert {j["id"] for j in search.search_jobs(conn, is_remote=True)["jobs"]} == {"j3"}


def test_closed_jobs_are_still_findable_by_status():
    """The old page hid them behind a toggle; here they are a filter, and
    searching them is the point of having a database."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Senior Backend Engineer", "url": "u1"}], LATER)
    assert {j["id"] for j in search.search_jobs(conn, status="closed")["jobs"]} == {"j2"}
    assert search.search_jobs(conn)["total"] == 3


def test_sorting_by_date_and_company():
    conn = _conn()
    assert [j["id"] for j in search.search_jobs(conn, sort="company")["jobs"]] == ["j1", "j2", "j3"]
    assert [j["id"] for j in search.search_jobs(conn, sort="date")["jobs"]][0] in {"j1", "j2", "j3"}


def test_pagination_reports_the_full_total():
    result = search.search_jobs(_conn(), page=2, size=2)
    assert result["total"] == 3 and [j["id"] for j in result["jobs"]] == ["j2"]


def test_a_query_with_no_matches_is_empty_not_an_error():
    assert search.search_jobs(_conn(), q="nonexistentterm")["total"] == 0


def test_fts_special_characters_do_not_crash_the_query():
    """A user typing C++ or a stray quote must not produce a syntax error."""
    for query in ["C++", 'senior "backend', "it's", "AND", "*", "-", ""]:
        assert search.search_jobs(_conn(), q=query)["total"] >= 0


def test_search_finds_a_hebrew_title():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j4", "title": "מהנדס תוכנה", "url": "u4"}], NOW)
    assert {j["id"] for j in search.search_jobs(conn, q="מהנדס")["jobs"]} == {"j4"}


def test_rows_carry_the_users_own_flags():
    from jobfit.store import state

    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    rows = {j["id"]: j for j in search.search_jobs(conn)["jobs"]}
    assert rows["j1"]["liked"] is True and rows["j1"]["hidden"] is False
    assert rows["j2"]["liked"] is False


def test_filtering_by_liked_and_hidden():
    from jobfit.store import state

    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    state.set_state(conn, "j3", hidden=True)
    assert {j["id"] for j in search.search_jobs(conn, liked=True)["jobs"]} == {"j1"}
    assert {j["id"] for j in search.search_jobs(conn, hidden=False)["jobs"]} == {"j1", "j2"}
    assert search.search_jobs(conn, liked=True, hidden=True)["total"] == 0


def test_a_job_with_no_state_row_is_still_returned():
    """Most jobs have no state row; an inner join would hide all of them."""
    assert search.search_jobs(_conn())["total"] == 3


def test_filtering_by_whether_anyone_i_know_works_there():
    from jobfit.store import companies as store_companies

    conn = _conn()
    store_companies.refresh_connection_counts(conn, {"acme": [_contact("Jane")]})
    assert {j["id"] for j in search.search_jobs(conn, has_connection=True)["jobs"]} == {"j1", "j2"}
    assert {j["id"] for j in search.search_jobs(conn, has_connection=False)["jobs"]} == {"j3"}


def test_rows_report_their_connection_count_and_who_those_contacts_are():
    """A job card names the people, not just how many: knowing someone is
    only useful once you know which someone."""
    from jobfit.store import companies as store_companies

    conn = _conn()
    store_companies.refresh_connection_counts(conn, {"acme": [_contact("Jane"), _contact("Bob")]})
    rows = {j["id"]: j for j in search.search_jobs(conn)["jobs"]}
    assert rows["j1"]["connection_count"] == 2 and rows["j3"]["connection_count"] == 0
    assert [c["name"] for c in rows["j1"]["contacts"]] == ["Bob", "Jane"]
    assert rows["j3"]["contacts"] == []


def test_status_open_means_anything_not_closed():
    """The old page hid closed jobs behind a toggle. "open" is one filter
    value rather than asking callers to enumerate new + seen."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Senior Backend Engineer", "url": "u1"}], LATER)
    assert {j["id"] for j in search.search_jobs(conn, status="open")["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, status="closed")["jobs"]} == {"j2"}


# --- the filters the old page had ------------------------------------------

def _rich():
    """Its own jobs, varied enough to tell the new filters apart."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme", industry="Software")
    companies.upsert_company(conn, "beta", "Beta", industry="Security")
    jobs.upsert_scraped(conn, "acme", [
        {"id": "a1", "title": "Senior Backend Engineer", "url": "a1", "department": "R&D",
         "description": "Requirements: 5+ years experience with Python", "years_required": 5,
         "posted_at": "2026-09-28", "source_language": "he"},
        {"id": "a2", "title": "Recruiter", "url": "a2", "department": "HR", "description": "",
         "posted_at": "2026-01-01"},
    ], NOW)
    jobs.upsert_scraped(conn, "beta", [
        {"id": "b1", "title": "Security Researcher", "url": "b1", "department": "R&D",
         "description": "Requirements: reverse engineering", "years_required": 2,
         "is_referral": True, "referral_contact": "Jane", "posted_at": "2026-09-29"},
    ], NOW)
    return conn


def test_search_can_be_limited_to_titles():
    conn = _rich()
    assert {j["id"] for j in search.search_jobs(conn, q="python")["jobs"]} == {"a1"}
    assert search.search_jobs(conn, q="python", scope="title")["total"] == 0
    assert {j["id"] for j in search.search_jobs(conn, q="engineer", scope="title")["jobs"]} == {"a1"}


def test_terms_can_be_excluded():
    conn = _rich()
    assert {j["id"] for j in search.search_jobs(conn, exclude="recruiter")["jobs"]} == {"a1", "b1"}
    assert {j["id"] for j in search.search_jobs(conn, q="engineer", exclude="senior")["jobs"]} == set()


def test_filtering_by_department_industry_and_language():
    """Both fixtures say department="R&D", and the store does not store that.
    "Senior Backend Engineer" becomes Software Engineering; "Security
    Researcher" becomes Security, because the title beats the org chart."""
    conn = _rich()
    assert {j["id"] for j in search.search_jobs(conn, department="Software Engineering")["jobs"]} == {"a1"}
    assert {j["id"] for j in search.search_jobs(conn, department="Security")["jobs"]} == {"b1"}
    assert search.search_jobs(conn, department="R&D")["total"] == 0
    assert {j["id"] for j in search.search_jobs(conn, industry="Security")["jobs"]} == {"b1"}
    assert {j["id"] for j in search.search_jobs(conn, language="he")["jobs"]} == {"a1"}


def test_filtering_by_years_required_and_posted_date():
    """A job that never stated its years stays in: silence is not evidence
    that it wants more experience than you have."""
    conn = _rich()
    assert {j["id"] for j in search.search_jobs(conn, max_years=3)["jobs"]} == {"a2", "b1"}
    assert {j["id"] for j in search.search_jobs(conn, posted_after="2026-09-01")["jobs"]} == {"a1", "b1"}


def test_filtering_by_referral_and_by_having_a_description():
    conn = _rich()
    assert {j["id"] for j in search.search_jobs(conn, is_referral=True)["jobs"]} == {"b1"}
    assert {j["id"] for j in search.search_jobs(conn, has_description=True)["jobs"]} == {"a1", "b1"}
    assert {j["id"] for j in search.search_jobs(conn, has_description=False)["jobs"]} == {"a2"}


def test_several_companies_or_cities_at_once():
    """The old page let you tick a set of companies, not just one."""
    conn = _conn()
    assert {j["id"] for j in search.search_jobs(conn, company_id="acme,beta")["jobs"]} == {"j1", "j2", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, city="Tel Aviv,Haifa")["jobs"]} == {"j1", "j2", "j3"}


def test_filtering_by_reached_out():
    from jobfit.store import state

    conn = _conn()
    state.set_state(conn, "j1", reached_out=True)
    assert {j["id"] for j in search.search_jobs(conn, reached_out=True)["jobs"]} == {"j1"}
