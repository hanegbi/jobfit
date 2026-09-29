"""Is a scrape result real, and is a plan still working? Never consults
CV score - that was the structural flaw in the old tier gate (a company
whose real jobs just didn't fit the CV got escalated to Playwright and
techmap on every run)."""

from __future__ import annotations

from datetime import datetime

from jobfit.scrape.candidates import JOB_URL_HINT_RE
from jobfit.scrape.models import JobPosting, PageFingerprint, ScrapePlan

TRUSTED_SOURCES = {"ats_api", "external_board", "special_case", "techmap"}


class HealthPolicy:
    def __init__(self, min_evidence_ratio: float = 0.3, empty_runs_to_suspect: int = 2, yield_drop_ratio: float = 0.7):
        self.min_evidence_ratio = min_evidence_ratio
        self.empty_runs_to_suspect = empty_runs_to_suspect
        self.yield_drop_ratio = yield_drop_ratio

    def is_healthy(self, postings: list[JobPosting]) -> bool:
        if not postings:
            return False
        if all(p.source in TRUSTED_SOURCES for p in postings):
            return True
        n = len(postings)
        with_evidence = sum(
            1 for p in postings
            if p.evidence is not None and (p.evidence.jsonld_jobposting or p.evidence.apply_cta or p.evidence.requirement_sections >= 1
                                           or p.evidence.previously_stored)
        )
        if with_evidence / n >= self.min_evidence_ratio:
            return True
        hinted = sum(1 for p in postings if p.url and JOB_URL_HINT_RE.search(p.url))
        return hinted / n >= self.min_evidence_ratio

    def update(self, plan: ScrapePlan, postings: list[JobPosting], now: datetime, fingerprint: PageFingerprint | None = None) -> ScrapePlan:
        health = plan.health.model_copy()
        health.last_run = now
        health.last_yield = len(postings)
        status = plan.status
        if status == "stale":
            return plan.model_copy(update={"health": health})
        if self.is_healthy(postings):
            health.consecutive_empty_runs = 0
            health.last_ok_run = now
            if status == "stale_suspect":
                status = "verified" if plan.verified_at else "unverified"
        elif health.baseline_yield > 0:
            health.consecutive_empty_runs += 1
            if health.consecutive_empty_runs >= self.empty_runs_to_suspect:
                status = "stale_suspect"
        if (
            health.baseline_yield > 0
            and len(postings) < health.baseline_yield * (1 - self.yield_drop_ratio)
            and fingerprint is not None and plan.page_fingerprint is not None
            and fingerprint.href_shape_set_hash != plan.page_fingerprint.href_shape_set_hash
        ):
            status = "stale_suspect"
        return plan.model_copy(update={"health": health, "status": status})
