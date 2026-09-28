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

logger = logging.getLogger("jobfit.ats")

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
    response = _request(session, "GET", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if response is None:  # EU-hosted boards (boards.eu.greenhouse.io/<slug>, e.g. NICE) live on the EU API
        response = _request(session, "GET", f"https://boards-api.eu.greenhouse.io/v1/boards/{token}/jobs?content=true")
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


def fetch_comeet_widget(session: requests.Session, uid: str, token: str) -> Optional[list[dict]]:
    """Comeet's *embedded widget* API - what a company's own careers page
    calls when it hosts the Comeet JS widget (`COMEET.init({token,
    "company-uid"})`) instead of linking to comeet.com. Distinct from the
    slug-based jobs-api used by fetch_comeet: keyed by the account uid
    (e.g. "B3.006") plus the page's public token, and it returns full
    descriptions with details=true. 104 of the tracked companies' career
    pages embed this widget, which is why plain link-scraping saw 0 jobs
    on all of them (the widget renders client-side, no <a href>).
    """
    url = f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=true"
    response = _request(session, "GET", url)
    if response is None:
        return None
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(payload, list):
        return None
    jobs = []
    for item in payload:
        if not isinstance(item, dict) or item.get("is_internal") is True:
            continue
        title = _clean(item.get("name"))
        if not title:
            continue
        location = item.get("location") or {}
        if isinstance(location, dict):
            where = location.get("name") or ", ".join(p for p in (location.get("city"), location.get("country")) if p)
            country = (location.get("country") or "").upper()
            if country == "IL" and "israel" not in (where or "").lower():
                where = f"{where}, Israel" if where else "Israel"
        else:
            where = location
        details = item.get("details") or []
        description = " ".join(
            strip_html(d.get("value") or "") for d in details if isinstance(d, dict) and d.get("value")
        ).strip()
        jobs.append({
            "title": title,
            "location": where or None,
            "url": item.get("url_active_page") or item.get("url_comeet_hosted_page") or item.get("url_recruit_hosted_page"),
            "description": description,
            "department": _clean(item.get("department")) or None,
            "employment_type": item.get("employment_type"),
            "posted_at": _posted_date(item.get("time_updated")),
        })
    return jobs


COMEET_WIDGET_TOKEN_RE = re.compile(r"""["']?token["']?\s*[:=]\s*["']([0-9A-Fa-f]{20,64})["']""")
# Seen in the wild as "company-uid": "98.004" (Comeet's own snippet) and as
# uid: 'B6.00F' inside a site's hand-rolled widget config (4M Analytics).
COMEET_WIDGET_UID_RE = re.compile(r"""["']?(?:company[-_]?uid|uid)["']?\s*[:=]\s*["']([A-Z0-9]{2}\.[0-9A-F]{3})["']""", re.I)
COMEET_WIDGET_API_RE = re.compile(r"comeet\.(?:co|com)/careers-api/2\.0/company/([A-Z0-9]{2}\.[0-9A-F]{3})/positions/?\?token=([0-9A-Fa-f]{20,64})", re.I)


def find_comeet_widget(html: str) -> Optional[tuple[str, str]]:
    """(uid, token) of an embedded Comeet widget in a page's raw HTML, or None.
    Looks for the widget's own API URL first, then the COMEET.init config."""
    if not html or "comeet" not in html.lower():
        return None
    m = COMEET_WIDGET_API_RE.search(html)
    if m:
        return m.group(1).upper(), m.group(2)
    uid, token = COMEET_WIDGET_UID_RE.search(html), COMEET_WIDGET_TOKEN_RE.search(html)
    if uid and token:
        return uid.group(1).upper(), token.group(1)
    return None


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


def _contains_a_job_link(tag) -> bool:
    from jobfit.scrape.candidates import link_title_text
    from jobfit.scrape.filters import DenylistFilter
    return any(DenylistFilter.text_ok(link_title_text(a)) for a in tag.find_all("a", href=True))


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "svg", "noscript"]):
        tag.decompose()
    # nav/header/footer/form are usually genuine site chrome worth
    # discarding, but not always: real example caught live on Adaptive6's
    # Webflow-built careers page - the *entire* job-cards section is wrapped
    # in <header class="section_careers">, a loose (if non-standard) use of
    # the tag as "this content block's heading area", not "site navigation
    # header". A blanket decompose() silently wiped every job listing before
    # any of the title-cleaning logic even ran. A second real example: Check
    # Point's career search-results page (careers.checkpoint.com) wraps its
    # entire results list - real job links included - in a <form> (the
    # search-filter widget's own form), so blanket-stripping every <form>
    # wiped that too. Only strip one of these four tags when it holds no
    # job-looking link itself - real site chrome/widget forms never do, so
    # this doesn't let genuine boilerplate back in.
    for tag in soup(["nav", "header", "footer", "form"]):
        if not _contains_a_job_link(tag):
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


