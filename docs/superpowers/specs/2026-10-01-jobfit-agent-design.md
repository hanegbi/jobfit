# jobfit-agent — Design

Status: draft for review (2026-10-01)

## Problem

jobfit scores postings deterministically and offline. It cannot say *why* a job fits, what to change
in the CV, what the company is like, what to ask for, or how the interviews run. This adds a
reasoning agent on top, as a separate sub-project, that takes the top N jobs and produces one
application brief per job.

It is also a portfolio piece: it should demonstrate LangGraph state, parallel fan-out, subgraphs,
tool use, a reflection loop, human-in-the-loop and checkpointing, with a model-agnostic design that
runs free on a CPU-only machine.

## Constraints

- **Isolation.** Lives in `jobfit_agent/` at the repo root with its own `pyproject.toml`. `jobfit`
  never imports it, so the "no model on the runtime path" rule and
  `test_scrape_no_llm_at_runtime.py` stay true. A guard test in the agent suite asserts nothing
  under `jobfit/` imports `jobfit_agent`.
- **Read-only access to jobfit.** Jobs, scores, companies and contacts come from the public
  functions of `jobfit/store/` (`search_jobs`, `jobs.detail`, `companies.contacts_for`) and the CV
  profiles from `config.py` paths. No SQL in the agent. The agent never writes to `jobfit.db`.
- **Cost.** Default is $0: local Ollama models and free search. Claude is an optional per-node
  override. Hardware: CPU only, 32GB RAM, so 4B-8B models, minutes per job, run as a batch.
- **Honesty.** Every external fact (salary, funding, review claim, interview question) carries
  `source_url` and `retrieved_at`. A missing fact is `None` with reason "no data", never a guess.
  Exit/IPO output is qualitative with evidence, never a made-up probability.

## Architecture

```
jobfit_agent/
  pyproject.toml          langgraph, langchain-ollama, langchain-anthropic (optional), ddgs, pydantic, rank-bm25
  agent/
    config.py             per-node model routing, N, cache TTLs, paths
    models.py             one factory: node name -> ChatOllama | ChatAnthropic
    state.py              TypedDicts / Pydantic schemas
    graph.py              top-level graph
    nodes/                one file per node
    tools/                jobfit_store.py, web_search.py, fetch_page.py, referrals.py
    cache.py              per-company research cache (JSON files, TTL)
    report/               render.py (report.json -> HTML), template + assets, inlined at build
  cli.py                  python -m jobfit_agent.cli --top 5 [--profile X] [--resume THREAD]
  tests/
```

### Graph

```
select_jobs ─► fan out one run per job (Send)
                 │
   ┌─────────────┴──────────────── per-job subgraph ────────────────────┐
   │ load_job ─► fit_analysis ─► referral_lookup                        │
   │                 │                                                  │
   │                 ├─► company_research (cached, shared by company)   │
   │                 │       parallel: facts · funding_exit · reviews   │
   │                 │                 salary · interview_questions     │
   │                 ▼                                                  │
   │            cv_planner ─► critic ──weak (max 2)──► cv_planner       │
   │                             │ok                                    │
   │                             ▼                                      │
   │                        brief (per job)                             │
   └────────────────────────────────────────────────────────────────────┘
        ▼
   aggregate (group briefs by company) ─► interrupt() for approval ─► render report.html (+ report.json)
```

- **select_jobs**: deterministic. Top N open jobs by `best_score` via `search_jobs`. No LLM.
- **fit_analysis**: LLM reads JD + CV + the existing deterministic score and matched/missing skills.
  Output `FitAnalysis{verdict, strengths[], gaps[], deal_breakers[], score_agreement}`. The LLM
  explains and challenges the number; it does not replace it.
