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
import base64
import hashlib
import json
import logging
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, company_registry, company_review, config, connections, cv, pipeline_lock, scoring, techmap_source, translation  # noqa: E402
from jobfit.atomic_io import write_json_atomic  # noqa: E402
from jobfit.scrape import bootstrap as scrape_bootstrap  # noqa: E402

logger = logging.getLogger("jobfit.update_jobs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COMPANIES_DIR = config.ROOT / "companies"
META_PATH = COMPANIES_DIR / "_meta.json"

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


def normalize_job_url(url: str | None) -> str | None:
    """The identity form of a job URL: whitespace and fragment stripped, no
    trailing slash - so `.../job/1/` and `.../job/1#apply` are one posting."""
    if not url:
        return None
    url = url.strip().split("#", 1)[0].rstrip("/")
    return url or None


def compute_job_id(company: str, title: str, location: str | None, url: str | None) -> str:
    """The job's URL, base64url-encoded (no padding) - a posting's identity IS
    its URL, so a re-run never treats an already-stored posting as new no
    matter how its title/location text is re-scraped. Only a URL-less job
    (techmap rows, some referrals) falls back to a hash of company +
    normalized title + location."""
    normalized_url = normalize_job_url(url)
    if normalized_url:
        return base64.urlsafe_b64encode(normalized_url.encode("utf-8")).decode("ascii").rstrip("=")
    normalized_title = re.sub(r"[^a-z0-9]+", "", (title or "").lower())
    raw = f"{company}|{normalized_title}|{location or ''}|"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def job_url_from_id(job_id: str) -> str | None:
    """Inverse of compute_job_id for URL-based ids; None for legacy hash ids."""
    try:
        padded = job_id + "=" * (-len(job_id) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


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


def atomic_write_json(path: Path, data, indent: int | None = 2) -> None:
    write_json_atomic(path, data, indent=indent)


def load_company_file(company: str) -> dict:
    path = COMPANIES_DIR / f"{_snake_case(company)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"name": company, "career_url": None, "last_checked": None, "jobs": []}


def save_company_file(company: str, data: dict) -> None:
    target_id = _snake_case(company)
    target_path = COMPANIES_DIR / f"{target_id}.json"
    if not target_path.exists():
        registry = company_registry.get_registry(COMPANIES_DIR, COMPANIES_DIR.parent / "data" / "company_registry.json")
        conflict_id = registry.register_if_new(target_id, company, data.get("career_url"))
        if conflict_id is not None:
            raise company_registry.DuplicateCompany(company, target_id, conflict_id)
    atomic_write_json(target_path, data)


def load_meta() -> dict:
    if META_PATH.exists():
        return json.loads(META_PATH.read_text(encoding="utf-8"))
    return {}


def save_meta(meta: dict) -> None:
    atomic_write_json(META_PATH, meta)


def load_companies_to_scrape() -> dict[str, str | None]:
    """Every company scrape_stage() should touch: companies with a real
    career URL, plus companies with no URL that were explicitly approved
    for the techmap fallback via company_review.py. A None value here means
    "use techmap only" (see fetch_company_jobs_async)."""
    pages = company_review.load_career_pages()
    review = company_review.load_review()
    companies: dict[str, str | None] = {}
    for name, url in pages.items():
        if url:
            companies[name] = url
        elif review.get(name, {}).get("decision") == "techmap":
            companies[name] = None
    return companies


def load_techmap_index() -> dict[str, list[dict]]:
    session = ats_fetchers.make_session()
    rows = techmap_source.load_all_rows(session)
    index: dict[str, list[dict]] = {}
    for row in rows:
        key = connections.normalize_company(row["company"])
        if key:
            index.setdefault(key, []).append(row)
    return index


TRUSTED_FETCH_SOURCES = ("ats_api", "external_board", "special_case")


def fetch_company_result(company: str, url: str | None, service, known_job_urls: list[str] | None = None) -> tuple[list[dict], object]:
    """Run the company's stored (or synthesised) ScrapePlan through
    CompanyScrapeService. Returns (jobs in diff_and_update's dict shape,
    the ScrapeResult). Pure Python; no model call on this path.
    known_job_urls: the company's already-stored job URLs - the service
    derives an ATS board from them when the plan itself finds nothing."""
    try:
        result = service.scrape(company, url, known_job_urls or [])
    except TypeError:  # a stub service with the old 2-arg signature (tests)
        result = service.scrape(company, url)
    jobs = []
    for posting in result.postings:
        job = posting.model_dump(mode="json")
        job["job_evidence"] = job.pop("evidence", None)
        job["scrape_source"] = job.pop("source", None)
        jobs.append(job)
    return jobs, result


def fetch_company_jobs(company: str, url: str | None, service) -> list[dict]:
    return fetch_company_result(company, url, service)[0]


def fetch_may_close(fetched: list[dict], result) -> bool:
    """May an update close the jobs this fetch didn't return? Yes when it
    returned anything, or came from a trusted source (an ATS API says what
    it says), or from a listing plan that has produced jobs before. No for
    an EMPTY result from an unverified listing plan: that page never
    yielded a job, so its silence says nothing about jobs stored from
    another source (LinkedIn matches, referrals, an earlier board fetch)."""
    if fetched:
        return True
    used = getattr(result, "strategy_used", None)
    if used in TRUSTED_FETCH_SOURCES:
        return True
    plan = getattr(result, "plan", None)
    return bool(plan is not None and (plan.health.baseline_yield or 0) > 0)


async def fetch_company_jobs_async(company: str, url: str | None, session, profiles: dict, techmap_index: dict, service=None) -> list[dict]:
    """Kept for callers of the old async signature; `profiles` is unused
    (scrape health no longer depends on CV score)."""
    service = service or scrape_bootstrap.build_scrape_service(session, techmap_index)
    return fetch_company_jobs(company, url, service)


def diff_and_update(company: str, career_url: str, fetched: list[dict], profiles: dict, may_close: bool = True) -> tuple[dict, int, int]:
    """Returns (updated_company_record, new_count, closed_count).

    Only newly-seen jobs get scored here (so they have a sane score
    immediately). Existing jobs' scores are left alone - recompute_stage()
    is the single place that rescands and rescores every stored job, on
    every trigger that could change a score (a new CV, a removed profile,
    or just periodically), not just when the CV hash changes.

    may_close=False (see fetch_may_close) records the check but leaves
    every job's status alone.
    """
    record = load_company_file(company)
    existing_by_id = {j["id"]: j for j in record["jobs"]}
    fetched_ids: set[str] = set()
    now = _now_iso()
    new_count = 0

    for job in fetched:
        title = (job.get("title") or "").strip()
        if not scoring.is_relevant_location(job.get("location")):
            continue  # non-Israel, non-remote office (e.g. "Texas", "Mexico") - not what this job search targets
        if not title:
            continue
        job_id = compute_job_id(company, title, job.get("location"), job.get("url"))
        fetched_ids.add(job_id)

        if job_id in existing_by_id:
            existing = existing_by_id[job_id]
            existing["last_seen"] = now
            if job.get("job_evidence") is not None:
                existing["job_evidence"] = job["job_evidence"]
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
                "department": job.get("department"),
                "employment_type": job.get("employment_type"),
                # Set only when translation.translate_job_if_needed() found
                # non-English (currently: Hebrew) content and translated it -
                # the UI shows a badge and can fall back to the original.
                "title_original": job.get("title_original"),
                "description_original": job.get("description_original"),
                "source_language": job.get("source_language"),
                # An ATS API tells us the job's real posting date - prefer that
                # over "now" (when *we* happened to first check) so a company
                # scraped for the first time doesn't make every one of its
                # existing postings look brand new.
                "first_seen": job.get("posted_at") or now,
                "last_seen": now,
                "status": "new",
                "job_evidence": job.get("job_evidence"),
                "scrape_source": job.get("scrape_source"),
            }
            new_job.update(scores)
            new_job["years_required"] = scoring.required_years(f"{title}\n{new_job['description'] or ''}")
            existing_by_id[job_id] = new_job
            new_count += 1

    closed_count = 0
    if may_close:
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


def _process_company(company, url, session, profiles, techmap_index, force, service=None):
    """Runs in a worker thread. Returns (company, new_count, closed_count, skipped, error)."""
    record = load_company_file(company)
    if _should_skip_company(record, force):
        return company, 0, 0, True, None
    try:
        service = service or scrape_bootstrap.build_scrape_service(session, techmap_index)
        known_job_urls = [j["url"] for j in record.get("jobs", []) if j.get("url")]
        fetched, result = fetch_company_result(company, url, service, known_job_urls)
        for job in fetched:
            translation.translate_job_if_needed(job)
        may_close = fetch_may_close(fetched, result)
        if not may_close:
            logger.info("%s: empty result from an unverified plan - existing jobs left open", company)
        updated, new_count, closed_count = diff_and_update(company, url, fetched, profiles, may_close=may_close)
        save_company_file(company, updated)
        return company, new_count, closed_count, False, None
    except Exception as error:  # noqa: BLE001 - one bad company must never abort the run
        return company, 0, 0, False, error


def scrape_stage(
    companies: dict[str, str], profiles: dict, force: bool = False, cancel_event=None
) -> RunStats:
    """The network-bound half of an update: fetch + diff every company,
    concurrently, skipping anything checked within COMPANY_RECHECK_TTL_HOURS
    unless force=True.

    cancel_event (a threading.Event, checked between submissions) lets a
    graceful stop request take effect without killing the process: once set,
    no more companies get queued, but whatever's already in flight (at most
    WORKERS of them) is allowed to finish and save normally, so nothing gets
    left half-written.
    """
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="scrape", scope=f"{len(companies)} companies"):
        session = ats_fetchers.make_session()
        logger.info("loading techmap data (fallback source for companies whose own site can't be parsed)...")
        techmap_index = load_techmap_index()
        service = scrape_bootstrap.build_scrape_service(session, techmap_index)

        stats = RunStats()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = []
            for company, url in companies.items():
                if cancel_event is not None and cancel_event.is_set():
                    logger.info("stop requested - not queuing the remaining companies")
                    break
                futures.append(pool.submit(_process_company, company, url, session, profiles, techmap_index, force, service))
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


