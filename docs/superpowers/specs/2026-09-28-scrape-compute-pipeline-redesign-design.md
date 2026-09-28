# Scrape/compute pipeline redesign

Date: 2026-09-28
Status: approved design, not yet implemented
Scope: `jobfit/` (the package), its CLI (`jobfit/scripts/update_jobs.py`), the control-panel server (`jobfit/server/`), and the rendered `jobfit.html`

---

## 1. Overview and motivation

jobfit scrapes job postings from a curated set of company career pages, scores each posting deterministically against the user's CV profiles, and renders one static `jobfit.html`. The pipeline works, but four classes of failure were reproduced on 2026-09-27/28 and none of them is prevented structurally. This spec restructures the scrape -> score -> cache -> aggregate -> render path so that each class is hard to reintroduce, while keeping the tool what it is: single user, single machine, no server required for the CLI, no LLM in scoring.

### 1.1 The four problems

**P1. Concurrent runs silently clobber each other.** A scoped `update_jobs --company X --force` was run while a full `recompute_stage()` was mid-flight. The scoped run wrote `status: closed` into `companies/x.json`; the concurrent `aggregate_to_jobs_v2()` had already read that file, so `data/jobs_v2.json` and `jobfit.html` silently carried stale data for that company. There is a pid lock (`jobfit/server/singleton_lock.py`, guarding `data/.server.lock`) and an in-process `threading.Lock` (`server/runner.py`), but both only protect server-vs-server. The CLI and any direct Python call to `recompute_stage()` bypass both. Every write is atomic per file (`atomic_io.write_json_atomic`), which prevents torn files but not read-then-overwrite interleaving; on Windows, `Path.replace()` onto a file another process has open can also raise `PermissionError`.

**P2. The score cache does not know when the scoring code changed.** `scoring.score_cache_key()` hashes only `description + cv_text`. A scoring-formula fix therefore leaves >90% of cache keys unchanged and a recompute run silently produces still-buggy output unless `force=True` is remembered. The key is also under-specified on inputs: `score_job()` uses the title (JD extraction, role-family classification in `_looks_unparseable`), department, location and employment type, none of which are in the key.

**P3. Company identity is not canonical.** Measured on the current data (1907 files under `jobfit/companies/`, 3014 entries in `jobfit/companies_career_pages.json`, not the ~780 the README describes):
- 111 groups of 2+ company files collapse under the existing `connections.normalize_company()` key (e.g. `apiiro.json` / `apiiro_ltd.json`, `axonius.json` / `axonius_solutions_ltd.json`).
- 168 groups collapse under a looser key (parentheticals and `.com/.io/.ai` stripped), e.g. `mondaycom.json` / `mondaycom_ltd_formerly_dapulse.json`.
- 174 groups share the same `career_url` host.
Both spellings are usually present in `companies_career_pages.json`, so both files are scraped every run. `merge_referral_jobs()` creates new company files from raw referral names, adding more. `compute_job_id()` embeds the company name in the job ID, so merging files changes every job ID. `data/company_addresses.json` (383 entries) is keyed by one spelling and does not apply to the other.

**P4. "Is this a job link" is allow-by-default and needs a new special case per site.** `listing_heuristics.looks_like_job_title()` (length + nav-word denylist) and `looks_like_job_link_href()` (URL substring denylist) accept anything not explicitly rejected. CopyLeaks (`copyleaks.com/code-governance-and-compliance`), Coralogix (a docs page titled "OpenTelemetry") and monday.com (a "Contact sales" form whose "description" is the site footer) all became stored "jobs". Each was found by eyeballing the rendered page and fixed with another denylist entry. The fetch cascade also escalates tiers based on CV score (`_any_job_scores_positive`, threshold 50), so a company whose real jobs simply do not fit the CV is pushed to Playwright and techmap on every run.

### 1.2 Scale facts that shape the design

