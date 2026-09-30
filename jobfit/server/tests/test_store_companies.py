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


def _contact(name, position="Engineer"):
    """One entry of connections.load_connections_index's output."""
    return {"name": name, "position": position, "url": f"https://linkedin.com/in/{name.lower()}"}


def test_connection_counts_are_refreshed_from_the_contacts_index():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.upsert_company(conn, "beta", "Beta")
    # Keys are connections.normalize_company output, which strips "Ltd.";
    # values are the index's own shape, one dict per contact.
    assert companies.refresh_connection_counts(conn, {"acme": [_contact("Jane"), _contact("Bob")]}) == 1
    assert companies.get_company(conn, "acme")["connection_count"] == 2
    assert companies.get_company(conn, "beta")["connection_count"] == 0


def test_refreshing_again_replaces_rather_than_adds():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.refresh_connection_counts(conn, {"acme": [_contact("Jane"), _contact("Bob")]})
    companies.refresh_connection_counts(conn, {"acme": [_contact("Jane")]})
    assert companies.get_company(conn, "acme")["connection_count"] == 1


def test_a_removed_connections_file_clears_every_count():
    """Deleting the CSV must mean "I know nobody", not "keep the old numbers"."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.refresh_connection_counts(conn, {"acme": [_contact("Jane")]})
    companies.refresh_connection_counts(conn, {})
    assert companies.get_company(conn, "acme")["connection_count"] == 0
    assert companies.contacts_for(conn, ["acme"]) == {}


def test_contacts_are_stored_with_the_count_so_the_two_cannot_disagree():
    """A card saying "3 contacts" must never be able to list two."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme Ltd.")
    companies.refresh_connection_counts(conn, {"acme": [_contact("Jane"), _contact("Bob", "Recruiter")]})

    stored = companies.contacts_for(conn, ["acme"])["acme"]
    assert [c["name"] for c in stored] == ["Bob", "Jane"]  # ordered by name
    assert stored[0]["position"] == "Recruiter"
    assert companies.get_company(conn, "acme")["connection_count"] == len(stored)


def test_contacts_for_takes_a_page_of_companies_at_once():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme")
    companies.upsert_company(conn, "beta", "Beta")
    companies.refresh_connection_counts(
        conn, {"acme": [_contact("Jane")], "beta": [_contact("Ann"), _contact("Zed")]})

    found = companies.contacts_for(conn, ["acme", "beta", "missing"])
    assert set(found) == {"acme", "beta"}
    assert len(found["beta"]) == 2
    assert companies.contacts_for(conn, []) == {}


# --- review decisions, ported from company_review.py ------------------------

def test_set_decision_requires_a_known_company_and_a_known_decision():
    import pytest

    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme")
    companies.set_decision(conn, "Acme", "techmap")
    assert companies.get_company(conn, "acme")["review_decision"] == "techmap"

    with pytest.raises(ValueError):
        companies.set_decision(conn, "Acme", "maybe")
    with pytest.raises(KeyError):
        companies.set_decision(conn, "Nobody", "skip")


def test_setting_a_career_url_clears_an_earlier_review_decision():
    """A company with a real URL flows through the normal cascade, so a
    decision made when it had none no longer applies."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", review_decision="techmap")
    companies.set_career_url(conn, "Acme", "https://acme.com/careers")
    row = companies.get_company(conn, "acme")
    assert row["career_url"] == "https://acme.com/careers" and row["review_decision"] is None


def test_needing_review_lists_companies_with_no_url_undecided_first():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers")
    companies.upsert_company(conn, "beta", "Beta")
    companies.upsert_company(conn, "gamma", "Gamma", review_decision="skip")
    techmap = {"beta": [{"title": "Backend Engineer"}]}

    result = companies.needing_review(conn, techmap)

    assert [r["company"] for r in result] == ["Beta", "Gamma"]   # Acme has a URL
    assert result[0]["decision"] == "pending" and result[0]["has_techmap"] is True
    assert result[0]["techmap_job_count"] == 1
    assert result[1]["decision"] == "skip"
