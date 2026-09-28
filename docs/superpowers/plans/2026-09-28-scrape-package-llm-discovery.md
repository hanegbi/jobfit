# Scrape Package, Plan-Driven Scraping, LLM Discovery — Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the procedural scrape cascade with an object-oriented `jobfit/scrape/` package driven by a stored, per-company `ScrapePlan`; derive those plans once per company (LLM-assisted, explicit `--discover` command only); and make every ordinary `update_jobs` run execute the stored plan in pure Python with zero model calls.

**Architecture:** A new package `jobfit/scrape/` with pydantic models, small single-method interfaces (`PageFetcher`, `LinkFilter`, `AtsClient`, `DetailEnricher`, `ScrapeStrategy`, `PlanClassifier`, `PlanStore`), a `StrategyFactory` that builds the strategy for a company from its plan, and a `CompanyScrapeService` that orchestrates one company's scrape. The existing procedural functions in `jobfit/ats_fetchers.py`, `jobfit/listing_heuristics.py` and `jobfit/scripts/update_jobs.py` are wrapped first (strangler pattern) and deleted once the new path passes the same tests. The LLM (`AnthropicLLMClient`) lives in its own lazily-imported module and is only ever constructed by the discovery command.

**Tech Stack:** Python 3.13, pydantic v2 (already a transitive dependency via fastapi and used directly by `jobfit/ats_scorer/config.py`), BeautifulSoup, requests, Playwright (already used), the `anthropic` Python SDK (new dependency, added in Task 15 only), pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-scrape-compute-pipeline-redesign-design.md` — this plan implements spec section 2.4 (the `jobfit/scrape/` package), section 3 (discovery vs runtime split), section 4 (discovery flow, LLM call, induction, staleness, runtime flow), and section 7.1 (scrape tests). It is build-sequencing steps 5, 6 and 7 of spec section 8. Step 8 (registry migration) is Plan C and is not here.

## Global Constraints

- **Zero model calls in the runtime path (spec principle 2).** `jobfit/scrape/llm_client.py` is the only module that imports the `anthropic` SDK. Nothing under `jobfit/scrape/` imports it at module top level; only `bootstrap.build_discovery_planner()` imports it, inside the function body. Task 16 adds a test that asserts `jobfit.scrape.llm_client` is absent from `sys.modules` after a runtime scrape.
- **Scoring stays untouched except for one additive parameter** (`scoring._looks_unparseable(job_req, evidence=None)`, Task 13). No other change under `jobfit/scoring.py` or `jobfit/ats_scorer/`.
- **Every new data model is a pydantic `BaseModel`** with the exact fields in spec section 2.4.1 (deviations are listed in the self-review note at the end of this plan). Plans are persisted with `model_dump(mode="json")` and loaded with `model_validate`.
- **All state writes use `jobfit.atomic_io.write_json_atomic`.** Plans go under `jobfit/data/scrape_plans/` (committed), page snapshots under `jobfit/cache/listing_snapshots/` (committed), the detail-page cache under `jobfit/cache/pages/` (gitignored like the rest of `cache/` except the two committed dirs — check `.gitignore` in Task 10).
- **Tests live under `jobfit/server/tests/`** and run via `uv run python -m pytest jobfit/server/tests -q`. 315 tests pass at the start of this plan; run the full suite after every task. No test touches the network: every fetcher in a test is `FakePageFetcher` or a monkeypatched `session`.
- **Every test that writes plans, snapshots or company files monkeypatches** `config.SCRAPE_PLANS_DIR`, `config.LISTING_SNAPSHOTS_DIR`, `config.PAGE_CACHE_DIR`, `config.LINK_REJECTS_PATH` and/or `update_jobs.COMPANIES_DIR` to a `tmp_path`.
- **Company identity is still the display name in this plan.** `CompanyScrapeService.scrape(company, career_url)` takes the same display-name string `update_jobs` uses today; plan files are keyed by `jobfit/scrape/ids.plan_id_for(company)`, which is byte-for-byte the same function as `update_jobs._snake_case` (Task 10 tests that). Plan C replaces both with the registry id.
- **Line numbers below refer to the files as of commit `989da17`** (the head after Plan A). Re-check them with Grep before editing if anything has moved.
- **Model id for discovery:** `config.SCRAPE_PLAN_LLM_MODEL = "claude-haiku-4-5"` (the user asked for "a small llm"; the approved spec says Haiku-class). Do not silently substitute another model.

## Scope note: three task groups, one file

This plan is larger than one comfortable sitting. It is split into three groups that each end in a working, tested tree; execute them in order, and treat each group's last task as a natural checkpoint for review:

- **Group A — Skeleton, behaviour-preserving (Tasks 1–8, spec step 5).** Adds `jobfit/scrape/` models, fetchers, candidate extraction, filters, ATS clients, enrichment and strategies. Tasks 1–4 and 6–8 are pure additions. Task 5 is the one behaviour-affecting task: it re-points `ats_fetchers.fetch_listing_links` and `playwright_listings.extract_job_links` at the new extractor + filter chain (configured to accept exactly what today's heuristics accept) and deletes `listing_heuristics.py`. The old cascade in `update_jobs.fetch_company_jobs_async` still runs unchanged at the end of Group A.
- **Group B — Plans, rules classifier, health policy, service wiring (Tasks 9–14, spec step 6).** Adds `HealthPolicy`, `PlanStore`, `RulesPlanClassifier`, `StrategyFactory`, `CompanyScrapeService`, the planner/inducer/validator (probes + rules only), and swaps `update_jobs` onto the service. This is where the CV-score tier gate (`_any_job_scores_positive` and friends) is deleted, per spec section 4.5 and step 6.
- **Group C — LLM discovery (Tasks 15–17, spec step 7).** Adds the `anthropic` dependency, `AnthropicLLMClient`, `LLMPlanClassifier`, the `--discover`/`--discover-max`/`--rediscover`/`--plans` CLI, the discovery batch with budget and cooldown, the audit script, `link_rejects.json`, and the snapshot replay test.

## File map

| Path | Responsibility | Task |
|---|---|---|
| `jobfit/scrape/__init__.py` | Package marker (docstring only) | 1 |
| `jobfit/scrape/models.py` | Every pydantic model: `Page`, `Candidate`, `Evidence`, `JobPosting`, the `Strategy` union, `Labels`, `ScrapePlan`, `ScrapeResult`, … | 1 |
| `jobfit/scrape/errors.py` | `FetchFailed`, `PlanInvalid`, `ClassifierFailed` | 2 |
| `jobfit/scrape/fetchers.py` | `PageFetcher` ABC, `HttpPageFetcher`, `PlaywrightPageFetcher`, `CachedPageFetcher`, `PageFetcherFactory`, `visible_text()` | 2 |
| `jobfit/scrape/candidates.py` | `CandidateExtractor`, `link_title_text()`, `href_shape()` | 3 |
| `jobfit/scrape/filters.py` | `Verdict`, `LinkFilter` ABC, the seven concrete filters, `FilterChain` | 4 |
| `jobfit/ats_fetchers.py` (modify) | `fetch_listing_links` delegates to extractor + chain; `_contains_a_job_link` uses `DenylistFilter.text_ok`; `parse_job_details_html` split out of `fetch_generic_job_details` | 5, 7 |
| `jobfit/scripts/playwright_listings.py` (modify) | `extract_job_links` delegates to extractor + chain | 5 |
| `jobfit/listing_heuristics.py` + its test (delete) | Replaced by `filters.py` | 5 |
| `jobfit/scrape/ats/__init__.py`, `base.py`, `clients.py` | `AtsClient` ABC, `AtsRegistry`, five provider clients | 6 |
| `jobfit/scrape/enrich.py` | `DetailEnricher` ABC, `GenericHtmlEnricher`, `NoopEnricher`, `extract_evidence()` | 7 |
| `jobfit/scrape/strategies.py` | `ScrapeStrategy` ABC and the seven concrete strategies | 8 |
| `jobfit/scrape/health.py` | `HealthPolicy` | 9 |
| `jobfit/scrape/ids.py`, `jobfit/scrape/plan_store.py` | `plan_id_for()`, `PlanStore` ABC, `FilePlanStore`, `MemoryPlanStore` | 10 |
| `jobfit/scrape/classifiers.py` | `PlanClassifier` ABC, `RulesPlanClassifier`, `RecordedPlanClassifier` (Task 11), `LLMPlanClassifier` (Task 15) | 11, 15 |
| `jobfit/scrape/factory.py` | `StrategyFactory` | 12 |
| `jobfit/scrape/service.py`, `jobfit/scrape/bootstrap.py` | `CompanyScrapeService`; `build_scrape_service()`, `build_discovery_planner()` | 13, 16 |
| `jobfit/scripts/update_jobs.py` (modify) | Service shim, `job_evidence`, delete the three tier-gate helpers, CLI flags, `discover_plans()` | 13, 16 |
| `jobfit/scoring.py` (modify) | `_looks_unparseable(job_req, evidence=None)` | 13 |
| `jobfit/scrape/planner.py` | `ScrapePlanner`, `PlanInducer`, `PlanValidator` | 14 |
| `jobfit/scrape/llm_client.py` | `LLMClient` protocol, `AnthropicLLMClient` | 15 |
| `jobfit/scripts/audit_scrape.py` | Suspicion ranking + `link_rejects.json` decisions | 17 |
| `jobfit/config.py` (modify) | New paths and constants (listed in Task 10 and Task 15) | 10, 15 |

---

## Group A — Skeleton, behaviour-preserving

### Task 1: Models

**Files:**
- Create: `jobfit/scrape/__init__.py`
- Create: `jobfit/scrape/models.py`
- Test: `jobfit/server/tests/test_scrape_models.py`

**Interfaces:**
- Produces: every model below, imported everywhere else as `from jobfit.scrape.models import ...`. `SCRAPE_PLAN_SCHEMA_VERSION: int = 1`. `Strategy` is a discriminated union on `kind`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_models.py
"""The scrape package's data models: plain pydantic, round-trippable
through JSON (plans are stored on disk as JSON and loaded back), with a
discriminated union for the per-company strategy so a plan file's
"kind" field alone picks the right model."""

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jobfit.scrape import models


def test_strategy_union_round_trips_each_kind_through_json():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    strategies = [
        models.AtsApiStrategy(provider="greenhouse", board="acme", board_url="https://boards.greenhouse.io/acme"),
        models.ExternalBoardStrategy(board_url="https://jobs.lever.co/acme"),
        models.HtmlListingStrategy(renderer="http", include_url=r"^https://acme\.com/careers/[a-z0-9-]+$"),
        models.SpecialCaseStrategy(host_fragment="elbitsystemscareer.com"),
        models.TechmapOnlyStrategy(reason="no career url"),
        models.BrokenUrlStrategy(reason="http 404"),
    ]
    for strategy in strategies:
        plan = models.ScrapePlan(
            company_id="acme", career_url="https://acme.com/careers", derived_by="probe",
            derived_at=now, status="verified", strategy=strategy,
        )
        dumped = json.dumps(plan.model_dump(mode="json"))
        loaded = models.ScrapePlan.model_validate(json.loads(dumped))
        assert loaded.strategy == strategy
        assert loaded.strategy.kind == strategy.kind


def test_unknown_strategy_kind_is_rejected():
    with pytest.raises(ValidationError):
        models.ScrapePlan.model_validate({
            "company_id": "acme", "career_url": None, "derived_by": "probe",
            "derived_at": "2026-09-28T00:00:00Z", "status": "verified",
            "strategy": {"kind": "magic"},
        })


def test_scrape_plan_defaults():
    plan = models.ScrapePlan(
        company_id="acme", career_url=None, derived_by="probe",
        derived_at=datetime(2026, 9, 28, tzinfo=timezone.utc), status="verified",
        strategy=models.TechmapOnlyStrategy(reason="no career url"),
    )
    assert plan.schema_version == models.SCRAPE_PLAN_SCHEMA_VERSION == 1
    assert plan.health.consecutive_empty_runs == 0
    assert plan.labels is None
    assert plan.notes == []
    assert plan.rediscover_after is None


def test_job_posting_defaults_and_evidence():
    posting = models.JobPosting(title="Backend Engineer", url="https://acme.com/careers/1", source="html_listing")
    assert posting.description == ""
    assert posting.evidence is None
    evidence = models.Evidence(jsonld_jobposting=True, url_shape="acme.com|careers|2")
    assert evidence.apply_cta is False
    assert evidence.requirement_sections == 0


def test_labels_require_a_page_verdict_from_the_fixed_set():
    with pytest.raises(ValidationError):
        models.Labels(page_verdict="maybe", candidates=[])
    labels = models.Labels(page_verdict="careers_page", candidates=[models.CandidateLabel(index=0, is_job=True, reason="ok")])
    assert labels.container_selector is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape'`

- [ ] **Step 3: Write the package marker and the models**

```python
# jobfit/scrape/__init__.py
"""Plan-driven company scraping.

Discovery (explicit `update_jobs --discover`) reads a company's career page
once - deterministic probes first, a small LLM only when the probes could
not decide - and stores a ScrapePlan. Runtime (every ordinary update) loads
that plan and executes it in pure Python: no model call anywhere on this
path. See docs/superpowers/specs/2026-09-28-scrape-compute-pipeline-redesign-design.md,
sections 2.4, 3 and 4.
"""
```

```python
# jobfit/scrape/models.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_models.py -q`
Expected: 5 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 320 passed (315 + 5)

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/__init__.py jobfit/scrape/models.py jobfit/server/tests/test_scrape_models.py
git commit -m "feat(scrape): add the scrape package's pydantic models (Page, Candidate, Evidence, JobPosting, Strategy union, Labels, ScrapePlan)"
```

---

### Task 2: Errors and page fetchers

**Files:**
- Create: `jobfit/scrape/errors.py`
- Create: `jobfit/scrape/fetchers.py`
- Test: `jobfit/server/tests/test_scrape_fetchers.py`

**Interfaces:**
- Consumes: `models.Page`, `ats_fetchers.USER_AGENT`, `ats_fetchers.TIMEOUT`.
- Produces: `errors.FetchFailed(RuntimeError)`, `errors.PlanInvalid(ValueError)`, `errors.ClassifierFailed(RuntimeError)`; `fetchers.PageFetcher` (ABC, `fetch(self, url: str) -> Page`, raises `FetchFailed` on network error/timeout/blocked; returns a `Page` with its HTTP `status` for every real HTTP response, including 404 and 5xx — callers decide what a status means); `fetchers.HttpPageFetcher(session)`; `fetchers.PlaywrightPageFetcher(user_agent=..., nav_timeout_ms=15000, settle_ms=2000)`; `fetchers.CachedPageFetcher(inner, cache_dir: Path, ttl_hours: float)`; `fetchers.PageFetcherFactory(session, playwright_available: bool = True)` with `build(renderer) -> PageFetcher`; `fetchers.visible_text(html) -> str`; `fetchers.looks_like_js_shell(html, text) -> bool`; `fetchers.make_page(requested_url, final_url, status, html, renderer, now) -> Page`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_fetchers.py
"""PageFetcher implementations. The HTTP fetcher is exercised with a fake
requests session; Playwright is never launched in tests (the factory is
asked for it with playwright_available=False to prove the fallback)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from jobfit.scrape import errors, fetchers
from jobfit.scrape.models import Page


class _Resp:
    def __init__(self, status, text, url):
        self.status_code = status
        self.text = text
        self.url = url


class _Session:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if self.error:
            raise self.error
        return self.response


def test_http_fetcher_returns_a_page_with_status_and_visible_text():
    html = "<html><head><script>x()</script><style>a{}</style></head><body><nav>Menu</nav><p>Hello  world</p></body></html>"
    session = _Session(_Resp(200, html, "https://acme.com/careers/"))
    page = fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")
    assert isinstance(page, Page)
    assert page.status == 200
    assert page.url == "https://acme.com/careers/"
    assert page.requested_url == "https://acme.com/careers"
    assert page.text == "Menu Hello world"
    assert page.renderer == "http"
    assert page.is_js_shell is False


def test_http_fetcher_returns_404_pages_rather_than_raising():
    session = _Session(_Resp(404, "<html><body>gone</body></html>", "https://acme.com/careers"))
    page = fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")
    assert page.status == 404


def test_http_fetcher_raises_fetch_failed_on_network_error():
    session = _Session(error=requests.ConnectionError("boom"))
    with pytest.raises(errors.FetchFailed):
        fetchers.HttpPageFetcher(session).fetch("https://acme.com/careers")


def test_js_shell_detection():
    shell = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
    assert fetchers.looks_like_js_shell(shell, fetchers.visible_text(shell)) is True
    real = "<html><body><main>" + "<p>Backend Engineer - Tel Aviv</p>" * 40 + "</main></body></html>"
    assert fetchers.looks_like_js_shell(real, fetchers.visible_text(real)) is False


def test_cached_fetcher_serves_a_fresh_entry_without_calling_inner(tmp_path):
    session = _Session(_Resp(200, "<p>one</p>", "https://acme.com/j/1"))
    inner = fetchers.HttpPageFetcher(session)
    cached = fetchers.CachedPageFetcher(inner, tmp_path, ttl_hours=1)
    first = cached.fetch("https://acme.com/j/1")
    second = cached.fetch("https://acme.com/j/1")
    assert first.html == second.html == "<p>one</p>"
    assert len(session.calls) == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_cached_fetcher_refetches_an_expired_entry(tmp_path):
    session = _Session(_Resp(200, "<p>one</p>", "https://acme.com/j/1"))
    cached = fetchers.CachedPageFetcher(fetchers.HttpPageFetcher(session), tmp_path, ttl_hours=1)
    cached.fetch("https://acme.com/j/1")
    entry_path = next(tmp_path.glob("*.json"))
    entry = json.loads(entry_path.read_text(encoding="utf-8"))
    entry["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    entry_path.write_text(json.dumps(entry), encoding="utf-8")
    cached.fetch("https://acme.com/j/1")
    assert len(session.calls) == 2


def test_cached_fetcher_does_not_cache_failures(tmp_path):
    session = _Session(error=requests.Timeout("slow"))
    cached = fetchers.CachedPageFetcher(fetchers.HttpPageFetcher(session), tmp_path, ttl_hours=1)
    with pytest.raises(errors.FetchFailed):
        cached.fetch("https://acme.com/j/1")
    assert list(tmp_path.glob("*.json")) == []


def test_factory_builds_http_and_falls_back_to_http_when_playwright_is_unavailable():
    factory = fetchers.PageFetcherFactory(session=_Session(), playwright_available=False)
    assert isinstance(factory.build("http"), fetchers.HttpPageFetcher)
    assert isinstance(factory.build("playwright"), fetchers.HttpPageFetcher)


def test_factory_builds_a_playwright_fetcher_when_available():
    factory = fetchers.PageFetcherFactory(session=_Session(), playwright_available=True)
    assert isinstance(factory.build("playwright"), fetchers.PlaywrightPageFetcher)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_fetchers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.errors'`

- [ ] **Step 3: Write errors.py and fetchers.py**

```python
# jobfit/scrape/errors.py
"""Exceptions shared across the scrape package."""


class FetchFailed(RuntimeError):
    """A listing page, detail page or ATS board could not be fetched at all
    (network error, timeout, WAF block, 5xx). Propagates out of
    CompanyScrapeService.scrape so update_jobs._process_company records a
    failure and leaves the company file untouched - a transient outage must
    never close every stored job for a company."""


class PlanInvalid(ValueError):
    """A stored plan cannot be turned into a runnable strategy (e.g. an
    external_board URL no ATS client recognizes any more)."""


class ClassifierFailed(RuntimeError):
    """A PlanClassifier could not produce valid Labels (API error, schema
    validation failed twice, unknown candidate index). The planner falls
    back to RulesPlanClassifier when it sees this."""
```

```python
# jobfit/scrape/fetchers.py
"""Fetching a page as a `Page` model: plain HTTP, a headless browser, and a
TTL cache decorator over either. The extractor and enricher never know
which renderer produced the page they are given."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.atomic_io import write_json_atomic
from jobfit.scrape.errors import FetchFailed
from jobfit.scrape.models import Page, Renderer

_JS_SHELL_MARKERS = ('id="root"', 'id="app"', 'id="__next"', "__NEXT_DATA__", 'id="___gatsby"', "data-reactroot")
JS_SHELL_MAX_TEXT = 500


def visible_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001 - malformed markup must not break a fetch
        return ""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def looks_like_js_shell(html: str, text: str) -> bool:
    return len(text) < JS_SHELL_MAX_TEXT and any(marker in html for marker in _JS_SHELL_MARKERS)


def make_page(requested_url: str, final_url: str, status: int, html: str, renderer: Renderer, now: datetime | None = None) -> Page:
    text = visible_text(html)
    return Page(
        url=final_url or requested_url, requested_url=requested_url, status=status, html=html, text=text,
        renderer=renderer, fetched_at=now or datetime.now(timezone.utc), is_js_shell=looks_like_js_shell(html, text),
    )


class PageFetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> Page:
        """Return a Page for any real HTTP response (the caller inspects
        `status`); raise FetchFailed when no response could be obtained."""


class HttpPageFetcher(PageFetcher):
    def __init__(self, session: requests.Session, timeout: float = ats_fetchers.TIMEOUT):
        self.session = session
        self.timeout = timeout

    def fetch(self, url: str) -> Page:
        try:
            response = self.session.request("GET", url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as error:
            raise FetchFailed(f"GET {url}: {error}") from error
        return make_page(url, getattr(response, "url", url), response.status_code, response.text or "", "http")


class PlaywrightPageFetcher(PageFetcher):
    """Headless Chromium render. Runs its own event loop on a dedicated
    thread so it works whether the caller is plain sync code or already
    inside a running asyncio loop (update_jobs' worker threads are the
    former today; the old cascade was the latter)."""

    def __init__(self, user_agent: str = ats_fetchers.USER_AGENT, nav_timeout_ms: int = 15000, settle_ms: int = 2000):
        self.user_agent = user_agent
        self.nav_timeout_ms = nav_timeout_ms
        self.settle_ms = settle_ms

    async def _fetch_async(self, url: str) -> Page:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=self.user_agent)
            page = await context.new_page()
            try:
                response = await page.goto(url, timeout=self.nav_timeout_ms, wait_until="domcontentloaded")
                await page.wait_for_timeout(self.settle_ms)
                html = await page.content()
                final_url = page.url
                status = response.status if response is not None else 200
            finally:
                await browser.close()
        preview = visible_text(html)[:200].lower()
        if "access denied" in preview or "captcha" in preview:
            raise FetchFailed(f"{url}: blocked (WAF/captcha)")
        return make_page(url, final_url, status, html, "playwright")

    def fetch(self, url: str) -> Page:
        result: dict = {}

        def runner():
            try:
                result["page"] = asyncio.run(self._fetch_async(url))
            except Exception as error:  # noqa: BLE001 - surfaced below as FetchFailed
                result["error"] = error

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            error = result["error"]
            if isinstance(error, FetchFailed):
                raise error
            raise FetchFailed(f"playwright {url}: {error}") from error
        return result["page"]


class CachedPageFetcher(PageFetcher):
    """Decorator: serves a page from cache_dir when its entry is younger
    than ttl_hours, otherwise fetches through `inner` and stores the
    result. Failures are never cached."""

    def __init__(self, inner: PageFetcher, cache_dir: Path, ttl_hours: float, now: Callable[[], datetime] | None = None):
        self.inner = inner
        self.cache_dir = cache_dir
        self.ttl = timedelta(hours=ttl_hours)
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _path(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()}.json"

    def fetch(self, url: str) -> Page:
        path = self._path(url)
        if path.exists():
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                fetched_at = datetime.fromisoformat(entry["fetched_at"])
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                if self.now() - fetched_at < self.ttl:
                    return Page.model_validate(entry["page"])
            except (OSError, ValueError, KeyError):
                pass
        page = self.inner.fetch(url)
        write_json_atomic(path, {"fetched_at": page.fetched_at.isoformat(), "page": page.model_dump(mode="json")})
        return page


class PageFetcherFactory:
    def __init__(self, session: requests.Session, playwright_available: bool = True):
        self.session = session
        self.playwright_available = playwright_available

    def build(self, renderer: Renderer) -> PageFetcher:
        if renderer == "playwright" and self.playwright_available:
            return PlaywrightPageFetcher()
        return HttpPageFetcher(self.session)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_fetchers.py -q`
