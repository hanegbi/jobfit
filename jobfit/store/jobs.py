"""Job rows and the scrape diff.

Nothing is ever deleted. A job that disappears from a listing is closed
and keeps its row, because a listing that used to exist is still evidence -
of a company that hires, of a role that was open, of a URL worth checking.
"""

from __future__ import annotations

import json
import sqlite3

from jobfit.scrape import titles
from jobfit.scrape.ids import normalize_job_url

_INSERT_FIELDS = (
    "title", "url", "description", "location", "city", "is_remote", "department", "employment_type",
    "posted_at", "years_required", "is_referral", "referral_contact", "source_language",
    "title_original", "description_original", "scrape_source",
)
# Filled from a re-scrape only when the stored value is empty: a later scrape
# is not better evidence than the first one, it is just more recent.
_FILL_IF_EMPTY = ("location", "city", "description", "department", "employment_type", "url")


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def jobs_for_company(conn: sqlite3.Connection, company_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()


def row_to_job(row: sqlite3.Row) -> dict:
    """A row as the rest of jobfit expects a job: job_evidence decoded back
    into a dict. It is stored as JSON text, and scoring reads it with .get() -
    handing out the raw string crashed a whole recompute run."""
    job = dict(row)
    evidence = job.get("job_evidence")
    if isinstance(evidence, str):
        try:
            job["job_evidence"] = json.loads(evidence)
        except ValueError:
            job["job_evidence"] = None
    return job


def iter_all(conn: sqlite3.Connection):
    """Every job, in id order, as a decoded dict. Streams rather than
    materializing 29,000 rows at once."""
    for row in conn.execute("SELECT * FROM jobs ORDER BY id"):
        yield row_to_job(row)


def all_with_company(conn: sqlite3.Connection) -> list[dict]:
    """Every job with its company's display name, industry and size - what
    the page needs on each row."""
    rows = conn.execute(
        "SELECT j.*, c.display_name AS company, c.industry, c.size AS company_size "
        "FROM jobs j JOIN companies c ON c.id = j.company_id ORDER BY j.id"
    ).fetchall()
    return [row_to_job(row) for row in rows]


def open_with_url(conn: sqlite3.Connection) -> list[dict]:
    """Open jobs that have a URL - the URL audit's worklist."""
    return [dict(row) for row in conn.execute(
        "SELECT id, url, title, company_id FROM jobs "
        "WHERE url IS NOT NULL AND status != 'closed' ORDER BY id"
    )]


def open_linkedin_ranked(conn: sqlite3.Connection, min_score: float = 0) -> list[dict]:
    """Open LinkedIn-hosted jobs, best-scoring first: if a slow check is cut
    short, the jobs worth acting on are the ones already verified."""
    return [dict(row) for row in conn.execute(
        "SELECT j.id, j.url, (SELECT max(score) FROM job_scores s WHERE s.job_id = j.id) AS best_score "
        "FROM jobs j WHERE j.url IS NOT NULL AND lower(j.url) LIKE '%linkedin.com%' AND j.status != 'closed' "
        "AND COALESCE((SELECT max(score) FROM job_scores s WHERE s.job_id = j.id), 0) >= ? "
        "ORDER BY best_score DESC, j.id", (min_score,)
    )]


def counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT count(*) AS total, sum(CASE WHEN status != 'closed' THEN 1 ELSE 0 END) AS open FROM jobs"
    ).fetchone()
    return {"total": row["total"], "open": row["open"] or 0}


def _insert(conn: sqlite3.Connection, company_id: str, job: dict, now: str) -> None:
    values = {field: job.get(field) for field in _INSERT_FIELDS}
    values.update(
        id=job["id"],
        company_id=company_id,
        status="new",
        description=job.get("description") or "",
        is_remote=int(bool(job.get("is_remote"))),
        is_referral=int(bool(job.get("is_referral"))),
        # An ATS states when a job was posted; prefer that over the moment we
        # first looked, so a newly scraped company isn't all "brand new".
        first_seen=job.get("posted_at") or now,
        last_seen=now,
        job_evidence=json.dumps(job["job_evidence"]) if job.get("job_evidence") else None,
    )
    columns = ", ".join(values)
    conn.execute(
        f"INSERT INTO jobs ({columns}) VALUES ({', '.join(':' + c for c in values)})",
        values,
    )


