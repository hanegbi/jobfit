"""plan -> ScrapeStrategy. One builder per plan kind, looked up in a dict;
adding a kind is one model in models.py plus one method here."""

from __future__ import annotations

from typing import Callable, Sequence

from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.ids import normalize_job_url
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import DetailEnricher
from jobfit.scrape.errors import PlanInvalid
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.filters import (
    CategoryPrefixFilter, DenylistFilter, EvidenceThresholdFilter, FilterChain, HrefMarkerFilter,
    PlanPatternFilter, RejectListFilter, UrlShapeClusterFilter,
)
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.models import HtmlListingStrategy, ScrapePlan
from jobfit.scrape.strategies import (
    AtsApiScrape, EmbeddedAtsScrape, ExternalBoardScrape, FallbackScrape, HtmlListingScrape, InlineJsonScrape, NoScrape,
    ScrapeStrategy, SpecialCaseScrape, TechmapScrape,
)


class StrategyFactory:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 enricher: DetailEnricher, reject_patterns: list[str], techmap_index: dict[str, list[dict]],
                 health: HealthPolicy, special_fetchers: dict[str, Callable], session, max_links: int = 50):
        self.registry, self.fetchers, self.extractor, self.enricher = registry, fetchers, extractor, enricher
        self.reject_patterns, self.techmap_index, self.health = reject_patterns, techmap_index, health
        self.special_fetchers, self.session, self.max_links = special_fetchers, session, max_links
        self._builders = {
            "ats_api": self._ats_api, "external_board": self._external_board, "html_listing": self._html_listing,
            "special_case": self._special_case, "techmap_only": self._techmap_only, "broken_url": self._broken_url,
        }

    def build(self, plan: ScrapePlan, known_job_urls: Sequence[str] = ()) -> ScrapeStrategy:
        """known_job_urls: the company's already-stored job URLs - an HTML
        listing skips re-fetching those jobs' pages."""
        known = frozenset(u for u in (normalize_job_url(x) for x in known_job_urls) if u)
        return self._builders[plan.strategy.kind](plan, known)

    def chain_for(self, strategy: HtmlListingStrategy) -> FilterChain:
        return FilterChain([
            DenylistFilter(), HrefMarkerFilter(), RejectListFilter(self.reject_patterns), CategoryPrefixFilter(),
            PlanPatternFilter(strategy.include_url, strategy.exclude_url, strategy.explicit_accept),
            UrlShapeClusterFilter(strategy.url_shape), EvidenceThresholdFilter(min_signals=2, reject_chrome=True),
        ])

    def _ats_api(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        s = plan.strategy
        return AtsApiScrape(self.registry.client(s.provider), s.board, known_url=s.board_url)

    def _external_board(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        return ExternalBoardScrape(self.registry, plan.strategy.board_url)

    def _html_listing(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        s = plan.strategy
        chain = self.chain_for(s)
        primary = HtmlListingScrape(self.fetchers.build(s.renderer), self.extractor, chain, self.enricher, s, self.max_links, known)
        fallbacks: list[ScrapeStrategy] = []
        for name in s.fallbacks:
            if name == "playwright" and s.renderer != "playwright":
                fallbacks.append(HtmlListingScrape(self.fetchers.build("playwright"), self.extractor, chain, self.enricher, s, self.max_links, known))
            elif name == "techmap":
                # An ATS embedded in the page (widget/script/iframe) is a far
                # better source than techmap's title-only rows - try it first.
                fallbacks.append(EmbeddedAtsScrape(self.fetchers.build("http"), self.registry))
                fallbacks.append(TechmapScrape(self.techmap_index))
        if not any(isinstance(f, EmbeddedAtsScrape) for f in fallbacks):
            fallbacks.append(EmbeddedAtsScrape(self.fetchers.build("http"), self.registry))
        # Jobs inlined as page JSON (Next.js/Nuxt) come right after the link scrape itself.
        first_non_listing = next((i for i, f in enumerate(fallbacks) if not isinstance(f, HtmlListingScrape)), len(fallbacks))
        fallbacks.insert(first_non_listing, InlineJsonScrape(self.fetchers.build("http")))
        return FallbackScrape(primary, fallbacks, self.health.is_healthy)

    def _special_case(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        fn = self.special_fetchers.get(plan.strategy.host_fragment)
        if fn is None:
            raise PlanInvalid(f"no special-case fetcher registered for {plan.strategy.host_fragment!r}")
        return SpecialCaseScrape(fn, self.session)

    def _techmap_only(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        return TechmapScrape(self.techmap_index)

    def _broken_url(self, plan: ScrapePlan, known: frozenset[str] = frozenset()) -> ScrapeStrategy:
        # Only a probe-verified broken URL (404/410, homepage redirect) is
        # allowed to return nothing; anything less certain keeps techmap.
        return NoScrape() if plan.status == "verified" else TechmapScrape(self.techmap_index)
