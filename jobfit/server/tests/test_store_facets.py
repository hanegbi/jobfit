"""Counts beside a filtered search. They share the search's own WHERE
clause, so a count can never disagree with the list it annotates."""

from jobfit.store import companies, db, facets, jobs

NOW = "2026-09-30T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers")
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "u1", "city": "Tel Aviv"},
        {"id": "j2", "title": "Data Scientist", "url": "u2", "city": "Haifa"},
    ], NOW)
    jobs.upsert_scraped(conn, "beta", [
        {"id": "j3", "title": "Platform Engineer", "url": "u3", "city": "Tel Aviv"},
    ], NOW)
    return conn


def test_counts_group_by_company_city_and_status():
    result = facets.counts(_conn())
    assert {c["name"]: c["n"] for c in result["companies"]} == {"Acme": 2, "Beta": 1}
    assert {c["city"]: c["n"] for c in result["cities"]} == {"Tel Aviv": 2, "Haifa": 1}
    assert result["statuses"] == {"new": 3}


def test_counts_respect_the_current_filter():
    """The point of a facet: "how many would each of these narrow me to"."""
    result = facets.counts(_conn(), city="Tel Aviv")
    assert {c["name"]: c["n"] for c in result["companies"]} == {"Acme": 1, "Beta": 1}


def test_counts_respect_a_text_query():
    result = facets.counts(_conn(), q="engineer")
    assert {c["name"]: c["n"] for c in result["companies"]} == {"Acme": 1, "Beta": 1}


def test_counts_respect_the_users_flags():
    from jobfit.store import state

    conn = _conn()
    state.set_state(conn, "j1", liked=True)
    assert facets.counts(conn, liked=True)["statuses"] == {"new": 1}


def test_a_query_with_no_searchable_terms_counts_nothing():
    result = facets.counts(_conn(), q="***")
    assert result["companies"] == [] and result["statuses"] == {}


def test_cities_omit_jobs_with_no_city():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j4", "title": "Remote Role", "url": "u4"}], NOW)
    assert sum(c["n"] for c in facets.counts(conn)["cities"]) == 3


def test_counts_agree_with_the_search_they_annotate():
    """The invariant the shared filter exists for."""
    from jobfit.store import search

    conn = _conn()
    for filters in ({}, {"city": "Tel Aviv"}, {"q": "engineer"}, {"company_id": "acme"}):
        listed = search.search_jobs(conn, size=500, **filters)["total"]
        counted = sum(facets.counts(conn, **filters)["statuses"].values())
        assert listed == counted, filters


def test_companies_report_open_and_total_counts():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Backend Engineer", "url": "u1"}],
                        "2026-10-02T10:00:00Z")
    listing = {c["id"]: c for c in facets.companies(conn)}
    assert listing["acme"]["total_jobs"] == 2 and listing["acme"]["open_jobs"] == 1
    assert listing["acme"]["career_url"] == "https://acme.com/careers"


def test_companies_with_no_jobs_still_appear():
    """3,496 companies are tracked and most have never yielded a job; a list
    that hides them cannot be used to fix their career URLs."""
    conn = _conn()
    companies.upsert_company(conn, "dormant", "Dormant")
    listing = {c["id"]: c for c in facets.companies(conn)}
    assert listing["dormant"]["total_jobs"] == 0 and listing["dormant"]["open_jobs"] == 0


# --- the shape that keeps it fast -------------------------------------------

def test_every_dimension_comes_from_one_pass_over_the_rows():
    """Seven separate GROUP BYs over the same filtered set cost seven scans
    (measured: 1.09s on 13,448 rows, against 0.23s for one materialized
    pass). If this ever becomes several statements again, the search gets
    slow in a way no functional test would notice."""
    assert facets._FACET_SQL.count("SELECT") == 8  # the CTE plus seven dimensions
    assert "MATERIALIZED" in facets._FACET_SQL, "without this SQLite re-runs the CTE per branch"
    assert facets._FACET_SQL.count(";") == 0, "one statement, one scan"


def test_a_dimension_is_capped_so_the_response_stays_small():
    """1,492 companies was 85KB of a 104KB response, for a list that shows
    eight. The cut falls on the smallest counts, which is why it is ordered."""
    conn = _conn()
    for index in range(facets.MAX_PER_DIMENSION + 20):
        company_id = f"c{index:04d}"
        companies.upsert_company(conn, company_id, f"Company {index:04d}")
        # Earlier companies get more jobs, so the biggest must survive the cut.
        for job in range(2 if index < 5 else 1):
            jobs.upsert_scraped(conn, company_id, [
                {"id": f"{company_id}-{job}", "title": "Engineer", "url": f"https://x/{company_id}/{job}"},
            ], NOW)

    result = facets.counts(conn)
    assert len(result["companies"]) == facets.MAX_PER_DIMENSION
    assert result["companies"][0]["n"] == 2, "the biggest counts are the ones kept"


def test_a_capped_dimension_still_reports_its_true_total():
    """The cap is for payload size, not for arithmetic. Reporting the capped
    length as the count made the page say "across 250 companies" when the
    answer was 1,492."""
    conn = _conn()
    wanted = facets.MAX_PER_DIMENSION + 30
    for index in range(wanted):
        company_id = f"c{index:04d}"
        companies.upsert_company(conn, company_id, f"Company {index:04d}")
        jobs.upsert_scraped(conn, company_id, [
            {"id": f"{company_id}-1", "title": "Engineer", "url": f"https://x/{company_id}"},
        ], NOW)

    result = facets.counts(conn)
    assert len(result["companies"]) == facets.MAX_PER_DIMENSION
    # _conn() seeds its own companies, so the total is at least what we added.
    assert result["totals"]["companies"] >= wanted
    assert result["totals"]["companies"] > len(result["companies"])


def test_totals_are_present_even_when_nothing_matches():
    """The front end reads totals.companies unconditionally."""
    result = facets.counts(_conn(), q="nothingmatchesthisquery")
    assert result["totals"] == {} or result["totals"]["companies"] == 0
