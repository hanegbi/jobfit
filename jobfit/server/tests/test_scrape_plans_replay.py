"""Every committed plan that carries labels and a snapshot is replayed
through the factory's chain: a heuristic change that breaks a company
fails here BY NAME. Verified plans must reproduce their labels exactly;
unverified plans must at least still accept every labelled job."""

from datetime import datetime, timezone

import pytest

from jobfit import config
from jobfit.scrape.ats import default_registry
from jobfit.scrape.bootstrap import load_reject_patterns
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcherFactory, make_page
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.plan_store import FilePlanStore


def _replayable():
    store = FilePlanStore(config.SCRAPE_PLANS_DIR)
    out = []
    for plan in store.all():
        snapshot = config.LISTING_SNAPSHOTS_DIR / f"{plan.company_id}.html"
        if plan.labels is not None and plan.strategy.kind == "html_listing" and plan.career_url and snapshot.exists():
            out.append(pytest.param(plan, snapshot, id=plan.company_id))
    return out


@pytest.mark.parametrize("plan, snapshot", _replayable() or [pytest.param(None, None, id="no-plans", marks=pytest.mark.skip(reason="no replayable plans committed yet"))])
def test_plan_reproduces_its_labels_on_its_snapshot(plan, snapshot):
    factory = StrategyFactory(registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=False),
                              extractor=CandidateExtractor(), enricher=NoopEnricher(), reject_patterns=load_reject_patterns(), techmap_index={},
                              health=HealthPolicy(), special_fetchers={}, session=None)
    page = make_page(plan.career_url, plan.career_url, 200, snapshot.read_text(encoding="utf-8"), plan.strategy.renderer, datetime(2026, 9, 28, tzinfo=timezone.utc))
    candidates = CandidateExtractor().extract(page, plan.career_url, plan.strategy.container_selector, cap=200)
    accepted, _ = factory.chain_for(plan.strategy).run(candidates)
    got = {c.href for c in accepted}
    by_index = {c.index: c for c in candidates}
    want = {by_index[l.index].href for l in plan.labels.candidates if l.is_job and l.index in by_index}
    forbid = {by_index[l.index].href for l in plan.labels.candidates if not l.is_job and l.index in by_index}
    missing = want - got
    assert not missing, f"{plan.company_id}: labelled jobs no longer accepted: {sorted(missing)[:5]}"
    if plan.status == "verified":
        leaked = forbid & got
        assert not leaked, f"{plan.company_id}: labelled non-jobs now accepted: {sorted(leaked)[:5]}"
