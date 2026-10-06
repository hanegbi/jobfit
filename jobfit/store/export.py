"""Turn a filtered search into the exported job list and its run report.

Relevance lives here, not in the scraper. The store keeps every real
posting it finds; this decides which of them one export contains, from
config.EXPORT_* - so widening a list re-exports jobs already stored
rather than needing a re-scrape to get them back.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from jobfit import config
from jobfit.connections import normalize_company
from jobfit.scrape.ids import normalize_job_url
from jobfit.scrape.titles import names_foreign_country

_ENGINEER_RE = re.compile(r"\bengineer(ing)?\b", re.I)


def _words(terms: list[str]) -> re.Pattern:
    """Whole-word alternation, so "staff" does not fire on "staffing" and
    "architect" does not fire on "architecture"."""
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(t) for t in terms) + r")(?!\w)", re.I)


_KEEP_RE = _words(config.EXPORT_TITLE_KEEP)
_DROP_RE = _words(config.EXPORT_TITLE_DROP)
_DROP_UNLESS_ENG_RE = _words(config.EXPORT_TITLE_DROP_UNLESS_ENGINEER)
_AGENCY_RE = _words(config.AGENCY_COMPANY_MARKERS)


def title_verdict(title: str) -> str | None:
    """None to keep, else why the title was dropped."""
    text = title or ""
    if _DROP_RE.search(text):
        return "title drop-list"
    if _DROP_UNLESS_ENG_RE.search(text) and not _ENGINEER_RE.search(text):
        return "analyst without engineer"
    if not _KEEP_RE.search(text):
        return "not on the title keep-list"
    return None


def is_agency(company: str) -> bool:
    """A recruiter or consultancy placing someone else's role. Flagged, not
    dropped: the job can be real while the company name is not the employer."""
    return bool(_AGENCY_RE.search(company or ""))


def _company_key(company: str) -> str:
    """normalize_company already folds "Xpander AI Ltd." and "xpander.ai"
    together (both lose the suffix and the dot), but it keeps the "ai"
    token, which leaves "Brandlight" and "Brandlight AI Ltd." apart.

    The extra trim lives here rather than in normalize_company because that
    function is also what matches LinkedIn connections and merges referrals
    to a company - widening it there would quietly merge two employers in
    places where being wrong matters more than it does in one export.
    """
    key = normalize_company(company)
    return re.sub(r"ai$", "", key) or key


def _dedupe_key(company: str, title: str, city: str | None) -> tuple:
    """Company and title normalised, so "Brandlight" and "Brandlight AI Ltd."
    are one company and casing/punctuation in a title does not split a job
    in two. City stays in the key: the same role in two cities is two jobs."""
    return (_company_key(company), " ".join((title or "").lower().split()), (city or "").lower())


def _prefers(candidate: dict, incumbent: dict) -> bool:
    """Whether candidate should replace incumbent as the kept duplicate.

    An ATS link beats a LinkedIn one outright - it is the employer's own
    posting, it outlives the aggregator's copy, and LinkedIn is the host
    most likely to answer a scrape with a wall. Otherwise prefer the row
    whose page actually fetched.
    """
    def linkedin(row):
        return "linkedin.com" in (row.get("url") or "").lower()

    if linkedin(incumbent) != linkedin(candidate):
        return linkedin(incumbent)
    return (candidate.get("fetch_status") == "ok") and (incumbent.get("fetch_status") != "ok")


def build(rows: list[sqlite3.Row], now: datetime | None = None) -> tuple[list[dict], dict]:
    """(jobs, report) for an already-filtered search result.

    rows carry company/title/url/city/work_mode/fetch_status/posted_at.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = {"found": len(rows), "kept": 0, "dropped_by_title": 0, "dropped_by_location": 0,
              "deduped": 0, "not_ok_fetch": []}
    kept: dict[tuple, dict] = {}

    for row in rows:
        row = dict(row)
        # Israel only, judged on what the POSTING says - names_foreign_country
        # answers False for anything naming an Israeli city or no country at
        # all, so a posting that simply never stated a location is kept and
        # exported with city null rather than guessed at.
        if names_foreign_country(row.get("location")) or names_foreign_country(row.get("city")):
            report["dropped_by_location"] += 1
            continue
        if title_verdict(row["title"]):
            report["dropped_by_title"] += 1
            continue
        job = {
            "company": row["company"],
            "title": row["title"],
            "url": normalize_job_url(row["url"]) or row["url"],
            "city": row.get("city") or None,
            "work_mode": row.get("work_mode") or None,
            "is_agency": is_agency(row["company"]),
            "fetch_status": row.get("fetch_status") or "ok",
            "posted_date": row.get("posted_at") or None,
            "scraped_at": stamp,
        }
        key = _dedupe_key(job["company"], job["title"], job["city"])
        if key in kept:
            report["deduped"] += 1
            if _prefers(job, kept[key]):
                kept[key] = job
            continue
        kept[key] = job

    jobs = sorted(kept.values(), key=lambda j: (j["company"].lower(), j["title"].lower()))
    report["kept"] = len(jobs)
    report["not_ok_fetch"] = [
        {"company": j["company"], "title": j["title"], "url": j["url"], "fetch_status": j["fetch_status"]}
        for j in jobs if j["fetch_status"] != "ok"
    ]
    return jobs, report
