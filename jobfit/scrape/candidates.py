"""Page -> list[Candidate]: every anchor on a listing page with the
features the filters, the rules classifier and the LLM prompt all use.
Pure and deterministic. Parses the UNSTRIPPED DOM (page.html) so in_chrome
and ancestor_path can be computed; only cookie-consent widgets are removed
up front, since nothing inside one is ever a job link."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.ats_scorer.taxonomy import load_role_families
from jobfit.scrape.models import Candidate, Page
from jobfit.scrape.titles import split_card_text

# A job-indicating path token, or a run of 3+ digits (a job/req id - real
# postings are routinely id-numbered even when the surrounding path has no
# English job word at all, e.g. Check Point's ?joborderid=0936589).
JOB_URL_HINT_RE = re.compile(r"(job|career|position|opening|vacan|opportunit|\d{3,})", re.I)
_CHROME_TAGS = {"nav", "header", "footer"}
_SKIP_SCHEMES = ("#", "javascript:", "mailto:", "tel:")


def _has_job_url_hint(path: str, absolute: str) -> bool:
    """job-shape word in the path or a query PARAMETER NAME (Check Point's
    ?joborderid=... - "job" is in the key), or a 3+ digit id anywhere in the
    query (Wiz's ?gh_jid=4702745006 - the digits are in the value). Deliberately
    does NOT word-match query VALUES: a tracking/CTA link's value can carry an
    unrelated "career" substring (?cta_source=careers on a "Get a demo" link,
    caught live) that has nothing to do with the link being a job posting."""
    query = urlsplit(absolute).query
    keys = "&".join(kv.split("=", 1)[0] for kv in query.split("&") if kv)
    if JOB_URL_HINT_RE.search(path) or JOB_URL_HINT_RE.search(keys):
        return True
    return bool(re.search(r"\d{3,}", query))


NON_CONTENT_TAGS = ("script", "style", "noscript", "svg")


def strip_non_content(html: str) -> str:
    """The page with the tags no link can live in removed. Saved listing
    snapshots are only ever read back through CandidateExtractor, which
    decomposes these before doing anything else - so dropping them at write
    time changes nothing the extractor sees and cuts the committed fixtures
    by about three quarters (one page was 16MB of mostly inlined JSON)."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup is stored as-is
        return html or ""
    for tag in soup(NON_CONTENT_TAGS):
        tag.decompose()
    return str(soup)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def link_title_text(a) -> str:
    """Prefer a heading element's own text over the whole anchor's text. Many
    career-page builders wrap an entire job card - title, department tag,
    location, a description snippet, an "Apply Now" CTA - in one <a>, and
    a.get_text() then concatenates all of it into one garbled "title" (real
    example caught live: Adaptive6's Webflow careers page renders
    "Senior Backend Developer Engineering Israel Apply Now" as the link text,
    even though the real title lives cleanly in a nested <h2>).

    Other builders instead give the CTA its own small <a> ("Apply") as a
    SIBLING of a heading, both inside one narrow card div (real example
    caught live: Appcharge renders <div><div><h3>Data Analyst</h3></div>
    <a>Apply</a></div> - the anchor's own text is just "Apply"). When no
    heading is nested inside the anchor, look at the anchor's immediate
    parent for one - but only if that parent contains exactly this one
    anchor, so a heading is never borrowed from a different job's card in
    a shared list container.

    Falls back to the whole anchor's text when neither finds anything."""
    heading = a.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        heading_text = _clean(heading.get_text(" "))
        if heading_text:
            return heading_text
    if a.parent is not None and len(a.parent.find_all("a")) == 1:
        heading = a.parent.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading:
            heading_text = _clean(heading.get_text(" "))
            if heading_text:
                return heading_text
    return _clean(a.get_text(" "))


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def href_shape(url: str) -> str:
    """'<host>|<parent segments joined by />|<depth>' for path-style URLs;
    '<host>|?<sorted query keys>' for flat query-string schemes (a path of
    at most one segment plus a query)."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.query and len(segments) <= 1:
        keys = sorted(parse_qs(parts.query, keep_blank_values=True))
        return f"{_host(url)}|?{','.join(keys)}"
    return f"{_host(url)}|{'/'.join(segments[:-1])}|{len(segments)}"


class CandidateExtractor:
    def extract(self, page: Page, career_url: str, container_selector: str | None = None, cap: int = 200) -> list[Candidate]:
        try:
            soup = BeautifulSoup(page.html, "html.parser")
        except Exception:  # noqa: BLE001 - malformed markup yields no candidates, not a crash
            return []
        for tag in soup(NON_CONTENT_TAGS):
            tag.decompose()
        for el in list(soup.find_all(ats_fetchers._is_cookie_widget)):
            if el.parent is not None:
                el.decompose()

        root = soup
        if container_selector:
            try:
                scoped = soup.select_one(container_selector)
            except Exception:  # noqa: BLE001 - an invalid selector means "whole page"
                scoped = None
            if scoped is not None:
                root = scoped

        career_host = _host(career_url)
        career_path = urlsplit(career_url).path.rstrip("/")
        families = load_role_families()

        seen: set[str] = set()
        out: list[Candidate] = []
        for a in root.find_all("a", href=True):
            href = (a["href"] or "").strip()
            if not href or href.lower().startswith(_SKIP_SCHEMES):
                continue
            text = link_title_text(a)
            if not text:
                continue
            absolute = urljoin(page.url, href)
            if absolute in seen:
                continue
            seen.add(absolute)

            ancestors = [p for p in reversed(list(a.parents)) if p.name and p.name != "[document]"]
            ancestor_path = ">".join(p.name for p in ancestors) + ">a"
            in_chrome = any(
                p.name in _CHROME_TAGS or (p.get("role") or "").lower() == "navigation" for p in ancestors
            )
            # "Repeated structure": how many anchors under this link's
            # grandparent (the <ul> for a <li><a>, the card grid for a
            # <div><a>) share its URL shape - a listing is a list of
            # same-shaped links; a lone marketing slug is not.
            shape = href_shape(absolute)
            scope = a.parent.parent if a.parent is not None and a.parent.parent is not None else a.parent
            sibling_anchor_count = 1
            if scope is not None:
                sibling_anchor_count = sum(
                    1 for other in scope.find_all("a", href=True)
                    if href_shape(urljoin(page.url, (other["href"] or "").strip())) == shape
                )
            path = urlsplit(absolute).path.rstrip("/")
            card = split_card_text(text)
            out.append(Candidate(
                index=len(out), text=text, href=absolute, ancestor_path=ancestor_path,
                title=card.title or text, location_hint=card.location,
                employment_type_hint=card.employment_type,
                sibling_anchor_count=max(1, sibling_anchor_count),
                same_host=_host(absolute) == career_host,
                under_career_path=bool(career_path) and path.startswith(career_path) and path != career_path,
                has_job_url_hint=_has_job_url_hint(path, absolute),
                role_family=families.classify(text),
                in_chrome=in_chrome,
                href_shape=shape,
            ))
            if len(out) >= cap:
                break
        return out