def _jsonld_job_postings(soup: BeautifulSoup) -> list[dict]:
    """Every schema.org JobPosting object embedded as JSON-LD on the page -
    many career-page builders (and any site doing Google for Jobs SEO) embed
    this even when the visible page itself is a JS app plain HTTP can't
    render, so it's worth checking unconditionally rather than only as a
    fallback. A page can embed one object, a list of objects, or a
    "@graph" wrapper; this normalizes all three shapes."""
    postings = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            payload = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        expanded = []
        for item in candidates:
            if isinstance(item, dict) and "@graph" in item:
                expanded.extend(item["@graph"])
            else:
                expanded.append(item)
        for item in expanded:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                postings.append(item)
    return postings


def _address_part_str(value) -> Optional[str]:
    """A schema.org PostalAddress field is usually a plain string, but
    addressCountry in particular is often a nested Country object instead
    (e.g. {"@type": "Country", "name": "IL"} - confirmed live on A2Z
    Cust2Mate's job pages, which crashed the plain "," .join(...) this
    replaced since a dict isn't a str)."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("addressCountry")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _jsonld_location(posting: dict) -> Optional[str]:
    location = posting.get("jobLocation")
    if isinstance(location, list):
        location = location[0] if location else None
    if not isinstance(location, dict):
        return _clean(str(location)) if location else None
    address = location.get("address")
    if isinstance(address, str):
        return _clean(address) or None
    if isinstance(address, dict):
        parts = [
            _address_part_str(address.get("addressLocality")),
            _address_part_str(address.get("addressRegion")),
            _address_part_str(address.get("addressCountry")),
        ]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return None


_EMPTY_DETAILS = {"description": "", "location": None, "employment_type": None, "posted_at": None}


def parse_job_details_html(html: str) -> dict:
    """description/location/employment_type/posted_at from a job page's
    HTML: the page's own schema.org JobPosting JSON-LD when present (many
    career-page builders emit it for Google for Jobs), else the visible
    text with site chrome stripped. Pure - no network."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 - malformed HTML must not break the pipeline
        return dict(_EMPTY_DETAILS)

    postings = _jsonld_job_postings(soup)
    posting = postings[0] if postings else None

    description = ""
    if posting and posting.get("description"):
        try:
            description = _clean(BeautifulSoup(str(posting["description"]), "html.parser").get_text(" "))[:6000]
        except Exception:  # noqa: BLE001
            description = ""
    if not description:
        _strip_boilerplate(soup)
        text = _clean(soup.get_text(" "))
        description = "" if looks_like_boilerplate(text) else text[:6000]

    if not posting:
        return {**_EMPTY_DETAILS, "description": description}

    employment_type = posting.get("employmentType")
    if isinstance(employment_type, list):
        employment_type = employment_type[0] if employment_type else None

    return {
        "description": description,
        "location": _jsonld_location(posting),
        "employment_type": _clean(str(employment_type)) if employment_type else None,
        "posted_at": _posted_date(posting.get("datePosted")),
    }


def fetch_generic_job_details(session: requests.Session, url: str) -> dict:
    """Fetch a job's own page and parse it - see parse_job_details_html.
    Skips known dead ends (linkedin.com, comeet.com job pages)."""
    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return dict(_EMPTY_DETAILS)
    response = _request(session, "GET", url)
    if response is None:
        return dict(_EMPTY_DETAILS)
    return parse_job_details_html(response.text)


