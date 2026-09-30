"""The read the application is built on.

One query answers "which jobs match, and how many are there", so a client
shows 50 rows instead of downloading 30,000. Descriptions are deliberately
absent from list rows: they were most of the 77MB the static page shipped.
"""

from __future__ import annotations

import re
import sqlite3

_LIST_COLUMNS = (
    "j.id, j.company_id, j.title, j.url, j.location, j.city, j.is_remote, j.department, "
    "j.employment_type, j.status, j.first_seen, j.last_seen, j.posted_at, j.years_required, "
    "j.is_referral, j.referral_contact, c.display_name AS company, "
    "COALESCE(st.liked, 0) AS liked, COALESCE(st.hidden, 0) AS hidden, "
    "COALESCE(st.sent, 0) AS sent, COALESCE(st.reached_out, 0) AS reached_out"
)
# LEFT JOIN on job_state: most jobs have no state row, and an inner join
# would silently return only the handful the user has already flagged.
JOINS = ("FROM jobs j JOIN companies c ON c.id = j.company_id "
         "LEFT JOIN job_state st ON st.job_id = j.id")
_SORTS = {
    "score": "best_score DESC NULLS LAST, j.id",
    "date": "COALESCE(j.posted_at, j.first_seen) DESC, j.id",
    "company": "c.display_name COLLATE NOCASE, j.id",
}
_BOOL_FIELDS = ("liked", "hidden", "sent", "reached_out", "is_remote", "is_referral")
# Word characters plus the ones that carry meaning in this domain: C++, C#,
# .NET, node.js, and Hebrew.
_TERM_RE = re.compile(r"[0-9A-Za-z֐-׿#+.]+")

# Sentinel for "the text query had no searchable terms", which must return
# nothing rather than everything.
NO_MATCH = object()


def _fts_query(raw: str) -> str:
    """FTS5 has its own query syntax, where a bare quote or a lone AND is a
    syntax error rather than a search. Every run of term characters becomes
    one quoted term and the terms are ANDed, so whatever the user types is
    data, not syntax."""
    return " AND ".join(f'"{term}"' for term in _TERM_RE.findall(raw or ""))


def score_sql(profile: str = "best") -> str:
    if profile == "best":
        return "(SELECT max(score) FROM job_scores s WHERE s.job_id = j.id)"
    return "(SELECT score FROM job_scores s WHERE s.job_id = j.id AND s.profile_id = :profile)"


def build_filter(*, q: str | None = None, company_id: str | None = None, city: str | None = None,
                 status: str | None = None, is_remote: bool | None = None, min_score: float | None = None,
                 profile: str = "best", liked: bool | None = None, hidden: bool | None = None,
                 sent: bool | None = None):
    """(where clause, params) for every filter the API exposes, or NO_MATCH
    when the text query contained no searchable terms.

    search_jobs and facets.counts both call this: a facet count that
    disagreed with the list it annotates would be worse than no facet.
    """
    where: list[str] = []
    params: dict[str, object] = {}

    if q:
        match = _fts_query(q)
        if not match:
            return NO_MATCH, {}
        where.append("j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :match)")
        params["match"] = match
    if company_id:
        where.append("j.company_id = :company_id")
        params["company_id"] = company_id
    if city:
        where.append("j.city = :city")
        params["city"] = city
    if status:
        where.append("j.status = :status")
        params["status"] = status
    if is_remote is not None:
        where.append("j.is_remote = :is_remote")
        params["is_remote"] = int(is_remote)
    for field, wanted in (("liked", liked), ("hidden", hidden), ("sent", sent)):
        if wanted is not None:
            where.append(f"COALESCE(st.{field}, 0) = :{field}")
            params[field] = int(wanted)
    if min_score is not None:
        where.append(f"{score_sql(profile)} >= :min_score")
        params["min_score"] = min_score
    if profile != "best":
        params["profile"] = profile

    return (f"WHERE {' AND '.join(where)}" if where else ""), params


def search_jobs(conn: sqlite3.Connection, *, sort: str = "score", page: int = 1, size: int = 50,
                profile: str = "best", **filters) -> dict:
    clause, params = build_filter(profile=profile, **filters)
    if clause is NO_MATCH:
        return {"total": 0, "page": page, "size": size, "jobs": []}

    total = conn.execute(f"SELECT count(*) {JOINS} {clause}", params).fetchone()[0]
    params = {**params, "limit": max(1, size), "offset": max(0, (max(1, page) - 1) * max(1, size))}
    rows = conn.execute(
        f"SELECT {_LIST_COLUMNS}, {score_sql(profile)} AS best_score {JOINS} {clause} "
        f"ORDER BY {_SORTS.get(sort, _SORTS['score'])} LIMIT :limit OFFSET :offset",
        params,
    ).fetchall()

    jobs = []
    for row in rows:
        job = dict(row)
        for field in _BOOL_FIELDS:
            job[field] = bool(job[field])
        jobs.append(job)
    return {"total": total, "page": page, "size": size, "jobs": jobs}
