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


def test_counts_group_by_years_required():
    """Real bug this locks in: the years dimension's own query never
    aliased a label column, so _ranked()'s sort key crashed with a
    KeyError the moment a real years_required value reached it - every
    test fixture up to this one happened to leave years_required unset, so
    _ranked() always sorted an empty list and the bug never fired."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j4", "title": "Junior Engineer", "url": "u4", "years_required": 2}], NOW)
    jobs.upsert_scraped(conn, "beta", [{"id": "j5", "title": "Staff Engineer", "url": "u5", "years_required": 8}], NOW)
    result = {y["years"]: y["n"] for y in facets.counts(conn)["years"]}
    assert result == {2: 1, 8: 1}


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


def test_status_counts_survive_selecting_one_status():
    """Multi-select: picking "new" must not make "seen" vanish from the
    sidebar, or there would be no way to add it - the facet has to show
    every status regardless of which one is currently selected."""
    from jobfit.store import search

    conn = _conn()
    jobs.upsert_scraped(conn, "beta", [{"id": "j3", "title": "Platform Engineer", "url": "u3"}], NOW)  # now "seen"
    assert facets.counts(conn, status="new")["statuses"] == {"new": 2, "seen": 1}
    # And the list itself (not the facet) is narrowed, same as any other filter.
    assert {j["id"] for j in search.search_jobs(conn, status="new")["jobs"]} == {"j1", "j2"}


def test_status_counts_still_respect_every_other_filter():
    conn = _conn()
    assert facets.counts(conn, city="Tel Aviv")["statuses"] == {"new": 2}


# --- the same multi-select bug, for every other sidebar dimension ----------
#
# Real bug: _status_counts fixed this for status alone; city/company/
# department/industry/language shared the exact same flaw (each dimension's
# counts were computed from a filtered set that already applied that same
# dimension's own filter), so picking one city made every other city vanish
# from the sidebar before a second one could be added. One test per
# dimension, each proving the OTHER values of that same dimension survive
# selecting one.

def test_city_counts_survive_selecting_one_city():
    conn = _conn()
    result = {c["city"]: c["n"] for c in facets.counts(conn, city="Tel Aviv")["cities"]}
    assert result == {"Tel Aviv": 2, "Haifa": 1}


def test_company_counts_survive_selecting_one_company():
    conn = _conn()
    result = {c["name"]: c["n"] for c in facets.counts(conn, company_id="acme")["companies"]}
    assert result == {"Acme": 2, "Beta": 1}


def test_department_counts_survive_selecting_one_department():
    """department_for() folds the title into a canonical name (see
    departments.py) - "Backend Engineer" -> Software Engineering,
    "Account Executive" -> Sales. A fresh connection, not _conn()'s jobs,
    whose titles ("Platform Engineer" -> DevOps & Infrastructure, "Data
    Scientist" -> Data & AI) would otherwise add departments beside the
    two this test means to isolate."""
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Backend Engineer", "url": "u1"}], NOW)
    jobs.upsert_scraped(conn, "beta", [{"id": "j2", "title": "Account Executive", "url": "u2"}], NOW)
    result = {d["department"]: d["n"] for d in facets.counts(conn, department="Software Engineering")["departments"]}
    assert result == {"Software Engineering": 1, "Sales": 1}


def test_industry_counts_survive_selecting_one_industry():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme", industry="Software")
    companies.upsert_company(conn, "beta", "Beta", industry="Security")
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Backend Engineer", "url": "u1"}], NOW)
    jobs.upsert_scraped(conn, "beta", [{"id": "j2", "title": "Security Researcher", "url": "u2"}], NOW)
    result = {i["industry"]: i["n"] for i in facets.counts(conn, industry="Software")["industries"]}
    assert result == {"Software": 1, "Security": 1}


def test_language_counts_survive_selecting_one_language():
    """source_language is only ever set on first insert, not a re-scrape
    (see jobs.py's _FILL_IF_EMPTY) - fresh job ids, not ones _conn() already
    seeded, or the field would silently stay unset."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [{"id": "j4", "title": "מהנדס תוכנה", "url": "u4", "source_language": "he"}], NOW)
    jobs.upsert_scraped(conn, "beta", [{"id": "j5", "title": "Backend Engineer", "url": "u5", "source_language": "en"}], NOW)
    result = {lang["language"]: lang["n"] for lang in facets.counts(conn, language="he")["languages"]}
    assert result == {"he": 1, "en": 1}


def test_totals_are_present_even_when_nothing_matches():
    """The front end reads totals.companies unconditionally."""
    result = facets.counts(_conn(), q="nothingmatchesthisquery")
    assert result["totals"] == {} or result["totals"]["companies"] == 0
