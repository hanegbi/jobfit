"""connections.py's company-name normalization is shared by connections
matching AND referral-company dedup (referral_source.py, update_jobs.py) -
a bug here would silently break "which contacts work at this company" and
"is this the same company as one we already track" at once. Zero test
coverage before this."""

from jobfit import connections


# --- normalize_company ---------------------------------------------------

def test_normalize_company_strips_common_suffixes():
    assert connections.normalize_company("Acme Technologies Ltd") == connections.normalize_company("Acme")


def test_normalize_company_is_case_insensitive():
    assert connections.normalize_company("ACME") == connections.normalize_company("acme")


def test_normalize_company_strips_punctuation_and_whitespace_variance():
    assert connections.normalize_company("Acme, Inc.") == connections.normalize_company("Acme Inc")


def test_normalize_company_returns_empty_string_for_none_or_empty():
    assert connections.normalize_company(None) == ""
    assert connections.normalize_company("") == ""


def test_normalize_company_differs_for_genuinely_different_companies():
    assert connections.normalize_company("Acme") != connections.normalize_company("Widgets Co")


# --- load_connections_index -----------------------------------------------

def test_load_connections_index_parses_a_real_looking_export(tmp_path):
    csv_content = (
        "Notes:\n"
        "some LinkedIn preamble line\n"
        "\n"
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Doe,https://linkedin.com/in/janedoe,,Acme Inc,Engineer,01 Jan 2024\n"
        "John,Smith,https://linkedin.com/in/johnsmith,,Acme Inc,Manager,02 Jan 2024\n"
    )
    path = tmp_path / "Connections.csv"
    path.write_text(csv_content, encoding="utf-8")

    index = connections.load_connections_index(path)

    key = connections.normalize_company("Acme Inc")
    assert len(index[key]) == 2
    names = {c["name"] for c in index[key]}
    assert names == {"Jane Doe", "John Smith"}


def test_load_connections_index_dedupes_identical_rows(tmp_path):
    csv_content = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Doe,https://linkedin.com/in/janedoe,,Acme,Engineer,01 Jan 2024\n"
        "Jane,Doe,https://linkedin.com/in/janedoe,,Acme,Engineer,01 Jan 2024\n"
    )
    path = tmp_path / "Connections.csv"
    path.write_text(csv_content, encoding="utf-8")

    index = connections.load_connections_index(path)

    assert len(index[connections.normalize_company("Acme")]) == 1


def test_load_connections_index_returns_empty_for_a_missing_file(tmp_path):
    assert connections.load_connections_index(tmp_path / "does_not_exist.csv") == {}


def test_load_connections_index_skips_rows_with_no_company(tmp_path):
    csv_content = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Jane,Doe,https://linkedin.com/in/janedoe,,,Engineer,01 Jan 2024\n"
        "John,Smith,https://linkedin.com/in/johnsmith,,Acme,Manager,02 Jan 2024\n"
    )
    path = tmp_path / "Connections.csv"
    path.write_text(csv_content, encoding="utf-8")

    index = connections.load_connections_index(path)

    all_names = {c["name"] for contacts in index.values() for c in contacts}
    assert all_names == {"John Smith"}


# --- contacts_for_company --------------------------------------------------

def test_contacts_for_company_matches_by_normalized_name():
    index = {connections.normalize_company("Acme Ltd"): [{"name": "Jane Doe"}]}

    assert connections.contacts_for_company(index, "Acme Ltd.") == [{"name": "Jane Doe"}]
    assert connections.contacts_for_company(index, "Acme") == [{"name": "Jane Doe"}]


def test_contacts_for_company_returns_empty_list_for_no_match():
    index = {connections.normalize_company("Acme"): [{"name": "Jane Doe"}]}

    assert connections.contacts_for_company(index, "Totally Unrelated Co") == []
