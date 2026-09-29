"""Company rows.

The primary key IS company identity. company_registry.json used to guard
that with a duplicate-detection gate over 1,695 files, and still let 206
duplicates through; here a second record for a company that already exists
is simply an update.
"""

from __future__ import annotations

import sqlite3

_FIELDS = ("display_name", "career_url", "review_decision", "host", "industry",
           "size", "address_city", "last_checked")


def upsert_company(conn: sqlite3.Connection, company_id: str, display_name: str, **fields) -> None:
    unknown = set(fields) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unknown company field(s): {sorted(unknown)}")
    values = {"display_name": display_name, **fields}
    columns = ", ".join(["id", *values])
    placeholders = ", ".join([":id", *(f":{k}" for k in values)])
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    conn.execute(
        f"INSERT INTO companies ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        {"id": company_id, **values},
    )


def get_company(conn: sqlite3.Connection, company_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()


def list_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies ORDER BY display_name COLLATE NOCASE").fetchall()


def companies_to_scrape(conn: sqlite3.Connection) -> dict[str, str | None]:
    """{display_name: career_url|None} for every company a run should check.

    The rule update_jobs.load_companies_to_scrape() applied to the files: a
    company with a URL is checked; one without is checked only if a review
    said "techmap". A "skip" decision stops a company either way - that is
    how a deduplicated loser keeps its jobs but stops being scraped.
    """
    rows = conn.execute(
        "SELECT display_name, career_url FROM companies "
        "WHERE (review_decision IS NULL OR review_decision != 'skip') "
        "  AND (career_url IS NOT NULL OR review_decision = 'techmap') "
        "ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return {r["display_name"]: r["career_url"] for r in rows}


def mark_checked(conn: sqlite3.Connection, company_id: str, when: str) -> None:
    conn.execute("UPDATE companies SET last_checked = ? WHERE id = ?", (when, company_id))