Expected: 9 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 329 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/errors.py jobfit/scrape/fetchers.py jobfit/server/tests/test_scrape_fetchers.py
git commit -m "feat(scrape): add PageFetcher (http, playwright, cached) and the package's exceptions"
```

---

### Task 3: Candidate extraction

**Files:**
- Create: `jobfit/scrape/candidates.py`
- Test: `jobfit/server/tests/test_scrape_candidates.py`

**Interfaces:**
- Consumes: `models.Page`, `models.Candidate`, `ats_fetchers._is_cookie_widget`, `jobfit.ats_scorer.taxonomy.load_role_families`.
- Produces: `candidates.link_title_text(a) -> str` (moved verbatim from `ats_fetchers._link_title_text`), `candidates.href_shape(url) -> str`, `candidates.JOB_URL_HINT_RE`, `candidates.CandidateExtractor().extract(page, career_url, container_selector=None, cap=200) -> list[Candidate]`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_candidates.py
"""CandidateExtractor turns a Page into the feature table every filter,
the rules classifier, and the LLM prompt all read from. It is the only
place those features are computed."""

from datetime import datetime, timezone

from jobfit.scrape.candidates import CandidateExtractor, href_shape, link_title_text
from jobfit.scrape.fetchers import make_page

CAREER_URL = "https://acme.com/careers/"


def _page(html, url=CAREER_URL):
    return make_page(url, url, 200, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_href_shape_uses_host_parent_segments_and_depth():
    assert href_shape("https://www.acme.com/careers/backend-engineer") == "acme.com|careers|2"
    assert href_shape("https://acme.com/careers/eng/123/backend/all") == "acme.com|careers/eng/123/backend|5"
    assert href_shape("https://acme.com/") == "acme.com||0"


def test_href_shape_for_a_flat_query_string_scheme_uses_the_sorted_query_keys():
    assert href_shape("https://careers.checkpoint.com/index.php?a=show&joborderid=1") == "careers.checkpoint.com|?a,joborderid"


def test_extracts_features_for_each_anchor():
    html = """
    <html><body>
      <nav><a href="/about">About Us Page</a></nav>
      <main><ul>
        <li><a href="/careers/backend-engineer-123">Senior Backend Engineer</a></li>
        <li><a href="/careers/frontend-engineer-124">Frontend Engineer</a></li>
        <li><a href="/careers/devops-engineer-125">DevOps Engineer</a></li>
      </ul></main>
      <footer><a href="https://docs.acme.com/x">Code Governance and Compliance</a></footer>
    </body></html>
    """
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL)
    by_text = {c.text: c for c in candidates}
    assert [c.index for c in candidates] == [0, 1, 2, 3, 4]

    backend = by_text["Senior Backend Engineer"]
    assert backend.href == "https://acme.com/careers/backend-engineer-123"
    assert backend.same_host is True
    assert backend.under_career_path is True
    assert backend.has_job_url_hint is True
    assert backend.in_chrome is False
    assert backend.sibling_anchor_count == 3
    assert backend.ancestor_path.endswith("main>ul>li>a")
    assert backend.href_shape == "acme.com|careers|2"
    assert backend.role_family is not None

    about = by_text["About Us Page"]
    assert about.in_chrome is True
    assert about.under_career_path is False

    docs = by_text["Code Governance and Compliance"]
    assert docs.same_host is False
    assert docs.in_chrome is True
    assert docs.has_job_url_hint is False


def test_skips_fragment_javascript_and_mailto_links_and_dedupes_by_absolute_url():
    html = """
    <a href="#top">Top of page link</a>
    <a href="javascript:void(0)">Open the menu now</a>
    <a href="mailto:jobs@acme.com">Email us about jobs</a>
    <a href="/careers/one">Backend Engineer</a>
    <a href="https://acme.com/careers/one">Backend Engineer</a>
    """
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL)
    assert [c.href for c in candidates] == ["https://acme.com/careers/one"]


def test_container_selector_scopes_the_search_and_falls_back_to_the_whole_page_when_it_matches_nothing():
    html = """
    <div class="jobs"><a href="/careers/one">Backend Engineer</a></div>
    <div class="menu"><a href="/pricing">Pricing and Plans</a></div>
    """
    scoped = CandidateExtractor().extract(_page(html), CAREER_URL, container_selector="div.jobs")
    assert [c.text for c in scoped] == ["Backend Engineer"]
    fallback = CandidateExtractor().extract(_page(html), CAREER_URL, container_selector="div.nope")
    assert len(fallback) == 2


def test_prefers_a_nested_heading_over_the_whole_card_text():
    from bs4 import BeautifulSoup
    a = BeautifulSoup('<a href="/x"><h2>Senior Backend Developer</h2><span>Engineering Israel Apply Now</span></a>', "html.parser").a
    assert link_title_text(a) == "Senior Backend Developer"


def test_cookie_widget_links_are_dropped_and_cap_is_respected():
    html = '<div class="cookiebot"><a href="/cookies-policy">Cookie Preferences Center</a></div>' + "".join(
        f'<a href="/careers/job-{i}">Engineer number {i}</a>' for i in range(10)
    )
    candidates = CandidateExtractor().extract(_page(html), CAREER_URL, cap=4)
    assert len(candidates) == 4
    assert all("cookies-policy" not in c.href for c in candidates)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_candidates.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.candidates'`

- [ ] **Step 3: Write candidates.py**

```python
# jobfit/scrape/candidates.py
"""Page -> list[Candidate]: every anchor on a listing page with the
features the filters, the rules classifier and the LLM prompt all use.
Pure and deterministic. Parses the UNSTRIPPED DOM (page.html) so in_chrome
and ancestor_path can be computed; only cookie-consent widgets are removed
up front, since nothing inside one is ever a job link."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.ats_scorer.taxonomy import load_role_families
from jobfit.scrape.models import Candidate, Page

# A job-indicating path token, or a run of 3+ digits (a job/req id - real
# postings are routinely id-numbered even when the surrounding path has no
# English job word at all, e.g. Check Point's ?joborderid=0936589).
JOB_URL_HINT_RE = re.compile(r"(job|career|position|opening|vacan|opportunit|\d{3,})", re.I)
_CHROME_TAGS = {"nav", "header", "footer"}
_SKIP_SCHEMES = ("#", "javascript:", "mailto:", "tel:")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def link_title_text(a) -> str:
    """Prefer a heading element's own text over the whole anchor's text. Many
    career-page builders wrap an entire job card - title, department tag,
    location, a description snippet, an "Apply Now" CTA - in one <a>, and
    a.get_text() then concatenates all of it into one garbled "title" (real
    example caught live: Adaptive6's Webflow careers page renders
    "Senior Backend Developer Engineering Israel Apply Now" as the link text,
    even though the real title lives cleanly in a nested <h2>). Falls back to
    the whole anchor's text when no heading is nested inside it."""
    heading = a.find(["h1", "h2", "h3", "h4", "h5", "h6"])
    if heading:
        heading_text = _clean(heading.get_text(" "))
        if heading_text:
            return heading_text
    return _clean(a.get_text(" "))


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def href_shape(url: str) -> str:
    """'<host>|<parent segments joined by />|<depth>' for path-style URLs;
    '<host>|?<sorted query keys>' for flat query-string schemes (a path of
    at most one segment plus a query)."""
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.query and len(segments) <= 1:
        keys = sorted(parse_qs(parts.query, keep_blank_values=True))
        return f"{_host(url)}|?{','.join(keys)}"
    return f"{_host(url)}|{'/'.join(segments[:-1])}|{len(segments)}"


class CandidateExtractor:
    def extract(self, page: Page, career_url: str, container_selector: str | None = None, cap: int = 200) -> list[Candidate]:
        try:
            soup = BeautifulSoup(page.html, "html.parser")
        except Exception:  # noqa: BLE001 - malformed markup yields no candidates, not a crash
            return []
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        for el in list(soup.find_all(ats_fetchers._is_cookie_widget)):
            if el.parent is not None:
                el.decompose()

        root = soup
        if container_selector:
            try:
                scoped = soup.select_one(container_selector)
            except Exception:  # noqa: BLE001 - an invalid selector means "whole page"
                scoped = None
            if scoped is not None:
                root = scoped

        career_host = _host(career_url)
        career_path = urlsplit(career_url).path.rstrip("/")
        families = load_role_families()

        seen: set[str] = set()
        out: list[Candidate] = []
        for a in root.find_all("a", href=True):
            href = (a["href"] or "").strip()
            if not href or href.lower().startswith(_SKIP_SCHEMES):
                continue
            text = link_title_text(a)
            if not text:
                continue
            absolute = urljoin(page.url, href)
            if absolute in seen:
                continue
            seen.add(absolute)

            ancestors = [p for p in reversed(list(a.parents)) if p.name and p.name != "[document]"]
            ancestor_path = ">".join(p.name for p in ancestors) + ">a"
            in_chrome = any(
                p.name in _CHROME_TAGS or (p.get("role") or "").lower() == "navigation" for p in ancestors
            )
            # "Repeated structure": how many anchors under this link's
            # grandparent (the <ul> for a <li><a>, the card grid for a
            # <div><a>) share its URL shape - a listing is a list of
            # same-shaped links; a lone marketing slug is not.
            shape = href_shape(absolute)
            scope = a.parent.parent if a.parent is not None and a.parent.parent is not None else a.parent
            sibling_anchor_count = 1
            if scope is not None:
                sibling_anchor_count = sum(
                    1 for other in scope.find_all("a", href=True)
                    if href_shape(urljoin(page.url, (other["href"] or "").strip())) == shape
                )
            path = urlsplit(absolute).path.rstrip("/")
            out.append(Candidate(
                index=len(out), text=text, href=absolute, ancestor_path=ancestor_path,
                sibling_anchor_count=max(1, sibling_anchor_count),
                same_host=_host(absolute) == career_host,
                under_career_path=bool(career_path) and path.startswith(career_path) and path != career_path,
                has_job_url_hint=JOB_URL_HINT_RE.search(absolute) is not None,
                role_family=families.classify(text),
                in_chrome=in_chrome,
                href_shape=shape,
            ))
            if len(out) >= cap:
                break
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_candidates.py -q`
Expected: 7 passed. If `test_extracts_features_for_each_anchor` fails on `role_family is not None`, check `jobfit/ats_scorer/data/role_families.json` classifies "Senior Backend Engineer" (it does today: "backend"); do not weaken the assertion.

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 336 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/candidates.py jobfit/server/tests/test_scrape_candidates.py
git commit -m "feat(scrape): add CandidateExtractor - the one place link features are computed"
```

---

### Task 4: Link filters and the filter chain

**Files:**
- Create: `jobfit/scrape/filters.py`
- Test: `jobfit/server/tests/test_scrape_filters.py`

**Interfaces:**
- Consumes: `models.Candidate`.
- Produces: `filters.Verdict` (pydantic: `accept: bool, filter_name: str, reason: str`); `filters.LinkFilter` (ABC with class attribute `name: str` and `accept(self, candidate, batch) -> Verdict | None`; `None` = no opinion, any `Verdict` is final); `filters.DenylistFilter` (+ `DenylistFilter.text_ok(text) -> bool`, the old `looks_like_job_title`), `filters.HrefMarkerFilter` (+ `NON_JOB_LINK_HREF_MARKERS`), `filters.RejectListFilter(patterns: list[str])`, `filters.CategoryPrefixFilter`, `filters.PlanPatternFilter(include_url, exclude_url, explicit_accept)`, `filters.UrlShapeClusterFilter(expected_shape: str | None)`, `filters.EvidenceThresholdFilter(min_signals=2, reject_chrome=True)`, `filters.FilterChain(filters).run(batch) -> tuple[list[Candidate], list[tuple[Candidate, Verdict]]]` (deny by default: a candidate no filter accepts is rejected with `filter_name="chain"`).

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_filters.py
"""Each LinkFilter in isolation, then the chain. The cases here are the
ones test_listing_heuristics.py used to pin (that file is deleted in
Task 5) plus the new evidence/shape filters."""

from jobfit.scrape import filters
from jobfit.scrape.models import Candidate


def _cand(text="Senior Backend Engineer", href="https://acme.com/careers/backend-1", index=0, **overrides):
    base = dict(
        index=index, text=text, href=href, ancestor_path="body>main>ul>li>a", sibling_anchor_count=3,
        same_host=True, under_career_path=True, has_job_url_hint=True, role_family="backend",
        in_chrome=False, href_shape="acme.com|careers|2",
    )
    base.update(overrides)
    return Candidate(**base)


# --- DenylistFilter (the old looks_like_job_title) -------------------------

def test_text_ok_accepts_a_real_title_and_rejects_nav_phrases_lengths_emails_urls():
    ok = filters.DenylistFilter.text_ok
    assert ok("Senior Backend Engineer") is True
    assert ok("Learn More") is False
    assert ok("View All") is False
    assert ok("White Papers") is False
    assert ok("Case Studies") is False
    assert ok("QA") is False
    assert ok("x" * 130) is False
    assert ok("jobs@acme.com") is False
    assert ok("12345678") is False
    assert ok("https://www.simplex-mapping.com/") is False
    assert ok("www.example.com/careers") is False


def test_text_ok_accepts_a_hebrew_title():
    assert filters.DenylistFilter.text_ok("מהנדס תוכנה בכיר") is True


def test_denylist_filter_rejects_bad_text_and_has_no_opinion_on_good_text():
    f = filters.DenylistFilter()
    assert f.accept(_cand(text="Learn More"), []).accept is False
    assert f.accept(_cand(), []) is None


# --- HrefMarkerFilter ------------------------------------------------------

def test_href_marker_filter_rejects_maps_docs_blog_resources():
    f = filters.HrefMarkerFilter()
    for href in (
        "https://www.google.com/maps/place/HaMasger+St+35", "https://maps.google.com/?q=Tel+Aviv", "https://goo.gl/maps/abc123",
        "https://coralogix.com/docs/opentelemetry/getting-started/", "https://acme.com/blog/how-we-scaled", "https://acme.com/resources/whitepaper",
    ):
        assert f.accept(_cand(href=href), []).accept is False, href
    assert f.accept(_cand(href="https://acme.com/careers/backend-engineer"), []) is None


# --- RejectListFilter ------------------------------------------------------

def test_reject_list_filter_uses_regexes():
    f = filters.RejectListFilter([r"^https://copyleaks\.com/[a-z0-9-]+$"])
    assert f.accept(_cand(href="https://copyleaks.com/code-governance-and-compliance"), []).accept is False
    assert f.accept(_cand(href="https://copyleaks.com/careers/backend-1"), []) is None


# --- CategoryPrefixFilter (the old drop_category_prefix_links) --------------

def test_category_prefix_filter_drops_an_overview_that_is_a_prefix_of_a_sibling_posting():
    overview = _cand(text="Engineering Jobs", href="https://acme.com/careers/engineering/all", index=0)
    posting = _cand(href="https://acme.com/careers/engineering/123/backend-engineer/all", index=1)
    f = filters.CategoryPrefixFilter()
    assert f.accept(overview, [overview, posting]).accept is False
    assert f.accept(posting, [overview, posting]) is None


def test_category_prefix_filter_keeps_flat_query_string_links_and_unrelated_siblings():
    a = _cand(href="https://careers.checkpoint.com/index.php?a=show&joborderid=1", index=0)
    b = _cand(href="https://careers.checkpoint.com/index.php?a=show&joborderid=2", index=1)
    f = filters.CategoryPrefixFilter()
    assert f.accept(a, [a, b]) is None and f.accept(b, [a, b]) is None
    c = _cand(href="https://acme.com/careers/eng/1/backend-engineer", index=0)
    d = _cand(href="https://acme.com/careers/eng/2/frontend-engineer", index=1)
    assert f.accept(c, [c, d]) is None and f.accept(d, [c, d]) is None


# --- PlanPatternFilter -----------------------------------------------------

def test_plan_pattern_filter_excludes_then_includes_then_explicit_then_no_opinion():
    f = filters.PlanPatternFilter(
        include_url=r"^https://acme\.com/careers/[a-z0-9-]+$", exclude_url=[r"^https://acme\.com/[a-z0-9-]+$"],
        explicit_accept=["https://acme.com/jobs/special"],
    )
    assert f.accept(_cand(href="https://acme.com/code-governance"), []).accept is False
    assert f.accept(_cand(href="https://acme.com/careers/backend-1"), []).accept is True
    assert f.accept(_cand(href="https://acme.com/jobs/special"), []).accept is True
    assert f.accept(_cand(href="https://acme.com/team/people/dan"), []) is None


# --- UrlShapeClusterFilter -------------------------------------------------

def test_url_shape_filter_with_an_expected_shape_accepts_matches_only():
    f = filters.UrlShapeClusterFilter("acme.com|careers|2")
    assert f.accept(_cand(), []).accept is True
    assert f.accept(_cand(href_shape="acme.com||1"), []) is None


def test_url_shape_filter_in_batch_mode_rejects_a_singleton_shape_without_a_job_hint():
    jobs = [_cand(index=i, href=f"https://acme.com/careers/job-{i}") for i in range(3)]
    marketing = _cand(index=3, text="Code Governance and Compliance", href="https://acme.com/code-governance", href_shape="acme.com||1", has_job_url_hint=False, role_family=None, under_career_path=False)
    f = filters.UrlShapeClusterFilter(None)
    batch = jobs + [marketing]
    assert f.accept(marketing, batch).accept is False
    assert f.accept(jobs[0], batch) is None


def test_url_shape_filter_in_batch_mode_keeps_a_singleton_that_has_a_job_hint():
    jobs = [_cand(index=i, href=f"https://acme.com/careers/job-{i}") for i in range(3)]
    odd = _cand(index=3, href="https://acme.com/jobs/12345", href_shape="acme.com|jobs|2")
    assert filters.UrlShapeClusterFilter(None).accept(odd, jobs + [odd]) is None


# --- EvidenceThresholdFilter -----------------------------------------------

def test_evidence_threshold_accepts_with_two_signals_and_rejects_chrome():
    f = filters.EvidenceThresholdFilter(min_signals=2, reject_chrome=True)
    assert f.accept(_cand(), []).accept is True
    weak = _cand(same_host=False, under_career_path=False, has_job_url_hint=False, role_family=None, sibling_anchor_count=1)
    assert f.accept(weak, []) is None
    assert f.accept(_cand(in_chrome=True), []).accept is False


def test_evidence_threshold_at_zero_signals_with_chrome_allowed_accepts_everything():
    f = filters.EvidenceThresholdFilter(min_signals=0, reject_chrome=False)
    assert f.accept(_cand(in_chrome=True, same_host=False, role_family=None), []).accept is True


# --- FilterChain -----------------------------------------------------------

def test_chain_stops_at_the_first_verdict_and_denies_by_default():
    chain = filters.FilterChain([filters.DenylistFilter(), filters.HrefMarkerFilter()])
    good, bad_text, docs = _cand(index=0), _cand(index=1, text="Learn More"), _cand(index=2, href="https://acme.com/docs/x")
    accepted, rejected = chain.run([good, bad_text, docs])
    assert accepted == []  # nothing accepted it, so deny by default
    reasons = {c.index: v.filter_name for c, v in rejected}
    assert reasons == {0: "chain", 1: "denylist", 2: "href_marker"}


def test_chain_accepts_when_a_filter_accepts_and_preserves_order():
    chain = filters.FilterChain([filters.DenylistFilter(), filters.EvidenceThresholdFilter(min_signals=0, reject_chrome=False)])
    a, b = _cand(index=0), _cand(index=1, href="https://acme.com/careers/x-2")
    accepted, rejected = chain.run([b, a])
    assert [c.index for c in accepted] == [1, 0]
    assert rejected == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_filters.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.filters'`

- [ ] **Step 3: Write filters.py**