RECOMPUTE_WORKERS = 8


def _recompute_one_company(
    path_str: str, profiles: dict, profile_ids: set, stale_prefixes: tuple, force: bool = False,
) -> tuple[int, int]:
    """Runs in a worker process (see recompute_stage): rescore one
    company's stored jobs against the given profiles and save it back.
    Module-level (not a closure) and taking only picklable arguments,
    since ProcessPoolExecutor sends this function and its arguments to a
    separate process.

    Each job stores a `_score_cache_keys` dict (one hash per profile, from
    scoring.score_cache_key - the job's description + that profile's CV
    text). A job whose stored keys still match the profiles' current keys
    is skipped entirely rather than rescored - a rerun only does real work
    for jobs whose description changed (a rescrape) or whose CV changed (a
    re-upload); a profile being added/removed also naturally falls out of
    this (the key sets no longer match) without special-casing it.

    The cache key is a hash of the *inputs* (description + CV text) only,
    not of jobfit.scoring's own logic - a scoring-formula change (e.g. a
    gate fix) doesn't change any job's inputs, so it can't invalidate the
    cache on its own. `force=True` bypasses the cache check entirely so a
    formula change actually takes effect on already-scored jobs, at the
    cost of rescoring everything regardless of whether it's needed.

    Returns (jobs_rescored, jobs_skipped_unchanged).
    """
    path = Path(path_str)
    record = json.loads(path.read_text(encoding="utf-8"))
    rescored = 0
    skipped = 0
    for job in record["jobs"]:
        current_keys = {name: scoring.score_cache_key(job, profile) for name, profile in profiles.items()}
        if not force and job.get("_score_cache_keys") == current_keys:
            skipped += 1
            continue
        job.update(scoring.score_job_both(job, profiles))
        job["years_required"] = scoring.required_years(f"{job.get('title') or ''}\n{job.get('description') or ''}")
        job["_score_cache_keys"] = current_keys
        rescored += 1
        for key in list(job):
            for prefix in stale_prefixes:
                if key.startswith(prefix) and key[len(prefix):] not in profile_ids:
                    del job[key]
    save_company_file(record["name"], record)
    return rescored, skipped


