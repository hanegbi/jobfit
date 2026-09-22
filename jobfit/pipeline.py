"""Orchestrates: techmap ingest -> per-company ATS fetch -> score -> connections -> JSON."""

import argparse
import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from jobfit import ats_fetchers, config, connections, cv, referral_source, scoring, techmap_source
from jobfit import linkedin_match_bridge as lm_bridge

logger = logging.getLogger("jobfit.pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WORKERS = 8
MAX_TITLE_LENGTH = 150


def _job_id(company: str, title: str, url: str | None) -> str:
    raw = f"{company}|{title}|{url or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _group_by_company(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["company"], []).append(row)
    return groups


def _load_company_cache() -> dict:
    if config.COMPANY_JOBS_CACHE.exists():
        return json.loads(config.COMPANY_JOBS_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_company_cache(cache: dict) -> None:
    config.COMPANY_JOBS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    config.COMPANY_JOBS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")


def _cache_fresh(entry: dict) -> bool:
    try:
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
    except (KeyError, ValueError):
        return False
    age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
    return age_hours < config.COMPANY_JOBS_TTL_HOURS


def _resolve_ats_for_company(rows: list[dict]) -> tuple[str, str, str] | None:
    """Try each distinct URL for a company until one resolves to a known ATS."""
    seen_urls = []
    for row in rows:
        if row["url"] and row["url"] not in seen_urls:
            seen_urls.append(row["url"])
    for url in seen_urls:
        resolved = ats_fetchers.resolve_ats(url)
        if resolved:
            ats, token = resolved
            return ats, token, url
    return None


def _infer_location_fields(job: dict, company_location_hint: str | None = None) -> tuple[str | None, str | None, bool]:
    """Return (display_location, city, is_remote).

    Fallback order when the job's own location field is empty (common for
    company-career-page listings, where the scraper only captured title+url):
      1. A city/remote signal in the title (specific, per-job) - description
         is checked only if the title has nothing, since a description very
         often mentions the company's HQ city as boilerplate ("join our Tel
         Aviv team") regardless of which city THIS job is actually in; trusting
         whichever city matched first in a combined title+description blob
         previously mislabeled real Ramat Gan/Bnei Brak roles as Tel Aviv.
      2. techmap's own location data for this company (any of its listed
         postings) - a real, if coarse, per-company signal.
      3. The literal string "NaN" - so a missing location is a visible,
         filterable marker rather than a silently blank field.
    """
    raw = job.get("location")
    if raw:
        return scoring.to_english_location(raw), scoring.canonical_city(raw), scoring.is_remote_location(raw)

    title = job.get("title") or ""
    description = job.get("description") or ""
    city = scoring.canonical_city(title) or scoring.canonical_city(description)
    is_remote = scoring.is_remote_location(title) or scoring.is_remote_location(description)
    if city or is_remote:
        return (city or "Remote"), city, is_remote

    if company_location_hint:
        hint_city = scoring.canonical_city(company_location_hint)
        hint_remote = scoring.is_remote_location(company_location_hint)
        if hint_city or hint_remote:
            return scoring.to_english_location(company_location_hint), hint_city, hint_remote

    return "NaN", None, False


def _fallback_jobs_from_rows(rows: list[dict]) -> list[dict]:
    jobs = []
    for row in rows:
        jobs.append({
            "title": row["title"],
            "location": row["location"],
            "url": row["url"],
            "description": "",
            "department": row["function"],
            "employment_type": row["level"],
            "posted_at": row["posted_at"],
        })
    return jobs


def _load_lm_live_scraped() -> dict[str, list[dict]]:
    if config.LM_LIVE_SCRAPED_CACHE.exists():
        return json.loads(config.LM_LIVE_SCRAPED_CACHE.read_text(encoding="utf-8"))
    return {}


def _load_company_career_pages() -> dict[str, list[dict]]:
    if config.COMPANY_CAREER_PAGES_CACHE.exists():
        return json.loads(config.COMPANY_CAREER_PAGES_CACHE.read_text(encoding="utf-8"))
    return {}


_DEAD_END_HOSTS = ("linkedin.com", "comeet.com")


def _has_fetchable_url(jobs: list[dict]) -> bool:
    """Whether any job points at a URL the generic-description fetch can reach."""
    return any(
        j.get("url") and not any(h in j["url"].lower() for h in _DEAD_END_HOSTS)
        for j in jobs
    )


def _lm_live_worth_taking(jobs: list[dict]) -> bool:
    return bool(jobs) and (any(j.get("description") for j in jobs) or _has_fetchable_url(jobs))


def fetch_all_company_jobs(
    groups: dict[str, list[dict]],
    force: bool = False,
    scrape_missing: bool = False,
) -> dict[str, list[dict]]:
    """Return {company_name: [job dict, ...]}, richest source first.

    Tier 0: company_career_pages.json - a user-verified company->careers-URL
        map, scraped directly (plain HTTP first, Playwright fallback for
        JS-rendered listings). Real, current openings from the company's own
        site - the highest-confidence source available, so it wins outright
        whenever it has anything.
    Tier 1: linkedin-match's own scraped-jobs cache (real career-page data).
    Tier 2: our own techmap-URL ATS token resolution (Comeet/Lever/etc APIs).
    Tier 3: linkedin-match's live scraper, for companies neither above covers
        (cached to disk; pass scrape_missing=True to run it for new gaps).
    Tier 4: the raw techmap CSV row (title/location/level only).
    """
    result: dict[str, list[dict]] = {}

    career_pages = _load_company_career_pages()
    lm_index = lm_bridge.index_by_normalized_name(lm_bridge.load_lm_cache())
    lm_live = _load_lm_live_scraped()
    still_missing = []
    for company in groups:
        if career_pages.get(company):
            result[company] = career_pages[company]
            continue
        key = connections.normalize_company(company)
        entry = lm_index.get(key)
        if entry and lm_bridge.entry_has_real_jobs(entry):
            result[company] = lm_bridge.entry_jobs(entry)
        elif lm_live.get(company) and _lm_live_worth_taking(lm_live[company]):
            # Take lm_live when it already has a real description, OR when its
            # job URLs point at the company's own site rather than LinkedIn -
            # even without a description yet, that URL is what lets the
            # generic-description enrichment step (below) actually reach real
            # text later. techmap's own fallback URLs are LinkedIn far more
            # often, which that step always skips - so preferring lm_live's
            # URLs here measurably raises how many jobs end up with a
            # description, even when lm_live itself found none.
            result[company] = lm_live[company]
        else:
            still_missing.append(company)
    logger.info("tier1 linkedin-match cache: %d/%d companies covered", len(result), len(groups))

    cache = _load_company_cache()
    session = ats_fetchers.make_session()
    to_fetch: list[tuple[str, str, str, str]] = []  # company, ats, token, known_url
    tier4_fallback: list[str] = []

    for company in still_missing:
        rows = groups[company]
        resolved = _resolve_ats_for_company(rows)
        if not resolved:
            tier4_fallback.append(company)
            continue
        ats, token, url = resolved
        cache_key = f"{ats}:{token}"
        entry = cache.get(cache_key)
        if entry and not force and _cache_fresh(entry):
            result[company] = entry["jobs"] or _fallback_jobs_from_rows(rows)
            continue
        to_fetch.append((company, ats, token, url))

    logger.info(
        "tier2 own ATS resolution: %d cached/pending, %d to fetch live, %d have no resolvable ATS",
        len(still_missing) - len(to_fetch) - len(tier4_fallback), len(to_fetch), len(tier4_fallback),
    )

    def _do_fetch(item):
        company, ats, token, url = item
        try:
            jobs = ats_fetchers.fetch_company_board(session, ats, token, url)
        except Exception as error:  # noqa: BLE001 - one bad company must not abort the run
            logger.warning("fetch failed for %s (%s:%s): %s", company, ats, token, error)
            jobs = None
        return company, ats, token, jobs

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_do_fetch, item) for item in to_fetch]
        done = 0
        for future in as_completed(futures):
            company, ats, token, jobs = future.result()
            cache_key = f"{ats}:{token}"
            cache[cache_key] = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "company": company,
                "jobs": jobs or [],
            }
            result[company] = jobs if jobs else _fallback_jobs_from_rows(groups[company])
            done += 1
            if done % 50 == 0:
                logger.info("fetched %d/%d companies", done, len(to_fetch))

    _save_company_cache(cache)

    for company in tier4_fallback:
        result.setdefault(company, _fallback_jobs_from_rows(groups[company]))

    if scrape_missing:
        # Any company whose current best data has zero real descriptions is worth a
        # fresh company-name-based discovery attempt (find_careers_url + detect_ats
        # on ITS OWN domain), not just companies we have nothing at all for - a
        # techmap-provided Comeet/LinkedIn URL can leave every job title-only even
        # though the company has real jobs data (title, location) already.
        no_desc_companies = [c for c in groups if not any(j.get("description") for j in result.get(c, []))]
        newly_missing = [c for c in no_desc_companies if c not in lm_live]
        logger.info(
            "tier3 discovery: %d/%d companies have zero description, %d not yet attempted",
            len(no_desc_companies), len(groups), len(newly_missing),
        )
        if newly_missing:
            scraped = lm_bridge.scrape_missing_via_linkedin_match(newly_missing)
            lm_live.update(scraped)
            config.LM_LIVE_SCRAPED_CACHE.parent.mkdir(parents=True, exist_ok=True)
            config.LM_LIVE_SCRAPED_CACHE.write_text(json.dumps(lm_live, ensure_ascii=False), encoding="utf-8")
            upgraded = 0
            for company, jobs in scraped.items():
                has_real_desc = any(j.get("description") for j in jobs)
                if has_real_desc or not result.get(company):
                    result[company] = jobs
                    if has_real_desc:
                        upgraded += 1
            logger.info("tier3 discovery: %d companies upgraded with real descriptions", upgraded)

    return result


