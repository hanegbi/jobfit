"""Shared heuristics for pulling job links out of an arbitrary career listing
page - no company-specific selector to rely on, so this is pattern-based
(link text length + a denylist of nav/footer boilerplate). Used by both the
plain-HTTP listing fetch (ats_fetchers) and the Playwright fallback
(scripts/playwright_listings.py) so the two paths agree on what counts as a
job link.
"""

import re
from urllib.parse import urlsplit

NAV_DENYLIST = re.compile(
    r"^(home|about|contact( us)?|privacy( policy)?|terms( of (use|service))?|cookies?( policy)?|sign ?in|log ?in|"
    r"register|blog|news|press|resources?|white papers?|case stud(y|ies)|"
    r"investors?|sustainability|diversity|benefits?|life at|culture|our (team|story|values)|"
    r"locations?|offices?|leadership|board|help|faq|support|search( jobs?)?|filter|sort by|share|"
    r"apply( now| today)?|view all|see all|view (open )?positions?|view listing|browse all|"
    r"\+? ?view more positions?|learn more|read more( ?>)?|back to|skip to|menu|toggle|close|"
    r"let'?s talk|follow (us|gett .*)|submit (cv|resume)|eeo is the law|job search|"
    r"linkedin|facebook|twitter|instagram|youtube)$",
    re.I,
)
_FORM_TOKEN_RE = re.compile(r"^\[#|#\]$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+\??$")
_URL_TEXT_RE = re.compile(r"^(https?://|www\.)", re.I)
# Substrings that mark a link destination as never a job posting regardless
# of its text - real case: a careers page's "office location" links point at
# Google Maps, and their anchor text ("USA Office", a street address) passes
# every other heuristic just fine.
NON_JOB_LINK_HREF_MARKERS = ("google.com/maps", "maps.google.com", "goo.gl/maps")


def looks_like_job_link_href(href: str) -> bool:
    href_lower = (href or "").lower()
    return not any(marker in href_lower for marker in NON_JOB_LINK_HREF_MARKERS)

MAX_LINKS_PER_COMPANY = 8


def clean(text: str) -> str:
    return " ".join((text or "").split())


def looks_like_job_title(text: str) -> bool:
    text = text.strip()
    if not (8 <= len(text) <= 120):
        return False
    if NAV_DENYLIST.match(text):
        return False
    if _FORM_TOKEN_RE.search(text) or _EMAIL_RE.match(text):
        return False
    if _URL_TEXT_RE.match(text):
        return False
    if not re.search(r"[A-Za-z]{3,}", text):
        return False
    return True


def _url_parent_segments(url: str) -> tuple[str, ...]:
    """Path segments of the directory a URL's own page lives under (its path
    with the final segment - the page's own slug/id/"all" - removed)."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return tuple(segments[:-1])


def drop_category_prefix_links(results: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop entries that are a department/category overview rather than a post.

    Some career-page builders link both a department overview (e.g.
    .../career/co/engineering/all) and that department's individual postings
    (.../career/co/engineering/<id>/some-job/all) with link text that passes
    looks_like_job_title just as easily as a real title does - nothing in the
    text alone distinguishes "Engineering" the section heading from "Recruiter"
    the job title.

    The URL nesting does, once each URL's own trailing segment (its slug, id,
    or a shared "all"/"index" convention) is set aside: an overview link's
    parent directory is a strict prefix of its own postings' parent
    directories, whereas two real job links' parent directories never nest
    inside each other like that. Order-preserving; only removes entries that
    have a sibling nested under them in this same batch.
    """
    parents = [_url_parent_segments(url) for _, url in results]
    filtered = []
    for i, (title, url) in enumerate(results):
        mine = parents[i]
        if any(j != i and len(mine) < len(other) and other[: len(mine)] == mine for j, other in enumerate(parents)):
            continue
        filtered.append((title, url))
    return filtered
