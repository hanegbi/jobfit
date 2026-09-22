"""Resolve a company's ATS + token from a known job URL and fetch its full board.

Ported from linkedin-match's backend/core/fetchers.py + the token regexes in
backend/cli/scraper.py, trimmed to plain dicts (no pydantic) and to "I already
know one job URL for this company" (techmap gives us that), so no blind
homepage/careers-page discovery is needed — just token extraction + one API
call per company.
"""

import html as html_module
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("jobfit2.ats")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 15

TOKEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9_-]+)", re.I)),
    ("comeet", re.compile(r"comeet\.com/jobs(?:-api/[0-9.]+/company)?/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"https?://([A-Za-z0-9_-]+)\.workable\.com", re.I)),
]


def make_session(pool_size: int = 32) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def resolve_ats(url: Optional[str]) -> Optional[tuple[str, str]]:
    """Return (ats, token) for a known job URL, or None if unrecognized."""
    if not url:
        return None
    for ats, pattern in TOKEN_PATTERNS:
        match = pattern.search(url)
        if match:
            return ats, match.group(1)
    return None


def _request(session: requests.Session, method: str, url: str, **kwargs) -> Optional[requests.Response]:
    try:
        response = session.request(method, url, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as error:
        logger.debug("%s %s failed: %s", method, url, error)
        return None
    if response.status_code == 404:
        return None
    return response if response.ok else None


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return " ".join(text.split())


def strip_html(text: Optional[str]) -> str:
    """Reduce an ATS description field to plain text.

    Some providers (Greenhouse's `content`, occasionally Comeet's `details`)
    return HTML markup - sometimes already entity-escaped (literal `&lt;div&gt;`
    text rather than a real `<div>` tag) - rather than plain text. Unescape
    first so real tags surface, then strip them; otherwise escaping this for
    display double-encodes it into visible `&lt;div&gt;` noise.
    """
    if not text:
        return ""
    unescaped = html_module.unescape(text)
    if "<" not in unescaped:
        return _clean(unescaped)
    try:
        return _clean(BeautifulSoup(unescaped, "html.parser").get_text(" "))
    except Exception:  # noqa: BLE001 - malformed markup must not break the pipeline
        return _clean(unescaped)


def _posted_date(value) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return str(value)[:10]


def fetch_greenhouse(session: requests.Session, token: str) -> Optional[list[dict]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    response = _request(session, "GET", url)
    if response is None:
        return None
    jobs = []
    for item in response.json().get("jobs", []):
        offices = item.get("offices") or []
        location = (item.get("location") or {}).get("name") or _clean(
            ", ".join(o.get("name", "") for o in offices)
        )
        departments = item.get("departments") or []
        jobs.append({
            "title": _clean(item.get("title")),
            "location": location or None,
            "url": item.get("absolute_url"),
            "description": _clean(item.get("content"))[:6000],
            "department": departments[0].get("name") if departments else None,
            "employment_type": None,
            "posted_at": _posted_date(item.get("updated_at") or item.get("first_published")),
        })
    return jobs


def fetch_lever(session: requests.Session, token: str) -> Optional[list[dict]]:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    response = _request(session, "GET", url)
    if response is None:
        return None
    jobs = []
    for item in response.json():
        categories = item.get("categories") or {}
        jobs.append({
            "title": _clean(item.get("text")),
            "location": categories.get("location"),
            "url": item.get("hostedUrl"),
            "description": _clean(item.get("descriptionPlain"))[:6000],
            "department": categories.get("team") or categories.get("department"),
            "employment_type": categories.get("commitment"),
            "posted_at": _posted_date(item.get("createdAt")),
        })
    return jobs


def fetch_ashby(session: requests.Session, token: str) -> Optional[list[dict]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=false"
    response = _request(session, "GET", url)
    if response is None:
        return None
    payload = response.json()
    if not isinstance(payload, dict) or "jobs" not in payload:
        return None
    jobs = []
    for item in payload.get("jobs", []):
        jobs.append({
            "title": _clean(item.get("title")),
            "location": item.get("location"),
            "url": item.get("jobUrl") or item.get("applyUrl"),
            "description": _clean(item.get("descriptionPlain"))[:6000],
            "department": item.get("department") or item.get("team"),
            "employment_type": item.get("employmentType"),
            "posted_at": _posted_date(item.get("publishedAt") or item.get("updatedAt")),
        })
    return jobs


def fetch_workable(session: requests.Session, token: str) -> Optional[list[dict]]:
    url = f"https://apply.workable.com/api/v3/accounts/{token}/jobs"
    response = _request(session, "POST", url, json={"query": "", "location": [], "department": []})
    if response is None:
        return None
    payload = response.json()
    if not isinstance(payload, dict) or "results" not in payload:
        return None
    jobs = []
    for item in payload.get("results", []):
        location = item.get("location") or {}
        where = ", ".join(part for part in (location.get("city"), location.get("country")) if part)
        jobs.append({
            "title": _clean(item.get("title")),
            "location": where or None,
            "url": f"https://apply.workable.com/{token}/j/{item.get('shortcode')}/" if item.get("shortcode") else None,
            "description": "",
            "department": item.get("department"),
            "employment_type": item.get("type"),
            "posted_at": _posted_date(item.get("published_on") or item.get("created_at")),
        })
    return jobs


def fetch_comeet(session: requests.Session, token: str) -> Optional[list[dict]]:
    url = f"https://www.comeet.com/jobs-api/2.1/company/{token}/positions"
    response = _request(session, "GET", url)
    if response is None:
        return None
    payload = response.json()
    if not isinstance(payload, list):
        return None
    return _comeet_items_to_jobs(payload)


def _comeet_items_to_jobs(payload: list[dict]) -> list[dict]:
    jobs = []
    for item in payload:
        location = item.get("location") or {}
        if isinstance(location, dict):
            where = location.get("name") or ", ".join(
                part for part in (location.get("city"), location.get("country")) if part
            )
        else:
            where = location
        details = item.get("details") or []
        desc = _clean(details[0].get("value")) if details and isinstance(details[0], dict) else ""
        jobs.append({
            "title": _clean(item.get("name")),
            "location": where or None,
            "url": item.get("url_comeet_hosted_page") or item.get("url_active_page"),
            "description": desc[:6000],
            "department": item.get("department"),
            "employment_type": item.get("employment_type"),
            "posted_at": _posted_date(item.get("time_updated") or item.get("updated_at")),
        })
    return jobs


def _balanced_arrays(text: str):
    """Yield top-level array-of-objects JSON substrings via bracket matching."""
    i, n = 0, len(text)
    while i < n:
        start = text.find("[", i)
        if start == -1:
            return
        k = start + 1
        while k < n and text[k] in " \t\r\n":
            k += 1
        if k >= n or text[k] != "{":
            i = start + 1
            continue
        depth, in_str, esc, end = 0, False, False, -1
        for j in range(start, n):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end == -1:
            return
        yield text[start:end + 1]
        i = end + 1


def parse_comeet_hosted(html: str) -> Optional[list[dict]]:
    """Extract positions embedded as JSON in a Comeet hosted careers page.

    Comeet's hosted board renders via JS but ships positions as a JSON array
    in the page; the public API is token-gated for some companies, so this is
    the fallback when fetch_comeet() returns nothing.
    """
    best: list[dict] = []
    for blob in _balanced_arrays(html):
        if not any(m in blob for m in ("comeetapply.com", "url_comeet_hosted_page", "position_name")):
            continue
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict) and "name" in data[0]:
            if len(data) > len(best):
                best = data
    if not best:
        return None
    return _comeet_items_to_jobs(best)


_COMEET_BOARD = re.compile(r"comeet\.com/jobs/([A-Za-z0-9_-]+)/([A-Za-z0-9.]+)", re.I)


def comeet_board_url(url: str) -> Optional[str]:
    """Truncate a single-job Comeet URL down to its company+board listing page.

    The board page (.../<token>/<board_id>, no job slug/id suffix) is what embeds
    the full COMPANY_POSITIONS_DATA array; the individual job page only embeds
    that one job's POSITION_DATA.
    """
    match = _COMEET_BOARD.search(url or "")
    if not match:
        return None
    return f"https://www.comeet.com/jobs/{match.group(1)}/{match.group(2)}"


def fetch_comeet_hosted_page(session: requests.Session, hosted_url: str) -> Optional[list[dict]]:
    response = _request(session, "GET", hosted_url)
    if response is None:
        return None
    return parse_comeet_hosted(response.text)



# Note: an individual Comeet job page's description is loaded client-side via a
# token-gated call this app never makes (no browser automation, ever — see
# linkedin-match/CLAUDE.md). The board listing (COMPANY_POSITIONS_DATA) is the
# richest data reachable by plain HTTP for Comeet-fallback companies: title,
# location, department, employment_type, posted_at, but no description.


_SKIP_GENERIC_FETCH_HOSTS = ("linkedin.com", "comeet.com")

# Class/id prefixes used by common cookie-consent widgets (Complianz, Cookiebot,
# OneTrust, CookieYes, Osano, Borlabs, ...). These render as plain <div>s outside
# any <nav>/<footer>/<header> tag, so the generic tag-based strip below never
# touches them and their full "Manage Consent... Accept Deny" boilerplate leaks
# into the scraped description otherwise.
COOKIE_WIDGET_MARKERS = (
    "cmplz", "cookiebot", "onetrust", "cookieyes", "cky-consent", "osano",
    "borlabs-cookie", "cc-window", "cookie-notice", "cookie-consent", "gdpr-cookie",
    "usercentrics", "trustarc", "termly",
)

# Phrases that are essentially never part of a real job description but are
# near-universal in cookie-consent/newsletter/site-chrome boilerplate. Some
# company career pages (e.g. fully client-rendered "careers" SPAs) expose none
# of the actual job content over plain HTTP at all - the fetch above then
# scrapes pure site chrome as if it were the description. Rather than trying to
# detect "is this real prose" in general, flag the small set of tells that
# reliably mean "this is chrome, not a job posting" and drop it - the job still
# shows up (title/location/score), just without a bogus description.
BOILERPLATE_MARKERS = (
    "manage consent", "store and/or access device information", "subscribe to our newsletter",
    "view preferences", "manage services", "manage {vendor_count} vendors", "all rights reserved",
)
# A real job description is prose and virtually always longer than this. A
# short extraction that also hits even one boilerplate marker (a footer line,
# a leftover newsletter CTA) is overwhelmingly more likely to be pure site
# chrome than a real posting - this catches career pages whose actual job
# content is client-rendered and never present in the static HTML at all
# (nothing to strip; there's simply no real description to find).
_SHORT_BOILERPLATE_LEN = 1500


def _is_cookie_widget(tag) -> bool:
    haystack = (tag.get("id") or "") + " " + " ".join(tag.get("class") or [])
    haystack = haystack.lower()
    return any(marker in haystack for marker in COOKIE_WIDGET_MARKERS)


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "nav", "header", "footer", "svg", "form", "noscript"]):
        tag.decompose()
    # Computed as a static list first: decomposing a matched ancestor detaches
    # its descendants too, and re-decomposing an already-detached tag raises.
    for el in list(soup.find_all(_is_cookie_widget)):
        if el.parent is not None:
            el.decompose()


def looks_like_boilerplate(text: str) -> bool:
    if len(text) >= _SHORT_BOILERPLATE_LEN:
        # Long extractions routinely have a stray footer/newsletter line mixed
        # into otherwise-real content (the generic fetch grabs the whole page
        # body) - only a short extraction dominated by boilerplate markers is
        # a reliable "there's no real description here" signal.
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in BOILERPLATE_MARKERS)


