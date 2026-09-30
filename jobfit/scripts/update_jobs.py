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
from jobfit.scrape import candidates, titles  # noqa: E402
from jobfit.scrape.ids import normalize_job_url  # noqa: E402,F401 - re-exported: the job-id rule lives with the scrape package
from jobfit.store import companies as store_companies  # noqa: E402
from jobfit.store import db  # noqa: E402
from jobfit.store import jobs as store_jobs  # noqa: E402
from jobfit.store import scores as store_scores  # noqa: E402

logger = logging.getLogger("jobfit.update_jobs")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

COMPANIES_DIR = config.ROOT / "companies"
META_PATH = COMPANIES_DIR / "_meta.json"

def _snake_case(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s or "unnamed_company"


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
    career URL, plus companies with no URL that were explicitly approved for
    the techmap fallback. A None value here means "use techmap only" (see
    fetch_company_result). The rule now lives in the store, which holds both
    the URL and the review decision on one row."""
    return store_companies.companies_to_scrape(db.shared())


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
    result = service.scrape(company, url, known_job_urls or [])
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


def _score_rows(scored: dict, job: dict, profiles: dict) -> dict[str, dict]:
    """scoring.score_job_both returns one flat dict (score_default,
    matched_infra, ...); the store wants a row per profile."""
    return {
        profile_id: {
            "score": scored.get(f"score_{profile_id}"),
            "coverage": scored.get(f"coverage_{profile_id}"),
            "confidence": scored.get(f"confidence_{profile_id}"),
            "matched": scored.get(f"matched_{profile_id}") or [],
            "cache_key": scoring.score_cache_key(job, profile),
        }
        for profile_id, profile in profiles.items()
    }


def diff_and_update(company: str, career_url: str, fetched: list[dict], profiles: dict,
                    may_close: bool = True, techmap_index: dict | None = None) -> tuple[int, int]:
    """Apply one company's scrape to the store. Returns (new, closed).

    Only newly-seen jobs are scored here, so they have a sane score
    immediately; recompute_stage is the single place that rescores
    everything else, on every trigger that could change a score.

    may_close=False (see fetch_may_close) records the check but leaves every
    job's status alone.
    """
    conn = db.shared()
    company_id = _snake_case(company)
    now = _now_iso()
    store_companies.upsert_company(conn, company_id, company, career_url=career_url, last_checked=now)
    company_row = store_companies.get_company(conn, company_id)
    address_cities = {connections.normalize_company(company): company_row["address_city"]}

    relevant = []
    for job in fetched:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        # A non-Israel, non-remote office ("Texas", "Mexico") is not what this
        # job search targets, and storing it would only add noise to search.
        if not scoring.is_relevant_location(job.get("location")):
            continue
        prepared = dict(job)
        prepared["title"] = title
        prepared["id"] = compute_job_id(company, title, job.get("location"), job.get("url"))
        prepared["description"] = ats_fetchers.strip_html(job.get("description"))
        prepared["years_required"] = scoring.required_years(f"{title}\n{prepared['description'] or ''}")
        prepared.update(location_fields_for(prepared, company, techmap_index or {}, address_cities))
        relevant.append(prepared)

    new_count, closed_count = store_jobs.upsert_scraped(conn, company_id, relevant, now, may_close=may_close)

    if profiles:
        for job in relevant:
            if store_scores.scores_for_job(conn, job["id"]):
                continue  # already scored; recompute_stage owns rescoring
            store_scores.write_scores(conn, job["id"], _score_rows(scoring.score_job_both(job, profiles), job, profiles))
    return new_count, closed_count


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
    conn = db.shared()
    company_id = _snake_case(company)
    stored = store_companies.get_company(conn, company_id)
    if _should_skip_company(dict(stored) if stored else {}, force):
        return company, 0, 0, True, None
    try:
        service = service or scrape_bootstrap.build_scrape_service(session, techmap_index)
        known_job_urls = [row["url"] for row in store_jobs.jobs_for_company(conn, company_id) if row["url"]]
        fetched, result = fetch_company_result(company, url, service, known_job_urls)
        for job in fetched:
            translation.translate_job_if_needed(job)
        may_close = fetch_may_close(fetched, result)
        if not may_close:
            logger.info("%s: empty result from an unverified plan - existing jobs left open", company)
        new_count, closed_count = diff_and_update(company, url, fetched, profiles, may_close=may_close,
                                                  techmap_index=techmap_index)
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


def job_text(job: dict) -> str:
    """Title and description as one blob - what the requirement extractor and
    the years parser both read."""
    return "\n".join([job.get("title") or "", job.get("description") or ""])


def _score_one(args: tuple) -> dict:
    """Runs in a worker process: picklable in, picklable out. A sqlite
    connection cannot cross a process boundary, so workers compute and the
    parent writes."""
    job, profiles = args
    return scoring.score_job_both(job, profiles)


def _score_many(jobs: list[dict], profiles: dict) -> list[dict]:
    """Scoring is CPU-bound (regex requirement extraction and matching, not
    I/O), so it goes to processes rather than threads, which would serialize
    on the GIL. Below a few hundred jobs the process startup costs more than
    the work."""
    if len(jobs) < 200:
        return [scoring.score_job_both(job, profiles) for job in jobs]
    with ProcessPoolExecutor(max_workers=RECOMPUTE_WORKERS) as pool:
        return list(pool.map(_score_one, [(job, profiles) for job in jobs], chunksize=50))


def _rebuild_page() -> None:
    """Temporary scaffolding: the static page still exists until the app
    replaces it (phase 3), and it reads from the store."""
    from jobfit import build_html

    build_html.build()


def recompute_stage(force: bool = False) -> None:
    """Rescore every stored job against the *current* profile registry and
    rebuild the page. Safe to call after any admin edit, not just a scrape.

    A job carries one cache key per profile (scoring.score_cache_key: its own
    text, that profile's CV text, and the scoring engine's fingerprint), so a
    rerun does real work only for jobs whose text changed, whose CV changed,
    or when the scoring code itself changed. force=True ignores the keys.
    """
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="recompute",
                                   scope="all (forced)" if force else "all"):
        conn = db.shared()
        profiles = cv.load_profiles()
        # Bail out BEFORE touching scores. An empty registry means the CVs
        # aren't loaded, not that every score is garbage - dropping them here
        # once wiped all 60,294 score rows from the real database.
        if not profiles:
            logger.warning("recompute: no CV profiles registered - leaving stored scores alone")
            _rebuild_page()
            return
        dropped = store_scores.drop_scores_for_missing_profiles(conn, set(profiles))
        if dropped:
            logger.info("recompute: dropped %d score row(s) for profiles that no longer exist", dropped)

        start = time.time()
        pending: list[dict] = []
        wanted_keys: dict[str, dict[str, str]] = {}
        total = 0
        for row in conn.execute("SELECT * FROM jobs ORDER BY id"):
            job = dict(row)
            total += 1
            keys = {name: scoring.score_cache_key(job, profile) for name, profile in profiles.items()}
            stored = store_scores.scores_for_job(conn, job["id"])
            if not force and all(stored.get(name, {}).get("cache_key") == key for name, key in keys.items()):
                continue
            wanted_keys[job["id"]] = keys
            pending.append(job)

        logger.info("recompute: %d of %d job(s) need scoring against %d profile(s)%s",
                    len(pending), total, len(profiles), " (forced, ignoring cache)" if force else "")

        for index in range(0, len(pending), 1000):
            chunk = pending[index:index + 1000]
            for job, scored in zip(chunk, _score_many(chunk, profiles)):
                rows = _score_rows(scored, job, profiles)
                for name in rows:
                    rows[name]["cache_key"] = wanted_keys[job["id"]][name]
                store_scores.write_scores(conn, job["id"], rows)
                years = scoring.required_years(job_text(job))
                if years != job.get("years_required"):
                    conn.execute("UPDATE jobs SET years_required = ? WHERE id = ?", (years, job["id"]))
            logger.info("recompute progress: %d/%d jobs (%.0fs elapsed)",
                        min(index + 1000, len(pending)), len(pending), time.time() - start)

        # The page's footer shows which scoring engine produced the numbers,
        # and reads it from here.
        meta = load_meta()
        meta["scoring_engine"] = scoring.SCORING_ENGINE_FINGERPRINT
        save_meta(meta)
        logger.info("recompute: scored %d job(s), %d unchanged, engine %s",
                    len(pending), total - len(pending), scoring.SCORING_ENGINE_FINGERPRINT)
        _rebuild_page()


def close_jobs_by_url(closed_urls: dict[str, str]) -> dict[str, int]:
    """Close every open job whose (normalized) URL is a key of closed_urls,
    recording the reason - the path by which jobs that no company scrape
    re-verifies (LinkedIn matches, WhatsApp referrals) still age out:
    check_linkedin_closed feeds LinkedIn's "no longer accepting
    applications" banner in here, check_urls feeds confirmed 404/410s.
    Nothing is deleted; a closed job keeps its record, like the scrape path."""
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="close-stale",
                                   scope=f"{len(closed_urls)} urls"):
        return store_jobs.close_by_url(db.shared(), closed_urls, _now_iso())


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
        techmap_index = None

        conn = db.shared()
        existing_names = [row["display_name"] for row in store_companies.list_companies(conn)]
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
            company_id = _snake_case(canonical)
            store_companies.upsert_company(conn, company_id, canonical)
            stored_jobs = [dict(row) for row in store_jobs.jobs_for_company(conn, company_id)]
            for raw_job in company_entry.get("jobs") or []:
                title = (raw_job.get("title") or "").strip()
                if not title:
                    continue
                referral_job = referral_source._to_job_dict(raw_job)
                ref_url = (referral_job.get("url") or "").strip().rstrip("/")
                match = next(
                    (
                        j for j in stored_jobs
                        if (ref_url and (j.get("url") or "").strip().rstrip("/") == ref_url)
                        or referral_source._is_duplicate_title(j.get("title") or "", title)
                    ),
                    None,
                )
                if match is not None:
                    conn.execute(
                        "UPDATE jobs SET is_referral = 1, referral_contact = ?, last_seen = ?, "
                        "status = CASE WHEN status IN ('new', 'closed') THEN 'seen' ELSE status END, "
                        "closed_at = NULL, closed_reason = NULL WHERE id = ?",
                        (referral_job["referral_contact"], now, match["id"]),
                    )
                    stats["merged_into_existing_job"] += 1
                else:
                    new_job = {
                        "id": compute_job_id(canonical, title, referral_job.get("location"), referral_job.get("url")),
                        "title": title,
                        "location": referral_job.get("location"),
                        "url": referral_job.get("url"),
                        "description": ats_fetchers.strip_html(referral_job.get("description")),
                        "is_referral": True,
                        "referral_contact": referral_job.get("referral_contact"),
                    }
                    # may_close=False: a referral export says nothing about
                    # whether this company's other jobs are still open.
                    store_jobs.upsert_scraped(conn, company_id, [new_job], now, may_close=False)
                    store_scores.write_scores(
                        conn, new_job["id"],
                        _score_rows(scoring.score_job_both(referral_job, profiles), new_job, profiles),
                    )
                    stats["added_new_job"] += 1

            # A company first seen via referral must become scrapable too, or
            # it would only ever be refreshed by another referral touching it.
            if store_companies.get_company(conn, company_id)["career_url"] is None:
                if techmap_index is None:
                    techmap_index = load_techmap_index()
                if techmap_index.get(connections.normalize_company(canonical)):
                    store_companies.upsert_company(conn, company_id, canonical, review_decision="techmap")
                stats["added_to_career_pages"] += 1

        # Which of this upload's companies are actually eligible for a scoped
        # scrape right now (a real URL, or techmap-approved) - a company left
        # pending review isn't scrapable until that's resolved in the panel.
        scrapable = load_companies_to_scrape()
        stats["scrapable_companies"] = sorted(name for name in touched_companies if name in scrapable)

        return stats


COMPANY_ADDRESSES_PATH = config.ROOT / "data" / "company_addresses.json"


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


def location_fields_for(job: dict, company: str, techmap_index: dict, address_cities: dict) -> dict:
    """A job's display location, city and remote flag.

    The aggregate stage used to compute this for every row at the end of a
    run, which is why a job's own file never carried a city. It now happens
    once, when the job is written, so "jobs in Tel Aviv" is a column and
    therefore a query."""
    from jobfit import pipeline as _pipeline  # its already-debugged logic, not a copy

    key = connections.normalize_company(company)
    rows = techmap_index.get(key, [])
    location, city, is_remote = _pipeline._infer_location_fields(
        job, rows[0]["location"] if rows else None, address_cities.get(key)
    )
    return {"location": location, "city": city, "is_remote": is_remote}


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
                tmp.write_text(candidates.strip_non_content(page.html), encoding="utf-8")
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
    parser.add_argument("--skip-recompute", action="store_true", help="scrape only; don't rescore or rebuild the page")
    parser.add_argument("--wait", action="store_true", help="wait for another pipeline run to finish instead of exiting")
    parser.add_argument("--force-rescore", action="store_true", help="rescore every job regardless of the score cache")
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

        if not args.skip_recompute:
            recompute_stage(force=args.force_rescore)

        logger.info("done in %.1fs", time.time() - started)


if __name__ == "__main__":
    main()
