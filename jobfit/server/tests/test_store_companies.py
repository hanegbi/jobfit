"""Company rows. The primary key is company identity - what
company_registry.json used to guard with a duplicate-detection gate."""

from jobfit.store import companies, db


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def test_upsert_inserts_then_updates_without_losing_untouched_fields():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers", industry="Software")
    companies.upsert_company(conn, "acme", "Acme Ltd.", career_url="https://acme.com/jobs")
    row = companies.get_company(conn, "acme")
    assert row["display_name"] == "Acme Ltd."
    assert row["career_url"] == "https://acme.com/jobs"
    assert row["industry"] == "Software"


def test_an_unknown_field_is_refused_rather_than_silently_dropped():
    conn = _conn()
    try:
        companies.upsert_company(conn, "acme", "Acme", careerurl="typo")
    except ValueError as error:
        assert "careerurl" in str(error)
    else:
        raise AssertionError("a misspelled field must not be accepted")


def test_companies_to_scrape_matches_the_old_file_rules():
    """A company with a URL is scraped. A company without one is scraped
    only when a review decided 'techmap'; 'skip' and undecided are out."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers")
    companies.upsert_company(conn, "beta", "Beta")
    companies.upsert_company(conn, "gamma", "Gamma", review_decision="techmap")
    companies.upsert_company(conn, "delta", "Delta", review_decision="skip")
    assert companies.companies_to_scrape(conn) == {"Acme": "https://acme.com/careers", "Gamma": None}


def test_a_skip_decision_stops_a_company_with_a_url_too():
    """How a deduplicated loser stops being scraped without losing its jobs."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers", review_decision="skip")
    assert companies.companies_to_scrape(conn) == {}


def test_mark_checked_records_the_timestamp():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme")
    companies.mark_checked(conn, "acme", "2026-09-30T10:00:00Z")
    assert companies.get_company(conn, "acme")["last_checked"] == "2026-09-30T10:00:00Z"


def test_list_companies_is_ordered_by_name_case_insensitively():
    conn = _conn()
    for company_id, name in (("zeta", "zeta"), ("acme", "Acme"), ("beta", "beta")):
        companies.upsert_company(conn, company_id, name)
    assert [r["display_name"] for r in companies.list_companies(conn)] == ["Acme", "beta", "zeta"]