```python
# jobfit/scrape/filters.py
"""LinkFilters decide, per candidate link, "job posting or not". Each
filter returns a final Verdict or None (no opinion); FilterChain runs
them in order and DENIES BY DEFAULT - a candidate no filter accepts is
rejected. Hard rejects (denylist, href markers, the audit's reject list,
category overviews) come first, then the plan's own patterns, then the
generic evidence filters that act as the drift safety net."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter
from urllib.parse import urlsplit

from pydantic import BaseModel

from jobfit.scrape.models import Candidate

NAV_DENYLIST = re.compile(
    r"^(home|about|contact( us)?|privacy( policy)?|terms( of (use|service))?|cookies?( policy)?|sign ?in|log ?in|"
    r"register|blog|news|press|resources?|white papers?|case stud(y|ies)|"
    r"investors?|sustainability|diversity|benefits?|life at|culture|our (team|story|values)|"
    r"locations?|offices?|leadership|board|help|faq|support|search( jobs?)?|filter|sort by|share|"
    r"apply( now| today)?|view all|see all|view (open )?positions?|view listing|browse all|"
    r"\+? ?view more positions?|learn more|read more( ?>)?|back to|skip to|menu|toggle|close|"
    r"let'?s talk|follow (us|gett .*)|submit (cv|resume)|eeo is the law|job search|"
    r"linkedin|facebook|twitter|instagram|youtube)$",
    re.I,
)
_FORM_TOKEN_RE = re.compile(r"^\[#|#\]$")
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+\??$")
_URL_TEXT_RE = re.compile(r"^(https?://|www\.)", re.I)

# Substrings that mark a link destination as never a job posting regardless
# of its text - office links to Google Maps; a shared site-wide footer's
# docs/blog/legal/press links whose anchor text ("OpenTelemetry", "Code
# Governance & Compliance" - both caught live) reads like a plausible title.
NON_JOB_LINK_HREF_MARKERS = (
    "google.com/maps", "maps.google.com", "goo.gl/maps",
    "/docs/", "/documentation/", "/blog/", "/resources/", "/resource-library/",
    "/legal/", "/trust-center/", "/security-center/", "/press/", "/newsroom/",
    "/case-studies/", "/case-study/", "/webinars/", "/community/", "/partners/",
)


class Verdict(BaseModel):
    accept: bool
    filter_name: str
    reason: str


class LinkFilter(ABC):
    name: str = "filter"

    @abstractmethod
    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None: ...

    def _reject(self, reason: str) -> Verdict:
        return Verdict(accept=False, filter_name=self.name, reason=reason)

    def _accept(self, reason: str) -> Verdict:
        return Verdict(accept=True, filter_name=self.name, reason=reason)


class DenylistFilter(LinkFilter):
    name = "denylist"

    @staticmethod
    def text_ok(text: str) -> bool:
        text = (text or "").strip()
        if not (8 <= len(text) <= 120):
            return False
        if NAV_DENYLIST.match(text):
            return False
        if _FORM_TOKEN_RE.search(text) or _EMAIL_RE.match(text):
            return False
        if _URL_TEXT_RE.match(text):
            return False
        # Hebrew words carry no vowels, so they run shorter than the Latin
        # 3-letter floor (Elbit Systems Sigmabit's site is entirely Hebrew).
        if not (re.search(r"[A-Za-z]{3,}", text) or re.search(r"[א-ת]{2,}", text)):
            return False
        return True

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        return None if self.text_ok(candidate.text) else self._reject("nav/boilerplate text")


class HrefMarkerFilter(LinkFilter):
    name = "href_marker"

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        href = candidate.href.lower()
        for marker in NON_JOB_LINK_HREF_MARKERS:
            if marker in href:
                return self._reject(f"href contains {marker!r}")
        return None


class RejectListFilter(LinkFilter):
    name = "reject_list"

    def __init__(self, patterns: list[str]):
        self.patterns = [re.compile(p) for p in patterns]

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        for pattern in self.patterns:
            if pattern.search(candidate.href):
                return self._reject(f"matches reject pattern {pattern.pattern!r}")
        return None


def _parent_segments(url: str) -> tuple[str, ...]:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return tuple(segments[:-1])


class CategoryPrefixFilter(LinkFilter):
    """Reject a department/category overview link whose parent directory
    is a strict prefix of a sibling candidate's parent directory (the
    overview .../careers/engineering/all vs its postings
    .../careers/engineering/<id>/<slug>/all). An empty parent (flat
    query-string schemes like Check Point's index.php?joborderid=N) is
    never "more general" than anything."""
    name = "category_prefix"

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        mine = _parent_segments(candidate.href)
        if not mine:
            return None
        for other in batch:
            if other.index == candidate.index:
                continue
            theirs = _parent_segments(other.href)
            if len(mine) < len(theirs) and theirs[: len(mine)] == mine:
                return self._reject("category overview of a sibling posting")
        return None


class PlanPatternFilter(LinkFilter):
    name = "plan_pattern"

    def __init__(self, include_url: str | None, exclude_url: list[str], explicit_accept: list[str]):
        self.include = re.compile(include_url) if include_url else None
        self.excludes = [re.compile(p) for p in exclude_url]
        self.explicit = set(explicit_accept)

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        for pattern in self.excludes:
            if pattern.search(candidate.href):
                return self._reject(f"plan exclude {pattern.pattern!r}")
        if self.include is not None and self.include.search(candidate.href):
            return self._accept("plan include_url")
        if candidate.href in self.explicit:
            return self._accept("plan explicit_accept")
        return None


class UrlShapeClusterFilter(LinkFilter):
    """With an expected shape (from a plan): accept candidates of that
    shape. Without one (rules-only): reject a candidate whose shape is a
    singleton in the batch AND lacks a job-url hint - the flat marketing
    slug next to a cluster of /careers/<slug> links."""
    name = "url_shape"

    def __init__(self, expected_shape: str | None):
        self.expected_shape = expected_shape

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        if self.expected_shape is not None:
            return self._accept("matches plan url_shape") if candidate.href_shape == self.expected_shape else None
        counts = Counter(c.href_shape for c in batch)
        if counts[candidate.href_shape] == 1 and not candidate.has_job_url_hint and len(batch) >= 3:
            return self._reject("singleton url shape without a job hint")
        return None


class EvidenceThresholdFilter(LinkFilter):
    name = "evidence"
    SIGNALS = ("same_host", "under_career_path", "has_job_url_hint", "role_family", "siblings")

    def __init__(self, min_signals: int = 2, reject_chrome: bool = True):
        self.min_signals = min_signals
        self.reject_chrome = reject_chrome

    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None:
        if candidate.in_chrome and self.reject_chrome:
            return self._reject("inside nav/header/footer")
        signals = sum([
            candidate.same_host, candidate.under_career_path, candidate.has_job_url_hint,
            candidate.role_family is not None, candidate.sibling_anchor_count >= 3,
        ])
        if signals >= self.min_signals:
            return self._accept(f"{signals} positive signals")
        return None


class FilterChain:
    def __init__(self, filters: list[LinkFilter]):
        self.filters = filters

    def run(self, batch: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, Verdict]]]:
        accepted: list[Candidate] = []
        rejected: list[tuple[Candidate, Verdict]] = []
        for candidate in batch:
            verdict = None
            for f in self.filters:
                verdict = f.accept(candidate, batch)
                if verdict is not None:
                    break
            if verdict is None:
                verdict = Verdict(accept=False, filter_name="chain", reason="no filter accepted this link")
            (accepted if verdict.accept else rejected).append(candidate if verdict.accept else (candidate, verdict))
        return accepted, rejected
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_filters.py -q`
Expected: 17 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 353 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/filters.py jobfit/server/tests/test_scrape_filters.py
git commit -m "feat(scrape): add LinkFilter chain (denylist, href markers, reject list, category prefix, plan pattern, url shape, evidence)"
```

---

### Task 5: Re-point the legacy listing paths at the extractor + chain; delete `listing_heuristics.py`

This is the only behaviour-affecting task in Group A. The chain used here (`filters.legacy_listing_chain()`) is configured to accept exactly what today's heuristics accept: denylist text, href markers, category-prefix overviews rejected; everything else accepted (`EvidenceThresholdFilter(min_signals=0, reject_chrome=False)`). Today's `_strip_boilerplate` removal of nav/header/footer/form blocks "that hold no job-looking link" is equivalent under this chain (every link in such a block fails the denylist anyway), and cookie widgets are removed by the extractor.

**Files:**
- Modify: `jobfit/scrape/filters.py` (add `legacy_listing_chain()`)
- Modify: `jobfit/ats_fetchers.py:365-367` (`_contains_a_job_link`), `:543-559` (`_link_title_text`, delete), `:617-677` (`fetch_listing_links`)
- Modify: `jobfit/scripts/playwright_listings.py:34-47` (imports), `:72-109` (`extract_job_links`)
- Delete: `jobfit/listing_heuristics.py`, `jobfit/server/tests/test_listing_heuristics.py`
- Test: `jobfit/server/tests/test_scrape_legacy_paths.py`

**Interfaces:**
- Consumes: `candidates.CandidateExtractor`, `candidates.link_title_text`, `fetchers.make_page`, `filters.*`.
- Produces: `filters.legacy_listing_chain() -> FilterChain`. `ats_fetchers.fetch_listing_links(session, url, max_links=8) -> list[tuple[str, str]]` keeps its signature and return type. `playwright_listings.extract_job_links(page, base_url) -> list[tuple[str, str]]` keeps its signature.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_legacy_paths.py
"""The two legacy listing entry points (plain-HTTP fetch_listing_links and
the Playwright extract_job_links) now delegate to CandidateExtractor +
legacy_listing_chain, so they agree by construction. These tests pin the
behaviour the old listing_heuristics.py tests pinned."""

import asyncio

from jobfit import ats_fetchers
from jobfit.scrape import filters
from jobfit.scripts import playwright_listings


class _FakeHtmlResponse:
    def __init__(self, text, url="https://acme.com/careers/"):
        self.text = text
        self.url = url
        self.status_code = 200


def test_legacy_chain_accepts_everything_not_explicitly_rejected():
    names = [f.name for f in filters.legacy_listing_chain().filters]
    assert names == ["denylist", "href_marker", "category_prefix", "evidence"]
    evidence = filters.legacy_listing_chain().filters[-1]
    assert evidence.min_signals == 0 and evidence.reject_chrome is False


def test_fetch_listing_links_rejects_docs_maps_and_category_overviews_and_keeps_real_jobs(monkeypatch):
    """Behaviour-preserving: the nav's "About Us Page" is still accepted
    here, exactly as before (the old _strip_boilerplate kept a <nav> that
    held any job-looking link, and this one does). Group B's evidence
    chain is what finally rejects nav links; this task only pins today's
    behaviour under the new machinery."""
    html = """
    <nav><a href="/about">About Us Page</a><a href="https://coralogix.com/docs/opentelemetry/">OpenTelemetry</a></nav>
    <a href="/careers/engineering/all">Engineering Roles</a>
    <a href="/careers/engineering/123/backend-engineer/all">Backend Engineer</a>
    <a href="https://www.google.com/maps/place/HaMasger+St+35">HaMasger St 35, Tel Aviv</a>
    """
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeHtmlResponse(html))
    links = ats_fetchers.fetch_listing_links(session=None, url="https://acme.com/careers/", max_links=8)
    assert links == [
        ("About Us Page", "https://acme.com/about"),
        ("Backend Engineer", "https://acme.com/careers/engineering/123/backend-engineer/all"),
    ]


def test_fetch_listing_links_keeps_flat_query_string_jobs(monkeypatch):
    html = """
    <a href="index.php?a=show&joborderid=1">Administrative Assistant</a>
    <a href="index.php?a=show&joborderid=2">Backend Developer</a>
    """
    monkeypatch.setattr(ats_fetchers, "_request", lambda *a, **kw: _FakeHtmlResponse(html, "https://careers.checkpoint.com/index.php"))
    links = ats_fetchers.fetch_listing_links(session=None, url="https://careers.checkpoint.com/index.php?q=", max_links=8)
    assert [t for t, _ in links] == ["Administrative Assistant", "Backend Developer"]


def test_contains_a_job_link_uses_the_denylist_text_rule():
    from bs4 import BeautifulSoup
    nav = BeautifulSoup('<nav><a href="/x">Learn More</a></nav>', "html.parser").nav
    assert ats_fetchers._contains_a_job_link(nav) is False
    section = BeautifulSoup('<header><a href="/x">Senior Backend Developer</a></header>', "html.parser").header
    assert ats_fetchers._contains_a_job_link(section) is True


class _FakePwPage:
    def __init__(self, html, url):
        self._html = html
        self.url = url

    async def content(self):
        return self._html


def test_playwright_extract_job_links_uses_the_same_chain():
    html = """
    <footer><a href="/blog/how-we-scaled">How We Scaled</a></footer>
    <a href="/careers/one"><h2>Senior Backend Developer</h2><span>Engineering Israel Apply Now</span></a>
    """
    page = _FakePwPage(html, "https://acme.com/careers/")
    links = asyncio.run(playwright_listings.extract_job_links(page, "https://acme.com/careers/"))
    assert links == [("Senior Backend Developer", "https://acme.com/careers/one")]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_legacy_paths.py -q`
Expected: FAIL with `AttributeError: module 'jobfit.scrape.filters' has no attribute 'legacy_listing_chain'`

- [ ] **Step 3: Add `legacy_listing_chain` to filters.py**

Append to `jobfit/scrape/filters.py`:

```python
def legacy_listing_chain() -> FilterChain:
    """Exactly what the pre-plan heuristics accepted: reject denylisted
    text, non-job href markers and category overviews; accept everything
    else. Used by the two legacy listing entry points until Group B wires
    the plan-driven chain, and by RulesPlanClassifier as its base."""
    return FilterChain([
        DenylistFilter(), HrefMarkerFilter(), CategoryPrefixFilter(),
        EvidenceThresholdFilter(min_signals=0, reject_chrome=False),
    ])
```

- [ ] **Step 4: Rewrite `fetch_listing_links` and `_contains_a_job_link`, delete `_link_title_text`**

In `jobfit/ats_fetchers.py`, replace lines 365-367 with:

```python
def _contains_a_job_link(tag) -> bool:
    from jobfit.scrape.candidates import link_title_text
    from jobfit.scrape.filters import DenylistFilter
    return any(DenylistFilter.text_ok(link_title_text(a)) for a in tag.find_all("a", href=True))
```

Delete `_link_title_text` (lines 543-559; its body now lives in `jobfit/scrape/candidates.py` as `link_title_text`). Replace `fetch_listing_links` (lines 617-677) with:

```python
def fetch_listing_links(session: requests.Session, url: str, max_links: int = 8) -> list[tuple[str, str]]:
    """Pull candidate (title, absolute_url) job links off a career listing page.

    Delegates to jobfit.scrape's CandidateExtractor + legacy_listing_chain
    so the plain-HTTP and Playwright paths agree by construction. Same-host
    links are ordered first (stable) before the cap is applied: a shared
    marketing mega-menu (Check Point's careers.checkpoint.com page links
    out to www.checkpoint.com ahead of its own results) would otherwise
    fill the cap before a single real job link was reached.
    """
    from jobfit.scrape.candidates import CandidateExtractor
    from jobfit.scrape.fetchers import make_page
    from jobfit.scrape.filters import legacy_listing_chain

    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return []
    response = _request(session, "GET", url)
    if response is None:
        return []
    page = make_page(url, getattr(response, "url", url) or url, getattr(response, "status_code", 200), response.text or "", "http")
    candidates = CandidateExtractor().extract(page, url, cap=max_links * 4)
    accepted, _ = legacy_listing_chain().run(candidates)
    accepted.sort(key=lambda c: not c.same_host)
    return [(c.text, c.href) for c in accepted[:max_links]]
```

- [ ] **Step 5: Rewrite `extract_job_links` in playwright_listings.py**

Replace the imports at lines 40-47 with:

```python
from jobfit import config  # noqa: E402
from jobfit.ats_fetchers import COOKIE_WIDGET_MARKERS, looks_like_boilerplate  # noqa: E402
from jobfit.scrape.candidates import CandidateExtractor  # noqa: E402
from jobfit.scrape.fetchers import make_page  # noqa: E402
from jobfit.scrape.filters import legacy_listing_chain  # noqa: E402


def _clean(text: str) -> str:
    return " ".join((text or "").split())
```

Replace `extract_job_links` (lines 72-109) with:

```python
async def extract_job_links(page, base_url: str) -> list[tuple[str, str]]:
    """Return [(title, absolute_url), ...] from the rendered DOM, through
    the same CandidateExtractor + legacy_listing_chain the plain-HTTP path
    uses, so the two never drift apart again."""
    html = await page.content()
    rendered = make_page(base_url, page.url or base_url, 200, html, "playwright")
    candidates = CandidateExtractor().extract(rendered, base_url, cap=MAX_JOB_LINKS_PER_COMPANY * 4)
    accepted, _ = legacy_listing_chain().run(candidates)
    return [(c.text, c.href) for c in accepted[:MAX_JOB_LINKS_PER_COMPANY]]
```

- [ ] **Step 6: Delete the old module and its tests**

```bash
git rm jobfit/listing_heuristics.py jobfit/server/tests/test_listing_heuristics.py
```

Then Grep for `listing_heuristics` across `jobfit/` — the only remaining mention must be in comments/docstrings (fix any import you find; `jobfit/scripts/company_career_scrape.py` only calls `fetch_listing_links`, which is fine).

- [ ] **Step 7: Run the new tests and the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_legacy_paths.py -q`
Expected: 5 passed

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 340 passed (353 - 18 deleted listing_heuristics tests + 5). `test_ats_fetchers.py::test_fetch_listing_links_prioritizes_same_host_links_over_a_mega_menu` must still pass unchanged.

- [ ] **Step 8: Commit**

```bash
git add jobfit/scrape/filters.py jobfit/ats_fetchers.py jobfit/scripts/playwright_listings.py jobfit/server/tests/test_scrape_legacy_paths.py
git commit -m "refactor(scrape): route fetch_listing_links and the Playwright extractor through CandidateExtractor + FilterChain; delete listing_heuristics.py"
```

---

### Task 6: ATS clients and registry

**Files:**
- Create: `jobfit/scrape/ats/__init__.py`, `jobfit/scrape/ats/base.py`, `jobfit/scrape/ats/clients.py`
- Test: `jobfit/server/tests/test_scrape_ats.py`

**Interfaces:**
- Consumes: `ats_fetchers.fetch_greenhouse/fetch_lever/fetch_ashby/fetch_workable/fetch_comeet`, `ats_fetchers.comeet_board_url`, `ats_fetchers.fetch_comeet_hosted_page`, `models.JobPosting`, `errors.FetchFailed`, `errors.PlanInvalid`.
- Produces: `ats.AtsClient` (ABC: `provider: str`, `patterns: tuple[re.Pattern, ...]`, `__init__(session)`, `match(url) -> str | None`, `board_url(board) -> str`, `fetch_board(board, known_url=None, source="ats_api") -> list[JobPosting]` raising `FetchFailed` when the provider returns nothing at all); `ats.AtsRegistry(clients)` with `resolve(url) -> tuple[AtsClient, str] | None`, `client(provider) -> AtsClient` (raises `PlanInvalid` for an unknown provider), `providers() -> list[str]`; `ats.to_posting(item: dict, source) -> JobPosting | None`; `ats.default_registry(session) -> AtsRegistry`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_ats.py
"""AtsClient per provider (each owns its URL patterns - the old
TOKEN_PATTERNS list split by provider) and the registry that resolves a
URL to (client, board)."""

import pytest

from jobfit import ats_fetchers
from jobfit.scrape import errors
from jobfit.scrape.ats import AtsRegistry, default_registry, to_posting
from jobfit.scrape.ats.clients import ComeetClient, GreenhouseClient


@pytest.mark.parametrize("url, provider, board", [
    ("https://boards.greenhouse.io/acme/jobs/12345", "greenhouse", "acme"),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", "greenhouse", "acme"),
    ("https://jobs.lever.co/acme/abc123-def456", "lever", "acme"),
    ("https://jobs.ashbyhq.com/acme/abcdef", "ashby", "acme"),
    ("https://www.comeet.com/jobs/acme/12.345/some-job/67.890", "comeet", "acme"),
    ("https://apply.workable.com/acme/j/ABCDEF1234/", "workable", "acme"),
    ("https://acme.workable.com", "workable", "acme"),
])
def test_registry_resolves_known_board_urls(url, provider, board):
    client, token = default_registry(session=None).resolve(url)
    assert (client.provider, token) == (provider, board)


def test_registry_returns_none_for_unknown_or_empty_urls():
    registry = default_registry(session=None)
    assert registry.resolve("https://acme.com/careers/backend-engineer") is None
    assert registry.resolve(None) is None
    assert registry.resolve("") is None


def test_registry_client_lookup_and_unknown_provider():
    registry = default_registry(session=None)
    assert registry.client("lever").provider == "lever"
    assert registry.providers() == ["greenhouse", "lever", "ashby", "workable", "comeet"]
    with pytest.raises(errors.PlanInvalid):
        registry.client("taleo")


def test_board_urls():
    registry = default_registry(session=None)
    assert registry.client("greenhouse").board_url("acme") == "https://boards.greenhouse.io/acme"
    assert registry.client("lever").board_url("acme") == "https://jobs.lever.co/acme"
    assert registry.client("ashby").board_url("acme") == "https://jobs.ashbyhq.com/acme"
    assert registry.client("workable").board_url("acme") == "https://apply.workable.com/acme/"
    assert registry.client("comeet").board_url("acme") == "https://www.comeet.com/jobs/acme"


def test_to_posting_maps_fields_and_skips_untitled_items():
    posting = to_posting({
        "title": "Backend Engineer", "location": "Tel Aviv", "url": "https://x/1", "description": "build",
        "department": "Eng", "employment_type": "Full-time", "posted_at": "2026-06-15", "_ats": "greenhouse",
    }, "ats_api")
    assert posting.title == "Backend Engineer" and posting.source == "ats_api" and posting.department == "Eng"
    assert to_posting({"title": "", "url": "https://x"}, "ats_api") is None


def test_fetch_board_wraps_the_provider_fetcher(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: [
        {"title": "Backend Engineer", "url": "https://boards.greenhouse.io/acme/jobs/1", "description": "x"},
        {"title": "", "url": None},
    ])
    postings = GreenhouseClient(session=None).fetch_board("acme")
    assert [p.title for p in postings] == ["Backend Engineer"]
    assert postings[0].source == "ats_api"


def test_fetch_board_raises_fetch_failed_when_the_provider_returns_none(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_greenhouse", lambda session, token: None)
    with pytest.raises(errors.FetchFailed):
        GreenhouseClient(session=None).fetch_board("acme")


def test_comeet_falls_back_to_the_hosted_page_when_the_api_is_empty(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_comeet", lambda session, token: [])
    monkeypatch.setattr(ats_fetchers, "fetch_comeet_hosted_page", lambda session, url: [
        {"title": "Data Engineer", "url": "https://www.comeet.com/jobs/acme/87.00D/data/1"},
    ])
    postings = ComeetClient(session=None).fetch_board("acme", known_url="https://www.comeet.com/jobs/acme/87.00D/some-job/2")
    assert [p.title for p in postings] == ["Data Engineer"]


def test_fetch_board_can_tag_postings_as_external_board(monkeypatch):
    monkeypatch.setattr(ats_fetchers, "fetch_lever", lambda session, token: [{"title": "QA Engineer", "url": "https://jobs.lever.co/acme/1"}])
    postings = default_registry(session=None).client("lever").fetch_board("acme", source="external_board")
    assert postings[0].source == "external_board"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_ats.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.ats'`

- [ ] **Step 3: Write the ats package**

```python
# jobfit/scrape/ats/__init__.py
"""Direct-API clients for the ATS providers jobfit knows, and the registry
that resolves a URL to (client, board). Adding a provider = one subclass
in clients.py + one entry in default_registry()."""

from jobfit.scrape.ats.base import AtsClient, AtsRegistry, to_posting
from jobfit.scrape.ats.clients import AshbyClient, ComeetClient, GreenhouseClient, LeverClient, WorkableClient


def default_registry(session) -> AtsRegistry:
    return AtsRegistry([
        GreenhouseClient(session), LeverClient(session), AshbyClient(session), WorkableClient(session), ComeetClient(session),
    ])


__all__ = ["AtsClient", "AtsRegistry", "to_posting", "default_registry",
           "GreenhouseClient", "LeverClient", "AshbyClient", "WorkableClient", "ComeetClient"]
```

```python
# jobfit/scrape/ats/base.py
from __future__ import annotations

import re
from abc import ABC, abstractmethod

from jobfit.scrape.errors import FetchFailed, PlanInvalid
from jobfit.scrape.models import JobPosting, PostingSource


def to_posting(item: dict, source: PostingSource) -> JobPosting | None:
    title = " ".join((item.get("title") or "").split())
    if not title:
        return None
    return JobPosting(
        title=title, url=item.get("url"), location=item.get("location"),
        description=item.get("description") or "", department=item.get("department"),
        employment_type=item.get("employment_type"), posted_at=item.get("posted_at"), source=source,
    )


class AtsClient(ABC):
    provider: str = ""
    patterns: tuple[re.Pattern, ...] = ()

    def __init__(self, session):
        self.session = session

    def match(self, url: str | None) -> str | None:
        if not url:
            return None
        for pattern in self.patterns:
            m = pattern.search(url)
            if m:
                return m.group(1)
        return None

    @abstractmethod
    def board_url(self, board: str) -> str: ...

    @abstractmethod
    def _fetch_raw(self, board: str, known_url: str | None) -> list[dict] | None: ...

    def fetch_board(self, board: str, known_url: str | None = None, source: PostingSource = "ats_api") -> list[JobPosting]:
        raw = self._fetch_raw(board, known_url)
        if raw is None:
            raise FetchFailed(f"{self.provider} board {board!r}: no response")
        postings = [to_posting(item, source) for item in raw]
        return [p for p in postings if p is not None]


class AtsRegistry:
    def __init__(self, clients: list[AtsClient]):
        self._clients = clients
        self._by_provider = {c.provider: c for c in clients}

    def resolve(self, url: str | None) -> tuple[AtsClient, str] | None:
        for client in self._clients:
            board = client.match(url)
            if board:
                return client, board
        return None

    def client(self, provider: str) -> AtsClient:
        try:
            return self._by_provider[provider]
        except KeyError:
            raise PlanInvalid(f"unknown ATS provider {provider!r}") from None

    def providers(self) -> list[str]:
        return [c.provider for c in self._clients]
```

```python
# jobfit/scrape/ats/clients.py
"""One AtsClient per provider. Each owns its URL patterns (formerly
ats_fetchers.TOKEN_PATTERNS) and wraps the existing raw fetcher."""

from __future__ import annotations

import re

from jobfit import ats_fetchers
from jobfit.scrape.ats.base import AtsClient


class GreenhouseClient(AtsClient):
    provider = "greenhouse"
    patterns = (
        re.compile(r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)", re.I),
        re.compile(r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?([A-Za-z0-9_-]+)", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://boards.greenhouse.io/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_greenhouse(self.session, board)


class LeverClient(AtsClient):
    provider = "lever"
    patterns = (re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.lever.co/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_lever(self.session, board)


class AshbyClient(AtsClient):
    provider = "ashby"
    patterns = (re.compile(r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://jobs.ashbyhq.com/{board}"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_ashby(self.session, board)


class WorkableClient(AtsClient):
    provider = "workable"
    patterns = (
        re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I),
        re.compile(r"https?://([A-Za-z0-9_-]+)\.workable\.com", re.I),
    )

    def board_url(self, board: str) -> str:
        return f"https://apply.workable.com/{board}/"

    def _fetch_raw(self, board, known_url):
        return ats_fetchers.fetch_workable(self.session, board)


class ComeetClient(AtsClient):
    provider = "comeet"
    patterns = (re.compile(r"comeet\.com/jobs(?:-api/[0-9.]+/company)?/([A-Za-z0-9_-]+)", re.I),)

    def board_url(self, board: str) -> str:
        return f"https://www.comeet.com/jobs/{board}"

    def _fetch_raw(self, board, known_url):
        jobs = ats_fetchers.fetch_comeet(self.session, board)
        if not jobs and known_url:
            hosted = ats_fetchers.comeet_board_url(known_url) or known_url
            jobs = ats_fetchers.fetch_comeet_hosted_page(self.session, hosted)
        return jobs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_ats.py -q`
