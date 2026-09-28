"""StrategyFactory: plan.strategy.kind -> the ScrapeStrategy that runs it.
One registry entry per kind; html_listing gets the plan-specific filter
chain and, when the plan lists fallbacks, a FallbackScrape composite."""

from datetime import datetime, timezone

import pytest

from jobfit.scrape import errors, models, strategies
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.health import HealthPolicy

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _factory(playwright=True):
    return StrategyFactory(
        registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=playwright),
        extractor=CandidateExtractor(), enricher=NoopEnricher(), reject_patterns=[r"^https://acme\.com/legal"],
        techmap_index={"acme": []}, health=HealthPolicy(), special_fetchers={"elbitsystemscareer.com": lambda s: []}, session=None,
    )


def _plan(strategy, status="verified"):
    return models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="probe", derived_at=NOW, status=status, strategy=strategy)


def test_builds_each_kind():
    f = _factory()
    assert isinstance(f.build(_plan(models.AtsApiStrategy(provider="lever", board="acme", board_url="https://jobs.lever.co/acme"))), strategies.AtsApiScrape)
    assert isinstance(f.build(_plan(models.ExternalBoardStrategy(board_url="https://boards.greenhouse.io/acme"))), strategies.ExternalBoardScrape)
    # An html_listing plan is always wrapped so the embedded-ATS fallback can run.
    plain = f.build(_plan(models.HtmlListingStrategy(fallbacks=[])))
    assert isinstance(plain, strategies.FallbackScrape) and isinstance(plain.primary, strategies.HtmlListingScrape)
    assert [type(fb).__name__ for fb in plain.fallbacks] == ["EmbeddedAtsScrape"]
    assert isinstance(f.build(_plan(models.SpecialCaseStrategy(host_fragment="elbitsystemscareer.com"))), strategies.SpecialCaseScrape)
    assert isinstance(f.build(_plan(models.TechmapOnlyStrategy(reason="x"))), strategies.TechmapScrape)
    assert isinstance(f.build(_plan(models.BrokenUrlStrategy(reason="404"))), strategies.NoScrape)


def test_unverified_broken_url_falls_back_to_techmap_and_unknown_special_case_is_invalid():
    f = _factory()
    assert isinstance(f.build(_plan(models.BrokenUrlStrategy(reason="classifier"), status="unverified")), strategies.TechmapScrape)
    with pytest.raises(errors.PlanInvalid):
        f.build(_plan(models.SpecialCaseStrategy(host_fragment="nope.example")))


def test_html_listing_with_fallbacks_is_wrapped_in_a_fallback_composite():
    f = _factory()
    built = f.build(_plan(models.HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"])))
    assert isinstance(built, strategies.FallbackScrape)
    assert isinstance(built.primary, strategies.HtmlListingScrape)
    # The embedded-ATS probe sits just before techmap: a board mounted in the
    # page beats techmap's title-only rows.
    assert [type(fb).__name__ for fb in built.fallbacks] == ["HtmlListingScrape", "EmbeddedAtsScrape", "TechmapScrape"]
    assert built.fallbacks[0].fetcher.__class__.__name__ == "PlaywrightPageFetcher"


def test_playwright_fallback_is_skipped_when_the_primary_already_renders_with_playwright():
    built = _factory().build(_plan(models.HtmlListingStrategy(renderer="playwright", fallbacks=["playwright", "techmap"])))
    assert [type(fb).__name__ for fb in built.fallbacks] == ["EmbeddedAtsScrape", "TechmapScrape"]


def test_chain_for_a_plan_has_the_documented_order_and_carries_the_plan_patterns():
    strategy = models.HtmlListingStrategy(include_url=r"^https://acme\.com/careers/[a-z-]+$", url_shape="acme.com|careers|2")
    chain = _factory().chain_for(strategy)
    assert [f.name for f in chain.filters] == ["denylist", "href_marker", "reject_list", "category_prefix", "plan_pattern", "url_shape", "evidence"]
    assert chain.filters[4].include.pattern == strategy.include_url
    assert chain.filters[5].expected_shape == "acme.com|careers|2"
    assert chain.filters[2].patterns[0].pattern == r"^https://acme\.com/legal"
