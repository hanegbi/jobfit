"""ScrapeStrategy: BEHAVIOUR for one plan kind. Names carry a `Scrape`
suffix to keep them apart from the plan DATA models in models.py
(AtsApiStrategy is what a plan says; AtsApiScrape is what runs)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Callable

from jobfit import connections
from jobfit.scrape.ats import AtsClient, AtsRegistry, to_posting
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import DetailEnricher
from jobfit.scrape.errors import FetchFailed, PlanInvalid
from jobfit.scrape.fetchers import PageFetcher
from jobfit.scrape.filters import FilterChain
from jobfit.scrape.models import Candidate, HtmlListingStrategy, JobPosting, PageFingerprint, PostingSource


def page_fingerprint(candidates: list[Candidate]) -> PageFingerprint:
    shapes = sorted({c.href_shape for c in candidates})
    digest = hashlib.sha1("\n".join(shapes).encode("utf-8")).hexdigest()
    return PageFingerprint(href_shape_set_hash=digest, candidate_count=len(candidates))


class ScrapeStrategy(ABC):
    kind: str = ""
    last_fingerprint: PageFingerprint | None = None

    @abstractmethod
    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        """[] means "page reachable, no postings"; raises FetchFailed when
        nothing could be fetched (so the caller leaves stored jobs alone)."""


class AtsApiScrape(ScrapeStrategy):
    kind = "ats_api"

    def __init__(self, client: AtsClient, board: str, known_url: str | None = None, source: PostingSource = "ats_api"):
        self.client, self.board, self.known_url, self.source = client, board, known_url, source

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return self.client.fetch_board(self.board, known_url=self.known_url or career_url, source=self.source)


class ExternalBoardScrape(ScrapeStrategy):
    kind = "external_board"

    def __init__(self, registry: AtsRegistry, board_url: str):
        resolved = registry.resolve(board_url)
        if resolved is None:
            raise PlanInvalid(f"external board url {board_url!r} matches no ATS client")
        client, board = resolved
        self._inner = AtsApiScrape(client, board, known_url=board_url, source="external_board")

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return self._inner.fetch(company, career_url)


class HtmlListingScrape(ScrapeStrategy):
    kind = "html_listing"

    def __init__(self, fetcher: PageFetcher, extractor: CandidateExtractor, chain: FilterChain,
                 enricher: DetailEnricher, strategy: HtmlListingStrategy, max_links: int = 50):
        self.fetcher, self.extractor, self.chain, self.enricher, self.strategy, self.max_links = (
            fetcher, extractor, chain, enricher, strategy, max_links,
        )

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        if not career_url:
            return []
        page = self.fetcher.fetch(career_url)
        if page.status in (404, 410):
            return []
        if page.status >= 400:
            raise FetchFailed(f"{career_url}: http {page.status}")
        candidates = self.extractor.extract(page, career_url, self.strategy.container_selector, cap=self.max_links * 4)
        self.last_fingerprint = page_fingerprint(candidates)
        accepted, _ = self.chain.run(candidates)
        accepted.sort(key=lambda c: not c.same_host)
        return [
            self.enricher.enrich(JobPosting(title=c.text, url=c.href, source="html_listing"))
            for c in accepted[: self.max_links]
        ]


class SpecialCaseScrape(ScrapeStrategy):
    kind = "special_case"

    def __init__(self, fn: Callable[[object], list[dict]], session):
        self.fn, self.session = fn, session

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        try:
            raw = self.fn(self.session)
        except Exception as error:  # noqa: BLE001 - surfaced as a fetch failure, never a crash
            raise FetchFailed(f"special-case fetcher for {company}: {error}") from error
        return [p for p in (to_posting(item, "special_case") for item in raw or []) if p is not None]


class TechmapScrape(ScrapeStrategy):
    kind = "techmap"

    def __init__(self, techmap_index: dict[str, list[dict]]):
        self.techmap_index = techmap_index

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        rows = self.techmap_index.get(connections.normalize_company(company), [])
        return [
            JobPosting(title=r["title"], location=r.get("location"), url=r.get("url"), source="techmap")
            for r in rows if r.get("title")
        ]


class NoScrape(ScrapeStrategy):
    kind = "broken_url"

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return []


class FallbackScrape(ScrapeStrategy):
    """Composite: run the primary; if its result is not healthy (or it
    raised FetchFailed), try each fallback in order and return the first
    healthy result. If none is healthy, return the primary's own result
    (so diff_and_update can still close jobs on a genuinely empty page),
    or re-raise the primary's failure (so nothing is closed on an outage)."""
    kind = "fallback"

    def __init__(self, primary: ScrapeStrategy, fallbacks: list[ScrapeStrategy], is_healthy: Callable[[list[JobPosting]], bool]):
        self.primary, self.fallbacks, self.is_healthy = primary, fallbacks, is_healthy
        self.strategy_used = primary.kind

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        primary_error: FetchFailed | None = None
        primary_result: list[JobPosting] | None = None
        try:
            primary_result = self.primary.fetch(company, career_url)
            self.last_fingerprint = self.primary.last_fingerprint
            if self.is_healthy(primary_result):
                self.strategy_used = self.primary.kind
                return primary_result
        except FetchFailed as error:
            primary_error = error
        for fallback in self.fallbacks:
            try:
                result = fallback.fetch(company, career_url)
            except FetchFailed:
                continue
            if self.is_healthy(result):
                self.strategy_used = fallback.kind
                self.last_fingerprint = fallback.last_fingerprint or self.last_fingerprint
                return result
        if primary_error is not None:
            raise primary_error
        self.strategy_used = self.primary.kind
        return primary_result or []
