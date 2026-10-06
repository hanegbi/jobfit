"""One company, one scrape: load (or synthesise) its plan, build the
strategy, run it, persist health. This is the runtime path - it never
constructs a classifier, let alone a model client."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Callable, Sequence

from jobfit.ats_scorer.job_classifier import classify_job
from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.errors import PlanInvalid
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.ids import plan_id_for
from jobfit.scrape.models import (
    AtsApiStrategy, HtmlListingStrategy, JobPosting, ScrapePlan, ScrapeResult, SpecialCaseStrategy,
    TechmapOnlyStrategy,
)
from jobfit.scrape.plan_store import PlanStore

logger = logging.getLogger("jobfit.scrape")


def _classify_posting(posting: JobPosting) -> JobPosting:
    classification = classify_job(posting.title, posting.description)
    return posting.model_copy(update={
        "family": classification.family,
        "canonical_title": classification.canonical_title,
        "family_confidence": classification.confidence,
        "taxonomy_version": classification.taxonomy_version,
    })


class CompanyScrapeService:
    def __init__(self, store: PlanStore, factory: StrategyFactory, health: HealthPolicy, registry: AtsRegistry,
                 special_hosts: list[str], now: Callable[[], datetime] | None = None):
        self.store, self.factory, self.health, self.registry = store, factory, health, registry
        self.special_hosts = special_hosts
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._noop = NoopEnricher()

    def synthesize_plan(self, company_id: str, career_url: str | None) -> ScrapePlan:
        """A plan without discovery: probes only, else a rules-driven HTML
        listing plan marked unverified (picked up by the next --discover)."""
        now = self.now()
        if not career_url:
            return ScrapePlan(company_id=company_id, career_url=None, derived_by="probe", derived_at=now, verified_at=now,
                              status="verified", strategy=TechmapOnlyStrategy(reason="no career url"))
        resolved = self.registry.resolve(career_url)
        if resolved is not None:
            client, board = resolved
            return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now, verified_at=now, status="verified",
                              strategy=AtsApiStrategy(provider=client.provider, board=board, board_url=client.board_url(board)))
        for host in self.special_hosts:
            if host in career_url.lower():
                return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now, verified_at=now,
                                  status="verified", strategy=SpecialCaseStrategy(host_fragment=host))
        return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="rules", derived_at=now, status="unverified",
                          strategy=HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"]))

    def _plan_for(self, company_id: str, career_url: str | None, known_job_urls: Sequence[str] = ()) -> ScrapePlan:
        plan = self.store.get(company_id)
        if plan is None:
            plan = self.synthesize_plan(company_id, career_url)
            self.store.put(plan)
        elif plan.career_url != career_url:
            fresh = self.synthesize_plan(company_id, career_url)
            plan = fresh.model_copy(update={"notes": [f"career url changed from {plan.career_url!r}; plan re-synthesised"]})
            self.store.put(plan)
        if known_job_urls and plan.strategy.kind in ("html_listing", "techmap_only") and not (plan.health.baseline_yield or 0):
            derived = self.plan_from_job_urls(company_id, career_url, known_job_urls)
            if derived is not None:
                plan = derived
                self.store.put(plan)
        return plan

    def plan_from_job_urls(self, company_id: str, career_url: str | None, job_urls: Sequence[str]) -> ScrapePlan | None:
        """The company's own stored job URLs often point straight at its ATS
        board (comeet.com/jobs/<slug>/<uid>/..., boards.greenhouse.io/<slug>/
        jobs/<id>, ...) even when its careers page shows nothing scrapeable.
        The most-referenced board wins; None when no URL resolves."""
        counts: Counter[tuple[str, str]] = Counter()
        for url in job_urls:
            resolved = self.registry.resolve(url)
            if resolved is not None:
                client, board = resolved
                counts[(client.provider, board)] += 1
        if not counts:
            return None
        (provider, board), n = counts.most_common(1)[0]
        client = self.registry.client(provider)
        now = self.now()
        return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now, verified_at=now, status="verified",
                          strategy=AtsApiStrategy(provider=provider, board=board, board_url=client.board_url(board)),
                          notes=[f"board derived from {n} stored job url(s)"])

    def scrape(self, company: str, career_url: str | None, known_job_urls: Sequence[str] = ()) -> ScrapeResult:
        company_id = plan_id_for(company)
        plan = self._plan_for(company_id, career_url, known_job_urls)
        try:
            strategy = self.factory.build(plan, known_job_urls)
        except PlanInvalid as error:
            fresh = self.synthesize_plan(company_id, career_url)
            plan = fresh.model_copy(update={"notes": [f"plan invalid: {error}; re-synthesised"]})
            self.store.put(plan)
            strategy = self.factory.build(plan, known_job_urls)
        postings = strategy.fetch(company, career_url)  # FetchFailed propagates: nothing below runs, plan untouched
        postings = [p if p.evidence is not None else self._noop.enrich(p) for p in postings]
        # A posting whose own page says it is gone (404, bounced back to the
        # board, "no open positions") is not a job this run found - it is one
        # the listing has not caught up with. Dropping it here means
        # upsert_scraped stops seeing it and closes it, which is the path
        # every other disappearance already takes. Reported, never silent:
        # the caller writes them to the run's closed list.
        gone = [p for p in postings if p.gone_reason]
        # Never let this rule empty a whole listing. One posting bouncing to
        # the board is a withdrawn job; EVERY posting bouncing is the site
        # answering its careers page for any sub-path, and closing a
        # company's entire roster on that evidence is the one mistake here
        # that cannot be walked back by the next run.
        if gone and len(gone) == len(postings) and len(postings) > 1:
            logger.warning("%s: every posting looked gone (%s) - treating as a site-level redirect, keeping all",
                           company, gone[0].gone_reason)
            gone = []
        postings = [p for p in postings if p not in gone]
        if gone:
            logger.info("%s: %d posting(s) still listed but gone: %s", company, len(gone),
                        "; ".join(f"{p.title[:40]} ({p.gone_reason})" for p in gone[:3]))
        postings = [_classify_posting(p) for p in postings]
        plan = self.health.update(plan, postings, self.now(), fingerprint=strategy.last_fingerprint)
        self.store.put(plan)
        used = getattr(strategy, "strategy_used", strategy.kind)
        logger.info("%s: %d postings via %s (plan %s/%s)", company, len(postings), used, plan.derived_by, plan.status)
        return ScrapeResult(company_id=company_id, postings=postings, plan=plan, strategy_used=used, gone=gone)
