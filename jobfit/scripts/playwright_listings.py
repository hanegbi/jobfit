"""For companies whose careers page needs JS to show any listings at all
(confirmed: find_careers_url resolves the URL fine, but plain-HTTP extraction
finds 0 jobs - e.g. Microsoft, Akamai-style portals), render the page with
Playwright and pull real job links out of the rendered DOM, then fetch each
job's own page the same way for a description.

Input: cache/needs_playwright.json ({company: careers_url}) - written by
company_career_scrape.py for the companies its plain-HTTP pass couldn't get
any job links from.

Output: cache/playwright_listings.json ({company: [{title, url, description}]}),
merged back into cache/company_career_pages.json by company_career_scrape.py
(not read directly by pipeline.py). No company known to have a WAF/bot block
(confirmed by a 403/Access Denied response) is retried needlessly next time
this runs; everything else is cached by company so a rerun only chases
companies not yet attempted.

This is inherently noisier than the single-job-page fetch: extracting
structured (title, url) pairs from an arbitrary rendered career portal has no
company-specific selector to rely on, only heuristics (a11y "link" role text
length + a denylist of nav/footer boilerplate words). The pipeline's own
title-length cap and title_is_relevant filter catch most of what slips
through; this script does not try to be perfect on its own.

Usage: uv run python -m jobfit.scripts.playwright_listings [--limit N] [--concurrency N]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import config  # noqa: E402
from jobfit.ats_fetchers import COOKIE_WIDGET_MARKERS, looks_like_boilerplate  # noqa: E402
from jobfit.listing_heuristics import (  # noqa: E402
    clean as _clean,
    drop_category_prefix_links,
    looks_like_job_title as _looks_like_job_title,
)

logger = logging.getLogger("jobfit.playwright_listings")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

NAV_TIMEOUT_MS = 15000
JOB_PAGE_NAV_TIMEOUT_MS = 8000
SETTLE_MS = 2000
JOB_SETTLE_MS = 800
MAX_JOB_LINKS_PER_COMPANY = 6
MAX_DESC_LEN = 6000
PER_COMPANY_BUDGET_S = 75

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

OUTPUT_PATH = config.ROOT / "cache" / "playwright_listings.json"
INPUT_PATH = config.ROOT / "cache" / "needs_playwright.json"


async def extract_job_links(page, base_url: str) -> list[tuple[str, str]]:
    """Return [(title, absolute_url), ...] from the rendered page's anchors."""
    anchors = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({text: e.innerText || e.textContent || '', href: e.getAttribute('href')}))",
    )
    seen_urls: set[str] = set()
    results: list[tuple[str, str]] = []
    candidate_cap = MAX_JOB_LINKS_PER_COMPANY * 4
    for a in anchors:
        text = _clean(a.get("text") or "")
        href = a.get("href") or ""
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if not _looks_like_job_title(text):
            continue
        url = urljoin(base_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        results.append((text, url))
        if len(results) >= candidate_cap:
            break
    return drop_category_prefix_links(results)[:MAX_JOB_LINKS_PER_COMPANY]


# Cookie-consent widgets (Complianz, Cookiebot, OneTrust, ...) render as plain
# <div>s outside any nav/header/footer, so the tag-based strip below never
# touches them - their "Manage Consent... Accept Deny" boilerplate would
# otherwise leak into the scraped description (see ats_fetchers.py for the
# matching plain-HTTP-path fix and looks_like_boilerplate below).
_COOKIE_WIDGET_SELECTOR = ", ".join(
    f'[id*="{marker}" i], [class*="{marker}" i]' for marker in COOKIE_WIDGET_MARKERS
)


async def extract_description(page) -> str:
    for sel in ("script", "style", "nav", "header", "footer", "svg", "form", "noscript", _COOKIE_WIDGET_SELECTOR):
        try:
            await page.eval_on_selector_all(sel, "els => els.forEach(e => e.remove())")
        except Exception:  # noqa: BLE001
            pass
    text = _clean(await page.inner_text("body"))
    if looks_like_boilerplate(text):
        return ""
    return text[:MAX_DESC_LEN]


async def _scrape_company_inner(context, name: str, careers_url: str) -> list[dict]:
    page = await context.new_page()
    try:
        await page.goto(careers_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(SETTLE_MS)
        body_preview = (await page.inner_text("body"))[:200].lower()
        if "access denied" in body_preview or "captcha" in body_preview:
            logger.info("%s: blocked (WAF/captcha)", name)
            return []
        links = await extract_job_links(page, careers_url)
    except Exception as error:  # noqa: BLE001
        logger.debug("%s listing failed: %s", name, error)
        return []
    finally:
        await page.close()

    if not links:
        return []

    jobs: list[dict] = []
    for title, url in links:
        jpage = await context.new_page()
        try:
            await jpage.goto(url, timeout=JOB_PAGE_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            await jpage.wait_for_timeout(JOB_SETTLE_MS)
            desc = await extract_description(jpage)
        except Exception:  # noqa: BLE001
            desc = ""
        finally:
            await jpage.close()
        jobs.append({
            "title": title,
            "location": None,
            "url": url,
            "description": desc,
            "department": None,
            "employment_type": None,
            "posted_at": None,
        })
    return jobs


async def scrape_company(context, name: str, careers_url: str, sem: asyncio.Semaphore) -> tuple[str, list[dict]]:
    async with sem:
        try:
            jobs = await asyncio.wait_for(
                _scrape_company_inner(context, name, careers_url), timeout=PER_COMPANY_BUDGET_S
            )
        except asyncio.TimeoutError:
            logger.info("%s: exceeded %ds budget, skipping rest", name, PER_COMPANY_BUDGET_S)
            jobs = []
        return name, jobs


async def run(companies: dict[str, str], concurrency: int) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    sem = asyncio.Semaphore(concurrency)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        tasks = [asyncio.create_task(scrape_company(context, name, url, sem)) for name, url in companies.items()]
        done = 0
        found = 0
        for coro in asyncio.as_completed(tasks):
            name, jobs = await coro
            results[name] = jobs
            done += 1
            if jobs:
                found += 1
            if done % 5 == 0 or done == len(companies):
                logger.info("progress %d/%d companies (%d yielded jobs)", done, len(companies), found)
        await browser.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    if not INPUT_PATH.exists():
        logger.error("missing %s - run resolve_careers_urls.py first", INPUT_PATH)
        return
    companies = json.loads(INPUT_PATH.read_text(encoding="utf-8"))

    already = {}
    if OUTPUT_PATH.exists():
        already = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    companies = {name: url for name, url in companies.items() if name not in already}

    if args.limit:
        companies = dict(list(companies.items())[: args.limit])

    logger.info("scraping %d company career pages via Playwright (concurrency=%d)", len(companies), args.concurrency)
    if not companies:
        logger.info("nothing new to do")
        return

    results = asyncio.run(run(companies, args.concurrency))
    already.update(results)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(already, ensure_ascii=False), encoding="utf-8")

    found = sum(1 for jobs in results.values() if jobs)
    total_jobs = sum(len(jobs) for jobs in results.values())
    logger.info("done: %d/%d companies yielded jobs (%d jobs total)", found, len(companies), total_jobs)


if __name__ == "__main__":
    main()
