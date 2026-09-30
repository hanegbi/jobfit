"""Counts beside a filtered search: how many jobs each company, city and
status would contribute.

Built from the same WHERE clause as the search itself (search.build_filter),
because a count that disagreed with the list it annotates would be worse
than showing no count at all.
"""

from __future__ import annotations

import sqlite3

from jobfit.store.search import JOINS, NO_MATCH, build_filter

_EMPTY = {"companies": [], "cities": [], "statuses": {}, "departments": [], "industries": [], "languages": [], "years": []}


def counts(conn: sqlite3.Connection, **filters) -> dict:
    clause, params = build_filter(**filters)
    if clause is NO_MATCH:
        return {**_EMPTY}

    companies_rows = conn.execute(
        f"SELECT j.company_id AS id, c.display_name AS name, count(*) AS n {JOINS} {clause} "
        f"GROUP BY j.company_id ORDER BY n DESC, name COLLATE NOCASE", params).fetchall()
    city_clause = f"{clause} AND j.city IS NOT NULL" if clause else "WHERE j.city IS NOT NULL"
    cities_rows = conn.execute(
        f"SELECT j.city, count(*) AS n {JOINS} {city_clause} "
        f"GROUP BY j.city ORDER BY n DESC, j.city COLLATE NOCASE", params).fetchall()
    status_rows = conn.execute(
        f"SELECT j.status, count(*) AS n {JOINS} {clause} GROUP BY j.status", params).fetchall()

    def _by(column: str, alias: str) -> list[dict]:
        """One dimension of the sidebar. NULLs are omitted: "no department"
        is not a department you can usefully filter to."""
        extra = f"{clause} AND {column} IS NOT NULL" if clause else f"WHERE {column} IS NOT NULL"
        rows = conn.execute(
            f"SELECT {column} AS {alias}, count(*) AS n {JOINS} {extra} "
            f"GROUP BY {column} ORDER BY n DESC, {alias} COLLATE NOCASE", params).fetchall()
        return [dict(row) for row in rows]

    return {
        "companies": [dict(row) for row in companies_rows],
        "cities": [dict(row) for row in cities_rows],
        "statuses": {row["status"]: row["n"] for row in status_rows},
        "departments": _by("j.department", "department"),
        "industries": _by("c.industry", "industry"),
        "languages": _by("j.source_language", "language"),
        "years": _by("j.years_required", "years"),
    }


def companies(conn: sqlite3.Connection) -> list[dict]:
    """Every tracked company with its job counts. A LEFT JOIN so the ~1,800
    companies that have never yielded a job still appear - they are exactly
    the ones whose career URL needs fixing."""
    rows = conn.execute(
        "SELECT c.id, c.display_name AS name, c.career_url, c.review_decision, c.last_checked, "
        "count(j.id) AS total_jobs, "
        "sum(CASE WHEN j.status IS NOT NULL AND j.status != 'closed' THEN 1 ELSE 0 END) AS open_jobs "
        "FROM companies c LEFT JOIN jobs j ON j.company_id = c.id "
        "GROUP BY c.id ORDER BY open_jobs DESC, name COLLATE NOCASE"
    ).fetchall()
    return [{**dict(row), "open_jobs": row["open_jobs"] or 0} for row in rows]


def scored_profiles(conn: sqlite3.Connection) -> list[str]:
    """Profile ids that have at least one score."""
    return [row["profile_id"] for row in conn.execute(
        "SELECT DISTINCT profile_id FROM job_scores ORDER BY profile_id")]
