"""Incremental job update: fetch each company's career page, diff against its
saved companies/<name>.json, and mark new/seen/closed jobs (scrape_stage);
then rescore every stored job against the current CV profiles, reaggregate,
and rebuild jobfit.html (recompute_stage). Safe to run anytime, as often as
you like - a company checked within COMPANY_RECHECK_TTL_HOURS is skipped
unless --force is passed, and the fetch itself runs concurrently.

Source of companies: jobfit/companies_career_pages.json ({company: url}),
your own curated list - copied into the repo so this isn't a fragile
dependency on a Downloads-folder file.

A full run (no --company/--limit) also merges WhatsApp-referral-sourced jobs
(config.REFERRAL_JOBS_PATH) into companies/*.json, tagging matching existing
jobs as referrals or adding new referral-only ones - see merge_referral_jobs().

Usage:
  uv run python -m jobfit.scripts.update_jobs                # all companies
  uv run python -m jobfit.scripts.update_jobs --limit 5       # test on a few
  uv run python -m jobfit.scripts.update_jobs --company Wiz   # just one
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, config, connections, cv, scoring, techmap_source  # noqa: E402

logger = logging.getLogger("jobfit.update_jobs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COMPANIES_DIR = config.ROOT / "companies"
META_PATH = COMPANIES_DIR / "_meta.json"
MAX_LINKS_PER_COMPANY = 12

_ATS_ID_PATTERNS = [
    re.compile(r"greenhouse\.io/[^/]+/jobs/(\d+)", re.I),
    re.compile(r"jobs\.lever\.co/[^/]+/([\w-]{6,})", re.I),
    re.compile(r"jobs\.ashbyhq\.com/[^/]+/([\w-]{6,})", re.I),
    re.compile(r"smartrecruiters\.com/[^/]+/(\d{6,})", re.I),
    re.compile(r"comeet\.com/jobs/[^/]+/[\w.]+/[^/]+/([\w.]+)", re.I),
    re.compile(r"workable\.com/[^/]+/j/([\w-]{6,})", re.I),
    re.compile(r"[?&](?:gh_jid|jobId|job_id)=([\w-]{4,})", re.I),
]


def _snake_case(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s or "unnamed_company"


def extract_ats_id(url: str | None) -> str | None:
    if not url:
        return None
    for pattern in _ATS_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def compute_job_id(company: str, title: str, location: str | None, url: str | None) -> str:
    """ATS job ID when the URL reveals one (stable across reruns even if the
    title text is re-scraped slightly differently); otherwise a hash of
    company + normalized title + location + URL, so the same posting is never
    treated as new twice just because whitespace/casing shifted."""
    ats_id = extract_ats_id(url)
    if ats_id:
        return f"{_snake_case(company)}:{ats_id}"
    normalized_title = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    raw = f"{company}|{normalized_title}|{location or ''}|{url or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def cv_hash() -> str:
    """Kept for meta.json bookkeeping only - no longer gates rescoring
    (recompute_stage always rescores everything, unconditionally)."""
    h = hashlib.sha1()
    registry = cv.load_registry()
    for profile_id in sorted(registry):
        path = config.CV_PROFILES_DIR / registry[profile_id]["filename"]
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on the same filesystem - a crash mid-write never corrupts the real file


def load_company_file(company: str) -> dict:
    path = COMPANIES_DIR / f"{_snake_case(company)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"name": company, "career_url": None, "last_checked": None, "jobs": []}


def save_company_file(company: str, data: dict) -> None:
    atomic_write_json(COMPANIES_DIR / f"{_snake_case(company)}.json", data)


def load_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    return {}


def save_meta(meta: dict) -> None:
    atomic_write_json(META_PATH, meta)


def _any_job_scores_positive(jobs: list[dict], profiles: dict) -> bool:
    """Whether at least one fetched job looks like a real, relevant posting.

    Used to decide "did this fetch actually work" - a scrape that returns a
    handful of nav-link junk (a career page's own "Careers Homepage"/"Why Us"
    links, misidentified as job titles) still returns a non-empty list, so
    counting entries alone isn't enough; checking that something actually
    scores above 0 is what tells a real result from junk.
    """
    for job in jobs:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        if scoring.score_job_both(job, profiles)["best_score"] > 0:
            return True
    return False


async def _fetch_via_playwright(company: str, url: str) -> list[dict]:
    from jobfit.scripts.playwright_listings import _scrape_company_inner  # noqa: E402
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=ats_fetchers.USER_AGENT)
        try:
            jobs = await _scrape_company_inner(context, company, url)
        finally:
            await browser.close()
    return [{"title": j["title"], "location": None, "url": j["url"], "description": j["description"]} for j in jobs]


def _techmap_fallback_jobs(company: str, techmap_index: dict[str, list[dict]]) -> list[dict]:
    """Raw techmap rows (title/location/level, no description) for this company,
    keyed by the same normalize_company() used for LinkedIn-connections matching."""
    rows = techmap_index.get(connections.normalize_company(company), [])
    return [
        {"title": r["title"], "location": r["location"], "url": r["url"], "description": ""}
        for r in rows
        if r.get("title")
    ]


def load_techmap_index() -> dict[str, list[dict]]:
    session = ats_fetchers.make_session()
    rows = techmap_source.load_all_rows(session)
    index: dict[str, list[dict]] = {}
    for row in rows:
        key = connections.normalize_company(row["company"])
        if key:
            index.setdefault(key, []).append(row)
    return index


async def fetch_company_jobs_async(
    company: str, url: str, session, profiles: dict, techmap_index: dict[str, list[dict]]
) -> list[dict]:
    """Three-tier cascade, escalating only when the previous tier found
    nothing that actually scores as a real job (not just "found zero links" -
    a page whose only extractable links are nav junk needs the same rescue):
      1. Plain HTTP listing scrape (fast, cheap, works for most sites).
      2. Playwright render (JS-rendered listing pages plain HTTP can't see).
      3. techmap's own row for this company (title/location/level only, no
         description) - better than nothing when the company's own site
         can't be parsed by either of the above.
    """
    links = ats_fetchers.fetch_listing_links(session, url, max_links=MAX_LINKS_PER_COMPANY)
    jobs = []
    for title, job_url in links:
        desc = ats_fetchers.fetch_generic_description(session, job_url)
        jobs.append({"title": title, "location": None, "url": job_url, "description": desc})

    if _any_job_scores_positive(jobs, profiles):
        return jobs

    logger.info("%s: plain-HTTP found nothing scoring (%d raw entries) - trying Playwright", company, len(jobs))
    try:
        pw_jobs = await _fetch_via_playwright(company, url)
    except Exception as error:  # noqa: BLE001
        logger.debug("%s: playwright fallback failed: %s", company, error)
        pw_jobs = []
    if _any_job_scores_positive(pw_jobs, profiles):
        return pw_jobs

    techmap_jobs = _techmap_fallback_jobs(company, techmap_index)
    if techmap_jobs:
        logger.info("%s: still nothing scoring after Playwright - falling back to techmap (%d rows)", company, len(techmap_jobs))
        return techmap_jobs

    # Nothing worked at all - return whatever plain-HTTP had (even if empty/junk)
    # rather than silently discarding a company; diff_and_update will correctly
    # mark any previously-seen jobs as closed if this really is empty.
    return jobs


def diff_and_update(company: str, career_url: str, fetched: list[dict], profiles: dict) -> tuple[dict, int, int]:
    """Returns (updated_company_record, new_count, closed_count).

    Only newly-seen jobs get scored here (so they have a sane score
    immediately). Existing jobs' scores are left alone - recompute_stage()
    is the single place that rescands and rescores every stored job, on
    every trigger that could change a score (a new CV, a removed profile,
    or just periodically), not just when the CV hash changes.
    """
    record = load_company_file(company)
    existing_by_id = {j["id"]: j for j in record["jobs"]}
    fetched_ids: set[str] = set()
    now = _now_iso()
    new_count = 0

    for job in fetched:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        job_id = compute_job_id(company, title, job.get("location"), job.get("url"))
        fetched_ids.add(job_id)

        if job_id in existing_by_id:
            existing = existing_by_id[job_id]
            existing["last_seen"] = now
            if existing.get("status") in ("new", "closed"):
                existing["status"] = "seen"  # a job that was "new" last run has now been seen again
        else:
            scores = scoring.score_job_both(job, profiles)
            new_job = {
                "id": job_id,
                "title": title,
                "location": job.get("location"),
                "url": job.get("url"),
                "description": ats_fetchers.strip_html(job.get("description")),
                "first_seen": now,
                "last_seen": now,
                "status": "new",
            }
            new_job.update(scores)
            existing_by_id[job_id] = new_job
            new_count += 1

    closed_count = 0
    for job_id, existing in existing_by_id.items():
        if job_id not in fetched_ids and existing.get("status") != "closed":
            existing["status"] = "closed"
            closed_count += 1

    record["name"] = company
    record["career_url"] = career_url
    record["last_checked"] = now
    record["jobs"] = list(existing_by_id.values())
    return record, new_count, closed_count


WORKERS = 8


@dataclass
class RunStats:
    companies_checked: int = 0
    companies_skipped: int = 0
    new_jobs: int = 0
    closed_jobs: int = 0
    failures: list[str] = field(default_factory=list)


def _should_skip_company(record: dict, force: bool) -> bool:
    if force:
        return False
    last_checked = record.get("last_checked")
    if not last_checked:
        return False
    try:
        checked_at = datetime.strptime(last_checked, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age_hours = (datetime.now(timezone.utc) - checked_at).total_seconds() / 3600
    return age_hours < config.COMPANY_RECHECK_TTL_HOURS


def _process_company(company, url, session, profiles, techmap_index, force):
    """Runs in a worker thread. Returns (company, new_count, closed_count, skipped, error)."""
    record = load_company_file(company)
    if _should_skip_company(record, force):
        return company, 0, 0, True, None
    try:
        fetched = asyncio.run(fetch_company_jobs_async(company, url, session, profiles, techmap_index))
        updated, new_count, closed_count = diff_and_update(company, url, fetched, profiles)
        save_company_file(company, updated)
        return company, new_count, closed_count, False, None
    except Exception as error:  # noqa: BLE001 - one bad company must never abort the run
        return company, 0, 0, False, error


def scrape_stage(companies: dict[str, str], profiles: dict, force: bool = False) -> RunStats:
    """The network-bound half of an update: fetch + diff every company,
    concurrently, skipping anything checked within COMPANY_RECHECK_TTL_HOURS
    unless force=True."""
    session = ats_fetchers.make_session()
    logger.info("loading techmap data (fallback source for companies whose own site can't be parsed)...")
    techmap_index = load_techmap_index()

    stats = RunStats()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [
            pool.submit(_process_company, company, url, session, profiles, techmap_index, force)
            for company, url in companies.items()
        ]
        for future in as_completed(futures):
            company, new_count, closed_count, skipped, error = future.result()
            if skipped:
                stats.companies_skipped += 1
                continue
            if error is not None:
                stats.failures.append(company)
                logger.warning("%s: FAILED - %s: %s", company, type(error).__name__, error)
                continue
            stats.companies_checked += 1
            stats.new_jobs += new_count
            stats.closed_jobs += closed_count
            logger.info("%s: %d new, %d closed", company, new_count, closed_count)

    logger.info(
        "scrape done: %d checked, %d skipped (recently checked), %d new, %d closed, %d failures",
        stats.companies_checked, stats.companies_skipped, stats.new_jobs, stats.closed_jobs, len(stats.failures),
    )
    return stats


def recompute_stage() -> None:
    """The local-only half of an update: rescore every stored job against the
    *current* profile registry, drop any score fields for profiles that no
    longer exist, reaggregate, and rebuild jobfit.html. Cheap (pure regex
    scoring over already-fetched text) - safe to call after any admin edit,
    not just after a scrape."""
    profiles = cv.load_profiles()
    profile_ids = set(profiles)
    stale_prefixes = ("score_", "matched_", "coverage_", "confidence_", "requirements_")

    for path in sorted(COMPANIES_DIR.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for job in record["jobs"]:
            job.update(scoring.score_job_both(job, profiles))
            for key in list(job):
                for prefix in stale_prefixes:
                    if key.startswith(prefix) and key[len(prefix):] not in profile_ids:
                        del job[key]
        save_company_file(record["name"], record)

    count = aggregate_to_jobs_v2()
    logger.info("recompute: rescored against %d profile(s), aggregated %d jobs", len(profiles), count)
    from jobfit import build_html
    build_html.build()


def merge_referral_jobs(profiles: dict, path: "Path | None" = None) -> dict[str, int]:
    """Merge WhatsApp-referral-sourced jobs into companies/*.json - same
    canonical-company + title-similarity matching as the old pipeline's
    referral_source.merge_referral_jobs, adapted to this script's
    per-company-file / job-id / status / score record shape.

    `path` defaults to config.REFERRAL_JOBS_PATH (the CLI's fixed
    Downloads-folder file); the control panel passes an explicit
    uploaded-file path instead.

    A referral job that looks like a job already on file for that company
    (by title similarity) gets that existing record tagged as a referral
    instead of duplicated; otherwise it's added as a new, already-scored job.
    """
    from jobfit import referral_source  # noqa: E402

    path = path or config.REFERRAL_JOBS_PATH
    stats = {"matched_existing_company": 0, "new_company": 0, "merged_into_existing_job": 0, "added_new_job": 0}
    if not path.exists():
        return stats

    existing_names = [
        json.loads(p.read_text(encoding="utf-8"))["name"]
        for p in COMPANIES_DIR.glob("*.json") if p.name != "_meta.json"
    ]
    canonical_by_key = {connections.normalize_company(c): c for c in existing_names if connections.normalize_company(c)}
    now = _now_iso()

    for company_entry in referral_source.load_referral_companies(path):
        names = [company_entry.get("company", "")] + list(company_entry.get("also_posted_as") or [])
        canonical = None
        for name in names:
            key = connections.normalize_company(name)
            if key and key in canonical_by_key:
                canonical = canonical_by_key[key]
                break
        if canonical:
            stats["matched_existing_company"] += 1
        else:
            canonical = (company_entry.get("company") or "").strip()
            if not canonical:
                continue
            key = connections.normalize_company(canonical)
            if key:
                canonical_by_key[key] = canonical
            stats["new_company"] += 1

        record = load_company_file(canonical)
        for raw_job in company_entry.get("jobs") or []:
            title = (raw_job.get("title") or "").strip()
            if not title:
                continue
            referral_job = referral_source._to_job_dict(raw_job)
            match = next(
                (j for j in record["jobs"] if referral_source._is_duplicate_title(j.get("title") or "", title)),
                None,
            )
            if match is not None:
                match["is_referral"] = True
                match["referral_contact"] = referral_job["referral_contact"]
                match["last_seen"] = now
                if match.get("status") in ("new", "closed"):
                    match["status"] = "seen"
                stats["merged_into_existing_job"] += 1
            else:
                job_id = compute_job_id(canonical, title, referral_job.get("location"), referral_job.get("url"))
                scores = scoring.score_job_both(referral_job, profiles)
                new_job = {
                    "id": job_id,
                    "title": title,
                    "location": referral_job.get("location"),
                    "url": referral_job.get("url"),
                    "description": ats_fetchers.strip_html(referral_job.get("description")),
                    "first_seen": now,
                    "last_seen": now,
                    "status": "new",
                    "is_referral": True,
                    "referral_contact": referral_job.get("referral_contact"),
                }
                new_job.update(scores)
                record["jobs"].append(new_job)
                stats["added_new_job"] += 1

        record["name"] = canonical
        record.setdefault("career_url", None)
        save_company_file(canonical, record)

    return stats


def aggregate_to_jobs_v2() -> int:
    """Flatten companies/*.json into the record shape build_html.py already
    expects, and write it to config.JOBS_OUTPUT_JSON - so build_html needs no
    changes at all, it just picks up whatever's there.
    """
    from jobfit import pipeline as _pipeline  # reuse its already-debugged location-inference logic, not a copy

    conn_index = connections.load_connections_index()
    techmap_index = load_techmap_index()

    dataset = []
    for path in sorted(COMPANIES_DIR.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        company = record["name"]
        contacts = connections.contacts_for_company(conn_index, company)
        techmap_rows = techmap_index.get(connections.normalize_company(company), [])
        industry = techmap_rows[0]["industry"] if techmap_rows else None
        size = techmap_rows[0]["size"] if techmap_rows else None
        techmap_location_hint = techmap_rows[0]["location"] if techmap_rows else None

        for job in record["jobs"]:
            loc, city, is_remote = _pipeline._infer_location_fields(job, techmap_location_hint)
            out = dict(job)  # carries id/title/url/description/status/first_seen/last_seen/score_*/matched_*/coverage_*/confidence_*/requirements_*/best_*
            out["company"] = company
            out["industry"] = industry
            out["company_size"] = size
            out["location"] = loc
            out["city"] = city
            out["is_remote"] = is_remote
            out["department"] = job.get("department")
            out["employment_type"] = job.get("employment_type")
            out["posted_at"] = job.get("last_seen")
            out["connections"] = contacts
            out["has_connection"] = bool(contacts)
            out["has_description"] = bool(job.get("description"))
            out["years_required"] = scoring.required_years(f"{job['title']}\n{job.get('description') or ''}")
            out["is_referral"] = bool(job.get("is_referral"))
            out["referral_contact"] = job.get("referral_contact")
            dataset.append(out)

    dataset.sort(key=lambda r: r.get("best_score") or 0, reverse=True)
    atomic_write_json(config.JOBS_OUTPUT_JSON, dataset)
    return len(dataset)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process the first N companies (testing)")
    parser.add_argument("--company", type=str, default=None, help="only process this one company (exact name match)")
    parser.add_argument("--force", action="store_true", help="re-check companies even if checked recently")
    parser.add_argument("--skip-aggregate", action="store_true", help="don't rescore/rebuild after updating")
    args = parser.parse_args()

    all_companies = json.loads(config.ROOT.joinpath("companies_career_pages.json").read_text(encoding="utf-8"))
    companies = {name: url for name, url in all_companies.items() if url}

    if args.company:
        if args.company not in companies:
            logger.error("company %r not found (or has no URL) in companies_career_pages.json", args.company)
            return
        companies = {args.company: companies[args.company]}
    elif args.limit:
        companies = dict(list(companies.items())[: args.limit])

    started = time.time()
    profiles = cv.load_profiles()
    stats = scrape_stage(companies, profiles, force=args.force)

    print()
    print("=== Update summary ===")
    print(f"companies checked: {stats.companies_checked}")
    print(f"companies skipped (recently checked): {stats.companies_skipped}")
    print(f"new jobs: {stats.new_jobs}")
    print(f"closed jobs: {stats.closed_jobs}")
    print(f"failures: {len(stats.failures)}" + (f" ({', '.join(stats.failures)})" if stats.failures else ""))

    if args.company or args.limit:
        logger.info("skipping referral-jobs merge (scoped run via --company/--limit)")
    else:
        referral_stats = merge_referral_jobs(profiles)
        logger.info(
            "referral jobs: %d matched to existing companies, %d new companies, "
            "%d merged into existing jobs (referral-tagged), %d added as new jobs",
            referral_stats["matched_existing_company"], referral_stats["new_company"],
            referral_stats["merged_into_existing_job"], referral_stats["added_new_job"],
        )

    meta = load_meta()
    meta["last_run"] = _now_iso()
    save_meta(meta)

    if not args.skip_aggregate:
        recompute_stage()

    logger.info("done in %.1fs", time.time() - started)


if __name__ == "__main__":
    main()
