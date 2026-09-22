"""Scrape every company's real career page from a user-supplied company->URL
map. Plain HTTP first (fast, cheap, threaded); only companies that come back
with zero job links fall through to the Playwright fallback (JS-rendered
listing pages), run as a separate async batch.

Input: a JSON file {company: url_or_null} - e.g. exported by hand, like
C:\\Users\\user\\Downloads\\israel_career_pages.json.

Output: cache/company_career_pages.json, {company: [{title, url, description}]}
- pipeline.py's highest-priority tier (these are real, user-verified company
career pages, not a techmap fallback or a guessed domain).

Usage: uv run python -m jobfit2.scripts.company_career_scrape <input.json> [--limit N] [--workers N]
"""

import argparse
import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit2 import ats_fetchers, config  # noqa: E402

logger = logging.getLogger("jobfit2.company_career_scrape")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WORKERS = 10
MAX_LINKS_PER_COMPANY = 8

OUTPUT_PATH = config.ROOT / "cache" / "company_career_pages.json"
NEEDS_PLAYWRIGHT_PATH = config.ROOT / "cache" / "needs_playwright.json"
PLAYWRIGHT_OUTPUT_PATH = config.ROOT / "cache" / "playwright_listings.json"


def load_company_urls(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: url for name, url in raw.items() if url}


def scrape_company_plain(session, name: str, url: str) -> tuple[str, list[dict]]:
    links = ats_fetchers.fetch_listing_links(session, url, max_links=MAX_LINKS_PER_COMPANY)
    if not links:
        return name, []
    jobs = []
    for title, job_url in links:
        desc = ats_fetchers.fetch_generic_description(session, job_url)
        jobs.append({
            "title": title,
            "location": None,
            "url": job_url,
            "description": desc,
            "department": None,
            "employment_type": None,
            "posted_at": None,
        })
    return name, jobs


def run_phase1_plain_http(companies: dict[str, str], workers: int) -> dict[str, list[dict]]:
    session = ats_fetchers.make_session(pool_size=max(32, workers))
    results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(scrape_company_plain, session, name, url): name for name, url in companies.items()}
        done = 0
        for future in as_completed(futures):
            name, jobs = future.result()
            results[name] = jobs
            done += 1
            if done % 25 == 0 or done == len(companies):
                found = sum(1 for j in results.values() if j)
                logger.info("phase1 (plain HTTP): %d/%d companies (%d yielded jobs)", done, len(companies), found)
    return results


def run_phase2_playwright(companies: dict[str, str]) -> dict[str, list[dict]]:
    if not companies:
        return {}
    NEEDS_PLAYWRIGHT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEEDS_PLAYWRIGHT_PATH.write_text(json.dumps(companies, ensure_ascii=False), encoding="utf-8")
    logger.info("phase2 (Playwright): %d companies need JS rendering, launching...", len(companies))

    result = subprocess.run(
        [sys.executable, "-m", "jobfit2.scripts.playwright_listings", "--concurrency", "4"],
        cwd=str(config.ROOT.parent),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    for line in (result.stderr or "").splitlines()[-40:]:
        logger.info("[playwright] %s", line)
    if result.returncode != 0:
        logger.warning("playwright_listings subprocess failed (exit %d)", result.returncode)

    if PLAYWRIGHT_OUTPUT_PATH.exists():
        return json.loads(PLAYWRIGHT_OUTPUT_PATH.read_text(encoding="utf-8"))
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=WORKERS)
    parser.add_argument("--skip-playwright", action="store_true")
    args = parser.parse_args()

    companies = load_company_urls(args.input_path)
    logger.info("loaded %d companies with a real URL (of %d total in the file)", len(companies), len(json.loads(args.input_path.read_text(encoding='utf-8'))))

    already = {}
    if OUTPUT_PATH.exists():
        already = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    companies = {name: url for name, url in companies.items() if name not in already}
    logger.info("%d companies not yet attempted this run", len(companies))

    if args.limit:
        companies = dict(list(companies.items())[: args.limit])
    if not companies:
        logger.info("nothing to do")
        return

    phase1 = run_phase1_plain_http(companies, args.workers)
    already.update(phase1)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(already, ensure_ascii=False), encoding="utf-8")

    needs_pw = {name: companies[name] for name, jobs in phase1.items() if not jobs}
    if not args.skip_playwright:
        phase2 = run_phase2_playwright(needs_pw)
        for name, jobs in phase2.items():
            if jobs:
                already[name] = jobs
        OUTPUT_PATH.write_text(json.dumps(already, ensure_ascii=False), encoding="utf-8")

    total_found = sum(1 for jobs in already.values() if jobs)
    total_jobs = sum(len(jobs) for jobs in already.values())
    logger.info("done: %d companies with jobs (%d jobs total) across %d attempted", total_found, total_jobs, len(already))


if __name__ == "__main__":
    main()