Expected: 15 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 355 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/ats jobfit/server/tests/test_scrape_ats.py
git commit -m "feat(scrape): add AtsClient per provider and AtsRegistry (replaces TOKEN_PATTERNS/ATS_FETCHERS dispatch)"
```

---

### Task 7: Detail enrichment and evidence

**Files:**
- Modify: `jobfit/ats_fetchers.py:493-540` (split `parse_job_details_html` out of `fetch_generic_job_details`)
- Create: `jobfit/scrape/enrich.py`
- Test: `jobfit/server/tests/test_scrape_enrich.py`

**Interfaces:**
- Consumes: `ats_fetchers._jsonld_job_postings`, `ats_fetchers._SKIP_GENERIC_FETCH_HOSTS`, `fetchers.PageFetcher`, `fetchers.visible_text`, `candidates.href_shape`, `jobfit.ats_scorer.jd_extractor._split_sections`, `jobfit.ats_scorer.taxonomy.load_role_families`.
- Produces: `ats_fetchers.parse_job_details_html(html: str) -> dict` (keys `description, location, employment_type, posted_at`; `fetch_generic_job_details` now calls it); `enrich.extract_evidence(html, title, url) -> Evidence`; `enrich.minimal_evidence(title, url, description) -> Evidence`; `enrich.DetailEnricher` (ABC, `enrich(posting) -> JobPosting`); `enrich.GenericHtmlEnricher(fetcher: PageFetcher)`; `enrich.NoopEnricher()`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_enrich.py
"""Detail-page enrichment: description/location/etc. from the posting's
own page (the old fetch_generic_job_details) plus the Evidence record the
health policy, the unparseable-job gate and the audit all read."""

from datetime import datetime, timezone

import pytest

from jobfit import ats_fetchers
from jobfit.scrape import enrich, errors
from jobfit.scrape.fetchers import PageFetcher, make_page
from jobfit.scrape.models import JobPosting

JOB_HTML = """
<html><head><script type="application/ld+json">{"@type": "JobPosting", "title": "Backend Engineer",
 "description": "<p>Requirements: 5+ years Python. Nice to have: Kubernetes.</p>", "datePosted": "2026-06-01",
 "employmentType": "FULL_TIME", "jobLocation": {"address": {"addressLocality": "Tel Aviv", "addressCountry": "IL"}}}</script></head>
<body><h1>Backend Engineer</h1><p>Requirements: 5+ years Python. Nice to have: Kubernetes.</p><a href="/apply/1">Apply now</a></body></html>
"""


class FakePageFetcher(PageFetcher):
    def __init__(self, pages: dict[str, tuple[int, str]], fail: set[str] = frozenset()):
        self.pages = pages
        self.fail = fail
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if url in self.fail:
            raise errors.FetchFailed(url)
        status, html = self.pages[url]
        return make_page(url, url, status, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_parse_job_details_html_extracts_jsonld_fields():
    details = ats_fetchers.parse_job_details_html(JOB_HTML)
    assert details["location"] == "Tel Aviv, IL"
    assert details["employment_type"] == "FULL_TIME"
    assert details["posted_at"] == "2026-06-01"
    assert "5+ years Python" in details["description"]


def test_extract_evidence_reads_jsonld_apply_cta_sections_role_family_and_shape():
    evidence = enrich.extract_evidence(JOB_HTML, "Backend Engineer", "https://acme.com/careers/backend-1")
    assert evidence.jsonld_jobposting is True
    assert evidence.apply_cta is True
    assert evidence.requirement_sections >= 1
    assert evidence.role_family_from_title == "backend"
    assert evidence.url_shape == "acme.com|careers|2"


def test_extract_evidence_on_a_marketing_page_is_empty():
    html = "<html><body><h1>Code Governance and Compliance</h1><p>Our platform helps teams ship safely.</p></body></html>"
    evidence = enrich.extract_evidence(html, "Code Governance and Compliance", "https://copyleaks.com/code-governance-and-compliance")
    assert evidence.jsonld_jobposting is False and evidence.apply_cta is False
    assert evidence.requirement_sections == 0 and evidence.role_family_from_title is None


def test_generic_enricher_fills_description_fields_and_evidence():
    fetcher = FakePageFetcher({"https://acme.com/careers/backend-1": (200, JOB_HTML)})
    posting = JobPosting(title="Backend Engineer", url="https://acme.com/careers/backend-1", source="html_listing")
    enriched = enrich.GenericHtmlEnricher(fetcher).enrich(posting)
    assert enriched.location == "Tel Aviv, IL" and enriched.posted_at == "2026-06-01"
    assert "Python" in enriched.description
    assert enriched.evidence.jsonld_jobposting is True
    assert posting.description == ""  # the input was not mutated


def test_generic_enricher_keeps_the_posting_on_fetch_failure_or_4xx_or_skipped_host():
    fetcher = FakePageFetcher({"https://acme.com/careers/gone": (404, "<p>gone</p>")}, fail={"https://acme.com/careers/down"})
    for url in ("https://acme.com/careers/gone", "https://acme.com/careers/down", "https://www.linkedin.com/jobs/view/1"):
        posting = JobPosting(title="Backend Engineer", url=url, source="html_listing")
        enriched = enrich.GenericHtmlEnricher(fetcher).enrich(posting)
        assert enriched.description == ""
        assert enriched.evidence.role_family_from_title == "backend"
    assert "https://www.linkedin.com/jobs/view/1" not in fetcher.calls


def test_noop_enricher_adds_minimal_evidence_only_when_missing():
    posting = JobPosting(title="Backend Engineer", url="https://boards.greenhouse.io/acme/jobs/1",
                         description="Requirements: Python", source="ats_api")
    enriched = enrich.NoopEnricher().enrich(posting)
    assert enriched.evidence.role_family_from_title == "backend"
    assert enriched.evidence.requirement_sections >= 1
    assert enriched.evidence.jsonld_jobposting is False
    again = enrich.NoopEnricher().enrich(enriched)
    assert again.evidence == enriched.evidence
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_enrich.py -q`
Expected: FAIL with `AttributeError: module 'jobfit.ats_fetchers' has no attribute 'parse_job_details_html'`

- [ ] **Step 3: Split `parse_job_details_html` out of `fetch_generic_job_details`**

In `jobfit/ats_fetchers.py`, replace `fetch_generic_job_details` (lines 493-540) with:

```python
_EMPTY_DETAILS = {"description": "", "location": None, "employment_type": None, "posted_at": None}


def parse_job_details_html(html: str) -> dict:
    """description/location/employment_type/posted_at from a job page's
    HTML: the page's own schema.org JobPosting JSON-LD when present (many
    career-page builders emit it for Google for Jobs), else the visible
    text with site chrome stripped. Pure - no network."""
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001 - malformed HTML must not break the pipeline
        return dict(_EMPTY_DETAILS)

    postings = _jsonld_job_postings(soup)
    posting = postings[0] if postings else None

    description = ""
    if posting and posting.get("description"):
        try:
            description = _clean(BeautifulSoup(str(posting["description"]), "html.parser").get_text(" "))[:6000]
        except Exception:  # noqa: BLE001
            description = ""
    if not description:
        _strip_boilerplate(soup)
        text = _clean(soup.get_text(" "))
        description = "" if looks_like_boilerplate(text) else text[:6000]

    if not posting:
        return {**_EMPTY_DETAILS, "description": description}

    employment_type = posting.get("employmentType")
    if isinstance(employment_type, list):
        employment_type = employment_type[0] if employment_type else None

    return {
        "description": description,
        "location": _jsonld_location(posting),
        "employment_type": _clean(str(employment_type)) if employment_type else None,
        "posted_at": _posted_date(posting.get("datePosted")),
    }


def fetch_generic_job_details(session: requests.Session, url: str) -> dict:
    """Fetch a job's own page and parse it - see parse_job_details_html.
    Skips known dead ends (linkedin.com, comeet.com job pages)."""
    if not url or any(host in url.lower() for host in _SKIP_GENERIC_FETCH_HOSTS):
        return dict(_EMPTY_DETAILS)
    response = _request(session, "GET", url)
    if response is None:
        return dict(_EMPTY_DETAILS)
    return parse_job_details_html(response.text)
```

- [ ] **Step 4: Write enrich.py**

```python
# jobfit/scrape/enrich.py
"""Fill a JobPosting's description fields from its own page and attach
the Evidence record: JSON-LD JobPosting, an apply CTA, requirement-style
section headers, the title's role family, the URL's shape."""

from __future__ import annotations

from abc import ABC, abstractmethod

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.ats_scorer.jd_extractor import _split_sections
from jobfit.ats_scorer.taxonomy import load_role_families
from jobfit.scrape.candidates import href_shape
from jobfit.scrape.errors import FetchFailed
from jobfit.scrape.fetchers import PageFetcher, visible_text
from jobfit.scrape.models import Evidence, JobPosting

_REQ_BUCKETS = ("must", "nice", "responsibility")


def _requirement_sections(text: str) -> int:
    sections = _split_sections(text or "")
    return sum(1 for bucket in _REQ_BUCKETS if sections.get(bucket))


def minimal_evidence(title: str, url: str | None, description: str = "") -> Evidence:
    return Evidence(
        role_family_from_title=load_role_families().classify(title or ""),
        url_shape=href_shape(url) if url else "",
        requirement_sections=_requirement_sections(description),
    )


def _has_apply_cta(soup: BeautifulSoup) -> bool:
    for tag in soup.find_all(["a", "button", "input"]):
        haystack = " ".join([
            tag.get_text(" ") if tag.name != "input" else "", tag.get("href") or "", tag.get("value") or "", tag.get("id") or "",
            " ".join(tag.get("class") or []),
        ]).lower()
        if "apply" in haystack:
            return True
    return False


def extract_evidence(html: str, title: str, url: str | None) -> Evidence:
    try:
        soup = BeautifulSoup(html or "", "html.parser")
    except Exception:  # noqa: BLE001
        return minimal_evidence(title, url)
    return Evidence(
        jsonld_jobposting=bool(ats_fetchers._jsonld_job_postings(soup)),
        apply_cta=_has_apply_cta(soup),
        requirement_sections=_requirement_sections(visible_text(html)),
        role_family_from_title=load_role_families().classify(title or ""),
        url_shape=href_shape(url) if url else "",
    )


class DetailEnricher(ABC):
    @abstractmethod
    def enrich(self, posting: JobPosting) -> JobPosting: ...


class GenericHtmlEnricher(DetailEnricher):
    def __init__(self, fetcher: PageFetcher):
        self.fetcher = fetcher

    def enrich(self, posting: JobPosting) -> JobPosting:
        url = posting.url
        fallback = posting.model_copy(update={"evidence": minimal_evidence(posting.title, url, posting.description)})
        if not url or any(host in url.lower() for host in ats_fetchers._SKIP_GENERIC_FETCH_HOSTS):
            return fallback
        try:
            page = self.fetcher.fetch(url)
        except FetchFailed:
            return fallback
        if page.status >= 400:
            return fallback
        details = ats_fetchers.parse_job_details_html(page.html)
        return posting.model_copy(update={
            "description": details["description"] or posting.description,
            "location": posting.location or details["location"],
            "employment_type": posting.employment_type or details["employment_type"],
            "posted_at": posting.posted_at or details["posted_at"],
            "evidence": extract_evidence(page.html, posting.title, url),
        })


class NoopEnricher(DetailEnricher):
    """For ATS/techmap/special-case postings whose fields are already
    structured (or intentionally absent): no fetch, minimal evidence."""

    def enrich(self, posting: JobPosting) -> JobPosting:
        if posting.evidence is not None:
            return posting
        return posting.model_copy(update={"evidence": minimal_evidence(posting.title, posting.url, posting.description)})
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_enrich.py -q`
Expected: 6 passed

- [ ] **Step 6: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 361 passed (the existing `test_fetch_generic_job_details_*` tests in `test_ats_fetchers.py` must pass unchanged)

- [ ] **Step 7: Commit**

```bash
git add jobfit/ats_fetchers.py jobfit/scrape/enrich.py jobfit/server/tests/test_scrape_enrich.py
git commit -m "feat(scrape): add DetailEnricher + Evidence extraction; split parse_job_details_html out of fetch_generic_job_details"
```

---

### Task 8: Strategies

**Files:**
- Create: `jobfit/scrape/strategies.py`
- Test: `jobfit/server/tests/test_scrape_strategies.py`

**Interfaces:**
- Consumes: `ats.AtsClient`, `ats.AtsRegistry`, `ats.to_posting`, `fetchers.PageFetcher`, `candidates.CandidateExtractor`, `filters.FilterChain`, `enrich.DetailEnricher`, `models.HtmlListingStrategy`, `models.JobPosting`, `models.PageFingerprint`, `errors.FetchFailed`, `errors.PlanInvalid`, `connections.normalize_company`.
- Produces: `strategies.ScrapeStrategy` (ABC: `kind: str`, `last_fingerprint: PageFingerprint | None`, `fetch(company: str, career_url: str | None) -> list[JobPosting]`; `[]` means "reachable, no postings"; raises `FetchFailed` otherwise), `strategies.AtsApiScrape(client, board, known_url=None, source="ats_api")`, `strategies.ExternalBoardScrape(registry, board_url)`, `strategies.HtmlListingScrape(fetcher, extractor, chain, enricher, strategy, max_links=50)`, `strategies.SpecialCaseScrape(fn, session)`, `strategies.TechmapScrape(techmap_index)`, `strategies.NoScrape()`, `strategies.FallbackScrape(primary, fallbacks, is_healthy)` with `strategy_used: str` set after `fetch`; `strategies.page_fingerprint(candidates) -> PageFingerprint`.

`company` is the display name today (what `update_jobs` passes); Plan C switches it to the registry id.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_strategies.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_strategies.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.strategies'`

- [ ] **Step 3: Write strategies.py**

```python
# jobfit/scrape/strategies.py
"""ScrapeStrategy: BEHAVIOUR for one plan kind. Names carry a `Scrape`
suffix to keep them apart from the plan DATA models in models.py
(AtsApiStrategy is what a plan says; AtsApiScrape is what runs)."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Callable

from jobfit import connections
from jobfit.scrape.ats import AtsClient, AtsRegistry, to_posting
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import DetailEnricher
from jobfit.scrape.errors import FetchFailed, PlanInvalid
from jobfit.scrape.fetchers import PageFetcher
from jobfit.scrape.filters import FilterChain
from jobfit.scrape.models import Candidate, HtmlListingStrategy, JobPosting, PageFingerprint, PostingSource


def page_fingerprint(candidates: list[Candidate]) -> PageFingerprint:
    shapes = sorted({c.href_shape for c in candidates})
    digest = hashlib.sha1("\n".join(shapes).encode("utf-8")).hexdigest()
    return PageFingerprint(href_shape_set_hash=digest, candidate_count=len(candidates))


class ScrapeStrategy(ABC):
    kind: str = ""
    last_fingerprint: PageFingerprint | None = None

    @abstractmethod
    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        """[] means "page reachable, no postings"; raises FetchFailed when
        nothing could be fetched (so the caller leaves stored jobs alone)."""


class AtsApiScrape(ScrapeStrategy):
    kind = "ats_api"

    def __init__(self, client: AtsClient, board: str, known_url: str | None = None, source: PostingSource = "ats_api"):
        self.client, self.board, self.known_url, self.source = client, board, known_url, source

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return self.client.fetch_board(self.board, known_url=self.known_url or career_url, source=self.source)


class ExternalBoardScrape(ScrapeStrategy):
    kind = "external_board"

    def __init__(self, registry: AtsRegistry, board_url: str):
        resolved = registry.resolve(board_url)
        if resolved is None:
            raise PlanInvalid(f"external board url {board_url!r} matches no ATS client")
        client, board = resolved
        self._inner = AtsApiScrape(client, board, known_url=board_url, source="external_board")

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return self._inner.fetch(company, career_url)


class HtmlListingScrape(ScrapeStrategy):
    kind = "html_listing"

    def __init__(self, fetcher: PageFetcher, extractor: CandidateExtractor, chain: FilterChain,
                 enricher: DetailEnricher, strategy: HtmlListingStrategy, max_links: int = 50):
        self.fetcher, self.extractor, self.chain, self.enricher, self.strategy, self.max_links = (
            fetcher, extractor, chain, enricher, strategy, max_links,
        )

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        if not career_url:
            return []
        page = self.fetcher.fetch(career_url)
        if page.status in (404, 410):
            return []
        if page.status >= 400:
            raise FetchFailed(f"{career_url}: http {page.status}")
        candidates = self.extractor.extract(page, career_url, self.strategy.container_selector, cap=self.max_links * 4)
        self.last_fingerprint = page_fingerprint(candidates)
        accepted, _ = self.chain.run(candidates)
        accepted.sort(key=lambda c: not c.same_host)
        return [
            self.enricher.enrich(JobPosting(title=c.text, url=c.href, source="html_listing"))
            for c in accepted[: self.max_links]
        ]


class SpecialCaseScrape(ScrapeStrategy):
    kind = "special_case"

    def __init__(self, fn: Callable[[object], list[dict]], session):
        self.fn, self.session = fn, session

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        try:
            raw = self.fn(self.session)
        except Exception as error:  # noqa: BLE001 - surfaced as a fetch failure, never a crash
            raise FetchFailed(f"special-case fetcher for {company}: {error}") from error
        return [p for p in (to_posting(item, "special_case") for item in raw or []) if p is not None]


class TechmapScrape(ScrapeStrategy):
    kind = "techmap"

    def __init__(self, techmap_index: dict[str, list[dict]]):
        self.techmap_index = techmap_index

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        rows = self.techmap_index.get(connections.normalize_company(company), [])
        return [
            JobPosting(title=r["title"], location=r.get("location"), url=r.get("url"), source="techmap")
            for r in rows if r.get("title")
        ]


class NoScrape(ScrapeStrategy):
    kind = "broken_url"

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        return []


class FallbackScrape(ScrapeStrategy):
    """Composite: run the primary; if its result is not healthy (or it
    raised FetchFailed), try each fallback in order and return the first
    healthy result. If none is healthy, return the primary's own result
    (so diff_and_update can still close jobs on a genuinely empty page),
    or re-raise the primary's failure (so nothing is closed on an outage)."""
    kind = "fallback"

    def __init__(self, primary: ScrapeStrategy, fallbacks: list[ScrapeStrategy], is_healthy: Callable[[list[JobPosting]], bool]):
        self.primary, self.fallbacks, self.is_healthy = primary, fallbacks, is_healthy
        self.strategy_used = primary.kind

    def fetch(self, company: str, career_url: str | None) -> list[JobPosting]:
        primary_error: FetchFailed | None = None
        primary_result: list[JobPosting] | None = None
        try:
            primary_result = self.primary.fetch(company, career_url)
            self.last_fingerprint = self.primary.last_fingerprint
            if self.is_healthy(primary_result):
                self.strategy_used = self.primary.kind
                return primary_result
        except FetchFailed as error:
            primary_error = error
        for fallback in self.fallbacks:
            try:
                result = fallback.fetch(company, career_url)
            except FetchFailed:
                continue
            if self.is_healthy(result):
                self.strategy_used = fallback.kind
                self.last_fingerprint = fallback.last_fingerprint or self.last_fingerprint
                return result
        if primary_error is not None:
            raise primary_error
        self.strategy_used = self.primary.kind
        return primary_result or []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_strategies.py -q`
Expected: 12 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 373 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/strategies.py jobfit/server/tests/test_scrape_strategies.py
git commit -m "feat(scrape): add ScrapeStrategy hierarchy (ATS, external board, HTML listing, special case, techmap, no-op, fallback composite)"
```

**Group A checkpoint.** The tree now contains the whole skeleton; the only behaviour change so far is Task 5. Review here before Group B.

---

## Group B — Plans, rules classifier, health policy, service wiring

### Task 9: Health policy

**Files:**
- Create: `jobfit/scrape/health.py`
- Test: `jobfit/server/tests/test_scrape_health.py`

**Interfaces:**
- Consumes: `models.JobPosting`, `models.ScrapePlan`, `models.PageFingerprint`, `candidates.JOB_URL_HINT_RE`.
- Produces: `health.HealthPolicy(min_evidence_ratio=0.3, empty_runs_to_suspect=2, yield_drop_ratio=0.7)` with `is_healthy(postings) -> bool` and `update(plan, postings, now, fingerprint=None) -> ScrapePlan`. `health.TRUSTED_SOURCES = {"ats_api", "external_board", "special_case", "techmap"}`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_health.py
"""HealthPolicy: whether a scrape result looks real (never consulting CV
score), and the plan status transitions of spec section 4.4."""

from datetime import datetime, timezone

from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.models import Evidence, JobPosting, PageFingerprint, PlanHealth, ScrapePlan, TechmapOnlyStrategy

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _plan(status="verified", baseline=5, fingerprint="abc"):
    return ScrapePlan(
        company_id="acme", career_url="https://acme.com/careers", derived_by="rules", derived_at=NOW,
        verified_at=NOW if status == "verified" else None, status=status,
        strategy=TechmapOnlyStrategy(reason="x"), health=PlanHealth(baseline_yield=baseline),
        page_fingerprint=PageFingerprint(href_shape_set_hash=fingerprint, candidate_count=10),
    )


def _p(source="html_listing", evidence=None, url="https://acme.com/x"):
    return JobPosting(title="Backend Engineer", url=url, source=source, evidence=evidence)


def test_empty_is_unhealthy_and_trusted_sources_are_healthy():
    policy = HealthPolicy()
    assert policy.is_healthy([]) is False
    for source in ("ats_api", "external_board", "special_case", "techmap"):
        assert policy.is_healthy([_p(source=source)]) is True


def test_html_listing_is_healthy_with_enough_evidence_or_job_shaped_urls():
    policy = HealthPolicy()
    strong = [_p(evidence=Evidence(jsonld_jobposting=True)), _p(evidence=Evidence()), _p(evidence=Evidence())]
    assert policy.is_healthy(strong) is True  # 1 of 3 >= 30%
    hinted = [_p(url="https://acme.com/careers/job-123", evidence=Evidence()), _p(url="https://acme.com/about", evidence=Evidence()), _p(url="https://acme.com/pricing", evidence=Evidence())]
    assert policy.is_healthy(hinted) is True
    marketing = [_p(url="https://acme.com/about", evidence=Evidence()), _p(url="https://acme.com/pricing", evidence=None)]
    assert policy.is_healthy(marketing) is False


def test_healthy_run_resets_counters_and_clears_stale_suspect():
    policy = HealthPolicy()
    plan = _plan(status="stale_suspect").model_copy(update={"health": PlanHealth(baseline_yield=5, consecutive_empty_runs=2)})
    updated = policy.update(plan, [_p(source="techmap")], NOW)
    assert updated.status == "verified"
    assert updated.health.consecutive_empty_runs == 0
    assert updated.health.last_ok_run == NOW and updated.health.last_run == NOW and updated.health.last_yield == 1


def test_two_consecutive_empty_runs_mark_stale_suspect_only_when_there_was_a_baseline():
    policy = HealthPolicy()
    first = policy.update(_plan(), [], NOW)
    assert first.status == "verified" and first.health.consecutive_empty_runs == 1
    second = policy.update(first, [], NOW)
    assert second.status == "stale_suspect"
    never = policy.update(_plan(baseline=0), [], NOW)
    assert never.status == "verified" and never.health.consecutive_empty_runs == 0


def test_yield_drop_with_fingerprint_drift_marks_stale_suspect_but_drift_alone_does_not():
    policy = HealthPolicy()
    plan = _plan(baseline=10)
    one = [_p(source="techmap")]
    drifted = PageFingerprint(href_shape_set_hash="zzz", candidate_count=3)
    assert policy.update(plan, one, NOW, fingerprint=drifted).status == "stale_suspect"
    same_yield = [_p(source="techmap") for _ in range(10)]
    assert policy.update(plan, same_yield, NOW, fingerprint=drifted).status == "verified"
    assert policy.update(plan, one, NOW, fingerprint=PageFingerprint(href_shape_set_hash="abc", candidate_count=10)).status == "verified"


def test_stale_schema_status_is_left_alone():
    policy = HealthPolicy()
    assert policy.update(_plan(status="stale"), [_p(source="techmap")], NOW).status == "stale"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_health.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.health'`

- [ ] **Step 3: Write health.py**

```python
# jobfit/scrape/health.py
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
            if p.evidence is not None and (p.evidence.jsonld_jobposting or p.evidence.apply_cta or p.evidence.requirement_sections >= 1)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_health.py -q`
Expected: 6 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 379 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/health.py jobfit/server/tests/test_scrape_health.py
git commit -m "feat(scrape): add HealthPolicy - evidence-based scrape health and plan staleness transitions"
```

---

### Task 10: Plan ids, plan store, config paths

**Files:**
- Modify: `jobfit/config.py:34` (append after `PIPELINE_LOCK_PATH`)
- Create: `jobfit/scrape/ids.py`, `jobfit/scrape/plan_store.py`
- Modify: `.gitignore` (see Step 3)
- Test: `jobfit/server/tests/test_scrape_plan_store.py`

**Interfaces:**
- Consumes: `models.ScrapePlan`, `models.SCRAPE_PLAN_SCHEMA_VERSION`, `atomic_io.write_json_atomic`.
- Produces: `config.SCRAPE_PLANS_DIR`, `config.LISTING_SNAPSHOTS_DIR`, `config.PAGE_CACHE_DIR`, `config.PAGE_CACHE_TTL_HOURS`, `config.LINK_REJECTS_PATH`; `ids.plan_id_for(company: str) -> str`; `plan_store.PlanStore` (ABC: `get(company_id) -> ScrapePlan | None`, `put(plan) -> None`, `all() -> Iterator[ScrapePlan]`), `plan_store.FilePlanStore(directory: Path)`, `plan_store.MemoryPlanStore()`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_plan_store.py
"""Plans on disk: one JSON per company under data/scrape_plans/, loaded
back through the pydantic model; an older schema_version comes back as
status "stale" so discovery re-derives it under the budget."""

import json
from datetime import datetime, timezone

from jobfit.scrape import ids, models
from jobfit.scrape.plan_store import FilePlanStore, MemoryPlanStore
from jobfit.scripts import update_jobs

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _plan(company_id="acme", **overrides):
    base = dict(company_id=company_id, career_url="https://acme.com/careers", derived_by="probe", derived_at=NOW,
                status="verified", strategy=models.TechmapOnlyStrategy(reason="x"))
    base.update(overrides)
    return models.ScrapePlan(**base)


def test_plan_id_matches_update_jobs_snake_case_for_real_names():
    for name in ("monday.com", "Monday.com Ltd. (Formerly DaPulse)", "Check Point", "4M Analytics", "  Wiz ", "Ré Sumé"):
        assert ids.plan_id_for(name) == update_jobs._snake_case(name)
    assert ids.plan_id_for("") == "unnamed_company"


def test_file_store_round_trips_and_lists_sorted(tmp_path):
    store = FilePlanStore(tmp_path)
    assert store.get("acme") is None
    store.put(_plan("zeta"))
    store.put(_plan("acme"))
    assert store.get("acme") == _plan("acme")
    assert [p.company_id for p in store.all()] == ["acme", "zeta"]
    assert (tmp_path / "acme.json").exists()


def test_file_store_marks_an_older_schema_version_stale(tmp_path):
    store = FilePlanStore(tmp_path)
    data = _plan("acme").model_dump(mode="json")
    data["schema_version"] = 0
    (tmp_path / "acme.json").write_text(json.dumps(data), encoding="utf-8")
    assert store.get("acme").status == "stale"


def test_file_store_returns_none_for_a_corrupt_file(tmp_path):
    (tmp_path / "acme.json").write_text("{not json", encoding="utf-8")
    assert FilePlanStore(tmp_path).get("acme") is None


def test_memory_store():
    store = MemoryPlanStore()
    store.put(_plan("acme"))
    assert store.get("acme").company_id == "acme"
    assert [p.company_id for p in store.all()] == ["acme"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_plan_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'ids' from 'jobfit.scrape'`

