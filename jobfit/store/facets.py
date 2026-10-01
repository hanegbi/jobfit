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

# The sidebar shows eight of a list and expands to a few hundred. Shipping
# every one of 1,492 companies cost 85KB of the 104KB response and bought
# nothing, so the tail is cut here rather than in the browser. The counts are
# ordered by size, so what is cut is always the smallest.
MAX_PER_DIMENSION = 250

# One row per (dimension, value). Every dimension groups the same filtered
# set, so the set is built ONCE: without MATERIALIZED, SQLite re-runs the CTE
# for each branch of the UNION and the whole thing costs the same as the seven
# separate queries this replaced (measured: 1.09s -> 0.23s on 13,448 rows).
_FACET_SQL = """
WITH m AS MATERIALIZED (
    SELECT j.company_id, c.display_name, j.city, j.status, j.department,
           c.industry, j.source_language, j.years_required
    {joins} {clause}
)
SELECT 'company' AS dim, company_id AS value, display_name AS label, count(*) AS n
  FROM m GROUP BY company_id
UNION ALL SELECT 'city', city, NULL, count(*) FROM m WHERE city IS NOT NULL GROUP BY city
UNION ALL SELECT 'status', status, NULL, count(*) FROM m GROUP BY status
UNION ALL SELECT 'department', department, NULL, count(*) FROM m
  WHERE department IS NOT NULL GROUP BY department
UNION ALL SELECT 'industry', industry, NULL, count(*) FROM m
  WHERE industry IS NOT NULL GROUP BY industry
UNION ALL SELECT 'language', source_language, NULL, count(*) FROM m
  WHERE source_language IS NOT NULL GROUP BY source_language
UNION ALL SELECT 'years', years_required, NULL, count(*) FROM m
  WHERE years_required IS NOT NULL GROUP BY years_required
"""


def counts(conn: sqlite3.Connection, **filters) -> dict:
    clause, params = build_filter(**filters)
    if clause is NO_MATCH:
        return {**_EMPTY}

    rows = conn.execute(_FACET_SQL.format(joins=JOINS, clause=clause), params).fetchall()

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["dim"], []).append(row)

    def ranked(dim: str) -> list[sqlite3.Row]:
        """Biggest first, then by name, so a tie is stable and the tail that
        MAX_PER_DIMENSION drops is always the least useful."""
        entries = grouped.get(dim, [])
        entries.sort(key=lambda r: (-r["n"], str(r["label"] or r["value"]).lower()))
        return entries[:MAX_PER_DIMENSION]

    return {
        "companies": [{"id": r["value"], "name": r["label"], "n": r["n"]} for r in ranked("company")],
        "cities": [{"city": r["value"], "n": r["n"]} for r in ranked("city")],
        "statuses": {r["value"]: r["n"] for r in grouped.get("status", [])},
        "departments": [{"department": r["value"], "n": r["n"]} for r in ranked("department")],
        "industries": [{"industry": r["value"], "n": r["n"]} for r in ranked("industry")],
        "languages": [{"language": r["value"], "n": r["n"]} for r in ranked("language")],
        "years": [{"years": r["value"], "n": r["n"]} for r in ranked("years")],
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
