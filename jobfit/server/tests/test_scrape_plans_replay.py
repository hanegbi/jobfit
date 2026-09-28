"""Every committed plan that carries labels and a snapshot is replayed
through the factory's chain: a heuristic change that breaks a company
fails here BY NAME. Verified plans must reproduce their labels exactly
(every labelled job still accepted, no labelled non-job newly accepted).

Unverified plans are not held to either invariant: PlanValidator.validate()
already rejected verifying them at discovery time, precisely because
induction could not capture every labelled job (real case: AU10TIX has
a genuine job titled just "NOC" - DenylistFilter's short-text guard
rejects it, by design, the same way it rejects nav junk like "QA"; no
plan-level override can rescue it, since the guard runs before any
plan-specific filter in the chain), or because it has no plan-specific
pattern at all and falls back to the generic evidence filters (real
case: 365Scores' own "Careers Homepage"/"Why 365Scores" section anchors
share a repeated-structure shape under /careers/ and clear the generic
evidence threshold even though none of them are a job - not drift, just
the inherent permissiveness of the bare fallback chain with nothing
plan-specific to pin). Replaying an unverified plan is informational
only here; it still runs, but is checked for crashes, not coverage."""

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
from jobfit.scrape.models import CandidateLabel, HtmlListingStrategy, Labels, ScrapePlan
from jobfit.scrape.plan_store import FilePlanStore

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _replay(plan, html):
    factory = StrategyFactory(registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=False),
                              extractor=CandidateExtractor(), enricher=NoopEnricher(), reject_patterns=load_reject_patterns(), techmap_index={},
                              health=HealthPolicy(), special_fetchers={}, session=None)
    base_url = plan.strategy.listing_url or plan.career_url  # the snapshot is of the listing page, one hop away for landing pages
    page = make_page(base_url, base_url, 200, html, plan.strategy.renderer, NOW)
    candidates = CandidateExtractor().extract(page, base_url, plan.strategy.container_selector, cap=200)
    accepted, _ = factory.chain_for(plan.strategy).run(candidates)
    got = {c.href for c in accepted}
    by_index = {c.index: c for c in candidates}
    want = {by_index[l.index].href for l in plan.labels.candidates if l.is_job and l.index in by_index}
    forbid = {by_index[l.index].href for l in plan.labels.candidates if not l.is_job and l.index in by_index}
    return got, want, forbid


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
    got, want, forbid = _replay(plan, snapshot.read_text(encoding="utf-8"))
    if plan.status != "verified":
        return  # informational only - see module docstring
    missing = want - got
    assert not missing, f"{plan.company_id}: labelled jobs no longer accepted: {sorted(missing)[:5]}"
    leaked = forbid & got
    assert not leaked, f"{plan.company_id}: labelled non-jobs now accepted: {sorted(leaked)[:5]}"


_HTML = """
<ul>
 <li><a href="/careers/backend-1">Backend Engineer</a></li>
 <li><a href="/careers/noc">NOC</a></li>
</ul>
<a href="/careers/benefits">Benefits Overview</a>
"""


def _plan(status):
    return ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="llm", derived_at=NOW, status=status,
                      strategy=HtmlListingStrategy(explicit_accept=["https://acme.com/careers/backend-1", "https://acme.com/careers/noc"]),
                      labels=Labels(page_verdict="careers_page", candidates=[
                          CandidateLabel(index=0, is_job=True, reason="real"), CandidateLabel(index=1, is_job=True, reason="real, but short-titled"),
                          CandidateLabel(index=2, is_job=False, reason="not a job"),
                      ]))


def test_a_verified_plan_missing_a_short_titled_labelled_job_fails():
    got, want, forbid = _replay(_plan("verified"), _HTML)
    missing = want - got
    assert missing == {"https://acme.com/careers/noc"}


def test_an_unverified_plan_missing_the_same_short_titled_job_is_not_flagged():
    got, want, forbid = _replay(_plan("unverified"), _HTML)
    assert "https://acme.com/careers/noc" in (want - got)  # the known, accepted gap - informational only, see docstring