- [ ] **Step 3: Add config paths, ids.py, plan_store.py; check .gitignore**

Append to `jobfit/config.py` right after line 34 (`PIPELINE_LOCK_PATH = ...`):

```python
# --- Plan-driven scraping (jobfit/scrape) ---
# One ScrapePlan per company, derived once by `update_jobs --discover`
# (deterministic probes first, a small LLM only when they cannot decide)
# and executed in pure Python on every ordinary run. Committed to git:
# producing one may have cost an API call, and hand-written plans are legal.
SCRAPE_PLANS_DIR = ROOT / "data" / "scrape_plans"
# The listing HTML a plan was derived from - the regression fixture the
# replay test runs every plan against. Committed to git.
LISTING_SNAPSHOTS_DIR = ROOT / "cache" / "listing_snapshots"
# TTL cache for job detail pages (replaces cache/generic_descriptions.json).
PAGE_CACHE_DIR = ROOT / "cache" / "pages"
PAGE_CACHE_TTL_HOURS = GENERIC_DESC_TTL_HOURS
# URL regexes the audit marked "not a job" - consulted by RejectListFilter.
LINK_REJECTS_PATH = ROOT / "data" / "link_rejects.json"
```

```python
# jobfit/scrape/ids.py
"""Plan-file id for a company. Byte-for-byte the same rule as
update_jobs._snake_case (which names companies/<id>.json) so a company's
plan and its job file share a stem. Duplicated rather than imported
because update_jobs imports this package, not the other way round; Plan C
replaces both with the registry id."""

import re


def plan_id_for(company: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (company or "").lower()).strip()
    s = re.sub(r"[\s-]+", "_", s)
    return s or "unnamed_company"
```

```python
# jobfit/scrape/plan_store.py
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from jobfit.atomic_io import write_json_atomic
from jobfit.scrape.models import SCRAPE_PLAN_SCHEMA_VERSION, ScrapePlan

logger = logging.getLogger("jobfit.scrape.plans")


class PlanStore(ABC):
    @abstractmethod
    def get(self, company_id: str) -> ScrapePlan | None: ...

    @abstractmethod
    def put(self, plan: ScrapePlan) -> None: ...

    @abstractmethod
    def all(self) -> Iterator[ScrapePlan]: ...


class FilePlanStore(PlanStore):
    def __init__(self, directory: Path):
        self.directory = directory

    def path_for(self, company_id: str) -> Path:
        return self.directory / f"{company_id}.json"

    def _load(self, path: Path) -> ScrapePlan | None:
        try:
            plan = ScrapePlan.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as error:
            logger.warning("unreadable plan %s: %s", path.name, error)
            return None
        if plan.schema_version < SCRAPE_PLAN_SCHEMA_VERSION:
            plan = plan.model_copy(update={"status": "stale"})
        return plan

    def get(self, company_id: str) -> ScrapePlan | None:
        path = self.path_for(company_id)
        return self._load(path) if path.exists() else None

    def put(self, plan: ScrapePlan) -> None:
        write_json_atomic(self.path_for(plan.company_id), plan.model_dump(mode="json"))

    def all(self) -> Iterator[ScrapePlan]:
        if not self.directory.exists():
            return
        for path in sorted(self.directory.glob("*.json")):
            plan = self._load(path)
            if plan is not None:
                yield plan


class MemoryPlanStore(PlanStore):
    def __init__(self):
        self._plans: dict[str, ScrapePlan] = {}

    def get(self, company_id: str) -> ScrapePlan | None:
        return self._plans.get(company_id)

    def put(self, plan: ScrapePlan) -> None:
        self._plans[plan.company_id] = plan

    def all(self) -> Iterator[ScrapePlan]:
        for key in sorted(self._plans):
            yield self._plans[key]
```

`.gitignore`: open it. If `jobfit/cache/` (or `cache/`) is ignored, add `!jobfit/cache/listing_snapshots/` so snapshots are committed. If it is not ignored, add `jobfit/cache/pages/` so the detail-page cache never is. Either way the outcome must be: `data/scrape_plans/` and `cache/listing_snapshots/` tracked, `cache/pages/` untracked.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_plan_store.py -q`
Expected: 5 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 384 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/config.py jobfit/scrape/ids.py jobfit/scrape/plan_store.py .gitignore jobfit/server/tests/test_scrape_plan_store.py
git commit -m "feat(scrape): add PlanStore (file + memory), plan ids, and the scrape package's config paths"
```

---

### Task 11: Rules and recorded plan classifiers

**Files:**
- Create: `jobfit/scrape/classifiers.py`
- Test: `jobfit/server/tests/test_scrape_classifiers.py`

**Interfaces:**
- Consumes: `filters.*`, `models.Page`, `models.Candidate`, `models.Labels`, `models.CandidateLabel`, `errors.ClassifierFailed`.
- Produces: `classifiers.PlanClassifier` (ABC: `classify(page, candidates, career_url) -> Labels`), `classifiers.rules_chain() -> FilterChain`, `classifiers.RulesPlanClassifier(chain=None)`, `classifiers.RecordedPlanClassifier(labels_by_url: dict[str, Labels])` with `RecordedPlanClassifier.from_dir(directory: Path)` reading `*.json` files shaped `{"career_url": "...", "labels": {...Labels...}}`. Task 15 adds `LLMPlanClassifier` to this same module.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_classifiers.py
"""PlanClassifier implementations that never touch a model: the rules
classifier (the LLM-free fallback and the base the LLM refines) and the
recorded classifier (replays saved Labels in tests)."""

import json
from datetime import datetime, timezone

import pytest

from jobfit.scrape import classifiers, errors
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.models import CandidateLabel, Labels

CAREER = "https://acme.com/careers/"
HTML = """
<nav><a href="/about">About Us Page</a></nav>
<ul>
 <li><a href="/careers/backend-1">Backend Engineer</a></li>
 <li><a href="/careers/frontend-2">Frontend Engineer</a></li>
 <li><a href="/careers/devops-3">DevOps Engineer</a></li>
</ul>
<a href="/code-governance">Code Governance and Compliance</a>
"""


def _page(html=HTML, url=CAREER):
    return make_page(url, url, 200, html, "http", datetime(2026, 9, 28, tzinfo=timezone.utc))


def test_rules_chain_order():
    assert [f.name for f in classifiers.rules_chain().filters] == ["denylist", "href_marker", "category_prefix", "url_shape", "evidence"]


def test_rules_classifier_labels_every_candidate_in_index_order():
    page = _page()
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = classifiers.RulesPlanClassifier().classify(page, candidates, CAREER)
    assert labels.page_verdict == "careers_page"
    assert [l.index for l in labels.candidates] == [c.index for c in candidates]
    by_text = {c.text: l for c, l in zip(candidates, labels.candidates)}
    assert by_text["Backend Engineer"].is_job is True
    assert by_text["About Us Page"].is_job is False
    assert by_text["Code Governance and Compliance"].is_job is False
    assert all(len(l.reason) <= 200 for l in labels.candidates)


def test_rules_classifier_reports_js_shell_pages():
    shell = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
    page = _page(shell)
    labels = classifiers.RulesPlanClassifier().classify(page, [], CAREER)
    assert labels.page_verdict == "js_shell" and labels.candidates == []


def test_recorded_classifier_replays_by_career_url_and_fails_loudly_otherwise(tmp_path):
    recorded = Labels(page_verdict="careers_page", candidates=[CandidateLabel(index=0, is_job=True, reason="fixture")])
    (tmp_path / "acme.json").write_text(json.dumps({"career_url": CAREER, "labels": recorded.model_dump(mode="json")}), encoding="utf-8")
    classifier = classifiers.RecordedPlanClassifier.from_dir(tmp_path)
    assert classifier.classify(_page(), [], CAREER) == recorded
    with pytest.raises(errors.ClassifierFailed):
        classifier.classify(_page(), [], "https://other.com/jobs")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_classifiers.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.classifiers'`

- [ ] **Step 3: Write classifiers.py**

```python
# jobfit/scrape/classifiers.py
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_classifiers.py -q`
Expected: 4 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 388 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/classifiers.py jobfit/server/tests/test_scrape_classifiers.py
git commit -m "feat(scrape): add PlanClassifier with rules-based and recorded implementations"
```

---

### Task 12: Strategy factory

**Files:**
- Create: `jobfit/scrape/factory.py`
- Test: `jobfit/server/tests/test_scrape_factory.py`

**Interfaces:**
- Consumes: everything from Tasks 2-9.
- Produces: `factory.StrategyFactory(registry, fetchers, extractor, enricher, reject_patterns, techmap_index, health, special_fetchers, session, max_links=50)` with `build(plan: ScrapePlan) -> ScrapeStrategy` and `chain_for(strategy: HtmlListingStrategy) -> FilterChain`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_factory.py
"""StrategyFactory: plan.strategy.kind -> the ScrapeStrategy that runs it.
One registry entry per kind; html_listing gets the plan-specific filter
chain and, when the plan lists fallbacks, a FallbackScrape composite."""

from datetime import datetime, timezone

import pytest

from jobfit.scrape import errors, models, strategies
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.health import HealthPolicy

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _factory(playwright=True):
    return StrategyFactory(
        registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=playwright),
        extractor=CandidateExtractor(), enricher=NoopEnricher(), reject_patterns=[r"^https://acme\.com/legal"],
        techmap_index={"acme": []}, health=HealthPolicy(), special_fetchers={"elbitsystemscareer.com": lambda s: []}, session=None,
    )


def _plan(strategy, status="verified"):
    return models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="probe", derived_at=NOW, status=status, strategy=strategy)


def test_builds_each_kind():
    f = _factory()
    assert isinstance(f.build(_plan(models.AtsApiStrategy(provider="lever", board="acme", board_url="https://jobs.lever.co/acme"))), strategies.AtsApiScrape)
    assert isinstance(f.build(_plan(models.ExternalBoardStrategy(board_url="https://boards.greenhouse.io/acme"))), strategies.ExternalBoardScrape)
    assert isinstance(f.build(_plan(models.HtmlListingStrategy())), strategies.HtmlListingScrape)
    assert isinstance(f.build(_plan(models.SpecialCaseStrategy(host_fragment="elbitsystemscareer.com"))), strategies.SpecialCaseScrape)
    assert isinstance(f.build(_plan(models.TechmapOnlyStrategy(reason="x"))), strategies.TechmapScrape)
    assert isinstance(f.build(_plan(models.BrokenUrlStrategy(reason="404"))), strategies.NoScrape)


def test_unverified_broken_url_falls_back_to_techmap_and_unknown_special_case_is_invalid():
    f = _factory()
    assert isinstance(f.build(_plan(models.BrokenUrlStrategy(reason="classifier"), status="unverified")), strategies.TechmapScrape)
    with pytest.raises(errors.PlanInvalid):
        f.build(_plan(models.SpecialCaseStrategy(host_fragment="nope.example")))


def test_html_listing_with_fallbacks_is_wrapped_in_a_fallback_composite():
    f = _factory()
    built = f.build(_plan(models.HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"])))
    assert isinstance(built, strategies.FallbackScrape)
    assert isinstance(built.primary, strategies.HtmlListingScrape)
    assert [type(fb).__name__ for fb in built.fallbacks] == ["HtmlListingScrape", "TechmapScrape"]
    assert built.fallbacks[0].fetcher.__class__.__name__ == "PlaywrightPageFetcher"


def test_playwright_fallback_is_skipped_when_the_primary_already_renders_with_playwright():
    built = _factory().build(_plan(models.HtmlListingStrategy(renderer="playwright", fallbacks=["playwright", "techmap"])))
    assert [type(fb).__name__ for fb in built.fallbacks] == ["TechmapScrape"]


def test_chain_for_a_plan_has_the_documented_order_and_carries_the_plan_patterns():
    strategy = models.HtmlListingStrategy(include_url=r"^https://acme\.com/careers/[a-z-]+$", url_shape="acme.com|careers|2")
    chain = _factory().chain_for(strategy)
    assert [f.name for f in chain.filters] == ["denylist", "href_marker", "reject_list", "category_prefix", "plan_pattern", "url_shape", "evidence"]
    assert chain.filters[4].include.pattern == strategy.include_url
    assert chain.filters[5].expected_shape == "acme.com|careers|2"
    assert chain.filters[2].patterns[0].pattern == r"^https://acme\.com/legal"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_factory.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.factory'`

- [ ] **Step 3: Write factory.py**

```python
# jobfit/scrape/factory.py
"""plan -> ScrapeStrategy. One builder per plan kind, looked up in a dict;
adding a kind is one model in models.py plus one method here."""

from __future__ import annotations

from typing import Callable

from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import DetailEnricher
from jobfit.scrape.errors import PlanInvalid
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.filters import (
    CategoryPrefixFilter, DenylistFilter, EvidenceThresholdFilter, FilterChain, HrefMarkerFilter,
    PlanPatternFilter, RejectListFilter, UrlShapeClusterFilter,
)
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.models import HtmlListingStrategy, ScrapePlan
from jobfit.scrape.strategies import (
    AtsApiScrape, ExternalBoardScrape, FallbackScrape, HtmlListingScrape, NoScrape, ScrapeStrategy,
    SpecialCaseScrape, TechmapScrape,
)


class StrategyFactory:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 enricher: DetailEnricher, reject_patterns: list[str], techmap_index: dict[str, list[dict]],
                 health: HealthPolicy, special_fetchers: dict[str, Callable], session, max_links: int = 50):
        self.registry, self.fetchers, self.extractor, self.enricher = registry, fetchers, extractor, enricher
        self.reject_patterns, self.techmap_index, self.health = reject_patterns, techmap_index, health
        self.special_fetchers, self.session, self.max_links = special_fetchers, session, max_links
        self._builders = {
            "ats_api": self._ats_api, "external_board": self._external_board, "html_listing": self._html_listing,
            "special_case": self._special_case, "techmap_only": self._techmap_only, "broken_url": self._broken_url,
        }

    def build(self, plan: ScrapePlan) -> ScrapeStrategy:
        return self._builders[plan.strategy.kind](plan)

    def chain_for(self, strategy: HtmlListingStrategy) -> FilterChain:
        return FilterChain([
            DenylistFilter(), HrefMarkerFilter(), RejectListFilter(self.reject_patterns), CategoryPrefixFilter(),
            PlanPatternFilter(strategy.include_url, strategy.exclude_url, strategy.explicit_accept),
            UrlShapeClusterFilter(strategy.url_shape), EvidenceThresholdFilter(min_signals=2, reject_chrome=True),
        ])

    def _ats_api(self, plan: ScrapePlan) -> ScrapeStrategy:
        s = plan.strategy
        return AtsApiScrape(self.registry.client(s.provider), s.board, known_url=s.board_url)

    def _external_board(self, plan: ScrapePlan) -> ScrapeStrategy:
        return ExternalBoardScrape(self.registry, plan.strategy.board_url)

    def _html_listing(self, plan: ScrapePlan) -> ScrapeStrategy:
        s = plan.strategy
        chain = self.chain_for(s)
        primary = HtmlListingScrape(self.fetchers.build(s.renderer), self.extractor, chain, self.enricher, s, self.max_links)
        fallbacks: list[ScrapeStrategy] = []
        for name in s.fallbacks:
            if name == "playwright" and s.renderer != "playwright":
                fallbacks.append(HtmlListingScrape(self.fetchers.build("playwright"), self.extractor, chain, self.enricher, s, self.max_links))
            elif name == "techmap":
                fallbacks.append(TechmapScrape(self.techmap_index))
        return FallbackScrape(primary, fallbacks, self.health.is_healthy) if fallbacks else primary

    def _special_case(self, plan: ScrapePlan) -> ScrapeStrategy:
        fn = self.special_fetchers.get(plan.strategy.host_fragment)
        if fn is None:
            raise PlanInvalid(f"no special-case fetcher registered for {plan.strategy.host_fragment!r}")
        return SpecialCaseScrape(fn, self.session)

    def _techmap_only(self, plan: ScrapePlan) -> ScrapeStrategy:
        return TechmapScrape(self.techmap_index)

    def _broken_url(self, plan: ScrapePlan) -> ScrapeStrategy:
        # Only a probe-verified broken URL (404/410, homepage redirect) is
        # allowed to return nothing; anything less certain keeps techmap.
        return NoScrape() if plan.status == "verified" else TechmapScrape(self.techmap_index)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_factory.py -q`
Expected: 5 passed

- [ ] **Step 5: Run the full suite**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 393 passed

- [ ] **Step 6: Commit**

```bash
git add jobfit/scrape/factory.py jobfit/server/tests/test_scrape_factory.py
git commit -m "feat(scrape): add StrategyFactory - plan kind to strategy, with the plan-specific filter chain and fallback composite"
```

---

### Task 13: CompanyScrapeService, bootstrap, and wiring `update_jobs` onto it

This task swaps the runtime path: `scrape_stage` builds one `CompanyScrapeService` and every company goes through `service.scrape()`. The old cascade and its three CV-score tier gates are deleted. A company with no plan gets a synthesised rules plan (spec section 3.1) — no model is involved anywhere in this task.

**Files:**
- Create: `jobfit/scrape/service.py`, `jobfit/scrape/bootstrap.py`
- Modify: `jobfit/scripts/update_jobs.py:37` (imports), `:45-50` (delete `MAX_LINKS_PER_COMPANY`), `:140-234` (delete `MIN_SCORE_FOR_REAL_MATCH`, `_any_job_scores_positive`, `MIN_DESCRIPTION_LEN`, `MIN_DESCRIPTION_RATIO`, `_has_real_descriptions`, `MIN_JOB_URL_RATIO`, `_JOB_URL_HINT_RE`, `_looks_like_real_job_urls`, `_fetch_via_playwright`, `_techmap_fallback_jobs`), `:264-409` (delete `_ats_api_jobs`, replace `fetch_company_jobs_async`), `:441-466` (`diff_and_update` new-job record), `:509-522` (`_process_company`), `:538-550` (`scrape_stage`)
- Modify: `jobfit/scoring.py:132-168` (`_looks_unparseable`), `:159-171` region (`score_cache_key`), `score_job`
- Modify: `jobfit/server/tests/test_scrape_stage.py` (delete the cascade/tier-gate tests, adjust the two `_fake_process` signatures)
- Test: `jobfit/server/tests/test_scrape_service.py`, additions to `jobfit/server/tests/test_recompute_score_cache.py` and `jobfit/server/tests/test_scoring_shared_weights.py`

**Interfaces:**
- Consumes: Tasks 1-12.
- Produces: `service.CompanyScrapeService(store, factory, health, registry, special_hosts: list[str], now=None)` with `scrape(company: str, career_url: str | None) -> ScrapeResult` and `synthesize_plan(company_id, career_url) -> ScrapePlan`; `bootstrap.load_reject_patterns(path=None) -> list[str]`; `bootstrap.build_scrape_service(session, techmap_index, plans_dir=None, playwright_available=True) -> CompanyScrapeService`; `update_jobs.fetch_company_jobs(company, url, service) -> list[dict]` (each dict has the `JobPosting` fields plus `job_evidence: dict | None` and `scrape_source: str`); `update_jobs.fetch_company_jobs_async(company, url, session, profiles, techmap_index, service=None)` kept as a thin async wrapper; `update_jobs._process_company(company, url, session, profiles, techmap_index, force, service=None)`; `scoring._looks_unparseable(job_req, evidence: dict | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_service.py
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
```

Add to `jobfit/server/tests/test_recompute_score_cache.py`:

```python
def test_score_cache_key_changes_when_job_evidence_is_present():
    profile = {"text": "Backend engineer with Python experience"}
    plain = {"description": "Python required"}
    with_evidence = {"description": "Python required", "job_evidence": {"jsonld_jobposting": True}}
    assert scoring.score_cache_key(plain, profile) != scoring.score_cache_key(with_evidence, profile)
```

Add to `jobfit/server/tests/test_scoring_shared_weights.py`:

```python
def test_empty_scrape_evidence_zero_scores_even_a_parseable_looking_body():
    """A marketing page can contain enough incidental structure to extract a
    requirement or two; when the scrape-time evidence says the page had no
    JSON-LD, no apply CTA, no requirement sections and a title with no role
    family, the job is not a job."""
    job = {"title": "Code Governance and Compliance", "description": "Requirements: 5+ years of Python and Kubernetes.",
           "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "x||1"}}
    result = scoring.score_job(job, cv_text="Backend Engineer\nAcme | 2020 - Present\n- Python, Kubernetes")
    assert result["score"] == 0
    assert result["confidence"] == "title_only"
```

Rewrite `jobfit/server/tests/test_scrape_stage.py`: delete everything from the `# --- fetch_company_jobs_async null-URL fallback` block (line 68) to the end EXCEPT the two `scrape_stage` cancellation tests, and change those two fake signatures to `def _fake_process(company, url, session, profiles, techmap_index, force, service=None):`; add `monkeypatch.setattr(update_jobs.scrape_bootstrap, "build_scrape_service", lambda session, techmap_index: None)` to both. Then append:

```python
# --- fetch_company_jobs (service shim) ------------------------------------

class _StubService:
    def __init__(self, postings):
        self.postings = postings

    def scrape(self, company, career_url):
        from jobfit.scrape.models import HtmlListingStrategy, ScrapePlan, ScrapeResult
        plan = ScrapePlan(company_id="acme", career_url=career_url, derived_by="rules", derived_at=datetime.now(timezone.utc),
                          status="unverified", strategy=HtmlListingStrategy())
        return ScrapeResult(company_id="acme", postings=self.postings, plan=plan, strategy_used="html_listing")


def test_fetch_company_jobs_maps_postings_to_job_dicts_with_evidence_and_source():
    from jobfit.scrape.models import Evidence, JobPosting
    posting = JobPosting(title="Backend Engineer", url="https://acme.com/careers/1", location="Tel Aviv", description="x",
                         evidence=Evidence(jsonld_jobposting=True, url_shape="acme.com|careers|2"), source="html_listing")
    jobs = update_jobs.fetch_company_jobs("Acme", "https://acme.com/careers", _StubService([posting]))
    assert jobs[0]["title"] == "Backend Engineer" and jobs[0]["location"] == "Tel Aviv"
    assert jobs[0]["job_evidence"]["jsonld_jobposting"] is True
    assert jobs[0]["scrape_source"] == "html_listing"
    assert "evidence" not in jobs[0] and "source" not in jobs[0]


def test_diff_and_update_stores_job_evidence_on_new_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    fetched = [{"title": "Backend Engineer", "url": "https://acme.com/careers/1", "location": "Tel Aviv", "description": "Requirements: Python",
                "job_evidence": {"jsonld_jobposting": True}, "scrape_source": "html_listing"}]
    record, new_count, _ = update_jobs.diff_and_update("Acme", "https://acme.com/careers", fetched, profiles={})
    assert new_count == 1
    assert record["jobs"][0]["job_evidence"] == {"jsonld_jobposting": True}
    assert record["jobs"][0]["scrape_source"] == "html_listing"


def test_process_company_records_a_failure_and_saves_nothing_on_fetch_failed(tmp_path, monkeypatch):
    from jobfit.scrape import errors
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)

    class _Down:
        def scrape(self, company, career_url):
            raise errors.FetchFailed("down")

    company, new, closed, skipped, error = update_jobs._process_company("Acme", "https://acme.com/careers", None, {}, {}, True, service=_Down())
    assert isinstance(error, errors.FetchFailed) and skipped is False
    assert list(tmp_path.glob("*.json")) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_service.py jobfit/server/tests/test_scrape_stage.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.service'` and `AttributeError: ... has no attribute 'scrape_bootstrap'`

