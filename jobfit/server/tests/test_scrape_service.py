"""CompanyScrapeService: the one entry point the runtime uses. Plan
missing -> synthesised rules plan (never a model). Strategy built by the
factory, health persisted, FetchFailed propagates untouched."""

from datetime import datetime, timezone

import pytest

from jobfit.scrape import errors, models, strategies
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scrape.service import CompanyScrapeService

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class StubFactory(StrategyFactory):
    """A real factory whose builders all return the stub - except
    special_case, which stays real so an unknown host raises PlanInvalid."""

    def __init__(self, stub):
        super().__init__(registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=False),
                         extractor=CandidateExtractor(), enricher=NoopEnricher(), reject_patterns=[], techmap_index={},
                         health=HealthPolicy(), special_fetchers={}, session=None)
        self.stub = stub
        for kind in list(self._builders):
            if kind != "special_case":
                self._builders[kind] = lambda plan: stub


class Stub(strategies.ScrapeStrategy):
    kind = "html_listing"

    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def fetch(self, company, career_url):
        self.calls.append((company, career_url))
        if self.error:
            raise self.error
        return self.result


def _service(stub, store=None):
    return CompanyScrapeService(store or MemoryPlanStore(), StubFactory(stub), HealthPolicy(), default_registry(session=None), special_hosts=["elbitsystemscareer.com"], now=lambda: NOW)


def test_synthesize_plan_picks_techmap_ats_special_or_rules_html():
    svc = _service(Stub([]))
    assert svc.synthesize_plan("acme", None).strategy.kind == "techmap_only"
    ats = svc.synthesize_plan("acme", "https://boards.greenhouse.io/acme")
    assert ats.strategy.kind == "ats_api" and ats.derived_by == "probe" and ats.status == "verified"
    special = svc.synthesize_plan("elbit", "https://elbitsystemscareer.com/")
    assert special.strategy.kind == "special_case" and special.strategy.host_fragment == "elbitsystemscareer.com"
    html = svc.synthesize_plan("acme", "https://acme.com/careers")
    assert html.strategy == models.HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"])
    assert html.derived_by == "rules" and html.status == "unverified"


def test_scrape_without_a_plan_synthesises_stores_and_runs_one():
    posting = models.JobPosting(title="Backend Engineer", url="https://acme.com/careers/1", source="html_listing")
    stub = Stub([posting])
    store = MemoryPlanStore()
    result = _service(stub, store).scrape("Acme Corp", "https://acme.com/careers")
    assert result.company_id == "acme_corp"
    assert stub.calls == [("Acme Corp", "https://acme.com/careers")]
    assert result.postings[0].evidence is not None  # NoopEnricher filled minimal evidence
    stored = store.get("acme_corp")
    assert stored.health.last_run == NOW and stored.health.last_yield == 1
    assert result.strategy_used == "html_listing"


def test_scrape_uses_a_stored_plan_and_persists_health_transitions():
    store = MemoryPlanStore()
    plan = models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW, status="verified",
                             verified_at=NOW, strategy=models.HtmlListingStrategy(), health=models.PlanHealth(baseline_yield=5, consecutive_empty_runs=1))
    store.put(plan)
    result = _service(Stub([]), store).scrape("Acme", "https://acme.com/careers")
    assert result.plan.status == "stale_suspect"
    assert store.get("acme").status == "stale_suspect"


def test_fetch_failed_propagates_and_leaves_the_plan_untouched():
    store = MemoryPlanStore()
    plan = models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW, status="verified", strategy=models.HtmlListingStrategy())
    store.put(plan)
    with pytest.raises(errors.FetchFailed):
        _service(Stub(error=errors.FetchFailed("down")), store).scrape("Acme", "https://acme.com/careers")
    assert store.get("acme") == plan


def test_a_changed_career_url_resynthesises_the_plan_with_a_note():
    store = MemoryPlanStore()
    store.put(models.ScrapePlan(company_id="acme", career_url="https://old.acme.com/jobs", derived_by="llm", derived_at=NOW, status="verified", strategy=models.HtmlListingStrategy()))
    _service(Stub([]), store).scrape("Acme", "https://boards.greenhouse.io/acme")
    stored = store.get("acme")
    assert stored.strategy.kind == "ats_api" and stored.career_url == "https://boards.greenhouse.io/acme"
    assert any("career url changed" in n for n in stored.notes)


def test_an_invalid_plan_is_resynthesised_with_a_note():
    store = MemoryPlanStore()
    store.put(models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="manual", derived_at=NOW, status="verified", strategy=models.SpecialCaseStrategy(host_fragment="gone.example")))
    _service(Stub([]), store).scrape("Acme", "https://acme.com/careers")
    stored = store.get("acme")
    assert stored.strategy.kind == "html_listing" and any("plan invalid" in n for n in stored.notes)
