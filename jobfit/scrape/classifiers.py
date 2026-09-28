"""PlanClassifier: (page, candidates) -> Labels. Three implementations:
RulesPlanClassifier (no network, the LLM-free fallback), RecordedPlanClassifier
(fixtures, tests only) and - added in Task 15 - LLMPlanClassifier, which
depends only on the LLMClient protocol, never on the vendor SDK."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from jobfit.scrape.errors import ClassifierFailed
from jobfit.scrape.filters import (
    CategoryPrefixFilter, DenylistFilter, EvidenceThresholdFilter, FilterChain, HrefMarkerFilter, UrlShapeClusterFilter,
)
from jobfit.scrape.models import Candidate, CandidateLabel, Labels, Page

REASON_MAX = 200


class PlanClassifier(ABC):
    derived_by: str = "rules"

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
    derived_by = "llm"

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


class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict: ...


LABELS_SCHEMA = {
    "type": "object",
    "properties": {
        "page_verdict": {"type": "string", "enum": ["careers_page", "not_careers_page", "js_shell", "external_board"]},
        "external_board_url": {"type": ["string", "null"]},
        "container_selector": {"type": ["string", "null"]},
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "is_job": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 200},
                },
                "required": ["index", "is_job", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["page_verdict", "external_board_url", "container_selector", "candidates"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You label links found on a company's careers page.

You receive the page URL, a truncated excerpt of its visible text, and a JSON table of candidate links (index, text, href, ancestor_path, sibling_anchor_count, in_chrome).

For EVERY candidate index in the table, decide is_job:
- true only if the link leads to ONE specific job posting (a single open role).
- false for department or category overviews, "view all" pages, products, docs, blog, legal, press, contact, office, benefits or culture pages, and anything in site navigation or footers that is not a single role.
Never invent candidates; use only the indexes given. Keep each reason under 200 characters.

page_verdict:
- "careers_page" when the page lists (or is meant to list) job postings;
- "not_careers_page" when it is clearly something else (a homepage, a product page, an error page);
- "js_shell" when the page text is essentially empty and the listing must render client-side;
- "external_board" when jobs are hosted on an external applicant-tracking board (Greenhouse, Lever, Ashby, Workable, Comeet) - then set external_board_url to that board's URL.

container_selector: a CSS selector for ONE element that wraps all job links and nothing else, or null when no such element is obvious.

Return JSON matching the schema exactly."""


def build_user_message(page: Page, candidates: list[Candidate], career_url: str, text_chars: int = 3000) -> str:
    rows = [
        {"index": c.index, "text": c.text[:150], "href": c.href, "ancestor_path": c.ancestor_path[-120:],
         "sibling_anchor_count": c.sibling_anchor_count, "in_chrome": c.in_chrome}
        for c in candidates[:200]
    ]
    return (
        f"Careers page URL: {career_url}\n\n"
        f"Visible text (truncated to {text_chars} characters):\n{page.text[:text_chars]}\n\n"
        f"Candidate links ({len(rows)}):\n{json.dumps(rows, ensure_ascii=False)}"
    )


class LLMPlanClassifier(PlanClassifier):
    derived_by = "llm"

    def __init__(self, client: LLMClient, model: str, max_tokens: int = 4096, text_chars: int = 3000):
        self.client, self.model, self.max_tokens, self.text_chars = client, model, max_tokens, text_chars

    def classify(self, page: Page, candidates: list[Candidate], career_url: str) -> Labels:
        user = build_user_message(page, candidates, career_url, self.text_chars)
        known = {c.index for c in candidates}
        last_error = ""
        for attempt in range(2):
            prompt = user if attempt == 0 else f"{user}\n\nYour previous answer was invalid: {last_error}\nReturn corrected JSON."
            data = self.client.complete_json(SYSTEM_PROMPT, prompt, LABELS_SCHEMA, self.max_tokens)
            try:
                labels = Labels.model_validate(data)
                unknown = {l.index for l in labels.candidates} - known
                if unknown:
                    raise ValueError(f"unknown candidate indexes {sorted(unknown)[:10]}")
                return labels
            except (ValidationError, ValueError) as error:
                last_error = str(error)[:500]
        raise ClassifierFailed(f"invalid labels twice: {last_error}")
