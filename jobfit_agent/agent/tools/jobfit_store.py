"""The agent's only door into jobfit. Read-only: it never writes jobfit.db."""

import base64
import re
import sqlite3
import threading

from jobfit import config as jobfit_config
from jobfit import cv
from jobfit.scrape import ids
from jobfit.store import companies, db, jobs, search

_conn: sqlite3.Connection | None = None
# The graph fans out with Send, so several nodes read through this one connection
# from different threads. Python's sqlite3 does no locking of its own, and
# concurrent use of one connection raises "bad parameter or other API misuse".
_LOCK = threading.RLock()
_AMOUNT = re.compile(r"[^.\n]*(?:[$€£₪]|USD|ILS|NIS)\s?\d[\d,.]*[^.\n]*")


def read_only(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_store() -> sqlite3.Connection:
    return read_only(db.connect(jobfit_config.DB_PATH))


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = open_store()
    return _conn


def set_conn(conn: sqlite3.Connection | None) -> None:
    global _conn
    _conn = conn


def job_id_for_url(url: str) -> str:
    """A job's id is base64url of its normalized url (jobfit/scrape/ids.py)."""
    normalized = ids.normalize_job_url(url) or url
    return base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")


def select_jobs(conn, profile: str, top_n: int) -> list[dict]:
    # min_score=0 drops jobs with no score for this profile (NULL fails the comparison), so an unknown
    # profile selects nothing instead of every job.
    with _LOCK:
        page = search.search_jobs(conn, sort="score", size=top_n, profile=profile, status="open", hidden=False,
                                  min_score=0)
    return [_row(j) for j in page["jobs"]]


def select_by_url(conn, url: str) -> list[dict]:
    """The one job at this url, in the same shape select_jobs returns."""
    with _LOCK:
        job = jobs.detail(conn, job_id_for_url(url))
    if job is None:
        return []
    best = max((s.get("score") or 0 for s in job["scores"].values()), default=0)
    return [{"id": job["id"], "company_id": job["company_id"], "company": job["company"],
             "title": job["title"], "best_score": best}]


def _row(job: dict) -> dict:
    return {"id": job["id"], "company_id": job["company_id"], "company": job["company"],
            "title": job["title"], "best_score": job["best_score"]}


def job_with_context(conn, job_id: str) -> dict:
    with _LOCK:
        job = jobs.detail(conn, job_id)
        if job is None:
            raise KeyError(job_id)
        job["contacts"] = companies.contacts_for(conn, [job["company_id"]]).get(job["company_id"], [])
    return job


def load_cv_text(profile: str) -> str:
    entry = cv.load_registry()[profile]
    return cv.extract_text(jobfit_config.CV_PROFILES_DIR / entry["filename"])


def salary_snippets(conn, company_id: str, limit: int = 5) -> dict[str, str]:
    """Sentences from the company's own open postings that state an amount."""
    found: dict[str, str] = {}
    with _LOCK:
        rows = jobs.jobs_for_company(conn, company_id)
    for row in rows:
        if row["status"] == "closed":
            continue
        match = _AMOUNT.search(row["description"] or "")
        if match:
            found[row["url"]] = match.group(0).strip().rstrip(".")
        if len(found) >= limit:
            break
    return found