def fetch_generic_description(session: requests.Session, url: str) -> str:
    """Fetch a job's own page and extract its visible text as a description.

    Used for jobs whose ATS wasn't one of the 5 we have an API fetcher for
    (a company-hosted careers page, a lesser ATS, etc.) - best-effort, plain
    HTTP only. Skips known dead ends: linkedin.com (never scraped) and
    comeet.com individual job pages (confirmed JS-gated, no description in
    the static HTML - see comeet_board_url/parse_comeet_hosted above).
    """
    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return ""
    response = _request(session, "GET", url)
    if response is None:
        return ""
    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception:  # noqa: BLE001 - malformed HTML must not break the pipeline
        return ""
    _strip_boilerplate(soup)
    text = _clean(soup.get_text(" "))
    if looks_like_boilerplate(text):
        return ""
    return text[:6000]


def fetch_listing_links(session: requests.Session, url: str, max_links: int = 8) -> list[tuple[str, str]]:
    """Pull candidate (title, absolute_url) job links off a career listing page.

    Plain-HTTP first pass for a user-supplied company->careers-URL map: many
    listing pages are static enough that BeautifulSoup already sees the real
    links; only the JS-rendered ones need the Playwright fallback
    (scripts/playwright_listings.py), so this keeps the fast/cheap path doing
    as much of the work as it can.
    """
    from urllib.parse import urljoin

    from jobfit2.listing_heuristics import drop_category_prefix_links, looks_like_job_title

    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return []
    response = _request(session, "GET", url)
    if response is None:
        return []
    try:
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception:  # noqa: BLE001
        return []
    _strip_boilerplate(soup)

    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    # Collect a wider pool than max_links before filtering: a career page that
    # links both department overviews and individual postings (see
    # drop_category_prefix_links) would otherwise have its cap eaten by the
    # overview links before the filter ever runs.
    candidate_cap = max_links * 4
    for a in soup.find_all("a", href=True):
        text = _clean(a.get_text(" "))
        href = a["href"]
        if not text or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if not looks_like_job_title(text):
            continue
        absolute = urljoin(url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        results.append((text, absolute))
        if len(results) >= candidate_cap:
            break
    return drop_category_prefix_links(results)[:max_links]


ATS_FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "comeet": fetch_comeet,
}


def fetch_company_board(session: requests.Session, ats: str, token: str, known_url: str) -> Optional[list[dict]]:
    """Fetch a company's full current job board given its resolved (ats, token)."""
    fetcher = ATS_FETCHERS.get(ats)
    jobs = fetcher(session, token) if fetcher else None
    if not jobs and ats == "comeet" and known_url:
        board_url = comeet_board_url(known_url) or known_url
        jobs = fetch_comeet_hosted_page(session, board_url)
    if jobs:
        for job in jobs:
            job["_ats"] = ats
    return jobs
