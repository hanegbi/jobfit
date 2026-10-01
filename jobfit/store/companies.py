"""Company rows.

The primary key IS company identity. company_registry.json used to guard
that with a duplicate-detection gate over 1,695 files, and still let 206
duplicates through; here a second record for a company that already exists
is simply an update.
"""

from __future__ import annotations

import sqlite3

_FIELDS = ("display_name", "career_url", "review_decision", "host", "industry",
           "size", "address_city", "last_checked", "connection_count")


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


def refresh_connection_counts(conn: sqlite3.Connection, contacts_by_key: dict[str, list]) -> int:
    """Set every company's contacts, and their count, from the connections index.

    Clears both for companies absent from it, so deleting the CSV really does
    mean "I know nobody" rather than leaving stale numbers behind. The count
    and the names are written together and from the same source, so a card
    saying "3 contacts" can never list two. Returns how many companies ended
    up with at least one contact."""
    from jobfit import connections as connections_module

    conn.execute("UPDATE companies SET connection_count = 0 WHERE connection_count != 0")
    conn.execute("DELETE FROM company_contacts")
    touched = 0
    for row in conn.execute("SELECT id, display_name FROM companies").fetchall():
        contacts = contacts_by_key.get(connections_module.normalize_company(row["display_name"])) or []
        if not contacts:
            continue
        conn.execute("UPDATE companies SET connection_count = ? WHERE id = ?", (len(contacts), row["id"]))
        conn.executemany(
            "INSERT OR IGNORE INTO company_contacts (company_id, name, position, url) VALUES (?, ?, ?, ?)",
            [(row["id"], c.get("name") or "", c.get("position"), c.get("url")) for c in contacts if c.get("name")],
        )
        touched += 1
    return touched


def contacts_for(conn: sqlite3.Connection, company_ids: list[str]) -> dict[str, list[dict]]:
    """{company id: [{name, position, url}, ...]} for the companies given.

    One query for a whole page of results rather than a join onto jobs, which
    would multiply every job row by its company's contact count."""
    if not company_ids:
        return {}
    placeholders = ", ".join("?" * len(company_ids))
    rows = conn.execute(
        f"SELECT company_id, name, position, url FROM company_contacts "
        f"WHERE company_id IN ({placeholders}) ORDER BY company_id, name COLLATE NOCASE",
        company_ids,
    ).fetchall()
    by_company: dict[str, list[dict]] = {}
    for row in rows:
        by_company.setdefault(row["company_id"], []).append(
            {"name": row["name"], "position": row["position"], "url": row["url"]})
    return by_company


DECISIONS = ("techmap", "skip")


def _id_for(conn: sqlite3.Connection, display_name: str) -> str:
    """The company id for a display name, raising KeyError when it is not
    tracked - so a typo from the panel is a 404 rather than a new company."""
    row = conn.execute("SELECT id FROM companies WHERE display_name = ?", (display_name,)).fetchone()
    if row is None:
        raise KeyError(display_name)
    return row["id"]


def set_decision(conn: sqlite3.Connection, display_name: str, decision: str) -> None:
    """Approve techmap's own data as a fallback source, or mark the company
    skipped."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS!r}, got {decision!r}")
    conn.execute("UPDATE companies SET review_decision = ? WHERE id = ?", (decision, _id_for(conn, display_name)))


def set_career_url(conn: sqlite3.Connection, display_name: str, url: str) -> None:
    """Give a company a real career URL. Any earlier review decision is
    cleared: it then flows through the normal cascade like any other company,
    so a decision made when it had no URL no longer applies."""
    conn.execute(
        "UPDATE companies SET career_url = ?, review_decision = NULL WHERE id = ?",
        (url, _id_for(conn, display_name)),
    )


def needing_review(conn: sqlite3.Connection, techmap_index: dict[str, list[dict]]) -> list[dict]:
    """Every company with no career URL, with techmap availability and the
    current decision. Undecided companies that techmap can actually cover
    come first - those are the ones where a click helps right now."""
    from jobfit import connections as connections_module

    results = []
    for row in conn.execute(
        "SELECT display_name, review_decision FROM companies WHERE career_url IS NULL"
    ).fetchall():
        rows = techmap_index.get(connections_module.normalize_company(row["display_name"]), [])
        results.append({
            "company": row["display_name"],
            "decision": row["review_decision"] or "pending",
            "has_techmap": bool(rows),
            "techmap_job_count": len(rows),
            "techmap_sample_title": rows[0]["title"] if rows else None,
        })
    results.sort(key=lambda r: (r["decision"] != "pending", not r["has_techmap"], r["company"].lower()))
    return results


def exclusive_career_hosts(conn: sqlite3.Connection) -> dict[str, str]:
    """{host: company_id} for hosts that belong to exactly ONE company.

    A scrape of company A should not claim jobs served from company B's
    career host. Team8's portfolio board is the case that forced this: its
    52 jobs for FlowRx, Briya and C8 Health were all filed under BlueSpine,
    whose own scrape had wandered onto it.

    Shared ATS hosts are deliberately excluded by the "exactly one" rule -
    boards.greenhouse.io and jobs.lever.co host hundreds of companies, and
    there the host says nothing about whose job it is.
    """
    from urllib.parse import urlsplit

    owners: dict[str, set[str]] = {}
    for row in conn.execute("SELECT id, career_url FROM companies WHERE career_url IS NOT NULL AND career_url != ''"):
        host = urlsplit(row["career_url"]).netloc.lower().removeprefix("www.")
        if host:
            owners.setdefault(host, set()).add(row["id"])
    return {host: next(iter(ids)) for host, ids in owners.items() if len(ids) == 1}
