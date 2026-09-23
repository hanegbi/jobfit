"""techmap_source.parse_category_csv turns techmap's raw CSV export into the
row shape the rest of the pipeline depends on - zero test coverage before this."""

from jobfit import techmap_source


def test_parses_expected_fields_and_strips_the_url_query_string():
    csv_text = (
        "company,category,size,title,level,city,url,updated\n"
        '"Acme","Fintech","l","Backend Engineer","Senior","Tel Aviv",'
        '"https://acme.com/job/1?utm_source=techmap&utm_medium=csv","2026-01-01"\n'
    )

    rows = techmap_source.parse_category_csv(csv_text, "software")

    assert len(rows) == 1
    row = rows[0]
    assert row["company"] == "Acme"
    assert row["industry"] == "Fintech"
    assert row["size"] == "l"
    assert row["title"] == "Backend Engineer"
    assert row["level"] == "Senior"
    assert row["location"] == "Tel Aviv"
    assert row["url"] == "https://acme.com/job/1"
    assert row["posted_at"] == "2026-01-01"
    assert row["function"] == "software"


def test_skips_rows_missing_company_or_title():
    csv_text = (
        "company,category,size,title,level,city,url,updated\n"
        ",Fintech,l,Backend Engineer,Senior,Tel Aviv,https://x,2026-01-01\n"
        "Acme,Fintech,l,,Senior,Tel Aviv,https://x,2026-01-01\n"
    )

    rows = techmap_source.parse_category_csv(csv_text, "software")

    assert rows == []


def test_strips_a_byte_order_mark_from_the_start_of_the_csv():
    csv_text = "﻿company,category,size,title,level,city,url,updated\nAcme,Fintech,l,Backend Engineer,,Tel Aviv,https://x,2026-01-01\n"

    rows = techmap_source.parse_category_csv(csv_text, "software")

    assert len(rows) == 1
    assert rows[0]["company"] == "Acme"
