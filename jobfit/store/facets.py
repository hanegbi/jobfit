"""Counts beside a filtered search: how many jobs each company, city and
status would contribute.

Built from the same WHERE clause as the search itself (search.build_filter),
because a count that disagreed with the list it annotates would be worse
than showing no count at all.
"""

from __future__ import annotations

import sqlite3

from jobfit.store.search import JOINS, NO_MATCH, build_filter

_EMPTY = {"companies": [], "cities": [], "statuses": {}, "departments": [], "industries": [],
          "languages": [], "years": [], "totals": {}}

# The sidebar shows eight of a list and expands to a few hundred. Shipping
# every one of 1,492 companies cost 85KB of the 104KB response and bought
# nothing, so the tail is cut here rather than in the browser. The counts are
# ordered by size, so what is cut is always the smallest.
MAX_PER_DIMENSION = 250


def _ranked(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Biggest first, then by name, so a tie is stable and the tail that
    MAX_PER_DIMENSION drops is always the least useful."""
    return sorted(rows, key=lambda r: (-r["n"], str(r["label"] or r["value"]).lower()))[:MAX_PER_DIMENSION]


def _dimension_counts(
    conn: sqlite3.Connection, filters: dict, *, filter_key: str, column: str, label_column: str | None = None,
) -> list[sqlite3.Row]:
    """Counts for one multi-select sidebar dimension, with THAT dimension's
    own filter excluded from the query that counts it.

    Without this, picking "Tel Aviv" would filter the row set down to
    city='Tel Aviv' BEFORE counting cities, so every other city vanishes
    from the sidebar and there is no way to add a second one - multi-select
    in the UI with no way to select more than one value underneath it. Every
    OTHER filter (q, status, liked, a different dimension's own selection,
    ...) still narrows the count, same as search_jobs itself.

    This is the same fix _status_counts already applied to status,
    generalized to every dimension that is actually a multi-select picker in
    the sidebar (company, city, department, industry, language) - "years" is
    not one (it is a single number input, not a tickable list) and stays out
    of this.
    """
    clause, params = build_filter(**{**filters, filter_key: None})
    if clause is NO_MATCH:
        return []
    not_null = f"{column} IS NOT NULL"
    clause = f"{clause} AND {not_null}" if clause else f"WHERE {not_null}"
    label_sql = f"{label_column} AS label" if label_column else f"{column} AS label"
    return conn.execute(
        f"SELECT {column} AS value, {label_sql}, count(*) AS n {JOINS} {clause} GROUP BY {column}", params,
    ).fetchall()


def _status_counts(conn: sqlite3.Connection, filters: dict) -> dict[str, int]:
    """Status is multi-select too (see search.build_filter), but it has no
    label/cap/rank to carry - a flat {value: n} is all it ever needed."""
    clause, params = build_filter(**{**filters, "status": None})
    if clause is NO_MATCH:
        return {}
    rows = conn.execute(f"SELECT j.status AS value, count(*) AS n {JOINS} {clause} GROUP BY j.status", params).fetchall()
    return {row["value"]: row["n"] for row in rows}


def counts(conn: sqlite3.Connection, **filters) -> dict:
    # years has no picker of its own (Max years is a number input, not a
    # tickable list), so it stays computed from the full, unmodified filter
    # set - there is nothing to self-exclude. Also doubles as the NO_MATCH
    # short-circuit every dimension needs, before six dimension-specific passes.
    full_clause, full_params = build_filter(**filters)
    if full_clause is NO_MATCH:
        return {**_EMPTY}

    company_rows = _dimension_counts(conn, filters, filter_key="company_id", column="j.company_id", label_column="c.display_name")
    city_rows = _dimension_counts(conn, filters, filter_key="city", column="j.city")
    department_rows = _dimension_counts(conn, filters, filter_key="department", column="j.department")
    industry_rows = _dimension_counts(conn, filters, filter_key="industry", column="c.industry")
    language_rows = _dimension_counts(conn, filters, filter_key="language", column="j.source_language")
    status_counts = _status_counts(conn, filters)

    years_clause = f"{full_clause} AND j.years_required IS NOT NULL" if full_clause else "WHERE j.years_required IS NOT NULL"
    years_rows = conn.execute(
        f"SELECT j.years_required AS value, j.years_required AS label, count(*) AS n {JOINS} {years_clause} "
        f"GROUP BY j.years_required",
        full_params,
    ).fetchall()

    # How many distinct values each dimension really has, before the cap. The
    # list is for picking from; this is for counting by. Capping without it
    # made the page report "across 250 companies" when the answer was 1,492.
    return {
        "totals": {
            "companies": len(company_rows), "cities": len(city_rows),
            "departments": len(department_rows), "industries": len(industry_rows),
            "languages": len(language_rows), "years": len(years_rows),
        },
        "companies": [{"id": r["value"], "name": r["label"], "n": r["n"]} for r in _ranked(company_rows)],
        "cities": [{"city": r["value"], "n": r["n"]} for r in _ranked(city_rows)],
        "statuses": status_counts,
        "departments": [{"department": r["value"], "n": r["n"]} for r in _ranked(department_rows)],
        "industries": [{"industry": r["value"], "n": r["n"]} for r in _ranked(industry_rows)],
        "languages": [{"language": r["value"], "n": r["n"]} for r in _ranked(language_rows)],
        "years": [{"years": r["value"], "n": r["n"]} for r in _ranked(years_rows)],
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