- [ ] **Step 3: Write service.py and bootstrap.py**

```python
# jobfit/scrape/service.py
"""One company, one scrape: load (or synthesise) its plan, build the
strategy, run it, persist health. This is the runtime path - it never
constructs a classifier, let alone a model client."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.enrich import NoopEnricher
from jobfit.scrape.errors import PlanInvalid
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.ids import plan_id_for
from jobfit.scrape.models import (
    AtsApiStrategy, HtmlListingStrategy, ScrapePlan, ScrapeResult, SpecialCaseStrategy, TechmapOnlyStrategy,
)
from jobfit.scrape.plan_store import PlanStore

logger = logging.getLogger("jobfit.scrape")


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

    def _plan_for(self, company_id: str, career_url: str | None) -> ScrapePlan:
        plan = self.store.get(company_id)
        if plan is None:
            plan = self.synthesize_plan(company_id, career_url)
            self.store.put(plan)
        elif plan.career_url != career_url:
            fresh = self.synthesize_plan(company_id, career_url)
            plan = fresh.model_copy(update={"notes": [f"career url changed from {plan.career_url!r}; plan re-synthesised"]})
            self.store.put(plan)
        return plan

    def scrape(self, company: str, career_url: str | None) -> ScrapeResult:
        company_id = plan_id_for(company)
        plan = self._plan_for(company_id, career_url)
        try:
            strategy = self.factory.build(plan)
        except PlanInvalid as error:
            fresh = self.synthesize_plan(company_id, career_url)
            plan = fresh.model_copy(update={"notes": [f"plan invalid: {error}; re-synthesised"]})
            self.store.put(plan)
            strategy = self.factory.build(plan)
        postings = strategy.fetch(company, career_url)  # FetchFailed propagates: nothing below runs, plan untouched
        postings = [p if p.evidence is not None else self._noop.enrich(p) for p in postings]
        plan = self.health.update(plan, postings, self.now(), fingerprint=strategy.last_fingerprint)
        self.store.put(plan)
        used = getattr(strategy, "strategy_used", strategy.kind)
        logger.info("%s: %d postings via %s (plan %s/%s)", company, len(postings), used, plan.derived_by, plan.status)
        return ScrapeResult(company_id=company_id, postings=postings, plan=plan, strategy_used=used)
```

```python
# jobfit/scrape/bootstrap.py
"""Composition root. build_scrape_service() is the production runtime
graph and contains no classifier of any kind. build_discovery_planner()
(Task 14/16) is the only function that ever constructs a model client,
and it imports jobfit.scrape.llm_client lazily inside its body."""

from __future__ import annotations

import json
from pathlib import Path

from jobfit import ats_fetchers, config
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.enrich import GenericHtmlEnricher
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import CachedPageFetcher, HttpPageFetcher, PageFetcherFactory
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.plan_store import FilePlanStore
from jobfit.scrape.service import CompanyScrapeService


def load_reject_patterns(path: Path | None = None) -> list[str]:
    path = path or config.LINK_REJECTS_PATH
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("patterns", []))
    except (OSError, ValueError):
        return []


def build_scrape_service(session, techmap_index: dict[str, list[dict]], plans_dir: Path | None = None,
                         playwright_available: bool = True) -> CompanyScrapeService:
    registry = default_registry(session)
    fetchers = PageFetcherFactory(session, playwright_available)
    enricher = GenericHtmlEnricher(CachedPageFetcher(HttpPageFetcher(session), config.PAGE_CACHE_DIR, config.PAGE_CACHE_TTL_HOURS))
    health = HealthPolicy()
    factory = StrategyFactory(
        registry=registry, fetchers=fetchers, extractor=CandidateExtractor(), enricher=enricher,
        reject_patterns=load_reject_patterns(), techmap_index=techmap_index, health=health,
        special_fetchers=ats_fetchers.SPECIAL_CASE_FETCHERS, session=session,
    )
    store = FilePlanStore(plans_dir or config.SCRAPE_PLANS_DIR)
    return CompanyScrapeService(store, factory, health, registry, special_hosts=list(ats_fetchers.SPECIAL_CASE_FETCHERS))
```

- [ ] **Step 4: Rewire update_jobs.py**

Line 37 import: add `from jobfit.scrape import bootstrap as scrape_bootstrap  # noqa: E402` on the line after it.

Delete lines 45-50 (`MAX_LINKS_PER_COMPANY` and its comment), lines 140-234 (everything from `MIN_SCORE_FOR_REAL_MATCH` through `_techmap_fallback_jobs`) and lines 264-409 (`_ats_api_jobs` and `fetch_company_jobs_async`). In their place (after `load_techmap_index`) add:

```python
def fetch_company_jobs(company: str, url: str | None, service) -> list[dict]:
    """Run the company's stored (or synthesised) ScrapePlan through
    CompanyScrapeService and convert each JobPosting to the dict shape
    diff_and_update stores. Pure Python; no model call on this path."""
    result = service.scrape(company, url)
    jobs = []
    for posting in result.postings:
        job = posting.model_dump(mode="json")
        job["job_evidence"] = job.pop("evidence", None)
        job["scrape_source"] = job.pop("source", None)
        jobs.append(job)
    return jobs


async def fetch_company_jobs_async(company: str, url: str | None, session, profiles: dict, techmap_index: dict, service=None) -> list[dict]:
    """Kept for callers of the old async signature; `profiles` is unused
    (scrape health no longer depends on CV score)."""
    service = service or scrape_bootstrap.build_scrape_service(session, techmap_index)
    return fetch_company_jobs(company, url, service)
```

In `diff_and_update`, add two keys to the `new_job` dict (after `"status": "new",`):

```python
                "job_evidence": job.get("job_evidence"),
                "scrape_source": job.get("scrape_source"),
```

and in the `if job_id in existing_by_id:` branch, after `existing["last_seen"] = now`, add:

```python
            if job.get("job_evidence") is not None:
                existing["job_evidence"] = job["job_evidence"]
```

Replace `_process_company` with:

```python
def _process_company(company, url, session, profiles, techmap_index, force, service=None):
    """Runs in a worker thread. Returns (company, new_count, closed_count, skipped, error)."""
    record = load_company_file(company)
    if _should_skip_company(record, force):
        return company, 0, 0, True, None
    try:
        service = service or scrape_bootstrap.build_scrape_service(session, techmap_index)
        fetched = fetch_company_jobs(company, url, service)
        for job in fetched:
            translation.translate_job_if_needed(job)
        updated, new_count, closed_count = diff_and_update(company, url, fetched, profiles)
        save_company_file(company, updated)
        return company, new_count, closed_count, False, None
    except Exception as error:  # noqa: BLE001 - one bad company must never abort the run
        return company, 0, 0, False, error
```

In `scrape_stage`, after `techmap_index = load_techmap_index()` add `service = scrape_bootstrap.build_scrape_service(session, techmap_index)` and change the submit line to `pool.submit(_process_company, company, url, session, profiles, techmap_index, force, service)`.

Remove the now-unused `import asyncio` if nothing else in the file uses it (Grep first).

- [ ] **Step 5: Make scoring evidence-aware**

In `jobfit/scoring.py`, change `_looks_unparseable`'s signature and add the evidence check at the top of its body (keep the docstring and the existing logic below it):

```python
def _looks_unparseable(job_req: JobRequirements, evidence: dict | None = None) -> bool:
    if evidence:
        has_signal = any([
            evidence.get("jsonld_jobposting"), evidence.get("apply_cta"),
            (evidence.get("requirement_sections") or 0) >= 1, evidence.get("role_family_from_title"),
        ])
        if not has_signal:
            return True
    if job_req.must_have or job_req.nice_to_have:
        return False
    from jobfit.ats_scorer.taxonomy import load_role_families
    return load_role_families().classify(job_req.title) is None
```

In `score_job`, change `if _looks_unparseable(job_req):` to `if _looks_unparseable(job_req, job.get("job_evidence")):`.

In `score_cache_key`, after the existing `parts` list is built, add (before hashing):

```python
    evidence = job.get("job_evidence")
    if evidence:
        parts.append(json.dumps(evidence, sort_keys=True))
```

(add `import json` at the top of `scoring.py`). Only jobs that carry evidence get a new key, so this does not force a corpus-wide rescore.

- [ ] **Step 6: Run the new and modified tests, then the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_service.py jobfit/server/tests/test_scrape_stage.py jobfit/server/tests/test_recompute_score_cache.py jobfit/server/tests/test_scoring_shared_weights.py -q`
Expected: all pass

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 385 passed (393 - 20 deleted cascade/tier-gate tests + 6 service + 3 stage + 1 cache + 1 scoring + 1 unchanged count adjustments; if your count differs by a few, verify that only the intended tests were deleted)

- [ ] **Step 7: Smoke-test the real runtime path on one company**

Run: `uv run python -m jobfit.scripts.update_jobs --company Wiz --force --skip-aggregate`
Expected: a log line `Wiz: N postings via <kind> (plan rules/unverified)` (or `probe/verified` for an ATS board), a new file `jobfit/data/scrape_plans/wiz.json`, and `jobfit/companies/wiz.json` jobs carrying `job_evidence`. No traceback.

- [ ] **Step 8: Commit**

```bash
git add jobfit/scrape/service.py jobfit/scrape/bootstrap.py jobfit/scripts/update_jobs.py jobfit/scoring.py jobfit/server/tests/test_scrape_service.py jobfit/server/tests/test_scrape_stage.py jobfit/server/tests/test_recompute_score_cache.py jobfit/server/tests/test_scoring_shared_weights.py jobfit/data/scrape_plans/wiz.json
git commit -m "feat(scrape): run every company through CompanyScrapeService; delete the CV-score tier gates; store scrape evidence and use it in the unparseable-job gate"
```

---

### Task 14: Planner, inducer, validator (probes + rules classifier), `--plans`

Discovery without a model: the deterministic probes of spec section 4.1 steps 1-7, classification by `RulesPlanClassifier`, induction of a plan from labels (spec 4.3), validation before `verified`. Task 15/16 slot the LLM classifier into this same planner.

**Files:**
- Create: `jobfit/scrape/planner.py`
- Modify: `jobfit/scrape/classifiers.py` (add a `derived_by` class attribute to each classifier)
- Modify: `jobfit/scrape/bootstrap.py` (add `build_discovery_planner`)
- Modify: `jobfit/scripts/update_jobs.py` `main()` (add `--plans`)
- Test: `jobfit/server/tests/test_scrape_planner.py`

**Interfaces:**
- Consumes: Tasks 1-13, `ats_fetchers._jsonld_job_postings`, `strategies.page_fingerprint`.
- Produces: `classifiers.PlanClassifier.derived_by: DerivedBy` (class attribute: `"rules"` on `RulesPlanClassifier`, `"llm"` on `RecordedPlanClassifier` since it stands in for the LLM in tests); `planner.PlanInducer().induce(labels, candidates, page, renderer) -> HtmlListingStrategy`; `planner.PlanValidator(chain_builder).validate(strategy, labels, candidates) -> bool`; `planner.ScrapePlanner(registry, fetchers, extractor, classifier, inducer, validator, special_hosts, now=None, cooldown_days=7).discover(company_id, career_url) -> tuple[ScrapePlan, Page | None]`; `bootstrap.build_discovery_planner(session, classifier=None) -> ScrapePlanner`; `update_jobs.print_plan_summary(store) -> None` and the `--plans` flag.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_planner.py
"""ScrapePlanner.discover: probes decide most companies with no
classifier call at all; the rest are classified, induced and validated
before a plan can be marked verified. Everything here uses a fake
fetcher factory serving canned pages per (renderer, url)."""

from datetime import datetime, timedelta, timezone

import pytest

from jobfit.scrape import classifiers, errors, models
from jobfit.scrape.ats import default_registry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.factory import StrategyFactory
from jobfit.scrape.fetchers import PageFetcher, PageFetcherFactory, make_page
from jobfit.scrape.health import HealthPolicy
from jobfit.scrape.planner import PlanInducer, PlanValidator, ScrapePlanner
from jobfit.scrape.enrich import NoopEnricher

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CAREER = "https://acme.com/careers/"
LISTING = """
<html><body>
<nav><a href="/about">About Us Page</a><a href="https://acme.com/docs/x">Read The Documentation</a></nav>
<ul>
 <li><a href="/careers/backend-engineer-1">Backend Engineer</a></li>
 <li><a href="/careers/frontend-engineer-2">Frontend Engineer</a></li>
 <li><a href="/careers/devops-engineer-3">DevOps Engineer</a></li>
</ul>
<a href="/code-governance">Code Governance and Compliance</a>
</body></html>
"""
SHELL = '<html><body><div id="root"></div><script>window.__NEXT_DATA__={}</script></body></html>'
EXTERNAL = '<html><body><p>Jobs</p><iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe></body></html>'


class _Fetcher(PageFetcher):
    def __init__(self, pages, renderer):
        self.pages, self.renderer, self.calls = pages, renderer, []

    def fetch(self, url):
        self.calls.append(url)
        entry = self.pages.get((self.renderer, url))
        if entry is None:
            raise errors.FetchFailed(url)
        status, html, final = entry
        return make_page(url, final or url, status, html, self.renderer, NOW)


class _Factory(PageFetcherFactory):
    def __init__(self, pages):
        self.pages = pages
        self.http = _Fetcher(pages, "http")
        self.pw = _Fetcher(pages, "playwright")

    def build(self, renderer):
        return self.pw if renderer == "playwright" else self.http


class _Counting(classifiers.RulesPlanClassifier):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def classify(self, page, candidates, career_url):
        self.calls += 1
        return super().classify(page, candidates, career_url)


class _Failing(classifiers.PlanClassifier):
    derived_by = "llm"

    def classify(self, page, candidates, career_url):
        raise errors.ClassifierFailed("api down")


def _planner(pages, classifier=None):
    registry = default_registry(session=None)
    factory = StrategyFactory(registry=registry, fetchers=PageFetcherFactory(session=None, playwright_available=False), extractor=CandidateExtractor(),
                              enricher=NoopEnricher(), reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    classifier = classifier or _Counting()
    return ScrapePlanner(registry, _Factory(pages), CandidateExtractor(), classifier, PlanInducer(), PlanValidator(factory.chain_for),
                         special_hosts=["elbitsystemscareer.com"], now=lambda: NOW), classifier


def test_probes_decide_without_fetching_or_classifying():
    planner, classifier = _planner({})
    assert planner.discover("acme", None)[0].strategy.kind == "techmap_only"
    ats, _ = planner.discover("acme", "https://jobs.lever.co/acme")
    assert ats.strategy.kind == "ats_api" and ats.derived_by == "probe" and ats.status == "verified"
    special, _ = planner.discover("elbit", "https://elbitsystemscareer.com/")
    assert special.strategy.kind == "special_case"
    assert classifier.calls == 0


def test_http_404_and_homepage_redirect_are_verified_broken_urls_and_5xx_raises():
    planner, _ = _planner({
        ("http", "https://gone.com/careers"): (404, "<p>gone</p>", None),
        ("http", "https://home.com/careers"): (200, "<p>home</p>", "https://home.com/"),
        ("http", "https://down.com/careers"): (503, "<p>oops</p>", None),
    })
    assert planner.discover("gone", "https://gone.com/careers")[0].strategy == models.BrokenUrlStrategy(reason="http 404")
    home, _ = planner.discover("home", "https://home.com/careers")
    assert home.strategy == models.BrokenUrlStrategy(reason="redirects to homepage") and home.status == "verified"
    with pytest.raises(errors.FetchFailed):
        planner.discover("down", "https://down.com/careers")


def test_external_board_on_the_page_is_detected_before_classification():
    planner, classifier = _planner({("http", CAREER): (200, EXTERNAL, None)})
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy == models.ExternalBoardStrategy(board_url="https://boards.greenhouse.io/embed/job_board?for=acme")
    assert plan.derived_by == "probe" and classifier.calls == 0


def test_js_shell_is_refetched_with_playwright_and_the_plan_records_the_renderer():
    planner, _ = _planner({("http", CAREER): (200, SHELL, None), ("playwright", CAREER): (200, LISTING, None)})
    plan, page = planner.discover("acme", CAREER)
    assert page.renderer == "playwright"
    assert plan.strategy.kind == "html_listing" and plan.strategy.renderer == "playwright"
    assert plan.strategy.fallbacks == ["techmap"]


def test_rules_classified_listing_is_induced_and_verified():
    planner, classifier = _planner({("http", CAREER): (200, LISTING, None)})
    plan, page = planner.discover("acme", CAREER)
    assert classifier.calls == 1
    assert plan.derived_by == "rules" and plan.status == "verified" and plan.verified_at == NOW
    s = plan.strategy
    assert s.kind == "html_listing" and s.renderer == "http"
    assert s.include_url == r"^https?://(www\.)?acme\.com/careers/[^/?#]+/?$"
    assert s.url_shape == "acme.com|careers|2"
    assert s.fallbacks == ["playwright", "techmap"]
    assert plan.labels is not None and sum(l.is_job for l in plan.labels.candidates) == 3
    assert plan.health.baseline_yield == 3
    assert plan.page_fingerprint.candidate_count == 6
    assert plan.rediscover_after == NOW + timedelta(days=7)


def test_induction_failure_falls_back_to_explicit_accept_and_unverified():
    mixed = """
    <ul><li><a href="/careers/one">Backend Engineer</a></li><li><a href="/careers/two">Frontend Engineer</a></li><li><a href="/careers/three">DevOps Engineer</a></li></ul>
    <a href="/careers/benefits">Benefits And Perks Overview</a>
    """
    # Recorded labels: the three jobs yes, "Benefits" no - but it shares the jobs' URL shape, so no pattern can separate them.
    page = make_page(CAREER, CAREER, 200, mixed, "http", NOW)
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = models.Labels(page_verdict="careers_page", candidates=[
        models.CandidateLabel(index=c.index, is_job=("benefits" not in c.href), reason="fixture") for c in candidates
    ])
    planner, _ = _planner({("http", CAREER): (200, mixed, None)}, classifier=classifiers.RecordedPlanClassifier({CAREER: labels}))
    plan, _ = planner.discover("acme", CAREER)
    assert plan.status == "unverified" and plan.derived_by == "llm"
    assert plan.strategy.include_url is None
    assert sorted(plan.strategy.explicit_accept) == ["https://acme.com/careers/one", "https://acme.com/careers/three", "https://acme.com/careers/two"]


def test_not_a_careers_page_verdict_only_adds_a_note():
    labels = models.Labels(page_verdict="not_careers_page", candidates=[])
    planner, _ = _planner({("http", CAREER): (200, LISTING, None)}, classifier=classifiers.RecordedPlanClassifier({CAREER: labels}))
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy.kind == "html_listing" and plan.status == "unverified"
    assert any("not a careers page" in n for n in plan.notes)


def test_classifier_failure_falls_back_to_rules_with_a_note():
    planner, _ = _planner({("http", CAREER): (200, LISTING, None)}, classifier=_Failing())
    plan, _ = planner.discover("acme", CAREER)
    assert plan.derived_by == "rules" and plan.status == "verified"
    assert any("classifier failed" in n for n in plan.notes)


def test_page_with_no_anchors_is_techmap_only_unverified():
    planner, classifier = _planner({("http", CAREER): (200, "<html><body><p>We are hiring soon.</p></body></html>", None)})
    plan, _ = planner.discover("acme", CAREER)
    assert plan.strategy == models.TechmapOnlyStrategy(reason="no anchors on page") and plan.status == "unverified"
    assert classifier.calls == 0


def test_inducer_handles_flat_query_schemes_and_exclude_shapes():
    html = """
    <a href="index.php?a=show&joborderid=1">Administrative Assistant</a>
    <a href="index.php?a=show&joborderid=2">Backend Developer</a>
    <a href="index.php?a=show&joborderid=3">Frontend Developer</a>
    <a href="/solutions/firewall">Next Generation Firewall</a>
    """
    url = "https://careers.checkpoint.com/index.php?q="
    page = make_page(url, url, 200, html, "http", NOW)
    candidates = CandidateExtractor().extract(page, url)
    labels = models.Labels(page_verdict="careers_page", candidates=[models.CandidateLabel(index=c.index, is_job="joborderid" in c.href, reason="f") for c in candidates])
    strategy = PlanInducer().induce(labels, candidates, page, "http")
    assert strategy.include_url == r"^https?://(www\.)?careers\.checkpoint\.com/index\.php\?.*\bjoborderid="
    assert strategy.exclude_url == [r"^https?://(www\.)?careers\.checkpoint\.com/solutions/[^/?#]+/?$"]
    assert strategy.url_shape == "careers.checkpoint.com|?a,joborderid"


def test_validator_requires_every_labelled_job_accepted_and_no_labelled_non_job_accepted():
    page = make_page(CAREER, CAREER, 200, LISTING, "http", NOW)
    candidates = CandidateExtractor().extract(page, CAREER)
    labels = classifiers.RulesPlanClassifier().classify(page, candidates, CAREER)
    factory = StrategyFactory(registry=default_registry(session=None), fetchers=PageFetcherFactory(session=None, playwright_available=False), extractor=CandidateExtractor(),
                              enricher=NoopEnricher(), reject_patterns=[], techmap_index={}, health=HealthPolicy(), special_fetchers={}, session=None)
    validator = PlanValidator(factory.chain_for)
    good = PlanInducer().induce(labels, candidates, page, "http")
    assert validator.validate(good, labels, candidates) is True
    too_broad = models.HtmlListingStrategy(include_url=r"^https://acme\.com/")
    assert validator.validate(too_broad, labels, candidates) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_planner.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.scrape.planner'`

- [ ] **Step 3: Add `derived_by` to the classifiers**

In `jobfit/scrape/classifiers.py`: add `derived_by: str = "rules"` as a class attribute on `PlanClassifier` (under `class PlanClassifier(ABC):`), leave `RulesPlanClassifier` inheriting it, and add `derived_by = "llm"` on `RecordedPlanClassifier`.

- [ ] **Step 4: Write planner.py**