def _load_generic_desc_cache() -> dict:
    if config.GENERIC_DESC_CACHE.exists():
        return json.loads(config.GENERIC_DESC_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_generic_desc_cache(cache: dict) -> None:
    config.GENERIC_DESC_CACHE.parent.mkdir(parents=True, exist_ok=True)
    config.GENERIC_DESC_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _enrich_generic_descriptions(company_jobs: dict[str, list[dict]], session) -> None:
    """Best-effort plain-HTTP fetch of a job's own page for jobs still missing one.

    Bounded to Israel/remote + title-relevant jobs with a real (non-LinkedIn,
    non-Comeet - both confirmed dead ends) URL, so this stays a few hundred
    requests, not thousands. Cached by URL so reruns only chase new gaps.
    """
    cache = _load_generic_desc_cache()
    candidates: list[tuple[dict, str]] = []
    for jobs in company_jobs.values():
        for job in jobs:
            url = job.get("url")
            if job.get("description") or not url:
                continue
            if "linkedin.com" in url.lower() or "comeet.com" in url.lower():
                continue
            if not (scoring.is_relevant_location(job.get("location")) and scoring.title_is_relevant(job.get("title"))):
                continue
            candidates.append((job, url))

    if not candidates:
        return

    def _cache_fresh_desc(entry: dict) -> bool:
        try:
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
        except (KeyError, ValueError):
            return False
        age_hours = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 3600
        return age_hours < config.GENERIC_DESC_TTL_HOURS

    to_fetch = []
    for job, url in candidates:
        entry = cache.get(url)
        if entry and _cache_fresh_desc(entry):
            job["description"] = entry["description"]
        else:
            to_fetch.append((job, url))

    if not to_fetch:
        return
    logger.info("fetching generic descriptions for %d relevant jobs missing one", len(to_fetch))

    def _do_fetch(item):
        job, url = item
        try:
            return job, url, ats_fetchers.fetch_generic_description(session, url)
        except Exception as error:  # noqa: BLE001 - one bad page must not abort the run
            logger.debug("generic description fetch failed for %s: %s", url, error)
            return job, url, ""

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(_do_fetch, item) for item in to_fetch]
        done = 0
        for future in as_completed(futures):
            job, url, description = future.result()
            job["description"] = description
            cache[url] = {"fetched_at": datetime.now(timezone.utc).isoformat(), "description": description}
            done += 1
            if done % 50 == 0:
                logger.info("generic descriptions fetched %d/%d", done, len(to_fetch))

    _save_generic_desc_cache(cache)


