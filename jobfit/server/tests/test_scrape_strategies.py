"""Each ScrapeStrategy against fakes. FallbackScrape is the composite that
replaces the hardcoded http -> playwright -> techmap tier order."""

from datetime import datetime, timezone

import pytest

from jobfit.scrape import errors, filters, strategies
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import DetailEnricher
from jobfit.scrape.fetchers import PageFetcher, make_page
from jobfit.scrape.models import HtmlListingStrategy, JobPosting

CAREER = "https://acme.com/careers/"
LISTING = """
<nav><a href="/about">About Us Page</a></nav>
<ul><li><a href="/careers/backend-1">Backend Engineer</a></li><li><a href="/careers/frontend-2">Frontend Engineer</a></li></ul>
"""


class FakeFetcher(PageFetcher):
    def __init__(self, status=200, html=LISTING, fail=False):
        self.status, self.html, self.fail = status, html, fail

    def fetch(self, url):
        if self.fail:
            raise errors.FetchFailed(url)
        return make_page(url, url, self.status, self.html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


class EchoEnricher(DetailEnricher):
    def enrich(self, posting):
        return posting.model_copy(update={"description": f"desc of {posting.title}"})


class Stub(strategies.ScrapeStrategy):
    def __init__(self, kind, result=None, error=None):
        self.kind, self.result, self.error = kind, result, error
        self.calls = 0

    def fetch(self, company, career_url):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _posting(title="Backend Engineer", source="html_listing"):
    return JobPosting(title=title, url="https://acme.com/careers/x", source=source)


def test_html_listing_scrape_extracts_filters_orders_caps_and_enriches():
    strategy = HtmlListingStrategy(renderer="http", include_url=r"^https://acme\.com/careers/[a-z0-9-]+$")
    chain = filters.FilterChain([filters.DenylistFilter(), filters.PlanPatternFilter(strategy.include_url, [], [])])
    scrape = strategies.HtmlListingScrape(FakeFetcher(), CandidateExtractor(), chain, EchoEnricher(), strategy, max_links=1)
    postings = scrape.fetch("Acme", CAREER)
    assert [p.title for p in postings] == ["Backend Engineer"]
    assert postings[0].description == "desc of Backend Engineer"
    assert postings[0].source == "html_listing"
    assert scrape.last_fingerprint is not None and scrape.last_fingerprint.candidate_count == 3


def test_html_listing_scrape_returns_empty_on_404_and_raises_on_5xx_or_network_failure():
    strategy = HtmlListingStrategy()
    chain = filters.legacy_listing_chain()
    assert strategies.HtmlListingScrape(FakeFetcher(status=404), CandidateExtractor(), chain, EchoEnricher(), strategy).fetch("Acme", CAREER) == []
    with pytest.raises(errors.FetchFailed):
        strategies.HtmlListingScrape(FakeFetcher(status=503), CandidateExtractor(), chain, EchoEnricher(), strategy).fetch("Acme", CAREER)
    with pytest.raises(errors.FetchFailed):
        strategies.HtmlListingScrape(FakeFetcher(fail=True), CandidateExtractor(), chain, EchoEnricher(), strategy).fetch("Acme", CAREER)


def test_html_listing_scrape_with_no_url_returns_empty():
    scrape = strategies.HtmlListingScrape(FakeFetcher(), CandidateExtractor(), filters.legacy_listing_chain(), EchoEnricher(), HtmlListingStrategy())
    assert scrape.fetch("Acme", None) == []


def test_ats_api_scrape_delegates_to_the_client(monkeypatch):
    from jobfit import ats_fetchers
    monkeypatch.setattr(ats_fetchers, "fetch_lever", lambda session, token: [{"title": "QA Engineer", "url": "https://jobs.lever.co/acme/1"}])
    client = default_registry(session=None).client("lever")
    postings = strategies.AtsApiScrape(client, "acme").fetch("Acme", "https://jobs.lever.co/acme")
    assert [p.title for p in postings] == ["QA Engineer"] and postings[0].source == "ats_api"


def test_external_board_scrape_resolves_at_construction_and_tags_source(monkeypatch):
    from jobfit import ats_fetchers
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: [{"title": "Data Engineer", "url": "https://boards.greenhouse.io/acme/jobs/1"}])
    registry = default_registry(session=None)
    scrape = strategies.ExternalBoardScrape(registry, "https://boards.greenhouse.io/acme")
    postings = scrape.fetch("Acme", CAREER)
    assert postings[0].source == "external_board"
    with pytest.raises(errors.PlanInvalid):
        strategies.ExternalBoardScrape(registry, "https://acme.com/not-a-board")


def test_special_case_scrape_wraps_a_feed_function_and_maps_errors():
    ok = strategies.SpecialCaseScrape(lambda session: [{"title": "Welder", "url": "https://e.com/jobs/?id=1", "location": "Israel"}], session=None)
    assert ok.fetch("Elbit", "https://elbitsystemscareer.com/").pop().source == "special_case"

    def boom(session):
        raise RuntimeError("feed down")
    with pytest.raises(errors.FetchFailed):
        strategies.SpecialCaseScrape(boom, session=None).fetch("Elbit", "https://elbitsystemscareer.com/")


def test_techmap_scrape_uses_the_normalized_company_key():
    index = {"acme": [{"title": "Backend Engineer", "location": "Remote", "url": "https://x", "company": "Acme Ltd"}, {"title": "", "url": "y"}]}
    postings = strategies.TechmapScrape(index).fetch("Acme Ltd.", None)
    assert [(p.title, p.location, p.source) for p in postings] == [("Backend Engineer", "Remote", "techmap")]
    assert strategies.TechmapScrape(index).fetch("Nobody", None) == []


def test_no_scrape_returns_empty():
    assert strategies.NoScrape().fetch("Acme", CAREER) == []


def test_fallback_returns_the_first_healthy_result_and_records_which_strategy():
    primary = Stub("html_listing", result=[])
    second = Stub("playwright", result=[_posting()])
    third = Stub("techmap", result=[_posting(source="techmap")])
    composite = strategies.FallbackScrape(primary, [second, third], is_healthy=lambda ps: bool(ps))
    postings = composite.fetch("Acme", CAREER)
    assert postings == [_posting()]
    assert composite.strategy_used == "playwright"
    assert third.calls == 0


def test_fallback_returns_the_primary_result_when_nothing_is_healthy():
    primary = Stub("html_listing", result=[_posting("Contact sales")])
    composite = strategies.FallbackScrape(primary, [Stub("techmap", result=[])], is_healthy=lambda ps: False)
    assert composite.fetch("Acme", CAREER) == [_posting("Contact sales")]
    assert composite.strategy_used == "html_listing"


def test_fallback_reraises_the_primary_failure_when_no_fallback_is_healthy():
    primary = Stub("html_listing", error=errors.FetchFailed("down"))
    composite = strategies.FallbackScrape(primary, [Stub("techmap", result=[])], is_healthy=lambda ps: bool(ps))
    with pytest.raises(errors.FetchFailed):
        composite.fetch("Acme", CAREER)


def test_fallback_uses_a_healthy_fallback_even_when_the_primary_failed():
    primary = Stub("html_listing", error=errors.FetchFailed("down"))
    composite = strategies.FallbackScrape(primary, [Stub("techmap", result=[_posting(source="techmap")])], is_healthy=lambda ps: bool(ps))
    assert composite.fetch("Acme", CAREER)[0].source == "techmap"