```python
# jobfit/scrape/planner.py
"""Discovery: (company, career url) -> ScrapePlan. Probes first (no
classifier involved), then classification, induction and validation. The
classifier is injected - RulesPlanClassifier here, LLMPlanClassifier from
the discovery command - and the planner never knows which it got beyond
its `derived_by` label."""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from jobfit import ats_fetchers
from jobfit.scrape.ats import AtsRegistry
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.classifiers import PlanClassifier, RulesPlanClassifier
from jobfit.scrape.errors import ClassifierFailed, FetchFailed
from jobfit.scrape.fetchers import PageFetcherFactory
from jobfit.scrape.filters import FilterChain
from jobfit.scrape.models import (
    AtsApiStrategy, BrokenUrlStrategy, Candidate, ExternalBoardStrategy, HtmlListingStrategy, Labels, Page,
    Renderer, ScrapePlan, SpecialCaseStrategy, Strategy, TechmapOnlyStrategy,
)
from jobfit.scrape.strategies import page_fingerprint

logger = logging.getLogger("jobfit.scrape.discovery")
MAX_EXCLUDES = 10


def _host(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


class PlanInducer:
    """Labels + candidates -> HtmlListingStrategy, deterministically."""

    def induce(self, labels: Labels, candidates: list[Candidate], page: Page, renderer: Renderer) -> HtmlListingStrategy:
        by_index = {c.index: c for c in candidates}
        accepted = [by_index[l.index] for l in labels.candidates if l.is_job and l.index in by_index]
        rejected = [by_index[l.index] for l in labels.candidates if not l.is_job and l.index in by_index]
        fallbacks = ["playwright", "techmap"] if renderer == "http" else ["techmap"]
        if not accepted:
            return HtmlListingStrategy(renderer=renderer, fallbacks=fallbacks)
        url_shape = Counter(c.href_shape for c in accepted).most_common(1)[0][0]
        return HtmlListingStrategy(
            renderer=renderer,
            container_selector=labels.container_selector if self._selector_ok(labels.container_selector, page, accepted) else None,
            include_url=self._include_pattern(accepted),
            exclude_url=self._exclude_patterns(accepted, rejected),
            url_shape=url_shape,
            fallbacks=fallbacks,
        )

    @staticmethod
    def _include_pattern(accepted: list[Candidate]) -> str | None:
        hosts = {_host(c.href) for c in accepted}
        if len(hosts) != 1:
            return None
        host = re.escape(hosts.pop())
        parsed = [urlsplit(c.href) for c in accepted]
        if all(p.query and len(_segments(c.href)) <= 1 for p, c in zip(parsed, accepted)):
            paths = {p.path for p in parsed}
            if len(paths) != 1:
                return None
            key_sets = [set(parse_qs(p.query, keep_blank_values=True)) for p in parsed]
            common = set.intersection(*key_sets)
            if not common:
                return None
            # the key whose values vary most is the job id
            key = max(sorted(common), key=lambda k: len({parse_qs(p.query).get(k, [""])[0] for p in parsed}))
            return rf"^https?://(www\.)?{host}{re.escape(paths.pop())}\?.*\b{re.escape(key)}="
        parents = [_segments(c.href)[:-1] for c in accepted]
        prefix: list[str] = []
        for parts in zip(*parents):
            if len(set(parts)) == 1:
                prefix.append(parts[0])
            else:
                break
        if not prefix:
            return None
        depths = {len(_segments(c.href)) for c in accepted}
        tail = r"[^/?#]+/?$" if depths == {len(prefix) + 1} else r"[^?#]+$"
        return rf"^https?://(www\.)?{host}/{'/'.join(re.escape(s) for s in prefix)}/{tail}"

    @staticmethod
    def _exclude_patterns(accepted: list[Candidate], rejected: list[Candidate]) -> list[str]:
        accepted_shapes = {c.href_shape for c in accepted}
        patterns: list[str] = []
        seen: set[str] = set()
        for c in rejected:
            shape = c.href_shape
            if shape in accepted_shapes or shape in seen or "|?" in shape:
                continue
            seen.add(shape)
            host, parent, _depth = shape.split("|")
            body = "/".join(re.escape(s) for s in parent.split("/") if s)
            patterns.append(rf"^https?://(www\.)?{re.escape(host)}/{body + '/' if body else ''}[^/?#]+/?$")
            if len(patterns) >= MAX_EXCLUDES:
                break
        return patterns

    @staticmethod
    def _selector_ok(selector: str | None, page: Page, accepted: list[Candidate]) -> bool:
        if not selector:
            return False
        try:
            elements = BeautifulSoup(page.html, "html.parser").select(selector)
        except Exception:  # noqa: BLE001 - an invalid selector is simply not used
            return False
        wanted = {c.href for c in accepted}
        for el in elements:
            for a in el.find_all("a", href=True):
                if urljoin(page.url, a["href"]) in wanted:
                    return True
        return False


class PlanValidator:
    def __init__(self, chain_builder: Callable[[HtmlListingStrategy], FilterChain]):
        self.chain_builder = chain_builder

    def validate(self, strategy: HtmlListingStrategy, labels: Labels, candidates: list[Candidate]) -> bool:
        accepted, _ = self.chain_builder(strategy).run(candidates)
        got = {c.index for c in accepted}
        want = {l.index for l in labels.candidates if l.is_job}
        forbid = {l.index for l in labels.candidates if not l.is_job}
        return want <= got and not (forbid & got)


class ScrapePlanner:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 classifier: PlanClassifier, inducer: PlanInducer, validator: PlanValidator, special_hosts: list[str],
                 now: Callable[[], datetime] | None = None, cooldown_days: int = 7):
        self.registry, self.fetchers, self.extractor = registry, fetchers, extractor
        self.classifier, self.inducer, self.validator = classifier, inducer, validator
        self.special_hosts = special_hosts
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.cooldown = timedelta(days=cooldown_days)
        self._rules = RulesPlanClassifier()

    def _probe_plan(self, company_id: str, career_url: str | None, strategy: Strategy, status: str = "verified") -> ScrapePlan:
        now = self.now()
        return ScrapePlan(company_id=company_id, career_url=career_url, derived_by="probe", derived_at=now,
                          verified_at=now if status == "verified" else None, status=status, strategy=strategy)

    def _external_board(self, page: Page) -> str | None:
        soup = BeautifulSoup(page.html, "html.parser")
        counts: Counter[str] = Counter()
        for tag in soup.find_all(["a", "iframe"]):
            target = tag.get("href") or tag.get("src")
            if not target:
                continue
            absolute = urljoin(page.url, target)
            if self.registry.resolve(absolute) is not None:
                counts[absolute] += 1
        if not counts:
            return None
        return counts.most_common(1)[0][0]

    def _jsonld_urls(self, page: Page) -> set[str]:
        soup = BeautifulSoup(page.html, "html.parser")
        return {urljoin(page.url, p["url"]) for p in ats_fetchers._jsonld_job_postings(soup) if isinstance(p.get("url"), str)}

    def _classify(self, page: Page, candidates: list[Candidate], career_url: str, notes: list[str]) -> tuple[Labels, str]:
        try:
            return self.classifier.classify(page, candidates, career_url), self.classifier.derived_by
        except ClassifierFailed as error:
            notes.append(f"classifier failed ({error}); fell back to rules")
            return self._rules.classify(page, candidates, career_url), "rules"

    def discover(self, company_id: str, career_url: str | None) -> tuple[ScrapePlan, Page | None]:
        if not career_url:
            return self._probe_plan(company_id, None, TechmapOnlyStrategy(reason="no career url")), None
        resolved = self.registry.resolve(career_url)
        if resolved is not None:
            client, board = resolved
            return self._probe_plan(company_id, career_url, AtsApiStrategy(provider=client.provider, board=board, board_url=client.board_url(board))), None
        for host in self.special_hosts:
            if host in career_url.lower():
                return self._probe_plan(company_id, career_url, SpecialCaseStrategy(host_fragment=host)), None

        page = self.fetchers.build("http").fetch(career_url)
        if page.status in (404, 410):
            return self._probe_plan(company_id, career_url, BrokenUrlStrategy(reason=f"http {page.status}")), page
        if page.status >= 400:
            raise FetchFailed(f"{career_url}: http {page.status}")
        if urlsplit(page.url).path in ("", "/") and urlsplit(career_url).path not in ("", "/"):
            return self._probe_plan(company_id, career_url, BrokenUrlStrategy(reason="redirects to homepage")), page
        renderer: Renderer = "http"
        if page.is_js_shell:
            page = self.fetchers.build("playwright").fetch(career_url)
            renderer = "playwright"

        board_url = self._external_board(page)
        if board_url:
            return self._probe_plan(company_id, career_url, ExternalBoardStrategy(board_url=board_url)), page

        notes: list[str] = []
        candidates = self.extractor.extract(page, career_url, cap=200)
        if not candidates:
            return self._probe_plan(company_id, career_url, TechmapOnlyStrategy(reason="no anchors on page"), status="unverified"), page

        labels, derived_by = self._classify(page, candidates, career_url, notes)
        if labels.page_verdict == "js_shell" and renderer == "http":
            page = self.fetchers.build("playwright").fetch(career_url)
            renderer = "playwright"
            candidates = self.extractor.extract(page, career_url, cap=200)
            labels, derived_by = self._classify(page, candidates, career_url, notes)
        if labels.page_verdict == "external_board" and labels.external_board_url and self.registry.resolve(labels.external_board_url):
            plan = self._probe_plan(company_id, career_url, ExternalBoardStrategy(board_url=labels.external_board_url))
            return plan.model_copy(update={"derived_by": derived_by, "labels": labels}), page
        if labels.page_verdict == "not_careers_page":
            notes.append("classifier: not a careers page - confirm in the audit and set broken_url by hand if so")

        seeds = self._jsonld_urls(page)
        if seeds:
            by_index = {c.index: c for c in candidates}
            labels = labels.model_copy(update={"candidates": [
                l.model_copy(update={"is_job": True, "reason": "json-ld JobPosting"}) if l.index in by_index and by_index[l.index].href in seeds else l
                for l in labels.candidates
            ]})

        strategy = self.inducer.induce(labels, candidates, page, renderer)
        now = self.now()
        accepted_hrefs = [c.href for c in candidates if any(l.index == c.index and l.is_job for l in labels.candidates)]
        if accepted_hrefs and self.validator.validate(strategy, labels, candidates):
            status, verified_at = "verified", now
        else:
            strategy = strategy.model_copy(update={"include_url": None, "explicit_accept": accepted_hrefs})
            status, verified_at = "unverified", None

        plan = ScrapePlan(
            company_id=company_id, career_url=career_url, derived_by=derived_by, model=getattr(self.classifier, "model", None),
            derived_at=now, verified_at=verified_at, status=status, strategy=strategy, labels=labels,
            page_fingerprint=page_fingerprint(candidates), rediscover_after=now + self.cooldown, notes=notes,
        )
        plan.health.baseline_yield = len(accepted_hrefs)
        return plan, page
```

- [ ] **Step 5: Add `build_discovery_planner` and `--plans`**

Append to `jobfit/scrape/bootstrap.py`:

```python
def build_discovery_planner(session, classifier=None, playwright_available: bool = True):
    """The discovery graph. `classifier` defaults to RulesPlanClassifier;
    Task 16 passes an LLMPlanClassifier built from a lazily-imported
    llm_client. This function is called only by the discovery command."""
    from jobfit.scrape.classifiers import RulesPlanClassifier
    from jobfit.scrape.planner import PlanInducer, PlanValidator, ScrapePlanner

    registry = default_registry(session)
    fetchers = PageFetcherFactory(session, playwright_available)
    factory = StrategyFactory(
        registry=registry, fetchers=fetchers, extractor=CandidateExtractor(), enricher=GenericHtmlEnricher(HttpPageFetcher(session)),
        reject_patterns=load_reject_patterns(), techmap_index={}, health=HealthPolicy(),
        special_fetchers=ats_fetchers.SPECIAL_CASE_FETCHERS, session=session,
    )
    return ScrapePlanner(
        registry, fetchers, CandidateExtractor(), classifier or RulesPlanClassifier(), PlanInducer(), PlanValidator(factory.chain_for),
        special_hosts=list(ats_fetchers.SPECIAL_CASE_FETCHERS),
    )
```

In `jobfit/scripts/update_jobs.py`, add before `main()`:

```python
def print_plan_summary(store) -> None:
    from collections import Counter
    plans = list(store.all())
    by_status = Counter(p.status for p in plans)
    by_derived = Counter(p.derived_by for p in plans)
    by_kind = Counter(p.strategy.kind for p in plans)
    print(f"scrape plans: {len(plans)}")
    print("  by status:     " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("  by derived_by: " + ", ".join(f"{k}={v}" for k, v in sorted(by_derived.items())))
    print("  by kind:       " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    broken = [p for p in plans if p.strategy.kind == "broken_url"]
    if broken:
        print("  broken_url plans (review these):")
        for p in broken:
            print(f"    {p.company_id}: {p.strategy.reason} [{p.status}]")
```

In `main()`, add the argument `parser.add_argument("--plans", action="store_true", help="print a summary of stored scrape plans and exit")` and, immediately after `args = parser.parse_args()`:

```python
    if args.plans:
        from jobfit.scrape.plan_store import FilePlanStore
        print_plan_summary(FilePlanStore(config.SCRAPE_PLANS_DIR))
        return
```

- [ ] **Step 6: Run the tests, then the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_planner.py -q`
Expected: 11 passed

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 396 passed

Run: `uv run python -m jobfit.scripts.update_jobs --plans`
Expected: a summary listing the plan(s) created in Task 13's smoke test.

- [ ] **Step 7: Commit**

```bash
git add jobfit/scrape/planner.py jobfit/scrape/classifiers.py jobfit/scrape/bootstrap.py jobfit/scripts/update_jobs.py jobfit/server/tests/test_scrape_planner.py
git commit -m "feat(scrape): add ScrapePlanner (probes, classify, induce, validate) with the rules classifier, and --plans"
```

**Group B checkpoint.** Every company now runs through a stored plan; the CV-score tier gate is gone; discovery works LLM-free. Review here before Group C.

---

## Group C — LLM discovery

### Task 15: `AnthropicLLMClient` and `LLMPlanClassifier`

The one model call in the whole system. The classifier depends only on the `LLMClient` protocol; the vendor SDK is imported inside `jobfit/scrape/llm_client.py` only.

**Files:**
- Modify: `pyproject.toml` (via `uv add anthropic`)
- Modify: `jobfit/config.py` (append after the block added in Task 10)
- Create: `jobfit/scrape/llm_client.py`
- Modify: `jobfit/scrape/classifiers.py` (add `LLMClient` protocol, `LABELS_SCHEMA`, `SYSTEM_PROMPT`, `build_user_message`, `LLMPlanClassifier`)
- Test: `jobfit/server/tests/test_scrape_llm.py`

**Interfaces:**
- Consumes: `models.Labels`, `models.Candidate`, `models.Page`, `errors.ClassifierFailed`.
- Produces: `config.SCRAPE_PLAN_LLM_MODEL = "claude-haiku-4-5"`, `config.DISCOVERY_MAX_PER_RUN = 200`, `config.DISCOVERY_COOLDOWN_DAYS = 7`, `config.DISCOVERY_CONCURRENCY = 4`; `classifiers.LLMClient` (Protocol: `complete_json(system: str, user: str, schema: dict, max_tokens: int) -> dict`); `classifiers.LLMPlanClassifier(client, model, max_tokens=4096, text_chars=3000)` with `derived_by = "llm"` and a `model` attribute; `classifiers.build_user_message(page, candidates, career_url, text_chars) -> str`; `llm_client.AnthropicLLMClient(model, api_key=None, timeout=60.0)`.

- [ ] **Step 1: Add the dependency and config**

Run: `uv add anthropic`
Expected: `pyproject.toml` gains `"anthropic>=…"` and `uv.lock` updates. Commit these two files on their own: `git commit -m "chore: add the anthropic SDK (used only by scrape-plan discovery)"`.

Append to `jobfit/config.py` after `LINK_REJECTS_PATH`:

```python
# Discovery (`update_jobs --discover`): the ONLY place a model is called.
# A Haiku-class model - the user asked for "a small llm"; one classification
# call per company, once. Never silently substitute a larger model here.
SCRAPE_PLAN_LLM_MODEL = "claude-haiku-4-5"
DISCOVERY_MAX_PER_RUN = 200      # companies per --discover invocation (0 = unbounded, one-time full pass)
DISCOVERY_COOLDOWN_DAYS = 7      # minimum gap before a company is re-discovered
DISCOVERY_CONCURRENCY = 4
```

- [ ] **Step 2: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_llm.py
"""LLMPlanClassifier against a stub LLMClient (never the network), and
the prompt it builds. AnthropicLLMClient is only constructed, never called."""

import json
import sys
from datetime import datetime, timezone

import pytest

from jobfit.scrape import classifiers, errors
from jobfit.scrape.candidates import CandidateExtractor
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.models import Labels

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CAREER = "https://acme.com/careers/"
HTML = "<nav><a href='/about'>About Us Page</a></nav><ul>" + "".join(
    f"<li><a href='/careers/job-{i}'>Engineer number {i}</a></li>" for i in range(5)
) + "</ul>"


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete_json(self, system, user, schema, max_tokens):
        self.calls.append({"system": system, "user": user, "schema": schema, "max_tokens": max_tokens})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _page_and_candidates():
    page = make_page(CAREER, CAREER, 200, HTML, "http", NOW)
    return page, CandidateExtractor().extract(page, CAREER)


def _good_labels(candidates):
    return {"page_verdict": "careers_page", "external_board_url": None, "container_selector": "ul",
            "candidates": [{"index": c.index, "is_job": c.href.startswith("https://acme.com/careers/"), "reason": "ok"} for c in candidates]}


def test_user_message_contains_url_truncated_text_and_the_candidate_table():
    page, candidates = _page_and_candidates()
    page = page.model_copy(update={"text": "x" * 5000})
    message = classifiers.build_user_message(page, candidates, CAREER, text_chars=3000)
    assert CAREER in message
    assert "x" * 3000 in message and "x" * 3001 not in message
    rows = json.loads(message[message.index("["):message.rindex("]") + 1])
    assert len(rows) == len(candidates)
    assert set(rows[0]) == {"index", "text", "href", "ancestor_path", "sibling_anchor_count", "in_chrome"}


def test_classifier_returns_validated_labels_and_sends_the_schema():
    page, candidates = _page_and_candidates()
    client = StubClient([_good_labels(candidates)])
    labels = classifiers.LLMPlanClassifier(client, model="claude-haiku-4-5").classify(page, candidates, CAREER)
    assert isinstance(labels, Labels) and sum(l.is_job for l in labels.candidates) == 5
    call = client.calls[0]
    assert call["schema"] is classifiers.LABELS_SCHEMA and call["max_tokens"] == 4096
    assert "job posting" in call["system"].lower()


def test_classifier_retries_once_on_invalid_output_then_succeeds():
    page, candidates = _page_and_candidates()
    client = StubClient([{"page_verdict": "maybe", "candidates": []}, _good_labels(candidates)])
    labels = classifiers.LLMPlanClassifier(client, model="m").classify(page, candidates, CAREER)
    assert len(client.calls) == 2
    assert "invalid" in client.calls[1]["user"].lower()
    assert labels.page_verdict == "careers_page"


def test_classifier_fails_after_two_invalid_answers_or_unknown_indexes():
    page, candidates = _page_and_candidates()
    bad = {"page_verdict": "careers_page", "candidates": [{"index": 999, "is_job": True, "reason": "?"}]}
    with pytest.raises(errors.ClassifierFailed):
        classifiers.LLMPlanClassifier(StubClient([bad, bad]), model="m").classify(page, candidates, CAREER)


def test_classifier_propagates_client_failures_as_classifier_failed():
    page, candidates = _page_and_candidates()
    with pytest.raises(errors.ClassifierFailed):
        classifiers.LLMPlanClassifier(StubClient([errors.ClassifierFailed("rate limited")]), model="m").classify(page, candidates, CAREER)


def test_classifiers_module_does_not_import_the_vendor_sdk():
    """Static check (the runtime guard is test_scrape_no_llm_at_runtime.py)."""
    import importlib.util
    source = importlib.util.find_spec("jobfit.scrape.classifiers").origin
    text = open(source, encoding="utf-8").read()
    assert "import anthropic" not in text and "llm_client" not in text


def test_anthropic_client_maps_a_text_response_to_json():
    anthropic = pytest.importorskip("anthropic")
    from jobfit.scrape.llm_client import AnthropicLLMClient

    client = AnthropicLLMClient(model="claude-haiku-4-5", api_key="test-key")

    class _Block:
        type = "text"
        text = json.dumps({"page_verdict": "careers_page", "candidates": []})

    class _Response:
        stop_reason = "end_turn"
        content = [_Block()]

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Response()

    client.client.messages.create = fake_create
    data = client.complete_json("sys", "user", {"type": "object"}, 512)
    assert data == {"page_verdict": "careers_page", "candidates": []}
    assert captured["model"] == "claude-haiku-4-5" and captured["temperature"] == 0
    assert captured["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert captured["system"] == "sys" and captured["messages"] == [{"role": "user", "content": "user"}]


def test_anthropic_client_maps_sdk_errors_to_classifier_failed():
    anthropic = pytest.importorskip("anthropic")
    from jobfit.scrape.llm_client import AnthropicLLMClient

    client = AnthropicLLMClient(model="claude-haiku-4-5", api_key="test-key")

    def boom(**kwargs):
        raise anthropic.APIConnectionError(request=None)

    client.client.messages.create = boom
    with pytest.raises(errors.ClassifierFailed):
        client.complete_json("sys", "user", {"type": "object"}, 512)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_llm.py -q`
Expected: FAIL with `AttributeError: module 'jobfit.scrape.classifiers' has no attribute 'build_user_message'`

- [ ] **Step 4: Write llm_client.py**

```python
# jobfit/scrape/llm_client.py
"""The only module in jobfit that imports the Anthropic SDK. Nothing under
jobfit/scrape/ imports this at module level; bootstrap.build_discovery_planner
imports it lazily, and only the discovery command calls that."""

from __future__ import annotations

import json

from jobfit.scrape.errors import ClassifierFailed


class AnthropicLLMClient:
    """LLMClient over the Messages API with a JSON-schema structured output.
    Deterministic settings (temperature 0); one classification per call."""

    def __init__(self, model: str, api_key: str | None = None, timeout: float = 60.0):
        import anthropic

        self._anthropic = anthropic
        kwargs = {"timeout": timeout, "max_retries": 2}
        if api_key:
            kwargs["api_key"] = api_key
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model

    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        a = self._anthropic
        try:
            response = self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=0, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except a.RateLimitError as error:
            raise ClassifierFailed(f"rate limited: {error}") from error
        except a.APIStatusError as error:
            raise ClassifierFailed(f"api error {error.status_code}: {error}") from error
        except a.APIConnectionError as error:
            raise ClassifierFailed(f"connection error: {error}") from error
        if getattr(response, "stop_reason", None) == "refusal":
            raise ClassifierFailed("model refused the request")
        text = next((block.text for block in response.content if getattr(block, "type", "") == "text"), "")
        try:
            return json.loads(text)
        except ValueError as error:
            raise ClassifierFailed(f"non-JSON response: {text[:200]!r}") from error
```

- [ ] **Step 5: Add the protocol, prompt, schema and classifier to classifiers.py**

Append to `jobfit/scrape/classifiers.py` (add `from typing import Protocol` and `from pydantic import ValidationError` to its imports):

```python
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
```

- [ ] **Step 6: Run the tests, then the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_llm.py -q`
Expected: 8 passed

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 404 passed

- [ ] **Step 7: Commit**

```bash
git add jobfit/config.py jobfit/scrape/llm_client.py jobfit/scrape/classifiers.py jobfit/server/tests/test_scrape_llm.py
git commit -m "feat(scrape): add LLMPlanClassifier over an LLMClient protocol and the Anthropic implementation (discovery only)"
```

---

### Task 16: The discovery command, budget and cooldown, snapshots, and the no-model-at-runtime guard

**Files:**
- Modify: `jobfit/scrape/bootstrap.py` (`build_discovery_planner` gains `use_llm`/`model`)
- Modify: `jobfit/scripts/update_jobs.py` (`select_for_discovery`, `discover_plans`, `DiscoveryStats`, CLI flags)
- Test: `jobfit/server/tests/test_scrape_discover.py`, `jobfit/server/tests/test_scrape_no_llm_at_runtime.py`

**Interfaces:**
- Consumes: Tasks 10-15, `pipeline_lock.PipelineLock`.
- Produces: `bootstrap.build_discovery_planner(session, classifier=None, playwright_available=True, use_llm=False, model=None) -> ScrapePlanner`; `update_jobs.DiscoveryStats` (dataclass: `selected, discovered, verified, unverified, broken, failed: list[str]`); `update_jobs.select_for_discovery(companies, store, now, max_per_run, force_company=None) -> list[tuple[str, str | None]]`; `update_jobs.discover_plans(companies, planner, store, snapshots_dir, max_per_run, concurrency, force_company=None, now=None) -> DiscoveryStats`; CLI flags `--discover`, `--discover-max N`, `--rediscover`, `--no-llm`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_discover.py
"""The discovery batch: which companies get (re)discovered, in what
order, under what cap; plans and snapshots written; failures recorded."""

from datetime import datetime, timedelta, timezone

import pytest

from jobfit import config
from jobfit.scrape import errors, models
from jobfit.scrape.fetchers import make_page
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scripts import update_jobs

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _plan(company_id, status="verified", derived_by="llm", rediscover_after=None):
    return models.ScrapePlan(company_id=company_id, career_url=f"https://{company_id}.com/careers", derived_by=derived_by, derived_at=NOW,
                             status=status, strategy=models.HtmlListingStrategy(), rediscover_after=rediscover_after)


def test_selection_order_missing_then_stale_then_rules_unverified_with_cooldown_and_cap():
    store = MemoryPlanStore()
    store.put(_plan("verified_co"))
    store.put(_plan("stale_co", status="stale_suspect", rediscover_after=NOW - timedelta(days=1)))
    store.put(_plan("cooling_co", status="stale_suspect", rediscover_after=NOW + timedelta(days=3)))
    store.put(_plan("rules_co", status="unverified", derived_by="rules"))
    store.put(_plan("schema_co", status="stale"))
    companies = {"Missing Co": "https://missing.com/careers", "Verified Co": "https://verified_co.com/careers", "Stale Co": "https://stale_co.com/careers",
                 "Cooling Co": "https://cooling_co.com/careers", "Rules Co": "https://rules_co.com/careers", "Schema Co": "https://schema_co.com/careers"}
    selected = update_jobs.select_for_discovery(companies, store, NOW, max_per_run=0)
    assert [name for name, _ in selected] == ["Missing Co", "Stale Co", "Schema Co", "Rules Co"]
    assert [name for name, _ in update_jobs.select_for_discovery(companies, store, NOW, max_per_run=2)] == ["Missing Co", "Stale Co"]
    assert update_jobs.select_for_discovery(companies, store, NOW, max_per_run=0, force_company="Cooling Co") == [("Cooling Co", "https://cooling_co.com/careers")]


class _Planner:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def discover(self, company_id, career_url):
        if company_id in self.fail:
            raise errors.FetchFailed(company_id)
        status = "verified" if career_url else "unverified"
        plan = models.ScrapePlan(company_id=company_id, career_url=career_url, derived_by="rules", derived_at=NOW, status=status,
                                 strategy=models.HtmlListingStrategy() if career_url else models.TechmapOnlyStrategy(reason="x"))
        page = make_page(career_url, career_url, 200, "<p>snap</p>", "http", NOW) if career_url else None
        return plan, page


def test_discover_plans_writes_plans_and_snapshots_and_records_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".lock")
    store = MemoryPlanStore()
    companies = {"Acme": "https://acme.com/careers", "Beta": None, "Down": "https://down.com/careers"}
    stats = update_jobs.discover_plans(companies, _Planner(fail={"down"}), store, tmp_path / "snaps", max_per_run=0, concurrency=2, now=lambda: NOW)
    assert stats.selected == 3 and stats.discovered == 2 and stats.failed == ["Down"]
    assert stats.verified == 1 and stats.unverified == 1
    assert store.get("acme").status == "verified" and store.get("beta").status == "unverified"
    assert (tmp_path / "snaps" / "acme.html").read_text(encoding="utf-8") == "<p>snap</p>"
    assert not (tmp_path / "snaps" / "beta.html").exists()


def test_cli_rediscover_requires_company(monkeypatch):
    monkeypatch.setattr("sys.argv", ["update_jobs", "--rediscover"])
    with pytest.raises(SystemExit):
        update_jobs.main()
```

