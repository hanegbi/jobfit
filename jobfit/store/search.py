"""The read the application is built on.

One query answers "which jobs match, and how many are there", so a client
shows 50 rows instead of downloading 30,000. Descriptions are deliberately
absent from list rows: they were most of the 77MB the static page shipped.
"""

from __future__ import annotations

import re
import sqlite3

# SNIPPET_CHARS of description, not the description: a card shows a few lines,
# and the full text across 29,000 jobs is what the static page shipped and this
# API exists not to. The detail read is still the only way to the whole thing.
SNIPPET_CHARS = 320
_LIST_COLUMNS = (
    "j.id, j.company_id, j.title, j.url, j.location, j.city, j.is_remote, j.department, "
    "j.employment_type, j.status, j.first_seen, j.last_seen, j.posted_at, j.years_required, "
    "j.is_referral, j.referral_contact, j.source_language, "
    f"substr(j.description, 1, {SNIPPET_CHARS}) AS snippet, "
    "length(j.description) AS description_length, "
    "c.display_name AS company, c.connection_count, c.industry, "
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
    "title": "j.title COLLATE NOCASE",
}
_BOOL_FIELDS = ("liked", "hidden", "sent", "reached_out", "is_remote", "is_referral")
_STATE_FILTERS = ("liked", "hidden", "sent", "reached_out")
# Word characters plus the ones that carry meaning in this domain: C++, C#,
# .NET, node.js, and Hebrew.
_TERM_RE = re.compile(r"[0-9A-Za-z֐-׿#+.]+")

# Sentinel for "the text query had no searchable terms", which must return
# nothing rather than everything.
NO_MATCH = object()


def _fts_query(raw: str, scope: str = "all") -> str:
    """FTS5 has its own query syntax, where a bare quote or a lone AND is a
    syntax error rather than a search. Every run of term characters becomes
    one quoted term and the terms are ANDed, so whatever the user types is
    data, not syntax. scope="title" restricts matching to the title column."""
    terms = _TERM_RE.findall(raw or "")
    prefix = "title:" if scope == "title" else ""
    return " AND ".join(f'{prefix}"{term}"' for term in terms)


def score_sql(profile: str = "best") -> str:
    if profile == "best":
        return "(SELECT max(score) FROM job_scores s WHERE s.job_id = j.id)"
    return "(SELECT score FROM job_scores s WHERE s.job_id = j.id AND s.profile_id = :profile)"


def _csv(value: str) -> list[str]:
    """"a,b" -> ["a", "b"]. The old page let you tick a set of companies or
    cities, not just one."""
    return [part.strip() for part in value.split(",") if part.strip()]


