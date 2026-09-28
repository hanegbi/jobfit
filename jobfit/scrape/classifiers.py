"""PlanClassifier: (page, candidates) -> Labels. Three implementations:
RulesPlanClassifier (no network, the LLM-free fallback), RecordedPlanClassifier
(fixtures, tests only) and - added in Task 15 - LLMPlanClassifier, which
depends only on the LLMClient protocol, never on the vendor SDK."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from jobfit.scrape.errors import ClassifierFailed
from jobfit.scrape.filters import (
    CategoryPrefixFilter, DenylistFilter, EvidenceThresholdFilter, FilterChain, HrefMarkerFilter, UrlShapeClusterFilter,
)
from jobfit.scrape.models import Candidate, CandidateLabel, Labels, Page

REASON_MAX = 200


class PlanClassifier(ABC):
    @abstractmethod
    def classify(self, page: Page, candidates: list[Candidate], career_url: str) -> Labels: ...


def rules_chain() -> FilterChain:
    """Evidence-based accept: hard rejects, then the batch-mode shape
    filter, then >= 2 positive signals outside nav/header/footer."""
    return FilterChain([
        DenylistFilter(), HrefMarkerFilter(), CategoryPrefixFilter(),
        UrlShapeClusterFilter(None), EvidenceThresholdFilter(min_signals=2, reject_chrome=True),
    ])


class RulesPlanClassifier(PlanClassifier):
    def __init__(self, chain: FilterChain | None = None):
        self.chain = chain or rules_chain()

    def classify(self, page: Page, candidates: list[Candidate], career_url: str) -> Labels:
        accepted, rejected = self.chain.run(candidates)
        accepted_idx = {c.index for c in accepted}
        reasons = {c.index: v.reason for c, v in rejected}
        labels = [
            CandidateLabel(index=c.index, is_job=c.index in accepted_idx,
                           reason=("rules: accepted" if c.index in accepted_idx else f"rules: {reasons.get(c.index, 'rejected')}")[:REASON_MAX])
            for c in candidates
        ]
        return Labels(page_verdict="js_shell" if page.is_js_shell else "careers_page", candidates=labels)


class RecordedPlanClassifier(PlanClassifier):
    def __init__(self, labels_by_url: dict[str, Labels]):
        self.labels_by_url = labels_by_url

    @classmethod
    def from_dir(cls, directory: Path) -> "RecordedPlanClassifier":
        recorded: dict[str, Labels] = {}
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            recorded[data["career_url"]] = Labels.model_validate(data["labels"])
        return cls(recorded)

    def classify(self, page: Page, candidates: list[Candidate], career_url: str) -> Labels:
        try:
            return self.labels_by_url[career_url]
        except KeyError:
            raise ClassifierFailed(f"no recorded labels for {career_url}") from None
