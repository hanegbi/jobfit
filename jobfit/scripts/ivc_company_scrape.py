"""Harvest Israeli tech companies from IVC-Online's Advanced Search
(https://www.ivc-online.com) and add the ones not already tracked to
companies_career_pages.json, so they flow into the existing Companies
review panel (approve techmap / set career URL / skip).

IVC's Advanced Search results (company name, homepage, sector, tech
verticals) are public - no login needed, confirmed by fetching them with a
plain headless browser and no session/cookies at all. Filtered to Israel
Registration Number = Registered, Status = Active (IVC's default), Sector =
Enterprise Software & Infrastructure - the closest single-sector match to
this project's target roles (config.TARGET_ROLES). That still runs into the
thousands of results; only the free-tier UI's own "Annual Membership"
paywall (which blocks large page jumps, not sequential single-page steps)
is a real limit, so this steps one page at a time.

Two phases:
  1. harvest  - page through the filtered search and collect
     {company: homepage_url} into cache/ivc_companies_raw.json. Resumable:
     already-known companies (in companies_career_pages.json or already in
     the raw file) are skipped, and progress is saved every 10 pages and on
     exit (including on a paywall block or Ctrl-C).
  2. discover - for each newly-harvested company not already in
     companies_career_pages.json, try to find a real careers page on its
     homepage: plain HTTP first (fast/cheap), Playwright fallback for
     JS-rendered nav. Found -> real URL; not found -> null (goes to the
     Companies panel for manual review, same as any other unresolved
     company). This does NOT scrape job listings - that's the existing
     `update_jobs.py` pipeline's job, once a company has a real URL or is
     approved for the techmap fallback.

Usage:
  uv run python -m jobfit.scripts.ivc_company_scrape              # harvest + discover
  uv run python -m jobfit.scripts.ivc_company_scrape --harvest-only
  uv run python -m jobfit.scripts.ivc_company_scrape --discover-only
  uv run python -m jobfit.scripts.ivc_company_scrape --max-pages 20   # testing
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import ats_fetchers, company_review, config  # noqa: E402
from jobfit.atomic_io import write_json_atomic  # noqa: E402

logger = logging.getLogger("jobfit.ivc_company_scrape")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_URL = "https://www.ivc-online.com"
ADVANCED_SEARCH_URL = f"{BASE_URL}/Home/Advanced-Search"
NAV_TIMEOUT_MS = 20000
PAGE_SETTLE_MS = 1200
PAGE_DELAY_S = 1.5  # politeness delay between result pages

RAW_OUTPUT_PATH = config.ROOT / "cache" / "ivc_companies_raw.json"

SECTOR_FILTER_TEXT = "Enterprise Software & Infrastructure"

DOMAIN_RE = re.compile(r"^(?:www\.)?[a-zA-Z0-9][\w-]*(?:\.[a-zA-Z0-9-]+)+/?$")
ENTITY_TYPES = {"High-Tech Company", "Multinational Corporation"}

CAREER_LINK_WORDS = (
    "career", "careers", "jobs", "job openings", "join us", "we're hiring",
    "we are hiring", "work with us", "open positions", "join our team",
)


# --- phase 1: harvest from IVC ---------------------------------------------

def parse_company_cards(text: str) -> list[tuple[str, str]]:
    """Company name + homepage domain pairs out of a results page's inner
    text. IVC's cards render as a fixed 4-line shape (name, blank, domain,
    entity-type) confirmed against a real sample - see module docstring."""
    lines = [ln.strip() for ln in text.splitlines()]
    results: list[tuple[str, str]] = []
    i = 0
    n = len(lines)
    while i < n - 3:
        name, blank, domain, entity = lines[i], lines[i + 1], lines[i + 2], lines[i + 3]
        if name and blank == "" and DOMAIN_RE.match(domain) and entity in ENTITY_TYPES:
            results.append((name, domain.rstrip("/")))
            i += 4
            continue
        i += 1
    return results


async def apply_filters(page) -> None:
    await page.goto(ADVANCED_SEARCH_URL, timeout=NAV_TIMEOUT_MS)
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    await page.get_by_text(SECTOR_FILTER_TEXT, exact=True).click()
    await page.wait_for_timeout(300)
    await page.get_by_text("Registered", exact=True).click()
    await page.wait_for_timeout(300)
    await page.get_by_text("SEARCH", exact=True).click()
    await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    await page.wait_for_timeout(PAGE_SETTLE_MS)


async def go_to_page(page, n: int) -> bool:
    """Jump to result page n via the "page __ Go" control
    (input.txtJumpToPage + a.lnkJumpToPage - confirmed against the live DOM,
    there's also a second unrelated "Go" button for the top search box, so
    text-based matching alone is ambiguous). Returns False if that control
    can't be found (page structure changed) - the "Annual Membership" promo
    card is present on every page regardless, so its text alone can't
    signal a real block; an empty page (0 cards) after this is what
    actually indicates a limit was hit, checked by the caller."""
    try:
        await page.locator(".txtJumpToPage").fill(str(n))
        await page.locator(".lnkJumpToPage").click()
    except Exception as error:  # noqa: BLE001
        logger.error("page-jump control not found on page %d: %s", n, error)
        return False
    await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
    await page.wait_for_timeout(PAGE_SETTLE_MS)
    return True


def _load_raw() -> dict[str, str]:
    if RAW_OUTPUT_PATH.exists():
        return json.loads(RAW_OUTPUT_PATH.read_text(encoding="utf-8"))
    return {}


async def harvest(max_pages: int | None) -> dict[str, str]:
    raw = _load_raw()
    known = set(company_review.load_career_pages())

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await apply_filters(page)

            match = re.search(r"([\d,]+)\s*\nMatching Records Found", await page.inner_text("body"))
            total = int(match.group(1).replace(",", "")) if match else None
            last_page_str = await page.locator(".txtLastPage").input_value()
            last_page = int(last_page_str) if last_page_str.isdigit() else None
            logger.info("filtered search: %s matching records across %s pages", total if total else "unknown", last_page if last_page else "unknown")

            page_num = 1
            new_this_run = 0
            while True:
                if max_pages and page_num > max_pages:
                    logger.info("hit --max-pages %d, stopping", max_pages)
                    break
                if last_page and page_num > last_page:
                    logger.info("reached the last page (%d)", last_page)
                    break
                body_text = await page.inner_text("body")
                cards = parse_company_cards(body_text)
                if not cards:
                    if page_num == 1:
                        logger.error("found 0 companies on page 1 - the page structure probably changed, check manually")
                    else:
                        logger.warning("found 0 companies on page %d - stopping (a real limit, or a transient hiccup - safe to rerun)", page_num)
                    break
                added_this_page = 0
                for name, domain in cards:
                    if name in known or name in raw:
                        continue
                    raw[name] = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
                    added_this_page += 1
                    new_this_run += 1
                if page_num % 10 == 0 or added_this_page == 0:
                    write_json_atomic(RAW_OUTPUT_PATH, raw)
                    logger.info("page %d: %d cards, %d new (total new this run: %d)", page_num, len(cards), added_this_page, new_this_run)

                page_num += 1
                if not await go_to_page(page, page_num):
                    break
                await asyncio.sleep(PAGE_DELAY_S)
        finally:
            write_json_atomic(RAW_OUTPUT_PATH, raw)
            await browser.close()

    logger.info("harvest done: %d companies total in %s", len(raw), RAW_OUTPUT_PATH)
    return raw


# --- phase 2: career-page discovery ----------------------------------------

def _find_career_link(html: str, base_url: str) -> str | None:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = (a.get_text(" ") or "").strip().lower()
        if not text:
            continue
        if any(word in text for word in CAREER_LINK_WORDS):
            href = a["href"]
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            return urljoin(base_url, href)
    return None


def discover_career_url_plain(session, homepage_url: str) -> str | None:
    try:
        response = session.get(homepage_url, timeout=10)
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    return _find_career_link(response.text, homepage_url)


PLAIN_HTTP_WORKERS = 16
PLAYWRIGHT_CONCURRENCY = 6
DISCOVER_CHECKPOINT_EVERY = 25


def run_phase1_plain_http(targets: dict[str, str], workers: int = PLAIN_HTTP_WORKERS) -> dict[str, str | None]:
    """{company: found_url | None} for every target, run concurrently -
    same threaded-fast-path pattern as company_career_scrape.py, since a
    few thousand companies at ~2s/request sequentially would take hours."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = ats_fetchers.make_session(pool_size=max(32, workers))
    results: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(discover_career_url_plain, session, url): name for name, url in targets.items()}
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:  # noqa: BLE001
                results[name] = None
            done += 1
            if done % 100 == 0 or done == len(targets):
                found = sum(1 for v in results.values() if v)
                logger.info("plain HTTP: %d/%d done (%d found a careers link)", done, len(targets), found)
    return results