def build_jobs_dataset(
    force_fetch: bool = False,
    israel_or_remote_only: bool = True,
    scrape_missing: bool = False,
) -> list[dict]:
    session = ats_fetchers.make_session()
    rows = techmap_source.load_all_rows(session)
    groups = _group_by_company(rows)
    logger.info("techmap: %d rows across %d unique companies", len(rows), len(groups))

    company_metadata = {}
    company_location_hint: dict[str, str] = {}
    for company, company_rows in groups.items():
        first = company_rows[0]
        company_metadata[company] = {"industry": first["industry"], "size": first["size"]}
        for row in company_rows:
            if row.get("location"):
                company_location_hint[company] = row["location"]
                break

    company_jobs = fetch_all_company_jobs(groups, force=force_fetch, scrape_missing=scrape_missing)
    _enrich_generic_descriptions(company_jobs, session)

    if config.REFERRAL_JOBS_PATH.exists():
        referral_stats = referral_source.merge_referral_jobs(company_jobs, config.REFERRAL_JOBS_PATH)
        logger.info(
            "referral jobs: %d matched to existing companies, %d new companies, "
            "%d merged into existing jobs (referral-tagged), %d added as new jobs",
            referral_stats["matched_existing_company"], referral_stats["new_company"],
            referral_stats["merged_into_existing_job"], referral_stats["added_new_job"],
        )

    profiles = cv.load_profiles()
    conn_index = connections.load_connections_index()

    dataset = []
    for company, jobs in company_jobs.items():
        meta = company_metadata.get(company, {})
        contacts = connections.contacts_for_company(conn_index, company)
        for job in jobs:
            title = job.get("title")
            if not title:
                continue
            if len(title) > MAX_TITLE_LENGTH:
                # Some generic career-page scrapes grab the whole posting (title +
                # boilerplate) as the "title" when a site's markup doesn't cleanly
                # separate them - a real title is never this long, so drop it
                # rather than pollute scoring/display with the bogus text.
                continue
            location, city, is_remote = _infer_location_fields(job, company_location_hint.get(company))
            # Referral jobs are already known Israel-relevant by provenance (a
            # referral-group export) - their cities (Karmiel, Bar-Lev, ...) aren't
            # all in ISRAEL_LOCATION_TERMS, so don't let that filter drop them.
            # Everything else: relevant if a city or remote signal was found
            # (title/description fallback included), or - matching the original
            # policy - if there's no location signal anywhere (unspecified = ok).
            is_location_relevant = bool(city) or is_remote or not job.get("location")
            if israel_or_remote_only and not job.get("is_referral") and not is_location_relevant:
                continue
            record = {
                "id": _job_id(company, job["title"], job.get("url")),
                "company": company,
                "industry": meta.get("industry"),
                "company_size": meta.get("size"),
                "title": job["title"],
                "location": location,
                "city": city,
                "is_remote": is_remote,
                "url": job.get("url"),
                "description": ats_fetchers.strip_html(job.get("description")),
                "department": job.get("department"),
                "employment_type": job.get("employment_type"),
                "posted_at": job.get("posted_at"),
                "connections": contacts,
                "has_connection": bool(contacts),
                "has_description": bool(job.get("description")),
                "years_required": scoring.required_years(f"{job['title']}\n{job.get('description') or ''}"),
                "is_referral": bool(job.get("is_referral")),
                "referral_contact": job.get("referral_contact"),
            }
            record.update(scoring.score_job_both(job, profiles))
            dataset.append(record)

    dataset.sort(key=lambda r: r["best_score"], reverse=True)
    logger.info("scored %d jobs", len(dataset))
    return dataset


def write_dataset(dataset: list[dict]) -> None:
    config.JOBS_OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    config.JOBS_OUTPUT_JSON.write_text(json.dumps(dataset, ensure_ascii=False, indent=0), encoding="utf-8")
    logger.info("wrote %s (%d jobs)", config.JOBS_OUTPUT_JSON, len(dataset))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-fetch", action="store_true", help="ignore the company-board cache TTL")
    parser.add_argument("--all-locations", action="store_true", help="keep non-Israel/non-remote jobs too")
    parser.add_argument("--scrape-missing", action="store_true", help="live-scrape companies still uncovered via linkedin-match's scraper")
    args = parser.parse_args()

    started = time.time()
    dataset = build_jobs_dataset(
        force_fetch=args.force_fetch,
        israel_or_remote_only=not args.all_locations,
        scrape_missing=args.scrape_missing,
    )
    write_dataset(dataset)
    logger.info("done in %.1fs", time.time() - started)


if __name__ == "__main__":
    main()