- `data/jobs_v2.json`: 161 MB, 28,315 jobs (22,438 `new`, 3,789 `seen`, 2,088 `closed`), average description 3,671 characters. `jobfit.html`: 152 MB.
- `aggregate_to_jobs_v2()` takes ~6 minutes: it re-reads 1907 files, re-runs `scoring.required_years()` regex over every description, and serialises 161 MB with `indent=2`. This is the window that made P1 easy to hit and makes a one-company scoped run cost 6+ minutes.
- `_meta.json` holds a `cv_hash` that nothing reads (`cv_hash()`'s own docstring says so).
- `jobfit/ats_scorer/` already uses pydantic (`ats_scorer/config.py`) and has golden, gate, monotonicity and determinism tests under `jobfit/server/tests/`.

### 1.3 Principles

1. **Scoring stays 100% deterministic.** No model call anywhere in `jobfit/scoring.py` or `jobfit/ats_scorer/`. Unchanged.
2. **The LLM is a compiler, not a runtime.** A model may read a company's career page once, during an explicit discovery command, to produce a stored `ScrapePlan`. Every scrape run afterwards (incremental updates, reruns, forced rescrapes) executes that plan in pure Python with zero model calls. This is enforced by construction: the LLM client is only instantiated by the discovery command (section 4.2).
3. **One writer at a time.** All mutating stages run under one cross-process lock.
4. **Caches self-invalidate.** Anything derived from code or inputs carries a fingerprint of what it was derived from.
5. **One identity.** A company name becomes a company ID through exactly one function.
6. **Object-oriented scrape layer.** Abstract interfaces, concrete strategies per ATS/site pattern, a factory that builds the strategy for a company from its plan, dependencies injected. No new `if host in url` ladders.

---

## 2. Architecture

### 2.1 Pipeline lock (P1)

**Module:** `jobfit/pipeline_lock.py` (generalised from `jobfit/server/singleton_lock.py`, which is deleted; `server/app.py` uses the new module for the server-instance lock with a different path).

**Lock file:** `jobfit/data/.pipeline.lock`, JSON:
```json
{"pid": 1234, "started_at": "2026-09-28T04:31:00Z", "stage": "recompute", "scope": "all", "argv": ["update_jobs", "--force-rescore"]}
```

**API:**
```python
class PipelineLock:
    def __init__(self, path: Path, stage: str, scope: str = "all", wait: bool = False, poll_seconds: float = 5.0): ...
    def __enter__(self) -> "PipelineLock": ...   # acquire or raise PipelineBusy
    def __exit__(self, *exc) -> None: ...        # release only if this pid owns it

class PipelineBusy(RuntimeError):
    """Message names the holder: stage, scope, pid, started_at, argv."""
```

**Rules:**
- Acquired *inside* these functions, not only in `main()`: `scrape_stage`, `merge_referral_jobs`, `recompute_stage`, `aggregate_to_jobs_v2`, `discover_plans` (new, section 4), and `scripts/import_workday_sources.main`. A direct Python call to any of them is therefore covered.
- Re-entrant per pid: if the file exists and `pid == os.getpid()`, entering is a no-op and exiting does not release. `main()` acquires once for the whole run with `stage="update"`; nested stages inherit it.
- Stale detection: if the recorded pid is not alive, the lock is taken over and a warning is logged. Liveness: `os.kill(pid, 0)` on POSIX; `tasklist /FI "PID eq N"` on Windows (existing code). If liveness cannot be determined, treat as alive.
- Contention: raise `PipelineBusy` (CLI exits with status 2 and prints the holder). With `wait=True` (CLI flag `--wait`), poll every 5 s until free, logging once a minute.
- The server: `runner.start_run` keeps its in-process guard; the stage functions it calls take the pipeline lock, so a CLI run during a server run is refused with the same message, and vice versa. `POST /api/run` maps `PipelineBusy` to HTTP 409.
- The lock file is never deleted by hand; stale locks self-heal via the pid check. The `PipelineBusy` message says so.

### 2.2 Engine-fingerprinted score cache (P2)

**In `jobfit/scoring.py`:**
```python
SCORING_ENGINE_FINGERPRINT: str  # 12 hex chars, computed once at import
```
Computed as sha256 over, in sorted path order: the bytes of `jobfit/scoring.py`, every `jobfit/ats_scorer/*.py`, every `jobfit/ats_scorer/data/*.json`, and `DEFAULT_CONFIG.model_dump_json()`. Any edit to scorer code or taxonomy data changes it. Comment-only edits also change it; that is accepted (a wasted ~10-minute rescore beats a silently stale page).

**Cache key** (replaces the current body of `score_cache_key`):
```python
def score_cache_key(job: dict, profile: dict) -> str:
    parts = [SCORING_ENGINE_FINGERPRINT, job.get("title") or "", job.get("description") or "",
             job.get("department") or "", job.get("location") or "", job.get("employment_type") or "",
             _cv_text_for_profile(profile)]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]
```
`_score_cache_keys` on each job keeps its current shape (`{profile_id: key}`); `_recompute_one_company` is unchanged. `force=True` remains as an escape hatch.

**Visibility:**
- `_meta.json`: replace `cv_hash` with `scoring_engine` (the fingerprint of the last completed recompute).
- `data/jobs_v2.meta.json` (new, sibling of `jobs_v2.json`, which keeps its list shape): `{"scoring_engine": ..., "aggregated_at": ..., "job_count": ..., "company_count": ...}`.
- `build_html` prints the fingerprint next to "generated at" in the page footer.
- `recompute_stage` logs at start: `engine <new> (last completed recompute used <old>)`.

**Stored `years_required`:** `diff_and_update` (new jobs) and `_recompute_one_company` (rescored jobs) store `years_required` on the job record. `aggregate_to_jobs_v2` reads it and only falls back to computing it when the field is absent.

### 2.3 Incremental aggregate (P1 window, scoped-run cost)

**Cache:** `jobfit/cache/aggregate/<company_file_stem>.json`:
```json
{"source_sha1": "<sha1 of companies/<stem>.json bytes>", "context_sha1": "<see below>", "rows": [ ...flattened job records... ]}
```
`context_sha1` = sha1 over the bytes of `data/connections.csv` and of every file under `cache/techmap/` (sorted), because flattened rows embed connections, industry, size and the techmap location hint.

**Algorithm in `aggregate_to_jobs_v2()`:**
1. Compute `context_sha1` once.
2. For each company file (sorted): sha1 its bytes; if a cache entry exists with matching `source_sha1` and `context_sha1`, take its rows; else flatten (current logic) and write the cache entry.
3. Delete cache entries whose company file no longer exists.
4. Concatenate, sort by `best_score` desc (current behaviour), write `jobs_v2.json` with `indent=None` and `jobs_v2.meta.json`.

`--force-aggregate` (new CLI flag) ignores the cache. Expected effect: a run that touched one company re-flattens one file; the serialisation of 161 MB without indentation is the remaining cost, on the order of tens of seconds.

### 2.4 The `jobfit/scrape/` package (P4)

A new package replacing the procedural cascade in `update_jobs.fetch_company_jobs_async`, the anchor loop in `ats_fetchers.fetch_listing_links`, the `ATS_FETCHERS` dict and `TOKEN_PATTERNS` in `ats_fetchers.py`, and the shared heuristics in `listing_heuristics.py`. All models are pydantic v2 (`BaseModel`, `Field`, discriminated unions via `Literal` + `Field(discriminator="kind")`).

#### 2.4.1 Models (`jobfit/scrape/models.py`)

```python
class Page(BaseModel):
    url: str                      # final URL after redirects
    requested_url: str
    status: int
    html: str
    text: str                     # visible text, boilerplate stripped
    renderer: Literal["http", "playwright"]
    fetched_at: datetime
    is_js_shell: bool             # heuristic: <500 chars visible text AND (empty #root/#app/#__next OR __NEXT_DATA__ present)

class Candidate(BaseModel):
    index: int                    # stable position in the page's anchor order
    text: str                     # cleaned anchor text
    href: str                     # absolute URL
    ancestor_path: str            # e.g. "body>main>section>ul>li>a"
    sibling_anchor_count: int     # anchors under the same parent element
    same_host: bool               # host == career page host (www. stripped)
    under_career_path: bool       # href path starts with the career page's path
    has_job_url_hint: bool        # re (job|career|position|opening|vacan|opportunit|\d{3,}) on href
    role_family: str | None       # load_role_families().classify(text)
    in_chrome: bool               # inside <nav>, <header>, <footer>, or role=navigation
    href_shape: str               # "<host>|<parent segments joined by />|<depth>" or "<host>|?<sorted query keys>" for query-only schemes

class Evidence(BaseModel):
    jsonld_jobposting: bool
    apply_cta: bool               # a link/button/form whose text or href contains "apply"
    requirement_sections: int     # section headers found by ats_scorer.jd_extractor._split_sections
    role_family_from_title: str | None
    url_shape: str                # Candidate.href_shape of the posting URL

class JobPosting(BaseModel):
    title: str
    url: str
    location: str | None = None
    description: str = ""
    department: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None
    evidence: Evidence | None = None
    source: Literal["ats_api", "external_board", "html_listing", "special_case", "techmap"]

class AtsApiStrategy(BaseModel):
    kind: Literal["ats_api"] = "ats_api"
    provider: Literal["greenhouse", "lever", "ashby", "workable", "comeet"]
    board: str                    # provider-specific board token
    board_url: str

class ExternalBoardStrategy(BaseModel):
    kind: Literal["external_board"] = "external_board"
    board_url: str                # ATS board linked from the career page; resolved via AtsRegistry at build time

class HtmlListingStrategy(BaseModel):
    kind: Literal["html_listing"] = "html_listing"
    renderer: Literal["http", "playwright"]
    container_selector: str | None = None      # CSS selector scoping the anchor search; None = whole page
    include_url: str | None = None             # regex; None = rely on url_shape + evidence only
    exclude_url: list[str] = []                # regexes
    url_shape: str | None = None               # dominant Candidate.href_shape of accepted links
    explicit_accept: list[str] = []            # accepted URLs when induction failed (status stays "unverified")
    fallbacks: list[Literal["playwright", "techmap"]] = []

class SpecialCaseStrategy(BaseModel):
    kind: Literal["special_case"] = "special_case"
    host_fragment: str            # key of ats_fetchers.SPECIAL_CASE_FETCHERS

class TechmapOnlyStrategy(BaseModel):
    kind: Literal["techmap_only"] = "techmap_only"
    reason: str

class BrokenUrlStrategy(BaseModel):
    kind: Literal["broken_url"] = "broken_url"
    reason: str                   # e.g. "redirects to homepage", "404"

Strategy = Annotated[Union[AtsApiStrategy, ExternalBoardStrategy, HtmlListingStrategy,
                           SpecialCaseStrategy, TechmapOnlyStrategy, BrokenUrlStrategy],
                     Field(discriminator="kind")]

class CandidateLabel(BaseModel):
    index: int
    is_job: bool
    reason: str                   # <= 120 chars

class Labels(BaseModel):
    page_verdict: Literal["careers_page", "not_careers_page", "js_shell", "external_board"]
    external_board_url: str | None = None
    container_selector: str | None = None
    candidates: list[CandidateLabel]

class PageFingerprint(BaseModel):
    href_shape_set_hash: str      # sha1 of the sorted set of Candidate.href_shape values
    candidate_count: int

class PlanHealth(BaseModel):
    consecutive_empty_runs: int = 0
    last_ok_run: datetime | None = None
    last_run: datetime | None = None
    last_yield: int = 0           # postings returned by the last run
    baseline_yield: int = 0       # postings returned at verification time

class ScrapePlan(BaseModel):
    company_id: str
    schema_version: int = 1
    career_url: str | None
    derived_by: Literal["llm", "rules", "manual", "probe"]
    model: str | None = None      # model id when derived_by == "llm"
    derived_at: datetime
    verified_at: datetime | None = None
    status: Literal["verified", "unverified", "stale_suspect", "stale"]
    strategy: Strategy
    labels: Labels | None = None
    page_fingerprint: PageFingerprint | None = None
    health: PlanHealth = PlanHealth()
    rediscover_after: datetime | None = None   # cooldown
    notes: list[str] = []         # discovery remarks for the audit, e.g. "classifier: not a careers page"

class ScrapeResult(BaseModel):
    company_id: str
    postings: list[JobPosting]
    plan: ScrapePlan              # the plan used, with health updated
    strategy_used: str            # kind of the strategy that produced postings (after fallbacks)
    notes: list[str] = []
```

#### 2.4.2 Page fetching (`jobfit/scrape/fetchers.py`)

```python
class PageFetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> Page | None: ...   # None on network failure / non-2xx

class HttpPageFetcher(PageFetcher):     # wraps ats_fetchers._request + _strip_boilerplate; session injected
class PlaywrightPageFetcher(PageFetcher):  # headless chromium, user agent from ats_fetchers.USER_AGENT; wraps the browser setup now in scripts/playwright_listings.py
class CachedPageFetcher(PageFetcher):   # Decorator: (inner, cache_dir, ttl_hours); keyed by sha1(url); used for detail pages (replaces cache/generic_descriptions.json)

class PageFetcherFactory:
    def __init__(self, session, playwright_available: bool): ...
    def build(self, renderer: Literal["http", "playwright"]) -> PageFetcher: ...
```

#### 2.4.3 Candidate extraction (`jobfit/scrape/candidates.py`)

```python
class CandidateExtractor:
    def extract(self, page: Page, career_url: str, container_selector: str | None = None, cap: int = 200) -> list[Candidate]: ...
```
Pure and deterministic. Computes every `Candidate` field. This is the only place the evidence features are computed; both HTTP and Playwright paths use it (the Playwright fetcher returns a `Page`; the extractor does not know which renderer produced it). It parses `page.html` (the unstripped DOM) so that `in_chrome` and `ancestor_path` can be computed; only `page.text` is boilerplate-stripped. `_strip_boilerplate` therefore moves out of the listing path and into `GenericHtmlEnricher` (detail pages), where it belongs.

#### 2.4.4 Link filters (`jobfit/scrape/filters.py`)

```python
class Verdict(BaseModel):
    accept: bool
    filter_name: str
    reason: str

class LinkFilter(ABC):
    name: str
    @abstractmethod
    def accept(self, candidate: Candidate, batch: list[Candidate]) -> Verdict | None: ...
    # None = no opinion; any Verdict (accept or reject) is final and stops the chain for that candidate

class FilterChain:
    def __init__(self, filters: list[LinkFilter]): ...
    def run(self, batch: list[Candidate]) -> tuple[list[Candidate], list[tuple[Candidate, Verdict]]]:
        # returns (accepted, rejected_with_reasons); a candidate with no opinion from any filter is REJECTED (deny by default)
```
Concrete filters, in chain order:
1. `DenylistFilter` — `NAV_DENYLIST`, form-token, email, URL-as-text, length bounds, Latin/Hebrew minimum (today's `looks_like_job_title`). Reject only.
2. `HrefMarkerFilter` — `NON_JOB_LINK_HREF_MARKERS` (today's `looks_like_job_link_href`). Reject only.
3. `RejectListFilter` — URL regexes from `jobfit/data/link_rejects.json` (`{"patterns": ["^https://copyleaks\\.com/[a-z0-9-]+$", ...]}`), written by the audit (section 6.4). Reject only.
4. `PlanPatternFilter(strategy: HtmlListingStrategy)` — reject if any `exclude_url` matches; accept if `include_url` matches or `href in explicit_accept`. No opinion otherwise.
5. `UrlShapeClusterFilter(expected_shape: str | None)` — with an expected shape: accept when `candidate.href_shape == expected_shape`. Without one (rules-only plans): compute the dominant shape among candidates that pass filters 1-3 and have `has_job_url_hint or role_family`; reject candidates whose shape is a singleton in the batch and lack `has_job_url_hint`. Otherwise no opinion.
6. `EvidenceThresholdFilter(min_signals=2, reject_chrome=True)` — accept when at least `min_signals` of {`same_host`, `under_career_path`, `has_job_url_hint`, `role_family is not None`, `sibling_anchor_count >= 3`} hold and `in_chrome` is false. Reject when `in_chrome` is true and `reject_chrome` is set. No opinion otherwise.

Order matters: hard rejects first, then the plan (specific), then generic evidence. A verified plan therefore decides most links at step 4; steps 5-6 are the drift safety net and the whole story for rules-only plans.

#### 2.4.5 ATS clients (`jobfit/scrape/ats/`)

```python
class AtsClient(ABC):
    provider: str
    @abstractmethod
    def match(self, url: str) -> str | None: ...           # board token if url is this provider's board/job URL
    @abstractmethod
    def board_url(self, board: str) -> str: ...
    @abstractmethod
    def fetch_board(self, board: str) -> list[JobPosting]: ...

class GreenhouseClient(AtsClient) ...  # wraps ats_fetchers.fetch_greenhouse
class LeverClient, AshbyClient, WorkableClient, ComeetClient  # likewise

class AtsRegistry:
    def __init__(self, clients: list[AtsClient]): ...
    def resolve(self, url: str) -> tuple[AtsClient, str] | None: ...   # first client whose match() returns a token
    def client(self, provider: str) -> AtsClient: ...
```
Each client owns its own URL regex (moved from `ats_fetchers.TOKEN_PATTERNS`). Adding a provider = one subclass + one entry in the registry list in `bootstrap.py`.

#### 2.4.6 Detail enrichment (`jobfit/scrape/enrich.py`)

```python
class DetailEnricher(ABC):
    @abstractmethod
    def enrich(self, posting: JobPosting) -> JobPosting: ...

class GenericHtmlEnricher(DetailEnricher):
    # fetches posting.url via a CachedPageFetcher(HttpPageFetcher), fills description/location/employment_type/posted_at
    # (today's ats_fetchers.fetch_generic_job_details) and Evidence (JSON-LD JobPosting, apply CTA, requirement sections, role family from title, url_shape)
class NoopEnricher(DetailEnricher):   # ATS API and techmap postings: description already structured or intentionally absent; still fills Evidence from what is present
```

#### 2.4.7 Strategies (`jobfit/scrape/strategies.py`)

```python
class FetchFailed(RuntimeError):
    """The listing page or board could not be fetched (network error, timeout, 5xx). Propagates out of
    CompanyScrapeService.scrape so update_jobs._process_company records a failure and leaves the company
    file untouched - a transient outage must never close every stored job."""

class ScrapeStrategy(ABC):
    kind: str
    @abstractmethod
    def fetch(self, company_id: str, career_url: str | None) -> list[JobPosting]: ...   # [] means "page reachable, no postings"; raises FetchFailed otherwise

class AtsApiScrape(ScrapeStrategy):          # (client: AtsClient, board: str)
class ExternalBoardScrape(ScrapeStrategy):   # (registry, board_url) -> resolves and delegates to AtsApiScrape at construction; raises PlanInvalid if unresolvable
class HtmlListingScrape(ScrapeStrategy):     # (fetcher: PageFetcher, extractor, chain: FilterChain, enricher, strategy: HtmlListingStrategy, max_links=50)
class SpecialCaseScrape(ScrapeStrategy):     # (fn) wraps ats_fetchers.SPECIAL_CASE_FETCHERS[host_fragment]
class TechmapScrape(ScrapeStrategy):         # (techmap_index, company_registry) -> title/location/url rows, description ""
class NoScrape(ScrapeStrategy):              # broken_url: returns []; null object. Only ever built for a plan whose status is "verified" (a 404/410 or homepage redirect seen by the probe); an unverified broken_url plan is built as TechmapScrape instead
class FallbackScrape(ScrapeStrategy):        # Composite: (primary, fallbacks: list[ScrapeStrategy], health_policy)
    # runs primary; if health_policy.is_healthy(result) is False, runs the next fallback; returns the first healthy result,
    # else the primary's result (so diff_and_update can still close jobs correctly on a genuinely empty page)
```
Class names deliberately differ from the plan-variant model names (`AtsApiStrategy` is data; `AtsApiScrape` is behaviour).

#### 2.4.8 Health policy (`jobfit/scrape/health.py`)

```python
class HealthPolicy:
    def is_healthy(self, postings: list[JobPosting]) -> bool:
        # at least one posting AND (every posting's source is in {ats_api, external_board, special_case, techmap}
        #   OR at least 30% of postings have evidence with (jsonld_jobposting or apply_cta or requirement_sections >= 1)
        #   OR at least 30% of posting URLs match the job-URL-hint regex used for Candidate.has_job_url_hint)
        # CV score is never consulted
    def update(self, plan: ScrapePlan, postings: list[JobPosting], now: datetime) -> ScrapePlan:
        # writes PlanHealth; transitions status per section 4.4
```
`update_jobs._any_job_scores_positive`, `_has_real_descriptions` and `_looks_like_real_job_urls` are deleted once `HealthPolicy` is in place.

#### 2.4.9 Plan classifiers (`jobfit/scrape/classifiers.py`)

```python
class PlanClassifier(ABC):
    @abstractmethod
    def classify(self, page: Page, candidates: list[Candidate], career_url: str) -> Labels: ...

class RulesPlanClassifier(PlanClassifier):     # labels via EvidenceThresholdFilter + UrlShapeClusterFilter semantics; no network
class LLMPlanClassifier(PlanClassifier):       # (client: LLMClient, model: str); section 4.2
class RecordedPlanClassifier(PlanClassifier):  # replays Labels JSON from a fixture directory keyed by company_id; tests only

class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict: ...
```
`AnthropicLLMClient(LLMClient)` (Messages API, temperature 0, JSON output validated against `Labels`) lives in its own module, `jobfit/scrape/llm_client.py`, which imports the vendor SDK. Nothing in `jobfit/scrape/` imports that module at top level; `bootstrap.build_discovery_planner()` (section 2.4.12) imports it lazily inside the function body, and only the discovery command calls that function. `LLMPlanClassifier` itself depends only on the `LLMClient` protocol.

#### 2.4.10 Planner (`jobfit/scrape/planner.py`)

```python
class ScrapePlanner:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 classifier: PlanClassifier, inducer: "PlanInducer", validator: "PlanValidator", now: Callable[[], datetime]): ...
    def discover(self, company_id: str, career_url: str | None) -> tuple[ScrapePlan, Page | None]: ...  # section 4.1

class PlanInducer:
    def induce(self, labels: Labels, candidates: list[Candidate], page: Page) -> HtmlListingStrategy: ...
class PlanValidator:
    def validate(self, strategy: HtmlListingStrategy, labels: Labels, candidates: list[Candidate], chain_factory) -> bool: ...
```

#### 2.4.11 Plan store (`jobfit/scrape/plan_store.py`)

```python
class PlanStore(ABC):
    @abstractmethod
    def get(self, company_id: str) -> ScrapePlan | None: ...
    @abstractmethod
    def put(self, plan: ScrapePlan) -> None: ...
    @abstractmethod
    def all(self) -> Iterator[ScrapePlan]: ...

class FilePlanStore(PlanStore):    # jobfit/data/scrape_plans/<company_id>.json via atomic_io.write_json_atomic
class MemoryPlanStore(PlanStore):  # tests
```
Plans live under `data/` (durable, committed to git, hand-editable), not `cache/`, because producing one may have cost an API call and because `manual` plans are legitimate.

Snapshots of the page a plan was derived from live at `jobfit/cache/listing_snapshots/<company_id>.html` (committed too; they are the regression fixtures, section 6.1). A snapshot is overwritten on every re-discovery.

#### 2.4.12 Factory, service, composition root

```python
class StrategyFactory:
    def __init__(self, registry: AtsRegistry, fetchers: PageFetcherFactory, extractor: CandidateExtractor,
                 enricher: DetailEnricher, reject_patterns: list[str], techmap_index, health: HealthPolicy): ...
    def build(self, plan: ScrapePlan) -> ScrapeStrategy:
        # dispatch on plan.strategy.kind through a dict {kind: builder method};
        # html_listing -> HtmlListingScrape with FilterChain([Denylist, HrefMarker, RejectList, PlanPattern(strategy), UrlShapeCluster(strategy.url_shape), EvidenceThreshold()])
        # wrapped in FallbackScrape when strategy.fallbacks is non-empty ("playwright" -> same HtmlListingScrape with the playwright fetcher; "techmap" -> TechmapScrape)

class CompanyScrapeService:
    def __init__(self, store: PlanStore, rules_classifier: RulesPlanClassifier, factory: StrategyFactory,
                 fetchers: PageFetcherFactory, extractor: CandidateExtractor, health: HealthPolicy, now): ...
    def scrape(self, company_id: str, career_url: str | None) -> ScrapeResult:
        # 1. plan = store.get(company_id)
        # 2. if plan is None: plan = self._rules_plan(company_id, career_url)  (derived_by="rules", status="unverified"; NO model call)
        # 3. strategy = factory.build(plan); postings = strategy.fetch(...)
        # 4. plan = health.update(plan, postings, now()); store.put(plan)
        # 5. return ScrapeResult(...)
```

`jobfit/scrape/bootstrap.py`:
```python
def build_scrape_service(session, techmap_index) -> CompanyScrapeService: ...       # production graph, no LLM anywhere
def build_discovery_planner(session, llm: LLMClient | None) -> ScrapePlanner: ...    # the ONLY place LLMPlanClassifier is constructed; llm=None -> RulesPlanClassifier
```

`update_jobs.fetch_company_jobs_async` becomes a shim: build the service once per `scrape_stage`, call `service.scrape(...)`, convert `JobPosting` models to the dicts `diff_and_update` expects (`model_dump()` plus `evidence` stored under `job_evidence`). `scrape_stage`, `diff_and_update`, and `server/runner.py` do not change their interfaces.

### 2.5 Company registry (P3)

**File:** `jobfit/data/company_registry.json`:
```json
{
  "schema_version": 1,
  "companies": [
    {
      "id": "monday_com",
      "display_name": "monday.com",
      "aliases": ["monday.com", "Monday.com Ltd. (Formerly DaPulse)"],
      "career_url": "https://monday.com/careers",
      "hosts": ["monday.com"],
      "board_key": null,
      "sources": {"curated": true, "techmap": true, "referral": false, "ivc": false},
      "review": {"decision": null, "decided_at": null},
      "merged_from": ["mondaycom", "mondaycom_ltd_formerly_dapulse"]
    }
  ]
}
```
`board_key` is `"<provider>:<board>"` when the career URL is an ATS board (shared hosts such as `comeet.com` are never used as identity on their own).

**Module:** `jobfit/company_registry.py`:
```python
class CompanyRegistry:
    @classmethod
    def load(cls, path=config.COMPANY_REGISTRY_PATH) -> "CompanyRegistry": ...
    def save(self) -> None: ...
    def resolve(self, name: str, url: str | None = None) -> str | None:
        # 1. exact alias (case-insensitive)  2. connections.normalize_company(name) key
        # 3. url host (www. stripped) or board_key when url is an ATS board  4. loose key (parentheticals and .com/.io/.ai/.co stripped, then normalize_company)
    def get_or_create(self, name: str, url: str | None = None, source: str) -> str: ...   # resolve, else add with id = _snake_case(display_name) made unique
    def display_name(self, company_id: str) -> str: ...
    def entry(self, company_id: str) -> CompanyEntry: ...
    def check_invariants(self) -> list[str]:   # no two entries share a normalized key, host, or board_key
```
Every ingress goes through `resolve`/`get_or_create`: `load_companies_to_scrape` (returns `{company_id: career_url}`), `load_company_file`/`save_company_file` (keyed by id), `merge_referral_jobs`, `load_techmap_index`, `connections.contacts_for_company`, `build_html._load_company_addresses`, and `company_review`. `companies_career_pages.json` and `company_review.json` become derived views regenerated from the registry until the migration in section 5 removes them.

**Identity gate (shippable before the migration):** `save_company_file(company_id, record)` raises `DuplicateCompany` if `record["name"]`'s normalized key or career host already belongs to a *different* existing company file. A test asserts `check_invariants()` is empty on the committed registry.

**Job IDs:** `compute_job_id(company_id, title, location, url)` uses the registry id (not the display name) in both branches. Existing IDs are re-keyed by the migration (section 5).

---

## 3. Discovery versus runtime

This split is the core of the design and is stated once here, unambiguously:

| | Discovery | Runtime |
|---|---|---|
| Command | `update_jobs --discover [--company X] [--limit N]`, `update_jobs --rediscover --company X` | `update_jobs` (any other invocation), server "Run update", `recompute_stage` |
| Model calls | Up to one per company processed, only if a probe did not already decide | **Zero.** `LLMPlanClassifier` is never constructed. |
| Input | The company's live career page (HTTP, then Playwright if a JS shell) | The stored `ScrapePlan` and the live career page |
| Output | `data/scrape_plans/<id>.json`, `cache/listing_snapshots/<id>.html` | `companies/<id>.json` job records; updated `health` in the plan |
| Frequency | Once per company, then only on the triggers in section 4.4 within a budget | Every run |
| Fails without API key? | No: falls back to `RulesPlanClassifier` and marks the plan `derived_by: rules, status: unverified` | Not applicable, never needs a key |

"The first scrape is hard, then it is just Python": the first `--discover` pass over the corpus is the expensive step; afterwards `update_jobs` executes plans in pure Python, forever, until a plan is explicitly re-discovered.

### 3.1 What a company with no plan does at runtime

It is scraped anyway. `CompanyScrapeService` synthesises a rules-only plan (`derived_by: rules`, `status: unverified`, `HtmlListingStrategy(renderer="http", fallbacks=["playwright", "techmap"])`, or `AtsApiStrategy` when `AtsRegistry.resolve(career_url)` matches, or `TechmapOnlyStrategy` when `career_url` is `None`) and stores it. That plan is picked up by the next `--discover` run. Runtime never waits on discovery.

---

## 4. ScrapePlan discovery flow

### 4.1 `ScrapePlanner.discover(company_id, career_url)` — step by step

1. **No URL** -> `TechmapOnlyStrategy(reason="no career url")`, `derived_by="probe"`, `status="verified"`. Stop. No page fetch, no model.
2. **ATS probe** -> if `AtsRegistry.resolve(career_url)` matches: `AtsApiStrategy(provider, board, board_url)`, `derived_by="probe"`, `status="verified"`. Stop.
3. **Special-case probe** -> if a key of `ats_fetchers.SPECIAL_CASE_FETCHERS` is a substring of the URL: `SpecialCaseStrategy`, `derived_by="probe"`, `status="verified"`. Stop.
4. **Fetch** with `HttpPageFetcher`. Status 404 or 410 -> `BrokenUrlStrategy(reason=f"http {status}")`, `derived_by="probe"`, `status="verified"`. If the final URL's path is `/` or empty and differs from the requested path -> `BrokenUrlStrategy("redirects to homepage")`, `status="verified"`. `None` (network error, timeout) or any 5xx -> raise `FetchFailed`; discovery for this company is abandoned for this run and no plan is written. If `page.is_js_shell` -> refetch with `PlaywrightPageFetcher`; `renderer="playwright"`.
5. **External-board probe** -> if any anchor or iframe `src` on the page resolves via `AtsRegistry` -> `ExternalBoardStrategy(board_url)`, `derived_by="probe"`, `status="verified"`. Stop. (Multiple distinct boards: pick the one with the most links; note the others in `notes`.)
6. **JSON-LD probe** -> collect `JobPosting` URLs from `<script type="application/ld+json">`; these become pre-accepted labels.
7. **Candidates** -> `CandidateExtractor.extract(page, career_url, cap=200)`. Zero candidates -> `TechmapOnlyStrategy("no anchors on page")`, `derived_by="probe"`, `status="unverified"`. Stop.
8. **Classify** -> `classifier.classify(page, candidates, career_url) -> Labels`. With `LLMPlanClassifier` this is the one model call. If it raises (network, schema validation failure after one retry, rate limit), fall back to `RulesPlanClassifier` and record `derived_by="rules"`.
9. **Page verdict** -> `not_careers_page` -> append `"classifier: not a careers page"` to `notes` and continue to step 10 (a model verdict never on its own produces a plan that closes stored jobs; the audit lists these notes for a human to confirm and, if agreed, set `BrokenUrlStrategy` by hand with `derived_by="manual"`). `external_board` with a URL -> as step 5. `js_shell` when the renderer was `http` -> refetch with Playwright and go back to step 7 once.
10. **Induce** -> `PlanInducer.induce(labels, candidates, page) -> HtmlListingStrategy` (section 4.3).
11. **Validate** -> build the same `FilterChain` the factory would build for this strategy, run it over `candidates`, and require: every `is_job` candidate accepted, every `not is_job` candidate rejected. Pass -> `status="verified"`, `verified_at=now`. Fail -> `explicit_accept = [accepted hrefs]`, `include_url=None`, `status="unverified"`.
12. **Fingerprint and persist** -> `page_fingerprint`, `health.baseline_yield = len(accepted)`, `rediscover_after = now + 7 days`; write the plan and the snapshot HTML.

### 4.2 The LLM classification call

- **Client:** `AnthropicLLMClient` over the Anthropic Messages API. Model id from `config.SCRAPE_PLAN_LLM_MODEL` (a Haiku-class model; the exact id is chosen at implementation time from current documentation and recorded in the plan's `model` field). API key from `ANTHROPIC_API_KEY`. Temperature 0. `max_tokens` 4096. One retry on a schema-validation failure with the validation error appended to the user message.
- **System prompt (fixed text, versioned by `schema_version`):** the model is labelling links on a company careers page; it must return JSON matching the `Labels` schema; a link is a job only if it leads to a single specific job posting (not a department overview, not "view all", not a product, docs, blog, legal, contact or office page); it must not invent candidates; `container_selector` is optional and only when one element clearly wraps all job links.
- **User message:** the career URL; `page.text` truncated to 3,000 characters; the candidate table as a compact JSON list of `{index, text, href, ancestor_path, sibling_anchor_count, in_chrome}` (at most 200 rows). Raw HTML is never sent. Typical input 3-8k tokens, output under 2k.
- **Output:** parsed into `Labels`; every `index` must exist in the candidate list, else validation fails.
- **Cost bound:** one call per company processed; `--discover` processes at most `--discover-max N` companies per invocation (default `config.DISCOVERY_MAX_PER_RUN = 200`; `0` = unbounded), missing-plan companies first, then `stale_suspect`/`stale`, then `unverified` with `derived_by="rules"`. The first full pass over the ~1,400 companies that have both a URL and a company file is therefore either several invocations or one `--discover --discover-max 0`, at 4 concurrent calls, roughly an hour. Companies with `career_url=None` never trigger a call. `--discover-max` is distinct from the existing `--limit N`, which still scopes the scrape stage to the first N companies.

### 4.3 Plan induction (pure code)

Input: accepted candidates `A`, rejected candidates `R`.
1. If `A` is empty -> `HtmlListingStrategy(renderer, include_url=None, url_shape=None, explicit_accept=[], fallbacks=["playwright","techmap"])`; status will be `unverified`.
2. `url_shape` = the most common `href_shape` in `A`.
3. `include_url`: if all of `A` share a host and a path prefix of at least one segment, `^https?://(www\.)?<host>/<prefix>/[^/?#]+/?$` with `<prefix>` regex-escaped; if `A` are query-only URLs sharing a query key, `^https?://(www\.)?<host>/<path>\?.*\b<key>=`; else `None`.
4. `exclude_url`: for each `href_shape` that occurs in `R` but never in `A`, a regex for that shape (`^https?://(www\.)?<host>/<parent>/[^/?#]+/?$`), at most 10.
5. `container_selector` = `labels.container_selector` if it is a valid CSS selector that matches at least one element containing at least one accepted candidate, else `None`.
6. `fallbacks` = `["playwright", "techmap"]` when `renderer == "http"`, `["techmap"]` otherwise.

### 4.4 Staleness policy (the opposite of section 2.2, on purpose)

Re-discovery costs a model call, so plans are never invalidated by a code-hash. Instead:

- After every runtime scrape, `HealthPolicy.update`:
  - `is_healthy` true -> `consecutive_empty_runs = 0`, `last_ok_run = now`, `status` stays; if `status == "stale_suspect"` it returns to its pre-suspect value (`verified` if `verified_at` is set, else `unverified`).
  - `is_healthy` false and `baseline_yield > 0` -> `consecutive_empty_runs += 1`; at 2 -> `status = "stale_suspect"`.
  - yield dropped by more than 70% from `baseline_yield` **and** the page's `href_shape_set_hash` differs from the stored fingerprint -> `status = "stale_suspect"` immediately.
  - A fingerprint change alone never changes status.
- `--discover` picks up `stale_suspect` plans whose `rediscover_after` has passed, within the per-run cap. A company over budget or in cooldown keeps running its existing plan (`FallbackScrape` still tries Playwright and techmap), degraded but not dead.
- `schema_version`: bumped by hand when the plan schema or filter semantics change; `FilePlanStore.get` returns a plan with an older version as `status="stale"`, which `--discover` treats like `stale_suspect` (still budgeted).
- `--rediscover --company X` ignores budget and cooldown for that one company.
- `update_jobs --plans` prints counts per `status` and `derived_by`, and lists `broken_url` plans for review.

### 4.5 Runtime flow (pure Python, per company)

1. `plan = store.get(id)` or a synthesised rules plan (section 3.1).
2. `strategy = factory.build(plan)`.
3. `postings = strategy.fetch(id, career_url)`; for `HtmlListingScrape`: fetch listing page with the plan's renderer -> `extractor.extract(..., container_selector)` -> `chain.run(candidates)` -> take the first `max_links` accepted -> `enricher.enrich` each -> `JobPosting` with `Evidence`. Rejected candidates and their `Verdict`s are logged at DEBUG.
4. `FallbackScrape` consults `HealthPolicy.is_healthy` to decide whether to try the next strategy.
5. `health.update(plan, postings)`; `store.put(plan)`.
6. `diff_and_update` (unchanged logic) stores `job_evidence` on new jobs; `scoring._looks_unparseable` additionally returns `True` when `job_evidence` exists and has no `jsonld_jobposting`, no `apply_cta`, zero `requirement_sections` and no `role_family_from_title`.

---

## 5. Company registry and dedup migration

One-off script `jobfit/scripts/migrate_company_registry.py`, run under the pipeline lock, idempotent (re-running on migrated data is a no-op).

### 5.1 Build the registry
1. Seed entries from every `companies/*.json` (`name`, `career_url`) and every `companies_career_pages.json` entry not already present; record `sources` (`curated` for the map, `techmap` when `load_techmap_index()` has the normalized key, `referral` when the company file has any `is_referral` job, `ivc` when present in `cache/ivc_companies.json` if that file exists).
2. Group entries by `CompanyRegistry.resolve` semantics: exact normalized key **or** same host/board_key -> auto-merge. Loose key only -> write to `data/company_registry_review.json` as `{"group": [names], "suggested_id": ...}`; the control panel gets a "merge / keep separate" decision for each, and the script merges approved groups on its next run.
3. Choose `display_name`: the curated-map spelling if one exists, else the shortest alias. `id = _snake_case(display_name)`, de-duplicated with a numeric suffix.

### 5.2 Merge company files
For each merged group, produce one `companies/<id>.json`:
- `jobs` = union of all members' jobs, de-duplicated by normalized URL (scheme/host lowercased, `www.` stripped, tracking query keys `utm_*`, `gh_src`, `source`, `from` removed, trailing slash removed), then by `(normalized title, location)` for jobs without a URL.
- For duplicates: `first_seen = min`, `last_seen = max`, `status` from the member with the latest `last_checked`, all other fields from that member.
- `career_url` from the registry entry; `last_checked = max`; `merged_from = [old file stems]`.
- Old files are deleted from `companies/` (git history keeps them).

### 5.3 Re-key job IDs
`compute_job_id` now takes the registry id. For every job in every company file: `new_id = compute_job_id(company_id, title, location, url)`; if `new_id != job["id"]`, set `job["previous_ids"] = [old_id, *job.get("previous_ids", [])]` and `job["id"] = new_id`. `first_seen` and `status` are preserved, so a re-keyed job is not reported as new.

### 5.4 `localStorage` migration in the SPA
`jobfit.html` keeps per-job like/hide/sent/reached-out state in `localStorage` keyed by job id. `aggregate_to_jobs_v2` writes `data/id_aliases.json` (`{old_id: new_id}` for every job with `previous_ids`); `build_html` embeds it. On load, the page's JS migrates any `localStorage` entry whose key is an old id to the new id, once, and records `id_migration_done=<sha1 of the alias map>` so it does not repeat. The alias map is dropped from the build (by emptying `previous_ids`) in a later clean-up commit after the user confirms state survived; that clean-up is manual and out of this migration's scope.

### 5.5 Re-key other files
- `data/company_addresses.json`: keys resolved to registry ids; unresolved keys are kept under `_unresolved` for manual review.
- `data/company_review.json`: keys resolved to ids and folded into the registry's `review` field; the file is deleted.
- `companies_career_pages.json`: regenerated as a derived view `{display_name: career_url}` for one release for anything that still reads it, then deleted.
- `data/scrape_plans/`: plans for merged members are dropped; the merged company gets no plan and is picked up by the next `--discover`.

### 5.6 Risks
- Job-ID churn is the single riskiest edit: every job in the corpus is re-keyed. The migration runs on a git-clean tree, commits before and after, and the SPA migration is tested on a copy of the real `localStorage` export first.
- A conglomerate serving several brands from one careers host would auto-merge; the review file lists every auto-merge with its evidence so it can be split by hand (remove the alias, re-run).

---

## 6. Data flow: a normal `update_jobs` run after this design

1. `main()` parses flags (`--company`, `--limit`, `--force`, `--skip-aggregate`, `--wait`, `--force-rescore`, `--force-aggregate`, `--discover`, `--discover-max`, `--rediscover`, `--plans`).
2. `PipelineLock(stage="update", scope=...)` acquired (or `PipelineBusy` -> exit 2, or wait).
3. If `--discover`/`--rediscover`: `discover_plans()` runs first — `build_discovery_planner(session, llm=AnthropicLLMClient() if key present else None)`, selects companies (missing -> stale_suspect -> rules-unverified, capped), runs `planner.discover` with 4 threads, writes plans and snapshots, prints a summary. This is the only code path that can make a model call. Without these flags the LLM client class is never imported.
4. `scrape_stage`: `CompanyRegistry.load()`; `load_companies_to_scrape()` -> `{company_id: url}`; `build_scrape_service(session, techmap_index)`; thread pool of 8 runs `service.scrape(id, url)` per company (skipping those checked within `COMPANY_RECHECK_TTL_HOURS` unless `--force`); results go through `translation.translate_job_if_needed` and `diff_and_update`; `save_company_file(id, record)` under the identity gate; plan health persisted.
5. `merge_referral_jobs` (full runs only): referral companies resolved via `CompanyRegistry.get_or_create(name, url, source="referral")`; no new duplicate files can be created.
6. `_meta.json` `last_run` updated.
7. `recompute_stage(force=--force-rescore)`: 8 worker processes rescore jobs whose `_score_cache_keys` (engine fingerprint + inputs + CV text) changed; `years_required` stored; `_meta.json.scoring_engine` written on completion.
8. `aggregate_to_jobs_v2`: incremental per-company cache; `jobs_v2.json` (`indent=None`), `jobs_v2.meta.json`, `id_aliases.json`.
9. `build_html.build()`: embeds dataset, profiles, addresses (resolved by id), alias map, and the engine fingerprint.
10. Lock released.

A scoped run (`--company X`) does steps 2, 4 (one company), 6, 7 (one file's worth of rescoring; other files are cache hits), 8 (one cache miss), 9. Expected wall time: the company's fetch plus tens of seconds.

The server's "Run update" performs steps 2-10 through `runner._run_worker` with the same functions and never `--discover`; discovery from the panel is a separate button that calls `discover_plans()` with the same budget.

---

## 7. Testing strategy

All tests live under `jobfit/server/tests/` (the existing location) and run with `uv run pytest jobfit/server/tests`. None touch the network.

### 7.1 Scrape snapshots (P4)
- Fixture corpus: `cache/listing_snapshots/<company_id>.html` plus `data/scrape_plans/<company_id>.json` (both committed). `FakePageFetcher(snapshot_dir)` serves them.
- `test_scrape_plans_replay.py`: for every plan with `labels`, build the factory's `FilterChain`, run it over `CandidateExtractor.extract(snapshot)`, assert accepted == labelled jobs and rejected ⊇ labelled non-jobs. This is the regression suite for "how many companies leak"; a heuristic change that breaks a company fails here by name.
- `test_filters.py`: each `LinkFilter` in isolation (the cases from `test_listing_heuristics.py` move here: Check Point query-string scheme, Hebrew titles, Google Maps office links, `/docs/` and flat marketing slugs, department-overview prefixes).
- `test_strategies.py`: `FallbackScrape` ordering with a stub `HealthPolicy`; `HtmlListingScrape` end to end on a snapshot with `RecordedPlanClassifier`; `ExternalBoardScrape` resolution; `NoScrape`.
- `test_planner.py`: `discover` on snapshots with `RecordedPlanClassifier` fixtures (`tests/fixtures/labels/<company_id>.json`) for: verified induction, induction failure -> `explicit_accept`, js-shell refetch, redirect-to-homepage, ATS probe short-circuit (asserts the classifier is never called).
- `test_health_policy.py`: status transitions of section 4.4, including the "fingerprint change alone does nothing" case and cooldown/budget selection.
- `test_llm_classifier.py`: `LLMPlanClassifier` with a stub `LLMClient` returning malformed then valid JSON (one retry), unknown index rejection, fallback to rules on exception. `AnthropicLLMClient` itself is not unit-tested beyond construction.
- A guard test: importing `jobfit.scrape.bootstrap` and running one scrape through `build_scrape_service` with a `MemoryPlanStore` leaves `jobfit.scrape.llm_client` absent from `sys.modules`. This is the executable form of principle 2 (no model at runtime).

### 7.2 Scoring (existing, extended)
- Existing golden, gates, monotonicity, determinism and extractor tests in `ats_scorer` remain the correctness net for a forced rescore.
- `test_recompute_score_cache.py` gains: key changes when `SCORING_ENGINE_FINGERPRINT` changes (monkeypatch), when title/department/location/employment_type change; `_meta.json.scoring_engine` written only after a completed run.
- `test_scoring_shared_weights.py` unchanged.

### 7.3 Lock and aggregate
- `test_pipeline_lock.py`: re-entrancy by pid, stale takeover with a dead pid, `PipelineBusy` message content, `wait=True` polling with a fake clock, release only by owner.
- `test_aggregate_to_jobs_v2.py` (existing) gains: cache hit skips flattening (a stub that raises if called), cache invalidates on company file change and on `context_sha1` change, orphaned cache entries removed, `jobs_v2.meta.json` fields, `indent=None` output parses.

### 7.4 Registry and migration
- `test_company_registry.py`: resolve order (alias, normalized key, host, board_key, loose key), `get_or_create` uniqueness, `check_invariants`, the identity gate raising `DuplicateCompany`.
- `test_migrate_company_registry.py`: on a temp copy of a handful of real duplicate pairs (`apiiro`, `axonius`, `monday.com`): grouping, job union and de-dup rules, `first_seen`/`last_seen`/`status` merge rules, `previous_ids`, idempotency on a second run, addresses re-keying with `_unresolved`.
- The `localStorage` alias migration in the page's JS has no automated test in this repo (there is no JS test runner); it is verified manually on a copy of the real page, with the real `localStorage` contents exported and re-imported, before the clean-up commit (section 5.4).

---

## 8. Build sequencing

Each step is a shippable increment with its own commit and passing tests; later steps depend on earlier ones as noted.

1. **Pipeline lock.** `jobfit/pipeline_lock.py`; wrap the five stage functions and `import_workday_sources`; `--wait`; server 409. Delete `server/singleton_lock.py`. Ships: no two mutating runs can overlap.
2. **Identity gate.** `CompanyRegistry` with `resolve`/`check_invariants` seeded read-only from existing files (no migration yet); `save_company_file` raises `DuplicateCompany`; `merge_referral_jobs` uses `resolve` before creating a file. Ships: the duplicate count stops growing.
3. **Engine fingerprint.** `SCORING_ENGINE_FINGERPRINT`, widened `score_cache_key`, `_meta.json.scoring_engine`, `jobs_v2.meta.json`, stored `years_required`, footer display. Ships: scorer edits rescore automatically.
4. **Incremental aggregate.** `cache/aggregate/`, `indent=None`, `--force-aggregate`. Ships: scoped runs finish in under a minute after the fetch; the P1 window shrinks to seconds.
5. **Scrape package skeleton, behaviour-preserving.** `models`, `fetchers`, `candidates`, `filters` (`Denylist`, `HrefMarker`, `UrlShapeCluster` in batch mode, `EvidenceThreshold(min_signals=0, reject_chrome=False)` so acceptance matches today's allow-by-default heuristics exactly; the real thresholds arrive in step 6), `ats/` clients wrapping existing fetchers, `strategies`, `StrategyFactory`, `CompanyScrapeService` with a synthesised rules plan only, `bootstrap.build_scrape_service`; `fetch_company_jobs_async` becomes the shim; `scripts/playwright_listings.py` uses `CandidateExtractor` + `FilterChain`. Snapshot corpus started from ~30 companies (including every company fixed by hand this week). Ships: same results as before, now under tests; `listing_heuristics.py` and the anchor loop in `ats_fetchers` deleted.
6. **Plans, rules classifier, health policy.** `PlanStore`, `RulesPlanClassifier`, `HealthPolicy`, `ScrapePlanner` with probes only (steps 1-7 of section 4.1 plus rules classification), `--plans`, `EvidenceThresholdFilter(min_signals=2)`, `RejectListFilter`, `job_evidence` stored and consumed by `_looks_unparseable`. Delete `_any_job_scores_positive`, `_has_real_descriptions`, `_looks_like_real_job_urls`. Ships: tier escalation no longer depends on CV score; every company has a plan file.
7. **LLM discovery.** `LLMClient`, `AnthropicLLMClient`, `LLMPlanClassifier`, `PlanInducer`, `PlanValidator`, `--discover`/`--rediscover`, budget/cooldown, snapshot writing, the audit script `scripts/audit_scrape.py` (ranks jobs by missing evidence, unclassifiable title, off-shape URL, description near-duplicate of sibling jobs; decisions written to `link_rejects.json` and the plan's labels), the `AnthropicLLMClient`-never-imported guard test. Run the first full discovery pass under the lock and review `broken_url`/`unverified` output. Ships: the "first hard scrape"; from here every run is plan-driven Python.
8. **Registry migration.** `migrate_company_registry.py`, file merges, job-ID re-keying, `id_aliases.json` + SPA `localStorage` migration, addresses/review re-keying, `companies_career_pages.json` retired. Uses the "same board" hints from step 7's plans to seed the review file. Ships: one file, one display name, one address per company.

Steps 1-4 are independent of each other and of 5-8 and can land in any order among themselves; 5 -> 6 -> 7 are strictly sequential; 8 requires 1, 2 and 7.

Scope note: this spec is deliberately larger than one implementation plan. Write three plans from it: (A) steps 1-4, (B) steps 5-7, (C) step 8. Each plan is independently reviewable and each step within it is a separate commit.

---

## 9. Non-goals

- Any model call in the incremental update path, in scoring, or per job. The only model call is the discovery command's per-company classification.
- Letting a model emit per-site scraper *code*; plans are data.
- Auto-invalidating scrape plans on scraper-code changes (that is what `schema_version` + health triggers + budget are for; re-discovery costs money).
- Per-company file locks, lock managers, job queues, or a daemon that serialises everything. One pipeline lock with `--wait` is enough for one person on one machine.
- Making the CLI a thin client of the FastAPI server.
- Replacing the per-company JSON files or `jobs_v2.json` with SQLite. Revisit only if the incremental aggregate is insufficient.
- Shrinking `jobfit.html` (152 MB) by dropping closed-job descriptions or paginating; a real problem, but separate from these four.
- A dependency-injection container or abstract factories of factories; `bootstrap.py` is two functions.
- A per-site-builder allowlist of CSS selectors maintained by hand; plans carry a selector only when discovery found one.
- Changing the ATS provider list, the techmap source, the translation step, or the control panel's UI beyond the discover button, the merge-review decisions and the 409.
