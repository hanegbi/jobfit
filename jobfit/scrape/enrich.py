"""Fill a JobPosting's description fields from its own page and attach
the Evidence record: JSON-LD JobPosting, an apply CTA, requirement-style
section headers, the title's role family, the URL's shape."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.ats_scorer.jd_extractor import _split_sections
from jobfit.ats_scorer.taxonomy import load_role_families
from jobfit.scrape.candidates import href_shape
from jobfit.scrape.errors import FetchFailed
from jobfit.scrape.fetchers import PageFetcher, visible_text
from jobfit.scrape.models import Evidence, JobPosting
from jobfit.scrape.titles import authoritative_title, detail_title_candidates, split_card_text

_REQ_BUCKETS = ("must", "nice", "responsibility")

# A board's "this posting is gone" page. Comeet and Greenhouse answer 200
# with one of these rather than a 404, so status alone cannot tell a live
# posting from a withdrawn one.
_GONE_TEXT_RE = re.compile(
    r"no open positions|position (is )?(no longer|not) (available|open)|"
    r"this (job|position|posting) (is )?(no longer|has been) (available|open|filled|closed|removed)|"
    r"the (job|position) you('re| are) looking for|job not found|posting not found|"
    r"sorry,? (this|that) (job|position|opening)",
    re.I,
)
# A challenge or consent wall served instead of the page. The scrape saw
# HTTP 200 and no job - which is a scrape to retry with a browser, not a
# posting to close.
_BLOCKED_TEXT_RE = re.compile(
    r"attention required!|just a moment\.\.\.|checking your browser|cf-browser-verification|"
    r"enable javascript and cookies|access denied|request unsuccessful|are you a robot|"
    r"you have been blocked|blocked by|security check|verify you are human|ddos protection",
    re.I,
)
def _bounced_up_to_the_board(url: str, final_url: str) -> bool:
    """True when a redirect landed on an ANCESTOR of the job's own path.

    More robust than listing each board's index shape, and it is what the
    live failures look like: Comeet sends /jobs/rapyd/73.00E/data-analyst/
    44.A11 back to /jobs/rapyd/73.00E, Greenhouse sends /zscaler/jobs/123 to
    /zscaler?error=true. A redirect that goes DEEPER is the opposite signal
    - AppsFlyer rewrites /jobs/position/8732730002 to the same job with its
    slug appended, and that job is alive.
    """
    origin, landed = urlsplit(url or ""), urlsplit(final_url or "")
    if "error=" in (landed.query or ""):
        return True
    start, end = origin.path.rstrip("/"), landed.path.rstrip("/")
    return bool(end) and end != start and start.startswith(end + "/")


_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_REMOTE_RE = re.compile(r"\b(fully remote|remote[- ]first|work from home|100% remote|remote)\b", re.I)
_ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based)\b", re.I)


def work_mode_of(text: str) -> str | None:
    """"hybrid" | "remote" | "onsite" from the posting's own words, or None.

    Hybrid is checked first because a hybrid posting almost always also says
    "remote" ("2 days remote"), and answering "remote" to that is the error
    worth avoiding - it is the one mode is_remote cannot express, which is
    why this is a column of its own rather than a second boolean.
    """
    blob = text or ""
    if _HYBRID_RE.search(blob):
        return "hybrid"
    if _REMOTE_RE.search(blob):
        return "remote"
    if _ONSITE_RE.search(blob):
        return "onsite"
    return None


def fetch_outcome(status: int, html: str) -> str:
    """"ok" | "blocked" | "empty" for one fetched job page.

    "blocked" is the one worth separating: a Cloudflare challenge answers
    200 with a page that has no job on it, which is indistinguishable from
    a thin posting unless the challenge text is recognised. Eleven of 89
    links in a real export came back 403 or challenged.
    """
    if status in (401, 403, 429) or _BLOCKED_TEXT_RE.search(html or ""):
        return "blocked"
    if not (html or "").strip():
        return "empty"
    return "ok"


def posting_is_gone(url: str, final_url: str, status: int, html: str) -> str | None:
    """Why this posting is no longer live, or None if it still is.

    Four ways a job dies, and only the first announces itself honestly:
    a 404/410; a redirect that lands back on the board's index ("?error=true"
    on Greenhouse, /jobs/<company> on Comeet - both seen live); a 200 whose
    body says the position is gone; and a page that is simply empty.
    """
    if status in (404, 410):
        return f"http {status}"
    if _BLOCKED_TEXT_RE.search(html or ""):
        return None  # a wall, not a withdrawal - say nothing about the job
    if final_url and _bounced_up_to_the_board(url, final_url):
        return f"redirected to {final_url}"
    if _GONE_TEXT_RE.search(visible_text(html or "")[:4000]):
        return "board says the position is gone"
    return None


def _requirement_sections(text: str) -> int:
    sections = _split_sections(text or "")
    return sum(1 for bucket in _REQ_BUCKETS if sections.get(bucket))


def minimal_evidence(title: str, url: str | None, description: str = "") -> Evidence:
    return Evidence(
        role_family_from_title=load_role_families().classify(title or ""),
        url_shape=href_shape(url) if url else "",
        requirement_sections=_requirement_sections(description),
    )


def _has_apply_cta(soup: BeautifulSoup) -> bool:
    for tag in soup.find_all(["a", "button", "input"]):
        haystack = " ".join([
            tag.get_text(" ") if tag.name != "input" else "", tag.get("href") or "", tag.get("value") or "", tag.get("id") or "",
            " ".join(tag.get("class") or []),
        ]).lower()
        if "apply" in haystack:
            return True
    return False


def extract_evidence(html: str, title: str, url: str | None) -> Evidence:
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001
        return minimal_evidence(title, url)
    return Evidence(
        jsonld_jobposting=bool(ats_fetchers._jsonld_job_postings(soup)),
        apply_cta=_has_apply_cta(soup),
        requirement_sections=_requirement_sections(visible_text(html)),
        role_family_from_title=load_role_families().classify(title or ""),
        url_shape=href_shape(url) if url else "",
    )


class DetailEnricher(ABC):
    @abstractmethod
    def enrich(self, posting: JobPosting) -> JobPosting: ...


class GenericHtmlEnricher(DetailEnricher):
    def __init__(self, fetcher: PageFetcher):
        self.fetcher = fetcher

    def enrich(self, posting: JobPosting) -> JobPosting:
        """The incoming title is a listing card's whole text. The job's own
        page is the authority on what the job is called; when the page can't
        be read, the card is split on what the vocabulary recognizes."""
        url = posting.url
        card = split_card_text(posting.title)
        fallback = posting.model_copy(update={
            "title": card.title or posting.title,
            "location": posting.location or card.location,
            "employment_type": posting.employment_type or card.employment_type,
            "evidence": minimal_evidence(card.title or posting.title, url, posting.description),
        })
        if not url or any(host in url.lower() for host in ats_fetchers._SKIP_GENERIC_FETCH_HOSTS):
            return fallback
        try:
            page = self.fetcher.fetch(url)
        except FetchFailed:
            return fallback
        status = fetch_outcome(page.status, page.html)
        gone = posting_is_gone(url, page.url, page.status, page.html)
        if gone:
            # Keep the card's own title: this posting is on its way out of the
            # listing and the reason is what matters, not a better title.
            return fallback.model_copy(update={"fetch_status": status, "gone_reason": gone})
        if page.status >= 400:
            return fallback.model_copy(update={"fetch_status": status})
        details = ats_fetchers.parse_job_details_html(page.html)
        title = authoritative_title(detail_title_candidates(page.html), posting.title) or fallback.title
        # Whatever the page's title left behind is the card's metadata.
        leftover = split_card_text(posting.title[len(title):] if posting.title.startswith(title) else "")
        return posting.model_copy(update={
            "title": title,
            "description": details["description"] or posting.description,
            "location": posting.location or details["location"] or leftover.location or card.location,
            "employment_type": (posting.employment_type or details["employment_type"]
                                or leftover.employment_type or card.employment_type),
            "posted_at": posting.posted_at or details["posted_at"],
            "evidence": extract_evidence(page.html, title, url),
            "fetch_status": status,
            "work_mode": work_mode_of(
                " ".join(filter(None, [posting.location, details["location"], page.text[:2000]]))),
        })


class NoopEnricher(DetailEnricher):
    """For ATS/techmap/special-case postings whose fields are already
    structured (or intentionally absent): no fetch, minimal evidence."""

    def enrich(self, posting: JobPosting) -> JobPosting:
        if posting.evidence is not None:
            return posting
        return posting.model_copy(update={"evidence": minimal_evidence(posting.title, posting.url, posting.description)})