# --- More ATS providers with a public listing API -------------------------
# Each returns the same plain-dict job shape as the fetchers above (title,
# location, url, description, department, employment_type, posted_at) so the
# AtsClient subclasses in jobfit.scrape.ats.clients stay one-liners.


def _json_or_none(response):
    if response is None:
        return None
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


def fetch_recruitee(session: requests.Session, slug: str) -> Optional[list[dict]]:
    payload = _json_or_none(_request(session, "GET", f"https://{slug}.recruitee.com/api/offers/"))
    if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
        return None
    jobs = []
    for item in payload["offers"]:
        title = _clean(item.get("title"))
        if not title or item.get("status") not in (None, "published"):
            continue
        where = item.get("location") or ", ".join(p for p in (item.get("city"), item.get("country")) if p)
        jobs.append({
            "title": title, "location": where or None,
            "url": item.get("careers_url") or f"https://{slug}.recruitee.com/o/{item.get('slug')}",
            "description": strip_html((item.get("description") or "") + " " + (item.get("requirements") or "")),
            "department": _clean(item.get("department")) or None,
            "employment_type": item.get("employment_type_code"),
            "posted_at": _posted_date(item.get("published_at")),
        })
    return jobs


def fetch_bamboohr(session: requests.Session, slug: str) -> Optional[list[dict]]:
    payload = _json_or_none(_request(session, "GET", f"https://{slug}.bamboohr.com/careers/list"))
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), list):
        return None
    jobs = []
    for item in payload["result"]:
        title = _clean(item.get("jobOpeningName"))
        if not title:
            continue
        loc = item.get("location") or {}
        where = ", ".join(p for p in (loc.get("city"), loc.get("state")) if p) if isinstance(loc, dict) else loc
        if item.get("isRemote"):
            where = f"{where} (Remote)" if where else "Remote"
        jobs.append({
            "title": title, "location": where or None,
            "url": f"https://{slug}.bamboohr.com/careers/{item.get('id')}",
            "description": "",  # the list endpoint carries no description; the enricher fetches the job page
            "department": _clean(item.get("departmentLabel")) or None,
            "employment_type": item.get("employmentStatusLabel"),
            "posted_at": None,
        })
    return jobs


def fetch_breezy(session: requests.Session, slug: str) -> Optional[list[dict]]:
    payload = _json_or_none(_request(session, "GET", f"https://{slug}.breezy.hr/json"))
    if not isinstance(payload, list):
        return None
    jobs = []
    for item in payload:
        title = _clean(item.get("name"))
        if not title:
            continue
        loc = item.get("location") or {}
        country = (loc.get("country") or {}) if isinstance(loc, dict) else {}
        where = loc.get("name") if isinstance(loc, dict) else loc
        if isinstance(loc, dict) and not where:
            where = ", ".join(p for p in (loc.get("city"), country.get("name") if isinstance(country, dict) else None) if p)
        jobs.append({
            "title": title, "location": where or None,
            "url": item.get("url") or f"https://{slug}.breezy.hr/p/{item.get('friendly_id')}",
            "description": strip_html(item.get("description") or ""),
            "department": _clean(item.get("department")) or None,
            "employment_type": (item.get("type") or {}).get("name") if isinstance(item.get("type"), dict) else None,
            "posted_at": _posted_date(item.get("published_date")),
        })
    return jobs


def fetch_smartrecruiters(session: requests.Session, slug: str) -> Optional[list[dict]]:
    jobs, offset, total = [], 0, None
    while total is None or offset < min(total, 1000):
        payload = _json_or_none(_request(session, "GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"))
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
            return None if not jobs else jobs
        total = int(payload.get("totalFound") or 0)
        for item in payload["content"]:
            title = _clean(item.get("name"))
            if not title:
                continue
            loc = item.get("location") or {}
            where = ", ".join(p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p) if isinstance(loc, dict) else loc
            jobs.append({
                "title": title, "location": where or None,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{item.get('id')}",
                "description": "",  # per-posting description needs a second call; left to the enricher
                "department": (item.get("department") or {}).get("label") if isinstance(item.get("department"), dict) else None,
                "employment_type": (item.get("typeOfEmployment") or {}).get("label") if isinstance(item.get("typeOfEmployment"), dict) else None,
                "posted_at": _posted_date(item.get("releasedDate")),
            })
        if not payload["content"]:
            break
        offset += 100
    return jobs


