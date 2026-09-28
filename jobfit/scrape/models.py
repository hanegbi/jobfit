"""Data models for the scrape package - plain pydantic, JSON-round-trippable.

Naming rule: *Strategy models here are DATA (what a plan says to do);
the classes in strategies.py that carry the same prefix with a `Scrape`
suffix (AtsApiScrape, HtmlListingScrape, ...) are BEHAVIOUR.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

SCRAPE_PLAN_SCHEMA_VERSION = 1

Renderer = Literal["http", "playwright"]
PostingSource = Literal["ats_api", "external_board", "html_listing", "special_case", "techmap"]


class Page(BaseModel):
    url: str                      # final URL after redirects
    requested_url: str
    status: int
    html: str
    text: str                     # visible text with script/style/noscript/svg removed
    renderer: Renderer
    fetched_at: datetime
    is_js_shell: bool


class Candidate(BaseModel):
    index: int
    text: str
    href: str                     # absolute URL
    ancestor_path: str            # e.g. "body>main>section>ul>li>a"
    sibling_anchor_count: int     # anchors under this link's grandparent that share its href_shape (incl. itself)
    same_host: bool
    under_career_path: bool
    has_job_url_hint: bool
    role_family: str | None
    in_chrome: bool
    href_shape: str


class Evidence(BaseModel):
    jsonld_jobposting: bool = False
    apply_cta: bool = False
    requirement_sections: int = 0
    role_family_from_title: str | None = None
    url_shape: str = ""


class JobPosting(BaseModel):
    title: str
    url: str | None = None        # an ATS item can legitimately carry no URL (Workable without a shortcode)
    location: str | None = None
    description: str = ""
    department: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None
    evidence: Evidence | None = None
    source: PostingSource


class AtsApiStrategy(BaseModel):
    kind: Literal["ats_api"] = "ats_api"
    provider: Literal["greenhouse", "lever", "ashby", "workable", "comeet"]
    board: str
    board_url: str


class ExternalBoardStrategy(BaseModel):
    kind: Literal["external_board"] = "external_board"
    board_url: str


class HtmlListingStrategy(BaseModel):
    kind: Literal["html_listing"] = "html_listing"
    renderer: Renderer = "http"
    container_selector: str | None = None
    include_url: str | None = None
    exclude_url: list[str] = Field(default_factory=list)
    url_shape: str | None = None
    explicit_accept: list[str] = Field(default_factory=list)
    fallbacks: list[Literal["playwright", "techmap"]] = Field(default_factory=list)


class SpecialCaseStrategy(BaseModel):
    kind: Literal["special_case"] = "special_case"
    host_fragment: str


class TechmapOnlyStrategy(BaseModel):
    kind: Literal["techmap_only"] = "techmap_only"
    reason: str


class BrokenUrlStrategy(BaseModel):
    kind: Literal["broken_url"] = "broken_url"
    reason: str


Strategy = Annotated[
    Union[AtsApiStrategy, ExternalBoardStrategy, HtmlListingStrategy, SpecialCaseStrategy, TechmapOnlyStrategy, BrokenUrlStrategy],
    Field(discriminator="kind"),
]


class CandidateLabel(BaseModel):
    index: int
    is_job: bool
    reason: str = Field(max_length=200)


class Labels(BaseModel):
    page_verdict: Literal["careers_page", "not_careers_page", "js_shell", "external_board"]
    external_board_url: str | None = None
    container_selector: str | None = None
    candidates: list[CandidateLabel] = Field(default_factory=list)


class PageFingerprint(BaseModel):
    href_shape_set_hash: str
    candidate_count: int


class PlanHealth(BaseModel):
    consecutive_empty_runs: int = 0
    last_ok_run: datetime | None = None
    last_run: datetime | None = None
    last_yield: int = 0
    baseline_yield: int = 0


PlanStatus = Literal["verified", "unverified", "stale_suspect", "stale"]
DerivedBy = Literal["llm", "rules", "manual", "probe"]


class ScrapePlan(BaseModel):
    company_id: str
    schema_version: int = SCRAPE_PLAN_SCHEMA_VERSION
    career_url: str | None
    derived_by: DerivedBy
    model: str | None = None
    derived_at: datetime
    verified_at: datetime | None = None
    status: PlanStatus
    strategy: Strategy
    labels: Labels | None = None
    page_fingerprint: PageFingerprint | None = None
    health: PlanHealth = Field(default_factory=PlanHealth)
    rediscover_after: datetime | None = None
    notes: list[str] = Field(default_factory=list)


class ScrapeResult(BaseModel):
    company_id: str
    postings: list[JobPosting]
    plan: ScrapePlan
    strategy_used: str
    notes: list[str] = Field(default_factory=list)