```python
# jobfit/server/tests/test_scrape_no_llm_at_runtime.py
"""Executable form of spec principle 2: a runtime scrape through the
production composition root never imports the model client module or
the vendor SDK."""

import sys
from datetime import datetime, timezone

from jobfit import config
from jobfit.scrape import bootstrap


class _Resp:
    status_code = 200
    url = "https://acme.com/careers/"
    text = "<ul><li><a href='/careers/backend-1'>Backend Engineer</a></li><li><a href='/careers/frontend-2'>Frontend Engineer</a></li><li><a href='/careers/devops-3'>DevOps Engineer</a></li></ul>"


class _Session:
    def request(self, method, url, **kwargs):
        return _Resp()


def test_runtime_scrape_never_imports_llm_client_or_anthropic(tmp_path, monkeypatch):
    for name in list(sys.modules):
        if name == "jobfit.scrape.llm_client" or name == "anthropic" or name.startswith("anthropic."):
            sys.modules.pop(name)
    monkeypatch.setattr(config, "PAGE_CACHE_DIR", tmp_path / "pages")
    monkeypatch.setattr(config, "LINK_REJECTS_PATH", tmp_path / "rejects.json")

    service = bootstrap.build_scrape_service(_Session(), techmap_index={}, plans_dir=tmp_path / "plans", playwright_available=False)
    result = service.scrape("Acme", "https://acme.com/careers/")

    assert len(result.postings) == 3
    assert "jobfit.scrape.llm_client" not in sys.modules
    assert "anthropic" not in sys.modules


def test_importing_update_jobs_does_not_import_llm_client():
    for name in list(sys.modules):
        if name == "jobfit.scrape.llm_client":
            sys.modules.pop(name)
    import importlib
    import jobfit.scripts.update_jobs as uj
    importlib.reload(uj)
    assert "jobfit.scrape.llm_client" not in sys.modules
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_discover.py jobfit/server/tests/test_scrape_no_llm_at_runtime.py -q`
Expected: `test_scrape_discover.py` FAILS with `AttributeError: module ... has no attribute 'select_for_discovery'`; the guard tests already PASS (nothing on the runtime path imports the client) — they stay as the regression guard.

- [ ] **Step 3: Extend `build_discovery_planner`**

Replace the signature and the classifier line in `jobfit/scrape/bootstrap.py`:

```python
def build_discovery_planner(session, classifier=None, playwright_available: bool = True, use_llm: bool = False, model: str | None = None):
    """The discovery graph. classifier precedence: an explicit `classifier`;
    else, with use_llm, an LLMPlanClassifier over AnthropicLLMClient
    (imported lazily HERE and nowhere else); else RulesPlanClassifier. If
    the model client cannot be constructed (no credentials), log a warning
    and fall back to rules so discovery still produces plans."""
    import logging
    from jobfit.scrape.classifiers import RulesPlanClassifier
    from jobfit.scrape.planner import PlanInducer, PlanValidator, ScrapePlanner

    if classifier is None and use_llm:
        try:
            from jobfit.scrape.classifiers import LLMPlanClassifier
            from jobfit.scrape.llm_client import AnthropicLLMClient
            model = model or config.SCRAPE_PLAN_LLM_MODEL
            classifier = LLMPlanClassifier(AnthropicLLMClient(model), model)
        except Exception as error:  # noqa: BLE001 - missing SDK or credentials: degrade to rules, loudly
            logging.getLogger("jobfit.scrape.discovery").warning("LLM classifier unavailable (%s); using rules", error)
            classifier = None

    registry = default_registry(session)
    fetchers = PageFetcherFactory(session, playwright_available)
    factory = StrategyFactory(
        registry=registry, fetchers=fetchers, extractor=CandidateExtractor(), enricher=GenericHtmlEnricher(HttpPageFetcher(session)),
        reject_patterns=load_reject_patterns(), techmap_index={}, health=HealthPolicy(),
        special_fetchers=ats_fetchers.SPECIAL_CASE_FETCHERS, session=session,
    )
    return ScrapePlanner(
        registry, fetchers, CandidateExtractor(), classifier or RulesPlanClassifier(), PlanInducer(), PlanValidator(factory.chain_for),
        special_hosts=list(ats_fetchers.SPECIAL_CASE_FETCHERS), cooldown_days=config.DISCOVERY_COOLDOWN_DAYS,
    )
```

(This replaces the whole function from Task 14; the only differences are the `use_llm`/`model` parameters, the lazy-import block, and `cooldown_days`.)

- [ ] **Step 4: Add discovery to update_jobs.py**

Add after `print_plan_summary`:

```python
@dataclass
class DiscoveryStats:
    selected: int = 0
    discovered: int = 0
    verified: int = 0
    unverified: int = 0
    broken: int = 0
    failed: list[str] = field(default_factory=list)


def select_for_discovery(companies: dict[str, str | None], store, now: datetime, max_per_run: int, force_company: str | None = None) -> list[tuple[str, str | None]]:
    """Missing plans first, then stale/stale_suspect past their cooldown,
    then rules-derived unverified plans past their cooldown; capped."""
    from jobfit.scrape.ids import plan_id_for

    if force_company:
        return [(force_company, companies[force_company])]
    missing, stale, rules = [], [], []
    for company, url in companies.items():
        plan = store.get(plan_id_for(company))
        if plan is None:
            missing.append((company, url))
            continue
        cooling = plan.rediscover_after is not None and plan.rediscover_after > now
        if plan.status in ("stale_suspect", "stale") and not cooling:
            stale.append((company, url))
        elif plan.status == "unverified" and plan.derived_by == "rules" and not cooling:
            rules.append((company, url))
    ordered = missing + stale + rules
    return ordered if max_per_run == 0 else ordered[:max_per_run]


def discover_plans(companies: dict[str, str | None], planner, store, snapshots_dir: Path, max_per_run: int, concurrency: int,
                   force_company: str | None = None, now=None) -> DiscoveryStats:
    """The discovery batch - the only code path that may call a model
    (through the planner's classifier). Writes one plan per company and
    the listing snapshot it was derived from."""
    from jobfit.scrape.errors import FetchFailed
    from jobfit.scrape.ids import plan_id_for

    now = now or (lambda: datetime.now(timezone.utc))
    stats = DiscoveryStats()
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="discover", scope=force_company or "batch"):
        selected = select_for_discovery(companies, store, now(), max_per_run, force_company)
        stats.selected = len(selected)
        logger.info("discovery: %d companies selected (max %s)", len(selected), max_per_run or "unbounded")

        def one(company, url):
            company_id = plan_id_for(company)
            plan, page = planner.discover(company_id, url)
            store.put(plan)
            if page is not None:
                snapshots_dir.mkdir(parents=True, exist_ok=True)
                tmp = snapshots_dir / f"{company_id}.html.tmp"
                tmp.write_text(page.html, encoding="utf-8")
                tmp.replace(snapshots_dir / f"{company_id}.html")
            return company, plan

        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(one, company, url): company for company, url in selected}
            for future in as_completed(futures):
                company = futures[future]
                try:
                    _, plan = future.result()
                except FetchFailed as error:
                    stats.failed.append(company)
                    logger.warning("%s: discovery failed - %s", company, error)
                    continue
                except Exception as error:  # noqa: BLE001 - one bad company must never abort the batch
                    stats.failed.append(company)
                    logger.warning("%s: discovery crashed - %s: %s", company, type(error).__name__, error)
                    continue
                stats.discovered += 1
                if plan.strategy.kind == "broken_url":
                    stats.broken += 1
                if plan.status == "verified":
                    stats.verified += 1
                else:
                    stats.unverified += 1
                logger.info("%s: plan %s/%s via %s", company, plan.strategy.kind, plan.status, plan.derived_by)
        stats.failed.sort()
    return stats
```

In `main()`, add the arguments:

```python
    parser.add_argument("--discover", action="store_true", help="derive scrape plans for companies that need one (missing/stale/rules-unverified) before scraping; the only mode that may call a model")
    parser.add_argument("--discover-max", type=int, default=config.DISCOVERY_MAX_PER_RUN, help="companies per --discover run (0 = unbounded)")
    parser.add_argument("--rediscover", action="store_true", help="force re-discovery of --company, ignoring budget and cooldown")
    parser.add_argument("--no-llm", action="store_true", help="discovery with the rules classifier only")
```

Immediately after `args = parser.parse_args()` (before the `--plans` early return and before the lock is taken - argument errors must never need the lock):

```python
    if args.rediscover and not args.company:
        parser.error("--rediscover requires --company")
```

Then, inside the `with pipeline_lock.PipelineLock(...)` block right after the `--company/--limit` scoping and before `stats = scrape_stage(...)`:

```python
        if args.discover or args.rediscover:
            from jobfit.scrape.plan_store import FilePlanStore
            session = ats_fetchers.make_session()
            planner = scrape_bootstrap.build_discovery_planner(session, use_llm=not args.no_llm)
            discovery = discover_plans(
                companies, planner, FilePlanStore(config.SCRAPE_PLANS_DIR), config.LISTING_SNAPSHOTS_DIR,
                max_per_run=0 if args.rediscover else args.discover_max, concurrency=config.DISCOVERY_CONCURRENCY,
                force_company=args.company if args.rediscover else None,
            )
            print()
            print("=== Discovery summary ===")
            print(f"selected: {discovery.selected}, discovered: {discovery.discovered} (verified {discovery.verified}, unverified {discovery.unverified}, broken {discovery.broken})")
            print(f"failed: {len(discovery.failed)}" + (f" ({', '.join(discovery.failed)})" if discovery.failed else ""))
```

- [ ] **Step 5: Run the tests, then the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_discover.py jobfit/server/tests/test_scrape_no_llm_at_runtime.py -q`
Expected: 5 passed

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 409 passed

- [ ] **Step 6: Run the first real discovery, small, then review**

Run: `uv run python -m jobfit.scripts.update_jobs --discover --discover-max 10 --limit 10 --skip-aggregate`
Expected: 10 plans under `jobfit/data/scrape_plans/`, snapshots under `jobfit/cache/listing_snapshots/`, a discovery summary, then the normal scrape of those 10 companies through their new plans. Read two or three of the plan files by eye: `labels.candidates` should read sensibly. Then `uv run python -m jobfit.scripts.update_jobs --plans`.

Only after that looks right, the one-time full pass (this is the "first hard scrape"; roughly an hour at 4 concurrent calls):

Run: `uv run python -m jobfit.scripts.update_jobs --discover --discover-max 0 --skip-aggregate`

- [ ] **Step 7: Commit**

```bash
git add jobfit/scrape/bootstrap.py jobfit/scripts/update_jobs.py jobfit/server/tests/test_scrape_discover.py jobfit/server/tests/test_scrape_no_llm_at_runtime.py jobfit/data/scrape_plans jobfit/cache/listing_snapshots
git commit -m "feat(scrape): add update_jobs --discover/--rediscover/--discover-max/--no-llm - the one-time, budgeted, LLM-assisted plan discovery"
```

---

### Task 17: Audit script, reject list, and the snapshot replay test

**Files:**
- Create: `jobfit/scripts/audit_scrape.py`
- Create: `jobfit/data/link_rejects.json` (initial content `{"patterns": []}`)
- Test: `jobfit/server/tests/test_scrape_audit.py`, `jobfit/server/tests/test_scrape_plans_replay.py`

**Interfaces:**
- Consumes: `update_jobs.COMPANIES_DIR`, `plan_store.FilePlanStore`, `candidates.href_shape`, `jobfit.ats_scorer.taxonomy.load_role_families`, `config.LINK_REJECTS_PATH`, `bootstrap.load_reject_patterns`, `factory.StrategyFactory.chain_for`.
- Produces: `audit_scrape.suspicion_reasons(job: dict, sibling_descriptions: list[str], plan) -> list[str]`; `audit_scrape.audit(companies_dir, store, limit) -> list[dict]` (rows `{company, title, url, reasons}` sorted by number of reasons desc); `audit_scrape.add_reject_pattern(pattern, path) -> list[str]`; CLI `uv run python -m jobfit.scripts.audit_scrape [--limit N] [--company NAME] [--reject REGEX]`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_scrape_audit.py
"""The audit turns "eyeball the UI for fake jobs" into a ranked queue,
and a decision into a persistent reject pattern the scraper enforces."""

import json
from datetime import datetime, timezone

from jobfit.scrape import models
from jobfit.scrape.plan_store import MemoryPlanStore
from jobfit.scripts import audit_scrape

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)
CHROME = "Skip to main content AI platform Solutions Resources Enterprise Pricing Log in Contact sales " * 20


def _plan(url_shape="acme.com|careers|2"):
    return models.ScrapePlan(company_id="acme", career_url="https://acme.com/careers", derived_by="llm", derived_at=NOW, status="verified",
                             strategy=models.HtmlListingStrategy(url_shape=url_shape))


def test_reasons_flag_missing_evidence_unclassifiable_title_off_shape_url_and_site_chrome():
    job = {"title": "Contact sales", "url": "https://acme.com/sales/contact-us", "description": CHROME,
           "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "acme.com|sales|2"}}
    reasons = audit_scrape.suspicion_reasons(job, [CHROME + " extra"], _plan())
    assert set(reasons) == {"no evidence", "title is not a role", "url off plan shape", "description duplicates sibling (site chrome)"}


def test_a_real_job_has_no_reasons():
    job = {"title": "Backend Engineer", "url": "https://acme.com/careers/backend-1", "description": "Requirements: 5+ years Python",
           "job_evidence": {"jsonld_jobposting": True, "apply_cta": True, "requirement_sections": 1, "role_family_from_title": "backend", "url_shape": "acme.com|careers|2"}}
    assert audit_scrape.suspicion_reasons(job, ["Requirements: 3+ years Go"], _plan()) == []


def test_audit_ranks_companies_jobs_by_reason_count(tmp_path):
    (tmp_path / "acme.json").write_text(json.dumps({"name": "Acme", "career_url": "https://acme.com/careers", "jobs": [
        {"id": "1", "title": "Backend Engineer", "url": "https://acme.com/careers/backend-1", "description": "Requirements: Python", "status": "new",
         "job_evidence": {"jsonld_jobposting": True, "apply_cta": False, "requirement_sections": 1, "role_family_from_title": "backend", "url_shape": "acme.com|careers|2"}},
        {"id": "2", "title": "Contact sales", "url": "https://acme.com/sales/contact-us", "description": CHROME, "status": "new",
         "job_evidence": {"jsonld_jobposting": False, "apply_cta": False, "requirement_sections": 0, "role_family_from_title": None, "url_shape": "acme.com|sales|2"}},
    ]}), encoding="utf-8")
    store = MemoryPlanStore()
    store.put(_plan())
    rows = audit_scrape.audit(tmp_path, store, limit=10)
    assert rows[0]["title"] == "Contact sales" and len(rows[0]["reasons"]) >= 3
    assert all(r["title"] != "Backend Engineer" for r in rows)


def test_add_reject_pattern_appends_once(tmp_path):
    path = tmp_path / "link_rejects.json"
    assert audit_scrape.add_reject_pattern(r"^https://acme\.com/sales/", path) == [r"^https://acme\.com/sales/"]
    assert audit_scrape.add_reject_pattern(r"^https://acme\.com/sales/", path) == [r"^https://acme\.com/sales/"]
    assert json.loads(path.read_text(encoding="utf-8")) == {"patterns": [r"^https://acme\.com/sales/"]}
```

```python
# jobfit/server/tests/test_scrape_plans_replay.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_audit.py jobfit/server/tests/test_scrape_plans_replay.py -q`
Expected: `test_scrape_audit.py` FAILS with `ModuleNotFoundError: No module named 'jobfit.scripts.audit_scrape'`; the replay test PASSES or is skipped depending on how many plans Task 16 committed (both are fine at this step).

- [ ] **Step 3: Write audit_scrape.py and the empty reject list**

`jobfit/data/link_rejects.json`:

```json
{"patterns": []}
```

```python
# jobfit/scripts/audit_scrape.py
"""Rank stored jobs by how much they look like scraped junk, and record
"not a job" decisions as URL reject patterns the scraper enforces.

Usage:
  uv run python -m jobfit.scripts.audit_scrape [--limit 50] [--company NAME]
  uv run python -m jobfit.scripts.audit_scrape --reject '^https://copyleaks\\.com/[a-z0-9-]+$'
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import config  # noqa: E402
from jobfit.ats_scorer.taxonomy import load_role_families  # noqa: E402
from jobfit.atomic_io import write_json_atomic  # noqa: E402
from jobfit.scrape.candidates import href_shape  # noqa: E402
from jobfit.scrape.ids import plan_id_for  # noqa: E402
from jobfit.scrape.plan_store import FilePlanStore  # noqa: E402

SHARED_PREFIX_CHARS = 300
JACCARD_DUPLICATE = 0.8


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def _duplicates_sibling(description: str, siblings: list[str]) -> bool:
    if len(description or "") < SHARED_PREFIX_CHARS:
        return False
    mine = _words(description)
    for other in siblings:
        if other == description or not other:
            continue
        if description[:SHARED_PREFIX_CHARS] == other[:SHARED_PREFIX_CHARS]:
            return True
        theirs = _words(other)
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= JACCARD_DUPLICATE:
            return True
    return False


def suspicion_reasons(job: dict, sibling_descriptions: list[str], plan) -> list[str]:
    reasons = []
    evidence = job.get("job_evidence") or {}
    has_signal = any([evidence.get("jsonld_jobposting"), evidence.get("apply_cta"), (evidence.get("requirement_sections") or 0) >= 1, evidence.get("role_family_from_title")])
    if not has_signal:
        reasons.append("no evidence")
    if load_role_families().classify(job.get("title") or "") is None:
        reasons.append("title is not a role")
    expected = getattr(getattr(plan, "strategy", None), "url_shape", None) if plan is not None else None
    if expected and job.get("url") and href_shape(job["url"]) != expected:
        reasons.append("url off plan shape")
    if _duplicates_sibling(job.get("description") or "", sibling_descriptions):
        reasons.append("description duplicates sibling (site chrome)")
    return reasons


def audit(companies_dir: Path, store, limit: int, company: str | None = None) -> list[dict]:
    rows = []
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if company and record.get("name") != company:
            continue
        plan = store.get(plan_id_for(record.get("name", path.stem)))
        jobs = [j for j in record.get("jobs", []) if j.get("status") != "closed"]
        descriptions = [j.get("description") or "" for j in jobs]
        for job in jobs:
            reasons = suspicion_reasons(job, descriptions, plan)
            if reasons:
                rows.append({"company": record.get("name"), "title": job.get("title"), "url": job.get("url"), "reasons": reasons})
    rows.sort(key=lambda r: (-len(r["reasons"]), r["company"] or "", r["title"] or ""))
    return rows[:limit] if limit else rows


def add_reject_pattern(pattern: str, path: Path | None = None) -> list[str]:
    re.compile(pattern)  # fail fast on a bad regex
    path = path or config.LINK_REJECTS_PATH
    patterns: list[str] = []
    if path.exists():
        patterns = list(json.loads(path.read_text(encoding="utf-8")).get("patterns", []))
    if pattern not in patterns:
        patterns.append(pattern)
        write_json_atomic(path, {"patterns": patterns})
    return patterns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--company", type=str, default=None)
    parser.add_argument("--reject", type=str, default=None, help="append a URL regex to data/link_rejects.json and exit")
    args = parser.parse_args()
    if args.reject:
        patterns = add_reject_pattern(args.reject)
        print(f"reject patterns now: {len(patterns)}")
        return
    rows = audit(config.ROOT / "companies", FilePlanStore(config.SCRAPE_PLANS_DIR), args.limit, args.company)
    for row in rows:
        print(f"{row['company']!s:40} {row['title']!s:50} {', '.join(row['reasons'])}\n    {row['url']}")
    print(f"\n{len(rows)} suspicious job(s) shown")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_audit.py jobfit/server/tests/test_scrape_plans_replay.py -q`
Expected: 4 passed plus one test per replayable committed plan (or 1 skipped)

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: 413 passed plus the replay parametrizations

- [ ] **Step 5: Use it once on the real corpus**

Run: `uv run python -m jobfit.scripts.audit_scrape --limit 30`
Expected: the monday.com "Contact sales" record (and its kind) near the top. For each row that is genuinely not a job, add a pattern: `uv run python -m jobfit.scripts.audit_scrape --reject '<regex>'`, then `uv run python -m jobfit.scripts.update_jobs --company <name> --force --skip-aggregate` and confirm the job closes.

- [ ] **Step 6: Commit**

```bash
git add jobfit/scripts/audit_scrape.py jobfit/data/link_rejects.json jobfit/server/tests/test_scrape_audit.py jobfit/server/tests/test_scrape_plans_replay.py
git commit -m "feat(scrape): add the scrape audit (ranked suspicion queue, persistent reject patterns) and the plan snapshot replay test"
```

**Group C checkpoint.** Discovery is LLM-assisted and budgeted; every ordinary run is plan-driven Python; junk decisions are persistent and replay-tested.

---

## Self-review notes (deviations from the spec, decided while writing this plan)

These are deliberate and small; each is called out so the executor does not "fix" them back toward the spec text.

1. **`JobPosting.url` is `str | None`**, not `str` (spec 2.4.1). Workable items legitimately arrive without a URL; `diff_and_update` already tolerates `None`.
2. **`PageFetcher.fetch` returns a `Page` for every real HTTP response and raises `FetchFailed` only when no response was obtained** (spec 2.4.2 said "None on network failure / non-2xx"). The planner needs to tell 404 from 5xx (spec 4.1 step 4), so the status travels on the `Page`.
3. **`CategoryPrefixFilter` is added to the chain** between `RejectListFilter` and `PlanPatternFilter` (spec 2.4.4 lists six filters). It is the old `drop_category_prefix_links`, which the spec's Task-5 behaviour-preservation requires.
4. **`sibling_anchor_count` means "anchors under the candidate's grandparent that share its `href_shape`"** (spec: "anchors under the same parent element"). The literal definition counted every anchor on a flat page as siblings and made a lone marketing slug look like a repeated structure.
5. **`RecordedPlanClassifier` is keyed by `career_url`**, since `classify()` receives no company id (spec 2.4.9 said "keyed by company_id").
6. **The `LLMClient` protocol lives in `classifiers.py`**, not `llm_client.py`, so nothing on the runtime path imports the vendor module even for a type.
7. **Audit decisions are written to `link_rejects.json` only**; the spec's "and into the plan's `labels.rejected`" is not done because `Labels` has no such field (it has indexed `candidates`), and `RejectListFilter` is enforced on every plan anyway.
8. **`--discover` runs discovery and then continues into the normal scrape** of the same scoped company set (spec section 6 step 3), so a first discovery pass immediately exercises the new plans.
9. **A control-panel "discover" button is not in this plan** (spec section 6 mentions one). CLI only; add it in the panel's own follow-up.
10. **`strategies.ScrapeStrategy.fetch(company, career_url)` takes the display name.** Plan C renames it to the registry id along with `plan_id_for`.
11. **Test counts** in each task are running totals from 315; treat them as expectations to sanity-check, not as gates - if a count is off by one or two, verify only the intended tests were added or removed and move on.