def fetch_personio(session: requests.Session, slug: str) -> Optional[list[dict]]:
    """Personio publishes every job board as an XML feed at /xml."""
    import xml.etree.ElementTree as ET

    response = None
    for tld in ("de", "com"):
        response = _request(session, "GET", f"https://{slug}.jobs.personio.{tld}/xml")
        if response is not None:
            break
    if response is None:
        return None
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return None
    jobs = []
    for pos in root.iter("position"):
        get = lambda tag: (pos.findtext(tag) or "").strip()  # noqa: E731
        title = _clean(get("name"))
        if not title:
            continue
        description = " ".join(strip_html(d.findtext("value") or "") for d in pos.iter("jobDescription"))
        jobs.append({
            "title": title, "location": get("office") or None,
            "url": f"https://{slug}.jobs.personio.de/job/{get('id')}",
            "description": description.strip(),
            "department": get("department") or None,
            "employment_type": get("employmentType") or get("schedule") or None,
            "posted_at": _posted_date(get("createdAt")) if get("createdAt") else None,
        })
    return jobs


def fetch_workday(session: requests.Session, board: str, search_text: str = "Israel") -> Optional[list[dict]]:
    """board = "<tenant>.wd<n>/<site>" (e.g. "motorolasolutions.wd5/Careers").
    Workday's public listing endpoint is a POST to /wday/cxs/<tenant>/<site>/jobs;
    it's paged 20 at a time. Boards are global, so the default search text
    narrows to postings that mention Israel."""
    host_part, _, site = board.partition("/")
    tenant = host_part.split(".")[0]
    if not tenant or not site:
        return None
    base = f"https://{host_part}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    jobs, offset, total = [], 0, None
    while total is None or offset < min(total, 1000):
        response = _request(session, "POST", api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search_text},
                            headers={"Accept": "application/json", "Content-Type": "application/json"})
        payload = _json_or_none(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
            return None if not jobs else jobs
        total = int(payload.get("total") or 0)
        for item in payload["jobPostings"]:
            title = _clean(item.get("title"))
            path = item.get("externalPath") or ""
            if not title or not path:
                continue
            jobs.append({
                "title": title, "location": _clean(item.get("locationsText")) or None,
                "url": f"{base}/{site}{path}" if path.startswith("/") else f"{base}/{site}/{path}",
                "description": "",  # detail is one more call per job; left to the enricher
                "department": None, "employment_type": None,
                "posted_at": None,
            })
        if not payload["jobPostings"]:
            break
        offset += 20
    return jobs


def fetch_elbit_sigmabit_jobs(session: requests.Session) -> list[dict]:
    """Elbit Systems Sigmabit's careers site (elbitsystemscareer.com) renders
    every one of its ~578 job cards entirely client-side with no real <a
    href> link at all (confirmed live via Playwright - the anchors on the
    page are all site-chrome, zero of them point at a job). But the page
    itself loads its full listing from a plain JSON feed at /cron/jobs.json -
    use that directly instead of trying to scrape a DOM that was never going
    to expose real job links.
    """
    response = _request(session, "GET", "https://elbitsystemscareer.com/cron/jobs.json")
    if response is None:
        return []
    try:
        rows = response.json()
    except Exception:  # noqa: BLE001
        return []
    jobs = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != 1:
            continue
        title = _clean(row.get("jobTitle") or "")
        if not title:
            continue
        job_id = row.get("jobId")
        # Elbit Systems Sigmabit has no non-Israel offices in this feed - its
        # own "area" values are internal Israeli region labels (North, Sharon,
        # Shfela, Jerusalem Area, ...), none of which contain the word
        # "israel" and so wouldn't be recognized by scoring.is_relevant_location
        # on their own (real bug caught before it ran: 567 of 578 jobs would
        # have been silently dropped as "not Israel"). Tag every job as
        # Israel explicitly rather than teaching the generic location filter
        # about this one company's internal region vocabulary.
        area = row.get("area")
        jobs.append({
            "title": title,
            "location": f"{area}, Israel" if area else "Israel",
            "url": f"https://elbitsystemscareer.com/jobs/?id={job_id}" if job_id else "https://elbitsystemscareer.com/jobs/",
            "description": strip_html(row.get("description") or ""),
            "department": None,
            "employment_type": row.get("employmentType"),
            "posted_at": _posted_date(row.get("openDate")),
        })
    return jobs


# Company career sites whose job data can't be reached through the normal
# <a href> listing scrape or the known ATS APIs at all (a custom in-house
# portal that renders every job client-side with no anchor tags) but do
# expose their own plain JSON feed once you know where to look - keyed by
# the host fragment in the company's career URL.
def fetch_iai_jobs(session: requests.Session) -> list[dict]:
    """Israel Aerospace Industries' careers site (jobs.iai.co.il) renders its
    listing client-side from a static JSON feed the theme ships at
    /wp-content/themes/tyco-wp/assets/json/jobs.json (confirmed live: the
    plain-HTTP page has zero /job/ links, the rendered page shows 8 of 520
    behind infinite scroll, the feed has all 520 with full descriptions).
    Field names are abbreviated: tl=title, dc=description, ct=city,
    tp=employment type, jc=job category, id=job id (the /job/<id> page).
    """
    response = _request(session, "GET", "https://jobs.iai.co.il/wp-content/themes/tyco-wp/assets/json/jobs.json")
    if response is None:
        return []
    try:
        rows = response.json()
    except Exception:  # noqa: BLE001
        return []
    jobs = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = _clean(row.get("tl") or "")
        job_id = str(row.get("id") or "").strip()
        if not title or not job_id:
            continue
        city = _clean(row.get("ct") or "")
        # Cities in the feed are Hebrew town names (יהוד, נתב"ג, באר יעקב, ...)
        # with no country - tag Israel explicitly, same reasoning as Elbit above.
        jobs.append({
            "title": title,
            "location": f"{city}, Israel" if city else "Israel",
            "url": f"https://jobs.iai.co.il/job/{job_id}",
            "description": strip_html(row.get("dc") or ""),
            "department": _clean(row.get("jc") or "") or None,
            "employment_type": _clean(row.get("tp") or "") or None,
            "posted_at": None,
        })
    return jobs


# Not here, deliberately: career.rafael.co.il (Reblaze JS challenge that
# fingerprints every headless Chromium mode; a headed off-screen Chrome
# passed it standalone but not reliably inside the pipeline). Rafael stays
# on its devjobs.co.il listing, which plain HTTP reads fine.
SPECIAL_CASE_FETCHERS = {
    "elbitsystemscareer.com": fetch_elbit_sigmabit_jobs,
    "jobs.iai.co.il": fetch_iai_jobs,
}


def fetch_listing_links(session: requests.Session, url: str, max_links: int = 8) -> list[tuple[str, str]]:
    """Pull candidate (title, absolute_url) job links off a career listing page.

    Delegates to jobfit.scrape's CandidateExtractor + legacy_listing_chain
    so the plain-HTTP and Playwright paths agree by construction. Same-host
    links are ordered first (stable) before the cap is applied: a shared
    marketing mega-menu (Check Point's careers.checkpoint.com page links
    out to www.checkpoint.com ahead of its own results) would otherwise
    fill the cap before a single real job link was reached.
    """
    from jobfit.scrape.candidates import CandidateExtractor
    from jobfit.scrape.fetchers import make_page
    from jobfit.scrape.filters import legacy_listing_chain

    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return []
    response = _request(session, "GET", url)
    if response is None:
        return []
    page = make_page(url, getattr(response, "url", url) or url, getattr(response, "status_code", 200), response.text or "", "http")
    candidates = CandidateExtractor().extract(page, url, cap=max_links * 4)
    accepted, _ = legacy_listing_chain().run(candidates)
    accepted.sort(key=lambda c: not c.same_host)
    return [(c.text, c.href) for c in accepted[:max_links]]


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