def _update_seen(conn: sqlite3.Connection, existing: sqlite3.Row, job: dict, now: str) -> None:
    updates: dict[str, object] = {"last_seen": now}
    if existing["status"] in ("new", "closed"):
        updates["status"] = "seen"
        if existing["status"] == "closed":
            updates["closed_at"] = None
            updates["closed_reason"] = None
    # A re-scrape may trim card metadata off a stored title; it may never
    # rename the job. Same function the file path used, so the two rules
    # cannot drift apart.
    trimmed = titles.authoritative_title([job.get("title") or ""], existing["title"] or "")
    if trimmed:
        updates["title"] = trimmed
    for field in _FILL_IF_EMPTY:
        if job.get(field) and not (existing[field] or "").strip():
            updates[field] = job[field]
    if job.get("job_evidence") is not None:
        updates["job_evidence"] = json.dumps(job["job_evidence"])
    conn.execute(
        f"UPDATE jobs SET {', '.join(f'{k} = :{k}' for k in updates)} WHERE id = :id",
        {**updates, "id": existing["id"]},
    )


def upsert_scraped(conn: sqlite3.Connection, company_id: str, scraped: list[dict], now: str,
                   may_close: bool = True) -> tuple[int, int]:
    """Apply one company's scrape. Returns (new_count, closed_count).

    may_close=False records the visit without closing anything - for a fetch
    too weak to prove a job is gone rather than merely unseen."""
    stored = {row["id"]: row for row in jobs_for_company(conn, company_id)}
    seen_ids: set[str] = set()
    new_count = 0

    for job in scraped:
        job_id = job["id"]
        seen_ids.add(job_id)
        existing = stored.get(job_id)
        if existing is None:
            _insert(conn, company_id, job, now)
            new_count += 1
        else:
            _update_seen(conn, existing, job, now)

    closed_count = 0
    if may_close:
        gone = [jid for jid, row in stored.items() if jid not in seen_ids and row["status"] != "closed"]
        for job_id in gone:
            conn.execute(
                "UPDATE jobs SET status = 'closed', closed_at = ?, closed_reason = 'not on the listing' "
                "WHERE id = ?",
                (now, job_id),
            )
        closed_count = len(gone)
    return new_count, closed_count


def close_by_url(conn: sqlite3.Connection, closed_urls: dict[str, str], now: str) -> dict[str, int]:
    """Close open jobs by URL, recording why - how jobs that no company
    scrape re-verifies (LinkedIn matches, referrals) age out."""
    stats = {"jobs_closed": 0, "already_closed": 0, "companies_touched": 0}
    wanted = {normalize_job_url(url): reason for url, reason in closed_urls.items() if normalize_job_url(url)}
    if not wanted:
        return stats
    touched: set[str] = set()
    for row in conn.execute("SELECT id, url, status, company_id FROM jobs WHERE url IS NOT NULL").fetchall():
        reason = wanted.get(normalize_job_url(row["url"]))
        if reason is None:
            continue
        if row["status"] == "closed":
            stats["already_closed"] += 1
            continue
        conn.execute(
            "UPDATE jobs SET status = 'closed', closed_at = ?, closed_reason = ? WHERE id = ?",
            (now, reason, row["id"]),
        )
        stats["jobs_closed"] += 1
        touched.add(row["company_id"])
    stats["companies_touched"] = len(touched)
    return stats


def set_years_required(conn: sqlite3.Connection, job_id: str, years: int | None) -> None:
    conn.execute("UPDATE jobs SET years_required = ? WHERE id = ?", (years, job_id))


def mark_referral(conn: sqlite3.Connection, job_id: str, contact: str | None, now: str) -> None:
    """Tag an existing job as referral-sourced. A referral is evidence the job
    is live, so a closed one reopens."""
    conn.execute(
        "UPDATE jobs SET is_referral = 1, referral_contact = ?, last_seen = ?, "
        "status = CASE WHEN status IN ('new', 'closed') THEN 'seen' ELSE status END, "
        "closed_at = NULL, closed_reason = NULL WHERE id = ?",
        (contact, now, job_id),
    )


def detail(conn: sqlite3.Connection, job_id: str) -> dict | None:
    """One job in full: its description, its score per profile, its company's
    fields and the user's own flags. The list view deliberately carries none
    of this, which is what keeps a page of results small."""
    from jobfit.store import scores as scores_store
    from jobfit.store import state as state_store

    row = conn.execute(
        "SELECT j.*, c.display_name AS company, c.industry, c.size AS company_size, c.career_url "
        "FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    job = row_to_job(row)
    job["is_remote"] = bool(job["is_remote"])
    job["is_referral"] = bool(job["is_referral"])
    job["scores"] = scores_store.scores_for_job(conn, job_id)
    job["state"] = state_store.get_state(conn, job_id)
    return job
