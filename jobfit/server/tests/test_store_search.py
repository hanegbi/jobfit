"""The read the application is built on: filter, sort, paginate, count -
one query, so the client never holds the dataset."""

from jobfit.store import companies, db, jobs, scores, search

NOW = "2026-09-30T10:00:00Z"
LATER = "2026-10-01T10:00:00Z"


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