- **referral_lookup**: tool node over existing connections/referral data. No LLM.
- **company_research**: runs once per company per TTL, however many jobs share it. Five parallel
  nodes, each `search → fetch → retrieve → extract into a schema`:
  `facts` (size, location, funding, stage), `funding_exit` (last round, investors, signals),
  `reviews` (pros/cons themes, with counts of supporting snippets), `salary` (ranges from search
  snippets plus jobfit's own postings that state a salary), `interview_questions` (per stage).
  Levels.fyi and Glassdoor are reached through search snippets and fetch, best-effort. A blocked or
  empty source yields "no data", and the report says so.
- **cv_planner**: concrete edits to the CV for this job, grounded in `gaps` and the JD wording.
  Cannot invent experience; each suggestion cites the CV line it changes or marks it "new, only if
  true".
- **critic**: scores the plan against a rubric (grounded in CV? addresses top gaps? no fabricated
  claims?). If weak, returns feedback and the graph loops to `cv_planner`, max 2 iterations.
- **aggregate / interrupt**: collects briefs, pauses for the user to approve or drop jobs before
  the report is written (`interrupt()` plus checkpointer, so `--resume THREAD` continues).

### Report (`report.html`)

One self-contained HTML file (inline CSS and JS, no network, opens from disk like `jobfit.html`),
written to `jobfit_agent/out/<timestamp>/report.html` next to `report.json`, the same data the page
renders.

- **One tab per company**, ordered by best fit score; the tab label shows company name, job count
  and best score. A summary tab first lists all companies and jobs in one table with filters.
- **Inside a company tab:** a company panel (size, location, funding and stage, exit outlook with
  evidence, review pros/cons, salary range, contacts and referrals), then **one card per job** at
  that company, since a company can have several. Each job card holds the deterministic score next
  to the LLM verdict, strengths and gaps, the CV edit plan with the critic's verdict, and the
  per-stage interview questions, with a collapsible JD.
- **Provenance everywhere:** each external fact shows its source link and date; missing data shows
  as "no data" rather than blank. A footer per tab shows the models used, tokens and seconds per
  node.
- Keyboard-navigable tabs, a `#company-slug` URL hash so a tab can be linked, light and dark theme,
  print-friendly. Visual design is done at implementation time with the frontend design skills;
  the data contract is `report.json`, so the page can be restyled without touching the graph.

### State

`GraphState`: `profile_name`, `jobs[]`, `briefs[]` (reducer: append). `JobState`: `job`, `fit`,
`referrals`, `company` (ref to cache entry), `plan`, `critique`, `iterations`, `costs`. Every node
appends `{node, model, input_tokens, output_tokens, seconds}` to `costs`, so the report ends with a
cost and time line.

### Models

`config.py` maps each node to a model, e.g. `fit_analysis: ollama:qwen3:8b`,
`facts/reviews/...: ollama:qwen3:4b`, `cv_planner: ollama:qwen3:8b`, `critic: ollama:qwen3:8b`.
Any entry can be `anthropic:claude-haiku-4-5` or `anthropic:claude-sonnet-5-5` (needs
`ANTHROPIC_API_KEY`). Every LLM node uses structured output against a Pydantic schema; on a parse
failure it retries once, then records the node as failed and the brief carries the gap.

### Do we need RAG?

**Not as a vector database.** The CV is short and fits in the prompt. Research results are small
structured records, so a per-company cache is enough. What does help on a CPU is **retrieve-then-
extract** on fetched web pages: split the page into chunks, rank them with BM25 (`rank-bm25`, pure
Python, no embedding model) against the question ("salary", "interview process"), and send only
the top 3-5 chunks to the model. That cuts prompt reading time, which is the slow part on CPU, and
keeps small models on-topic. It is optional per node and can be added after the first version works.
Embeddings and a vector store would only pay off for cross-company questions ("what do similar
companies ask?") over a large corpus; that is out of scope and can be revisited.

## Error handling

- Search/fetch failures, 403s and empty results degrade to "no data" for that field, never fail the
  job. A per-domain delay and the cache keep us under rate limits.
- Node timeout (local model too slow): record failure, continue with the other nodes.
- Cache entries older than TTL (default 14 days) are refreshed; `--refresh` forces it.
- The run is resumable: checkpoints in SQLite via LangGraph's saver (agent's own file under
  `jobfit_agent/data/`, gitignored).

## Testing

- Unit tests use a fake chat model returning canned structured output, so graph wiring, the critic
  loop bound (max 2), fan-out, interrupt/resume and cache TTL are tested with no model and no
  network.
- Report tests render a fixture `report.json` with two companies (one with two jobs, one with a
  missing-data field) and assert the tabs, job cards and "no data" markers.
- Tool tests use recorded HTML/search fixtures, like the repo's scrape tests.
- The isolation guard test (above), plus a test that `jobfit_agent` opens the store read-only.
- One manual benchmark script times a single job on `qwen3:4b` and `qwen3:8b` so the time estimates
  are measured rather than guessed.

## Build order

1. Skeleton, config, model factory, state, store tool, `select_jobs`, CLI printing the selection.
2. `fit_analysis` → `cv_planner` → `critic` loop on one job, with fake-model tests.
3. `company_research`: `facts`, `salary`, cache.
4. `reviews`, `interview_questions`, then `funding_exit`.
5. Fan-out over N jobs, `aggregate`, `interrupt`, `report.json`, cost line.
5b. HTML report: company tabs, job cards, summary tab, provenance and cost footer.
6. Optional: BM25 retrieve-then-extract; Claude overrides.

## Non-goals

No live UI or server (the report is a static file), no vector DB, no auto-applying, no writing to the jobfit store, no scraping behind logins,
no LinkedIn data beyond what jobfit already holds.
