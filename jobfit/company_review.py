"""Triage companies that have no known career page (jobfit/companies_career_pages.json's
url is null): approve techmap's own row as a fallback source, set a real
career URL, or mark as skipped. update_jobs.load_companies_to_scrape() reads
the "techmap" decisions to decide what scrape_stage() actually processes.
"""

from datetime import datetime, timezone
from pathlib import Path

from jobfit import config, connections
from jobfit.atomic_io import write_json_atomic

DECISIONS = ("techmap", "skip")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_career_pages() -> dict[str, str | None]:
    import json
    if config.COMPANIES_CAREER_PAGES_PATH.exists():
        return json.loads(config.COMPANIES_CAREER_PAGES_PATH.read_text(encoding="utf-8"))
    return {}


def save_career_pages(pages: dict[str, str | None]) -> None:
    write_json_atomic(config.COMPANIES_CAREER_PAGES_PATH, pages)


def load_review() -> dict[str, dict]:
    import json
    if config.COMPANY_REVIEW_PATH.exists():
        return json.loads(config.COMPANY_REVIEW_PATH.read_text(encoding="utf-8"))
    return {}


def save_review(review: dict[str, dict]) -> None:
    write_json_atomic(config.COMPANY_REVIEW_PATH, review)


def set_decision(company: str, decision: str) -> None:
    """Approve techmap's own data as a fallback source, or mark skipped.
    Raises ValueError for an unknown decision, KeyError for an unknown company.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS!r}, got {decision!r}")
    pages = load_career_pages()
    if company not in pages:
        raise KeyError(company)
    review = load_review()
    review[company] = {"decision": decision, "decided_at": _now_iso()}
    save_review(review)


def set_career_url(company: str, url: str) -> None:
    """Give a previously-null company a real career URL - it then flows
    through the normal HTTP -> Playwright -> techmap cascade like any other
    company, so any earlier review decision for it no longer applies."""
    pages = load_career_pages()
    if company not in pages:
        raise KeyError(company)
    pages[company] = url
    save_career_pages(pages)
    review = load_review()
    if review.pop(company, None) is not None:
        save_review(review)


def companies_needing_review(techmap_index: dict[str, list[dict]]) -> list[dict]:
    """Every company with no career URL, with techmap availability and the
    current review decision (or "pending" if never reviewed). Sorted so
    undecided companies with techmap data available surface first - those
    are the ones a click actually helps right now."""
    pages = load_career_pages()
    review = load_review()

    results = []
    for company, url in pages.items():
        if url:
            continue
        key = connections.normalize_company(company)
        techmap_rows = techmap_index.get(key, [])
        results.append({
            "company": company,
            "decision": review.get(company, {}).get("decision", "pending"),
            "has_techmap": bool(techmap_rows),
            "techmap_job_count": len(techmap_rows),
            "techmap_sample_title": techmap_rows[0]["title"] if techmap_rows else None,
        })

    results.sort(key=lambda r: (r["decision"] != "pending", not r["has_techmap"], r["company"].lower()))
    return results