async def discover_career_url_playwright(browser, homepage_url: str) -> str | None:
    page = await browser.new_page()
    try:
        await page.goto(homepage_url, timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(1000)
        html = await page.content()
        return _find_career_link(html, homepage_url)
    except Exception:  # noqa: BLE001
        return None
    finally:
        await page.close()


async def run_phase2_playwright(
    targets: dict[str, str], career_pages: dict[str, str | None], concurrency: int = PLAYWRIGHT_CONCURRENCY,
) -> int:
    """Runs the JS-fallback pass with bounded concurrency, checkpointing
    career_pages to disk periodically so a crash partway through doesn't
    lose already-resolved companies."""
    if not targets:
        return 0
    found_count = 0
    done = 0
    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            async def _one(name: str, homepage_url: str) -> None:
                nonlocal found_count, done
                async with sem:
                    found = await discover_career_url_playwright(browser, homepage_url)
                career_pages[name] = found  # None is a valid, meaningful result - pending review
                if found:
                    found_count += 1
                done += 1
                if done % DISCOVER_CHECKPOINT_EVERY == 0 or done == len(targets):
                    company_review.save_career_pages(career_pages)
                    logger.info("playwright fallback: %d/%d done (%d found)", done, len(targets), found_count)

            await asyncio.gather(*(_one(name, url) for name, url in targets.items()))
        finally:
            await browser.close()
    return found_count


ATS_GUESS_WORKERS = 20

ATS_BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}",
    "comeet": "https://www.comeet.com/jobs/{slug}",
}


