"""Two runtime safeguards for companies whose careers page yields nothing:
(1) the service derives an ATS board from the company's own stored job
URLs; (2) an empty result from an unverified plan must not close jobs that
came from another source."""

import json
from datetime import datetime, timezone

from jobfit import ats_fetchers
from jobfit.scrape import models
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scrape.service import CompanyScrapeService
from jobfit.scripts import update_jobs

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _service():
    registry = default_registry(session=None)
    factory = StrategyFactory(registry=registry, fetchers=PageFetcherFactory(session=None, playwright_available=False), extractor=CandidateExtractor(),
                              enricher=NoopEnricher(), reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    return CompanyScrapeService(MemoryPlanStore(), factory, HealthPolicy(), registry, special_hosts=[], now=lambda: NOW)


def test_plan_from_job_urls_picks_the_most_referenced_board():
    plan = _service().plan_from_job_urls("acme", "https://acme.com/careers", [
        "https://www.comeet.com/jobs/acme/12.345/backend/67.890",
        "https://www.comeet.com/jobs/acme/12.345/frontend/67.891",
        "https://www.linkedin.com/jobs/view/123",
        "https://boards.greenhouse.io/other/jobs/1",
    ])
    assert plan.strategy.kind == "ats_api" and (plan.strategy.provider, plan.strategy.board) == ("comeet", "acme/12.345")
    assert plan.status == "verified" and "2 stored job url" in plan.notes[0]
    assert _service().plan_from_job_urls("acme", "https://acme.com/careers", ["https://acme.com/careers/x", None]) is None


def test_service_upgrades_an_empty_listing_plan_from_stored_job_urls(monkeypatch):
    service = _service()
    service.store.put(models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW,
                                        status="unverified", strategy=models.HtmlListingStrategy()))
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: [{"title": "Backend Engineer", "url": "https://boards.greenhouse.io/acme/jobs/1", "location": "Tel Aviv"}])
    result = service.scrape("Acme", "https://acme.com/careers", known_job_urls=["https://boards.greenhouse.io/acme/jobs/1"])
    assert [p.title for p in result.postings] == ["Backend Engineer"] and result.strategy_used == "ats_api"
    assert service.store.get("acme").strategy.kind == "ats_api"


def test_service_keeps_a_plan_that_already_yields(monkeypatch):
    service = _service()
    plan = models.ScrapePlan(company_id="acme", career_url="https://jobs.lever.co/acme", derived_by="probe", derived_at=NOW, status="verified",
                             strategy=models.AtsApiStrategy(provider="lever", board="acme", board_url="https://jobs.lever.co/acme"))
    service.store.put(plan)
    monkeypatch.setattr(ats_fetchers, "fetch_lever", lambda session, token: [{"title": "QA", "url": "https://jobs.lever.co/acme/1"}])
    service.scrape("Acme", "https://jobs.lever.co/acme", known_job_urls=["https://boards.greenhouse.io/acme/jobs/1"])
    assert service.store.get("acme").strategy.provider == "lever"


class _Result:
    def __init__(self, used, baseline):
        self.strategy_used = used
        self.plan = models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW,
                                      status="unverified", strategy=models.HtmlListingStrategy())
        self.plan.health.baseline_yield = baseline


def test_fetch_may_close_rules():
    assert update_jobs.fetch_may_close([{"title": "x"}], _Result("html_listing", 0)) is True
    assert update_jobs.fetch_may_close([], _Result("ats_api", 0)) is True
    assert update_jobs.fetch_may_close([], _Result("html_listing", 5)) is True
    assert update_jobs.fetch_may_close([], _Result("html_listing", 0)) is False
    assert update_jobs.fetch_may_close([], _Result("techmap", 0)) is False


def test_diff_and_update_leaves_statuses_alone_when_it_may_not_close(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    (tmp_path / "acme.json").write_text(json.dumps({"name": "Acme", "career_url": None, "last_checked": None, "jobs": [
        {"id": "j1", "title": "Backend Engineer", "url": "https://www.linkedin.com/jobs/view/1", "status": "seen", "first_seen": "x", "last_seen": "x"},
    ]}), encoding="utf-8")
    record, new, closed = update_jobs.diff_and_update("Acme", "https://acme.com/careers", [], {}, may_close=False)
    assert (new, closed) == (0, 0) and record["jobs"][0]["status"] == "seen" and record["last_checked"]
    record, new, closed = update_jobs.diff_and_update("Acme", "https://acme.com/careers", [], {}, may_close=True)
    assert closed == 1 and record["jobs"][0]["status"] == "closed"


LISTING = """<html><body><ul>
 <li><a href="/careers/backend-engineer-1">Backend Engineer</a></li>
 <li><a href="/careers/frontend-engineer-2">Frontend Engineer</a></li>
 <li><a href="/careers/devops-engineer-3">DevOps Engineer</a></li>
</ul></body></html>"""


class _CountingEnricher:
    def __init__(self):
        self.seen = []

    def enrich(self, posting):
        self.seen.append(posting.url)
        return posting.model_copy(update={"description": "fetched", "evidence": models.Evidence(apply_cta=True)})


def test_html_listing_skips_the_page_fetch_for_jobs_the_company_already_stores():
    """A steady-state run re-lists mostly known jobs: their pages are not
    fetched again, and they still count as evidence for the health gate."""
    from jobfit.scrape.classifiers import rules_chain
    from jobfit.scrape.fetchers import PageFetcher, make_page
    from jobfit.scrape.strategies import HtmlListingScrape

    class _Fetcher(PageFetcher):
        def fetch(self, url):
            return make_page(url, url, 200, LISTING, "http")

    enricher = _CountingEnricher()
    known = frozenset({"https://acme.com/careers/backend-engineer-1", "https://acme.com/careers/frontend-engineer-2"})
    scrape = HtmlListingScrape(_Fetcher(), CandidateExtractor(), rules_chain(), enricher, models.HtmlListingStrategy(), known_urls=known)
    postings = scrape.fetch("Acme", "https://acme.com/careers/")
    assert enricher.seen == ["https://acme.com/careers/devops-engineer-3"]  # only the new one was fetched
    by_url = {p.url: p for p in postings}
    assert by_url["https://acme.com/careers/backend-engineer-1"].evidence.previously_stored is True
    assert by_url["https://acme.com/careers/backend-engineer-1"].description == ""  # the stored description stands
    assert HealthPolicy().is_healthy(postings) is True


def test_service_passes_known_urls_to_the_listing_scrape(monkeypatch):
    from jobfit.scrape.fetchers import PageFetcher, make_page

    class _Fetcher(PageFetcher):
        def fetch(self, url):
            return make_page(url, url, 200, LISTING, "http")

    class _Fetchers(PageFetcherFactory):
        def __init__(self):
            pass

        def build(self, renderer):
            return _Fetcher()

    registry = default_registry(session=None)
    enricher = _CountingEnricher()
    factory = StrategyFactory(registry=registry, fetchers=_Fetchers(), extractor=CandidateExtractor(), enricher=enricher,
                              reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    service = CompanyScrapeService(MemoryPlanStore(), factory, HealthPolicy(), registry, special_hosts=[], now=lambda: NOW)
    service.store.put(models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers/", derived_by="rules", derived_at=NOW,
                                        status="verified", strategy=models.HtmlListingStrategy()))
    result = service.scrape("Acme", "https://acme.com/careers/", known_job_urls=["https://acme.com/careers/backend-engineer-1/", "https://acme.com/careers/frontend-engineer-2#apply"])
    assert len(result.postings) == 3
    assert enricher.seen == ["https://acme.com/careers/devops-engineer-3"]