def build_filter(*, q: str | None = None, scope: str = "all", exclude: str | None = None,
                 company_id: str | None = None, exclude_company_id: str | None = None,
                 city: str | None = None, status: str | None = None,
                 is_remote: bool | None = None, min_score: float | None = None, profile: str = "best",
                 liked: bool | None = None, hidden: bool | None = None, sent: bool | None = None,
                 reached_out: bool | None = None, has_connection: bool | None = None,
                 department: str | None = None, industry: str | None = None, language: str | None = None,
                 max_years: int | None = None, posted_after: str | None = None,
                 is_referral: bool | None = None, has_description: bool | None = None):
    """(where clause, params) for every filter the API exposes, or NO_MATCH
    when the text query contained no searchable terms.

    search_jobs and facets.counts both call this: a facet count that
    disagreed with the list it annotates would be worse than no facet.
    """
    where: list[str] = []
    params: dict[str, object] = {}

    if q:
        match = _fts_query(q, scope)
        if not match:
            return NO_MATCH, {}
        where.append("j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :match)")
        params["match"] = match
    if exclude:
        # Titles only, whatever the search scope is. A word in a description is
        # usually incidental - "no agencies", "reporting to the recruiter" - and
        # matching it threw away jobs whose own title never said it. What you
        # exclude is a kind of role, and the title is where the role is named.
        excluded = _fts_query(exclude, scope="title").replace(" AND ", " OR ")
        if excluded:
            where.append("j.rowid NOT IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :excluded)")
            params["excluded"] = excluded

    for field, value, column in (
        ("company_id", company_id, "j.company_id"),
        ("city", city, "j.city"),
        ("department", department, "j.department"),
        ("industry", industry, "c.industry"),
        ("language", language, "j.source_language"),
    ):
        if not value:
            continue
        wanted = _csv(value)
        keys = [f":{field}{index}" for index in range(len(wanted))]
        where.append(f"{column} IN ({', '.join(keys)})")
        params.update({key[1:]: item for key, item in zip(keys, wanted)})

    if exclude_company_id:
        excluded_companies = _csv(exclude_company_id)
        keys = [f":exclude_company{index}" for index in range(len(excluded_companies))]
        where.append(f"j.company_id NOT IN ({', '.join(keys)})")
        params.update({key[1:]: item for key, item in zip(keys, excluded_companies)})

    if status == "open":
        # One value for "anything still listed", rather than making every
        # caller enumerate new + seen and get it wrong when a third appears.
        where.append("j.status != 'closed'")
    elif status:
        # Comma-separated like company/city: "new,seen" picks either, same
        # IN-clause shape as every other multi-select filter.
        wanted = _csv(status)
        keys = [f":status{index}" for index in range(len(wanted))]
        where.append(f"j.status IN ({', '.join(keys)})")
        params.update({key[1:]: item for key, item in zip(keys, wanted)})
    if is_remote is not None:
        where.append("j.is_remote = :is_remote")
        params["is_remote"] = int(is_remote)
    if is_referral is not None:
        where.append("j.is_referral = :is_referral")
        params["is_referral"] = int(is_referral)
    if has_description is not None:
        where.append("j.description != ''" if has_description else "j.description = ''")
    if max_years is not None:
        # A job that never stated its years is not evidence of wanting more
        # than you have, so it stays in.
        where.append("(j.years_required IS NULL OR j.years_required <= :max_years)")
        params["max_years"] = max_years
    if posted_after:
        where.append("COALESCE(j.posted_at, j.first_seen) >= :posted_after")
        params["posted_after"] = posted_after

    for field, wanted in (("liked", liked), ("hidden", hidden), ("sent", sent), ("reached_out", reached_out)):
        if wanted is not None:
            where.append(f"COALESCE(st.{field}, 0) = :{field}")
            params[field] = int(wanted)
    if has_connection is not None:
        where.append("c.connection_count > 0" if has_connection else "c.connection_count = 0")
    if min_score is not None:
        where.append(f"{score_sql(profile)} >= :min_score")
        params["min_score"] = min_score
    if profile != "best":
        params["profile"] = profile

    return (f"WHERE {' AND '.join(where)}" if where else ""), params


def export_rows(conn: sqlite3.Connection, *, sort: str = "score", profile: str = "best",
                **filters) -> list[sqlite3.Row]:
    """Every job matching the filter, unpaginated - (company, title, url)
    and nothing else.

    Deliberately not search_jobs(size=...): the export is the whole result
    set, and size is clamped to 500 at the API precisely so one request
    cannot pull the dataset down as list rows. These three columns carry
    no description and no per-job score lookup, so the whole 12,825-job
    corpus is a couple of megabytes rather than the hundreds search_jobs
    would cost at the same row count.
    """
    clause, params = build_filter(profile=profile, **filters)
    if clause is NO_MATCH:
        return []
    # best_score is selected because _SORTS["score"] orders by that alias,
    # not because the export carries it - the caller reads company/title/url.
    return conn.execute(
        f"SELECT c.display_name AS company, j.title, j.url, {score_sql(profile)} AS best_score "
        f"{JOINS} {clause} ORDER BY {_SORTS.get(sort, _SORTS['score'])}",
        params,
    ).fetchall()


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

    from jobfit.store import companies as companies_store

    jobs = []
    for row in rows:
        job = dict(row)
        for field in _BOOL_FIELDS:
            job[field] = bool(job[field])
        jobs.append(job)

    # One query for the whole page's companies, not one per job.
    contacts = companies_store.contacts_for(conn, sorted({job["company_id"] for job in jobs}))
    # Same, for every profile's score - the card shows all of them, not just
    # the best_score the sort ran on.
    from jobfit.store import scores as scores_store

    per_profile = scores_store.scores_by_job(conn, [job["id"] for job in jobs])
    for job in jobs:
        job["contacts"] = contacts.get(job["company_id"], [])
        job["profile_scores"] = per_profile.get(job["id"], {})
    return {"total": total, "page": page, "size": size, "jobs": jobs}