def _slug_candidates(company_name: str) -> list[str]:
    """A homepage often has no obvious "careers" link at all when the real
    board is hosted on Greenhouse/Lever/Comeet/etc - this guesses the
    company's slug on each platform and verifies it against that platform's
    own public API (not just an HTTP 200, which a generic "not found" page
    can also return), so a wrong guess can't silently pass as a match."""
    base = re.sub(r"\s*\([^)]*\)", "", company_name)  # drop "(Formerly X)" / "(Vizit.co)" suffixes
    base = re.sub(r"\b(ltd\.?|inc\.?|llc|corp\.?|co\.?)\b", "", base, flags=re.I)
    base = base.strip()
    nospace = re.sub(r"[^a-z0-9]", "", base.lower())
    hyphen = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    candidates = []
    for c in (nospace, hyphen):
        if c and c not in candidates:
            candidates.append(c)
    return candidates


def guess_ats_url(session, company_name: str) -> str | None:
    for slug in _slug_candidates(company_name):
        for ats, fetcher in ats_fetchers.ATS_FETCHERS.items():
            try:
                jobs = fetcher(session, slug)
            except Exception:  # noqa: BLE001
                jobs = None
            if jobs is not None:  # a real board resolved, even with 0 current openings
                return ATS_BOARD_URL[ats].format(slug=slug)
    return None


def run_phase3_ats_guess(names: list[str], workers: int = ATS_GUESS_WORKERS) -> dict[str, str | None]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = ats_fetchers.make_session(pool_size=max(32, workers))
    results: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(guess_ats_url, session, name): name for name in names}
        done = 0
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:  # noqa: BLE001
                results[name] = None
            done += 1
            if done % 50 == 0 or done == len(names):
                found = sum(1 for v in results.values() if v)
                logger.info("ATS guess: %d/%d done (%d found)", done, len(names), found)
    return results


async def discover(raw: dict[str, str]) -> None:
    career_pages = company_review.load_career_pages()
    targets = {name: url for name, url in raw.items() if name not in career_pages}
    if not targets:
        logger.info("nothing new to discover career pages for")
        return
    logger.info("discovering career pages for %d companies", len(targets))

    plain_results = run_phase1_plain_http(targets)
    plain_found = 0
    needs_playwright: dict[str, str] = {}
    for name, found in plain_results.items():
        if found:
            career_pages[name] = found
            plain_found += 1
        else:
            needs_playwright[name] = targets[name]
    company_review.save_career_pages(career_pages)
    logger.info("plain HTTP: %d/%d found a careers link", plain_found, len(targets))

    pw_found = await run_phase2_playwright(needs_playwright, career_pages)
    if needs_playwright:
        logger.info("playwright fallback: %d/%d found a careers link", pw_found, len(needs_playwright))
    company_review.save_career_pages(career_pages)

    still_missing = [name for name in targets if not career_pages.get(name)]
    ats_results = run_phase3_ats_guess(still_missing) if still_missing else {}
    ats_found = 0
    for name, found in ats_results.items():
        if found:
            career_pages[name] = found
            ats_found += 1
    company_review.save_career_pages(career_pages)
    if still_missing:
        logger.info("ATS guess: %d/%d found a real board", ats_found, len(still_missing))

    total_found = plain_found + pw_found + ats_found
    logger.info(
        "discover done: %d companies added to companies_career_pages.json (%d with a real URL, %d pending review)",
        len(targets), total_found, len(targets) - total_found,
    )


def backfill_ats_guess_for_all_pending() -> None:
    """Standalone pass: try the ATS-guess tier against every company
    currently null in companies_career_pages.json, not just ones from the
    most recent harvest - useful to retroactively improve companies added
    earlier (via referrals, manual review, or prior sessions)."""
    career_pages = company_review.load_career_pages()
    pending = [name for name, url in career_pages.items() if not url]
    if not pending:
        logger.info("no pending companies to backfill")
        return
    logger.info("backfilling ATS guesses for %d pending companies", len(pending))
    results = run_phase3_ats_guess(pending)
    found = 0
    for name, url in results.items():
        if url:
            career_pages[name] = url
            found += 1
    company_review.save_career_pages(career_pages)
    logger.info("backfill done: %d/%d found a real board", found, len(pending))


# --- CLI ---------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest-only", action="store_true")
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument(
        "--ats-guess-backfill", action="store_true",
        help="retry the ATS-guess tier against every company currently pending review, not just this harvest's",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="stop harvesting after N result pages (testing)")
    args = parser.parse_args()

    if args.ats_guess_backfill:
        backfill_ats_guess_for_all_pending()
        return

    if args.discover_only:
        asyncio.run(discover(_load_raw()))
        return

    raw = asyncio.run(harvest(args.max_pages))
    if not args.harvest_only:
        asyncio.run(discover(raw))


if __name__ == "__main__":
    main()
