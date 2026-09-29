"""Fill a JobPosting's description fields from its own page and attach
the Evidence record: JSON-LD JobPosting, an apply CTA, requirement-style
section headers, the title's role family, the URL's shape."""

from __future__ import annotations

from abc import ABC, abstractmethod

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
        if page.status >= 400:
            return fallback
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
        })


class NoopEnricher(DetailEnricher):
    """For ATS/techmap/special-case postings whose fields are already
    structured (or intentionally absent): no fetch, minimal evidence."""

    def enrich(self, posting: JobPosting) -> JobPosting:
        if posting.evidence is not None:
            return posting
        return posting.model_copy(update={"evidence": minimal_evidence(posting.title, posting.url, posting.description)})
