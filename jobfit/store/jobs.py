"""Job rows and the scrape diff.

Nothing is ever deleted. A job that disappears from a listing is closed
and keeps its row, because a listing that used to exist is still evidence -
of a company that hires, of a role that was open, of a URL worth checking.
"""

from __future__ import annotations

import json
import sqlite3

from jobfit.departments import department_for
from jobfit.scrape import titles
from jobfit.scrape.ids import normalize_job_url

_INSERT_FIELDS = (
    "title", "url", "description", "location", "city", "is_remote", "department", "employment_type",
    "posted_at", "years_required", "is_referral", "referral_contact", "source_language",
    "title_original", "description_original", "scrape_source",
    "family", "canonical_title", "family_confidence", "taxonomy_version",
)
# Filled from a re-scrape only when the stored value is empty: a later scrape
# is not better evidence than the first one, it is just more recent.
_FILL_IF_EMPTY = ("location", "city", "description", "department", "employment_type", "url")
# Classification is re-derived every scrape, not just once - a taxonomy edit
# should reclassify a seen-again job without waiting for a dedicated backfill.
_ALWAYS_REFRESH = ("family", "canonical_title", "family_confidence", "taxonomy_version")


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


def iter_open(conn: sqlite3.Connection):
    """Every job that is not closed, in id order, as a decoded dict. What
    recompute_stage scores: a closed job is dead, the app never shows it,
    and spending CPU re-scoring it on every run bought nothing - roughly
    half the real corpus is closed jobs."""
    for row in conn.execute("SELECT * FROM jobs WHERE status != 'closed' ORDER BY id"):
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


def corpus_texts_by_family(conn: sqlite3.Connection, min_description_len: int = 50) -> dict[str, list[str]]:
    """{family: ["title. description", ...]} over active jobs with a real
    description and a known family - the adjacency corpus (see
    ats_scorer/adjacency.py). Same min_description_len default as
    corpus_texts(), for the same reason."""
    rows = conn.execute(
        "SELECT family, title, description FROM jobs "
        "WHERE status != 'closed' AND family IS NOT NULL AND length(description) >= ?",
        (min_description_len,),
    ).fetchall()
    by_family: dict[str, list[str]] = {}
    for row in rows:
        by_family.setdefault(row["family"], []).append(f"{row['title'] or ''}. {row['description'] or ''}")
    return by_family


def corpus_texts(conn: sqlite3.Connection, min_description_len: int = 50) -> list[str]:
    """"title. description" for every active job with a real description -
    the IDF corpus (see ats_scorer/idf.py). min_description_len's default
    matches scoring.MIN_DESCRIPTION_LEN_FOR_FULL_CONFIDENCE: below it a
    description is noise, not signal, the same line the scorer itself
    draws between "full" and "title_only" confidence."""
    rows = conn.execute(
        "SELECT title, description FROM jobs "
        "WHERE status != 'closed' AND length(description) >= ?",
        (min_description_len,),
    ).fetchall()
    return [f"{row['title'] or ''}. {row['description'] or ''}" for row in rows]


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
        department=department_for(job.get("title"), job.get("department")),
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
        if not job.get(field) or (existing[field] or "").strip():
            continue
        value = department_for(job.get("title"), job[field]) if field == "department" else job[field]
        if value:
            updates[field] = value
    for field in _ALWAYS_REFRESH:
        if job.get(field) is not None:
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


def delete_jobs(conn: sqlite3.Connection, job_ids: list[str]) -> dict[str, int]:
    """Remove rows that were never job postings - and only those.

    The exception to "jobs are closed, never deleted" (see the store's
    CLAUDE.md). That rule protects evidence: a posting that existed and
    closed is still evidence of a company that hires. A glossary page
    scraped off a careers URL is not evidence of anything, and closing it
    would leave it in every company's total forever.

    Deletes the score rows and user flags first: both carry a REFERENCES
    jobs(id), and a job_state row is the user's own work, so a caller that
    hands over a job the user has flagged destroys it - purge_non_jobs
    refuses to pass one. The FTS index needs no help, the jobs_fts_delete
    trigger follows the delete.
    """
    stats = {"jobs": 0, "scores": 0, "state": 0}
    if not job_ids:
        return stats
    for chunk_start in range(0, len(job_ids), 500):
        chunk = job_ids[chunk_start:chunk_start + 500]
        marks = ", ".join("?" * len(chunk))
        stats["scores"] += conn.execute(f"DELETE FROM job_scores WHERE job_id IN ({marks})", chunk).rowcount
        stats["state"] += conn.execute(f"DELETE FROM job_state WHERE job_id IN ({marks})", chunk).rowcount
        stats["jobs"] += conn.execute(f"DELETE FROM jobs WHERE id IN ({marks})", chunk).rowcount
    return stats


def closed_with_url(conn: sqlite3.Connection) -> list[dict]:
    """Closed jobs that still have a URL - the reopen audit's worklist. A
    closed job with no URL can never be re-verified, so it is not here."""
    return [dict(row) for row in conn.execute(
        "SELECT id, url, title, company_id, closed_reason FROM jobs "
        "WHERE url IS NOT NULL AND status = 'closed' ORDER BY id"
    )]


def reopen_by_url(conn: sqlite3.Connection, urls: list[str], now: str) -> dict[str, int]:
    """The other half of close_by_url: a closed job whose URL turns out to
    still be live. Reopens as "seen", not "new" - the posting isn't new, we
    were just wrong that it was gone."""
    stats = {"jobs_reopened": 0, "already_open": 0}
    wanted = {normalize_job_url(url) for url in urls if normalize_job_url(url)}
    if not wanted:
        return stats
    for row in conn.execute("SELECT id, url, status FROM jobs WHERE url IS NOT NULL").fetchall():
        if normalize_job_url(row["url"]) not in wanted:
            continue
        if row["status"] != "closed":
            stats["already_open"] += 1
            continue
        conn.execute(
            "UPDATE jobs SET status = 'seen', closed_at = NULL, closed_reason = NULL, last_seen = ? WHERE id = ?",
            (now, row["id"]),
        )
        stats["jobs_reopened"] += 1
    return stats


def duplicate_closed_jobs(conn: sqlite3.Connection) -> list[dict]:
    """Closed jobs that are, by normalized URL, the exact same posting as an
    OPEN job at the same company - the Tikalk bug: a company switches ATS
    host (or appends a volatile query parameter normalize_job_url now
    strips) mid-scrape, so the same real posting gets two different stored
    URLs and two different ids, and the one the next scrape no longer
    revisits closes while its twin stays open. Proven to be the same
    posting by URL identity, not a liveness guess - safe to reopen."""
    by_company: dict[str, dict[str, list[dict]]] = {}
    for row in conn.execute("SELECT id, company_id, url, status, title FROM jobs WHERE url IS NOT NULL"):
        normalized = normalize_job_url(row["url"])
        if not normalized:
            continue
        by_company.setdefault(row["company_id"], {}).setdefault(normalized, []).append(dict(row))

    duplicates = []
    for groups in by_company.values():
        for group in groups.values():
            if len(group) < 2:
                continue
            open_rows = [r for r in group if r["status"] != "closed"]
            closed_rows = [r for r in group if r["status"] == "closed"]
            if not open_rows or not closed_rows:
                continue
            for closed in closed_rows:
                duplicates.append({**closed, "open_sibling_id": open_rows[0]["id"], "open_sibling_url": open_rows[0]["url"]})
    return duplicates


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