def recompute_stage(force: bool = False, force_aggregate: bool = False) -> None:
    """The local-only half of an update: rescore every stored job against the
    *current* profile registry, drop any score fields for profiles that no
    longer exist, reaggregate, and rebuild jobfit.html. Safe to call after
    any admin edit, not just after a scrape.

    Rescoring is CPU-bound (regex-based requirement extraction and
    matching per job, not I/O), so it's parallelized across
    RECOMPUTE_WORKERS separate processes rather than threads - Python
    threads mostly serialize on the GIL for this kind of work, the same
    reason the scrape side's plain-HTTP calls don't benefit from asyncio.
    Each company is independent (its own file, no shared state), so
    process-per-company parallelizes cleanly with no coordination needed
    beyond collecting each worker's per-company job count for the
    progress log.

    force=True bypasses the per-job score cache (see _recompute_one_company)
    - needed after a jobfit.scoring/ats_scorer *logic* change, since the
    cache key only tracks input (description/CV text) changes.
    """
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="recompute", scope="all" if not force else "all (forced)"):
        profiles = cv.load_profiles()
        profile_ids = set(profiles)
        stale_prefixes = ("score_", "matched_", "coverage_", "confidence_", "requirements_")

        company_paths = [p for p in sorted(COMPANIES_DIR.glob("*.json")) if p.name != "_meta.json"]
        total_companies = len(company_paths)
        jobs_rescored = 0
        jobs_skipped = 0
        log_every = 50
        start = time.time()
        logger.info(
            "recompute: rescoring %d companies against %d profile(s) using %d worker processes%s...",
            total_companies, len(profiles), RECOMPUTE_WORKERS, " (forced, ignoring cache)" if force else "",
        )

        with ProcessPoolExecutor(max_workers=RECOMPUTE_WORKERS) as pool:
            futures = [
                pool.submit(_recompute_one_company, str(path), profiles, profile_ids, stale_prefixes, force)
                for path in company_paths
            ]
            for i, future in enumerate(as_completed(futures), start=1):
                rescored, skipped = future.result()
                jobs_rescored += rescored
                jobs_skipped += skipped
                if i % log_every == 0 or i == total_companies:
                    elapsed = time.time() - start
                    rate = jobs_rescored / elapsed if elapsed > 0 else 0
                    logger.info(
                        "recompute progress: %d/%d companies, %d jobs rescored, %d unchanged (skipped) "
                        "(%.0f jobs/s, %.0fs elapsed)",
                        i, total_companies, jobs_rescored, jobs_skipped, rate, elapsed,
                    )

        count = aggregate_to_jobs_v2(force=force_aggregate)
        meta = load_meta()
        meta["scoring_engine"] = scoring.SCORING_ENGINE_FINGERPRINT
        save_meta(meta)
        logger.info(
            "recompute: rescored %d jobs (%d unchanged, skipped) against %d profile(s), aggregated %d jobs, engine %s",
            jobs_rescored, jobs_skipped, len(profiles), count, scoring.SCORING_ENGINE_FINGERPRINT,
        )
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

    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="referral-merge", scope="all"):
        path = path or config.REFERRAL_JOBS_PATH
        stats = {
            "matched_existing_company": 0, "new_company": 0, "merged_into_existing_job": 0,
            "added_new_job": 0, "added_to_career_pages": 0, "scrapable_companies": [],
        }
        if not path.exists():
            return stats

        # A referral company (new or already tracked in companies/*.json) may still
        # be missing from the curated scrape-target list (companies_career_pages.json)
        # - that's what actually gates scrape_stage(). Fold it in here so a company
        # first seen via referral gets picked up by future scrape runs too, instead
        # of only ever being refreshed by another referral touching it.
        career_pages = company_review.load_career_pages()
        review = company_review.load_review()
        techmap_index = None

        existing_names = [
            json.loads(p.read_text(encoding="utf-8"))["name"]
            for p in COMPANIES_DIR.glob("*.json") if p.name != "_meta.json"
        ]
        canonical_by_key = {connections.normalize_company(c): c for c in existing_names if connections.normalize_company(c)}
        now = _now_iso()
        touched_companies: set[str] = set()

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

            touched_companies.add(canonical)
            record = load_company_file(canonical)
            for raw_job in company_entry.get("jobs") or []:
                title = (raw_job.get("title") or "").strip()
                if not title:
                    continue
                referral_job = referral_source._to_job_dict(raw_job)
                ref_url = (referral_job.get("url") or "").strip().rstrip("/")
                match = next(
                    (
                        j for j in record["jobs"]
                        if (ref_url and (j.get("url") or "").strip().rstrip("/") == ref_url)
                        or referral_source._is_duplicate_title(j.get("title") or "", title)
                    ),
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

            if canonical not in career_pages:
                if techmap_index is None:
                    techmap_index = load_techmap_index()
                has_techmap = bool(techmap_index.get(connections.normalize_company(canonical)))
                career_pages[canonical] = None
                if has_techmap:
                    review[canonical] = {"decision": "techmap", "decided_at": _now_iso()}
                stats["added_to_career_pages"] += 1

        if stats["added_to_career_pages"]:
            company_review.save_career_pages(career_pages)
            company_review.save_review(review)

        # Which of this upload's companies are actually eligible for a scoped
        # scrape right now (a real URL, or techmap-approved) - a company left
        # pending review isn't scrapable until that's resolved in the panel.
        scrapable = load_companies_to_scrape()
        stats["scrapable_companies"] = sorted(name for name in touched_companies if name in scrapable)

        return stats


COMPANY_ADDRESSES_PATH = config.ROOT / "data" / "company_addresses.json"


def _context_sha1() -> str:
    """Covers every input that a flattened row embeds besides the company
    file's own bytes: LinkedIn connections (contacts_for_company), techmap
    (industry/size/location hint) and the company address book (city
    fallback) all feed into every row, so a change to any must invalidate
    every company's cache entry, not just the one company file that
    happened to change."""
    hasher = hashlib.sha1()
    if config.CONNECTIONS_CSV.exists():
        hasher.update(config.CONNECTIONS_CSV.read_bytes())
    if config.TECHMAP_CACHE_DIR.exists():
        for path in sorted(config.TECHMAP_CACHE_DIR.glob("*")):
            if path.is_file():
                hasher.update(path.read_bytes())
    if COMPANY_ADDRESSES_PATH.exists():
        hasher.update(COMPANY_ADDRESSES_PATH.read_bytes())
    return hasher.hexdigest()


def load_company_address_cities() -> dict[str, str]:
    """normalized company key -> registered office city (first branch), from
    data/company_addresses.json. The city fallback for jobs located only as
    "Israel"."""
    if not COMPANY_ADDRESSES_PATH.exists():
        return {}
    try:
        data = json.loads(COMPANY_ADDRESSES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for name, branches in data.items():
        branches = branches if isinstance(branches, list) else [branches]
        city = next((b.get("city") for b in branches if isinstance(b, dict) and b.get("city")), None)
        key = connections.normalize_company(name)
        if city and key:
            out[key] = city
    return out


def _flatten_company(record: dict, company: str, contacts: list, industry, size, techmap_location_hint,
                     company_city: str | None = None) -> list[dict]:
    from jobfit import pipeline as _pipeline  # reuse its already-debugged location-inference logic, not a copy

    rows = []
    for job in record["jobs"]:
        loc, city, is_remote = _pipeline._infer_location_fields(job, techmap_location_hint, company_city)
        out = dict(job)  # carries id/title/url/description/status/first_seen/last_seen/score_*/matched_*/coverage_*/confidence_*/requirements_*/best_*
        out["company"] = company
        out["industry"] = industry
        out["company_size"] = size
        out["location"] = loc
        out["city"] = city
        out["is_remote"] = is_remote
        out["department"] = job.get("department")
        out["employment_type"] = job.get("employment_type")
        # first_seen (when jobfit first saw this listing), not last_seen
        # (which bumps every time a re-check still finds the job open) -
        # "posted" should read as roughly-stable, not reset on every run.
        out["posted_at"] = job.get("first_seen")
        out["connections"] = contacts
        out["has_connection"] = bool(contacts)
        out["has_description"] = bool(job.get("description"))
        if "years_required" in job:
            years_required = job["years_required"]
        else:
            years_required = scoring.required_years(f"{job['title']}\n{job.get('description') or ''}")
        out["years_required"] = years_required
        out["is_referral"] = bool(job.get("is_referral"))
        out["referral_contact"] = job.get("referral_contact")
        rows.append(out)
    return rows


def aggregate_to_jobs_v2(force: bool = False) -> int:
    """Flatten companies/*.json into the record shape build_html.py already
    expects, and write it to config.JOBS_OUTPUT_JSON - so build_html needs no
    changes at all, it just picks up whatever's there.

    Each company's flattened rows are cached under config.AGGREGATE_CACHE_DIR,
    keyed by the sha1 of its own companies/*.json bytes plus a shared
    context_sha1 (connections + techmap, which also feed every row). A
    company whose file and the shared context are both unchanged since its
    last flatten is a cache hit - this is what makes a scoped `--company X`
    run's aggregate step cost seconds instead of the ~6 minutes a full
    re-flatten of ~1900 companies takes. force=True ignores the cache
    entirely (e.g. after a bulk edit that touched context but you want to
    be sure).
    """
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="aggregate", scope="all"):
        conn_index = connections.load_connections_index()
        techmap_index = load_techmap_index()
        address_cities = load_company_address_cities()
        context_sha1 = _context_sha1()

        cache_dir = config.AGGREGATE_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        live_stems: set[str] = set()

        dataset = []
        for path in sorted(COMPANIES_DIR.glob("*.json")):
            if path.name == "_meta.json":
                continue
            stem = path.stem
            live_stems.add(stem)
            source_bytes = path.read_bytes()
            source_sha1 = hashlib.sha1(source_bytes).hexdigest()
            cache_path = cache_dir / f"{stem}.json"

            rows = None
            if not force and cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    cached = None
                if cached is not None and cached.get("source_sha1") == source_sha1 and cached.get("context_sha1") == context_sha1:
                    rows = cached["rows"]

            if rows is None:
                record = json.loads(source_bytes.decode("utf-8"))
                company = record["name"]
                contacts = connections.contacts_for_company(conn_index, company)
                techmap_rows = techmap_index.get(connections.normalize_company(company), [])
                industry = techmap_rows[0]["industry"] if techmap_rows else None
                size = techmap_rows[0]["size"] if techmap_rows else None
                techmap_location_hint = techmap_rows[0]["location"] if techmap_rows else None
                company_city = address_cities.get(connections.normalize_company(company))
                rows = _flatten_company(record, company, contacts, industry, size, techmap_location_hint, company_city)
                atomic_write_json(cache_path, {"source_sha1": source_sha1, "context_sha1": context_sha1, "rows": rows})

            dataset.extend(rows)

        for stale in cache_dir.glob("*.json"):
            if stale.stem not in live_stems:
                stale.unlink()

        dataset.sort(key=lambda r: r.get("best_score") or 0, reverse=True)
        atomic_write_json(config.JOBS_OUTPUT_JSON, dataset, indent=None)
        atomic_write_json(config.JOBS_OUTPUT_META_JSON, {
            "scoring_engine": scoring.SCORING_ENGINE_FINGERPRINT,
            "aggregated_at": _now_iso(),
            "job_count": len(dataset),
            "company_count": len({r["company"] for r in dataset}),
        })
        return len(dataset)


def print_plan_summary(store) -> None:
    from collections import Counter
    plans = list(store.all())
    by_status = Counter(p.status for p in plans)
    by_derived = Counter(p.derived_by for p in plans)
    by_kind = Counter(p.strategy.kind for p in plans)
    print(f"scrape plans: {len(plans)}")
    print("  by status:     " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("  by derived_by: " + ", ".join(f"{k}={v}" for k, v in sorted(by_derived.items())))
    print("  by kind:       " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    broken = [p for p in plans if p.strategy.kind == "broken_url"]
    if broken:
        print("  broken_url plans (review these):")
        for p in broken:
            print(f"    {p.company_id}: {p.strategy.reason} [{p.status}]")


@dataclass
class DiscoveryStats:
    selected: int = 0
    discovered: int = 0
    verified: int = 0
    unverified: int = 0
    broken: int = 0
    failed: list[str] = field(default_factory=list)


def select_for_discovery(companies: dict[str, str | None], store, now: datetime, max_per_run: int, force_company: str | None = None) -> list[tuple[str, str | None]]:
    """Missing plans first, then stale/stale_suspect past their cooldown,
    then rules-derived unverified plans past their cooldown; capped."""
    from jobfit.scrape.ids import plan_id_for

    if force_company:
        return [(force_company, companies[force_company])]
    missing, stale, rules = [], [], []
    for company, url in companies.items():
        plan = store.get(plan_id_for(company))
        if plan is None:
            missing.append((company, url))
            continue
        cooling = plan.rediscover_after is not None and plan.rediscover_after > now
        if plan.status in ("stale_suspect", "stale") and not cooling:
            stale.append((company, url))
        elif plan.status == "unverified" and plan.derived_by == "rules" and not cooling:
            rules.append((company, url))
    ordered = missing + stale + rules
    return ordered if max_per_run == 0 else ordered[:max_per_run]


def discover_plans(companies: dict[str, str | None], planner, store, snapshots_dir: Path, max_per_run: int, concurrency: int,
                   force_company: str | None = None, now=None) -> DiscoveryStats:
    """The discovery batch - the only code path that may call a model
    (through the planner's classifier). Writes one plan per company and
    the listing snapshot it was derived from."""
    from jobfit.scrape.errors import FetchFailed
    from jobfit.scrape.ids import plan_id_for

    now = now or (lambda: datetime.now(timezone.utc))
    stats = DiscoveryStats()
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="discover", scope=force_company or "batch"):
        selected = select_for_discovery(companies, store, now(), max_per_run, force_company)
        stats.selected = len(selected)
        logger.info("discovery: %d companies selected (max %s)", len(selected), max_per_run or "unbounded")

        def one(company, url):
            company_id = plan_id_for(company)
            plan, page = planner.discover(company_id, url)
            store.put(plan)
            if page is not None:
                snapshots_dir.mkdir(parents=True, exist_ok=True)
                tmp = snapshots_dir / f"{company_id}.html.tmp"
                tmp.write_text(page.html, encoding="utf-8")
                tmp.replace(snapshots_dir / f"{company_id}.html")
            return company, plan

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(one, company, url): company for company, url in selected}
            for future in as_completed(futures):
                company = futures[future]
                try:
                    _, plan = future.result()
                except FetchFailed as error:
                    stats.failed.append(company)
                    logger.warning("%s: discovery failed - %s", company, error)
                    continue
                except Exception as error:  # noqa: BLE001 - one bad company must never abort the batch
                    stats.failed.append(company)
                    logger.warning("%s: discovery crashed - %s: %s", company, type(error).__name__, error)
                    continue
                stats.discovered += 1
                if plan.strategy.kind == "broken_url":
                    stats.broken += 1
                if plan.status == "verified":
                    stats.verified += 1
                else:
                    stats.unverified += 1
                logger.info("%s: plan %s/%s via %s", company, plan.strategy.kind, plan.status, plan.derived_by)
        stats.failed.sort()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process the first N companies (testing)")
    parser.add_argument("--company", type=str, default=None, help="only process this one company (exact name match)")
    parser.add_argument("--force", action="store_true", help="re-check companies even if checked recently")
    parser.add_argument("--skip-aggregate", action="store_true", help="don't rescore/rebuild after updating")
    parser.add_argument("--wait", action="store_true", help="wait for another pipeline run to finish instead of exiting")
    parser.add_argument("--force-rescore", action="store_true", help="rescore every job regardless of the score cache")
    parser.add_argument("--force-aggregate", action="store_true", help="ignore the per-company aggregate cache")
    parser.add_argument("--plans", action="store_true", help="print a summary of stored scrape plans and exit")
    parser.add_argument("--discover", action="store_true", help="derive scrape plans for companies that need one (missing/stale/rules-unverified) before scraping; the only mode that may call a model")
    parser.add_argument("--discover-max", type=int, default=config.DISCOVERY_MAX_PER_RUN, help="companies per --discover run (0 = unbounded)")
    parser.add_argument("--rediscover", action="store_true", help="force re-discovery of --company, ignoring budget and cooldown")
    parser.add_argument("--no-llm", action="store_true", help="discovery with the rules classifier only")
    args = parser.parse_args()

    if args.rediscover and not args.company:
        parser.error("--rediscover requires --company")

    if args.plans:
        from jobfit.scrape.plan_store import FilePlanStore
        print_plan_summary(FilePlanStore(config.SCRAPE_PLANS_DIR))
        return

    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="update", scope=args.company or "all", wait=args.wait):
        companies = load_companies_to_scrape()

        if args.company:
            if args.company not in companies:
                logger.error("company %r not found (or has no URL) in companies_career_pages.json", args.company)
                return
            companies = {args.company: companies[args.company]}
        elif args.limit:
            companies = dict(list(companies.items())[: args.limit])

        if args.discover or args.rediscover:
            from jobfit.scrape.plan_store import FilePlanStore
            session = ats_fetchers.make_session()
            planner = scrape_bootstrap.build_discovery_planner(session, use_llm=not args.no_llm)
            discovery = discover_plans(
                companies, planner, FilePlanStore(config.SCRAPE_PLANS_DIR), config.LISTING_SNAPSHOTS_DIR,
                max_per_run=0 if args.rediscover else args.discover_max, concurrency=config.DISCOVERY_CONCURRENCY,
                force_company=args.company if args.rediscover else None,
            )
            print()
            print("=== Discovery summary ===")
            print(f"selected: {discovery.selected}, discovered: {discovery.discovered} (verified {discovery.verified}, unverified {discovery.unverified}, broken {discovery.broken})")
            print(f"failed: {len(discovery.failed)}" + (f" ({', '.join(discovery.failed)})" if discovery.failed else ""))

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
            recompute_stage(force=args.force_rescore, force_aggregate=args.force_aggregate)

        logger.info("done in %.1fs", time.time() - started)


if __name__ == "__main__":
    main()
