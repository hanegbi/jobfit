"""Load referral-sourced jobs (e.g. WhatsApp job-referral group exports) and
merge them into the pipeline's company_jobs structure.

Unlike every other source, these already carry clean, structured requirement
text (no scraping needed) plus a named contact for a warm referral - the
whole point of this source. Two things this module has to get right:
  1. Match each referral company to the SAME canonical company name already
     used elsewhere in the dataset (so it groups correctly), via the same
     normalize_company() used for LinkedIn-connections matching.
  2. Not duplicate a job that's already present from another source - title
     similarity within the same company, not an exact-match requirement.
"""

import difflib
import json
import re
from pathlib import Path

from jobfit2.connections import normalize_company

_TITLE_STRIP_RE = re.compile(r"[^a-z0-9 ]")
_DUPLICATE_THRESHOLD = 0.75


def _normalize_title(title: str) -> str:
    return _TITLE_STRIP_RE.sub("", (title or "").lower()).strip()


def _is_duplicate_title(a: str, b: str) -> bool:
    na, nb = _normalize_title(a), _normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= _DUPLICATE_THRESHOLD


def _normalize_date(raw: str | None) -> str | None:
    """Convert the export's M/D/YYYY dates to YYYY-MM-DD so date-sort stays correct
    when mixed with the ISO dates the rest of the pipeline already uses."""
    if not raw:
        return None
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    month, day, year = parts
    try:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    except ValueError:
        return raw


def _build_description(job: dict) -> str:
    requirements = job.get("requirements") or []
    parts = []
    if job.get("exp_years_min") is not None:
        parts.append(f"Requires {job['exp_years_min']}+ years of experience.")
    parts.extend(requirements)
    return " ".join(parts)


def _to_job_dict(job: dict) -> dict:
    locations = job.get("locations") or []
    seniority = job.get("seniority_group") or []
    return {
        "title": (job.get("title") or "").strip(),
        "location": ", ".join(locations) or None,
        "url": job.get("link"),
        "description": _build_description(job),
        "department": job.get("field"),
        "employment_type": ", ".join(seniority) or None,
        "posted_at": _normalize_date(job.get("last_seen") or job.get("first_seen")),
        "is_referral": True,
        "referral_contact": job.get("contact"),
    }


def load_referral_companies(path: Path) -> list[dict]:
    """Return the raw {company, also_posted_as, jobs: [...]} list from the export."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("companies", [])


def merge_referral_jobs(company_jobs: dict[str, list[dict]], path: Path) -> dict[str, int]:
    """Merge referral jobs into company_jobs in place.

    Matches each referral company (by name or its listed aliases) against the
    canonical company names already present in company_jobs; unmatched
    companies are added as new entries under their own referral-sourced name.
    A referral job that looks like an existing job for that company (by title
    similarity) gets the existing job tagged as a referral instead of being
    duplicated; otherwise it's appended as a new job.

    Returns stats: {"matched_existing_company": N, "new_company": N,
    "merged_into_existing_job": N, "added_new_job": N}.
    """
    stats = {"matched_existing_company": 0, "new_company": 0, "merged_into_existing_job": 0, "added_new_job": 0}
    if not path.exists():
        return stats

    canonical_by_key: dict[str, str] = {}
    for canonical in company_jobs:
        key = normalize_company(canonical)
        if key:
            canonical_by_key[key] = canonical

    for company_entry in load_referral_companies(path):
        names = [company_entry.get("company", "")] + list(company_entry.get("also_posted_as") or [])
        canonical = None
        for name in names:
            key = normalize_company(name)
            if key and key in canonical_by_key:
                canonical = canonical_by_key[key]
                break
        if canonical:
            stats["matched_existing_company"] += 1
        else:
            canonical = company_entry.get("company", "").strip()
            if not canonical:
                continue
            key = normalize_company(canonical)
            if key:
                canonical_by_key[key] = canonical
            stats["new_company"] += 1

        existing_jobs = company_jobs.setdefault(canonical, [])
        for raw_job in company_entry.get("jobs") or []:
            title = (raw_job.get("title") or "").strip()
            if not title:
                continue
            referral_job = _to_job_dict(raw_job)
            match = next((j for j in existing_jobs if _is_duplicate_title(j.get("title") or "", title)), None)
            if match is not None:
                match["is_referral"] = True
                match["referral_contact"] = referral_job["referral_contact"]
                stats["merged_into_existing_job"] += 1
            else:
                existing_jobs.append(referral_job)
                stats["added_new_job"] += 1

    return stats
