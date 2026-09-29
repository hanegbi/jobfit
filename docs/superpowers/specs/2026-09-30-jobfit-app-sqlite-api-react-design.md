# jobfit as an application: SQLite, an API, and a React front end

**Status:** approved design, 2026-09-30
**Supersedes:** the static-page output path described in
`2026-09-28-scrape-compute-pipeline-redesign-design.md` §5. Everything that spec says about
plan-driven scraping still holds; only where results are *stored and read* changes.

## 1. Why

jobfit works, but its storage has outgrown its shape. Jobs live in 1,695 per-company JSON files,
are flattened into a 77MB `jobs_v2.json`, and are shipped as a 77MB self-contained
`jobfit.html` that loads every job — open and closed, with descriptions — into the browser at once.

Four consequences, all observed rather than predicted:

- Both generated files passed GitHub's 100MB limit in September 2026 and blocked every push until
  they were trimmed. They are 77MB again today and growing with the dataset.
- A routine run rewrites thousands of tracked files, so `git diff` is useless for seeing what
  actually changed, and every run adds ~150MB to history permanently.
- There is no querying. Filtering 30,147 jobs happens in JavaScript, over data already parsed in
  memory, which is why the page must carry all of it.
- Per-job state (liked, hidden, CV sent, reached out) lives in one browser's `localStorage`. It
  cannot be queried, backed up, or reached from anywhere else.

The fix is the one the data has been asking for: a database, an API over it, and a front end that
queries instead of downloading.

## 2. Scope

**In:** SQLite as the single source of truth for company and job data; a query API; a React front
end that replaces `jobfit.html`; migration of existing data and of the browser's saved state.

**Out, deliberately:** authentication, deployment, multi-user support, application tracking
(status/notes/reminders beyond today's four flags), and historical trend analysis. Each was
considered and cut for v1. The schema leaves room for tracking and history; nothing is built for
them yet.

**Unchanged:** the scrape and scoring engines. `jobfit/scrape/` and `jobfit/ats_scorer/` keep their
behavior, their tests and their contracts. This spec changes where results are written, not how
they are produced.

## 3. Shape

One FastAPI process serves the React build and a JSON API. The scrape pipeline stays a separate
command. Both talk to one SQLite file.

```
update_jobs (CLI)  ─┐
                    ├─→  store/  ──→  jobfit/data/jobfit.db  (WAL, gitignored)
FastAPI (server)   ─┘                        ↑
   ├── /api/*  JSON                          │
   └── /       React build (static)  ────────┘  queries via the API
```

Decisions and their reasons:

- **SQLite, not Postgres.** One user, one machine, no daemon to run, and the database is a single
  file to copy or delete. Postgres would add operational weight for no gain at this size. If jobfit
  is ever deployed for other people, that is the moment to revisit — the store boundary below is
  what keeps that cheap.
- **Stdlib `sqlite3` with explicit SQL, no ORM.** The workload is bulk upserts and one complex
  read query. An ORM would obscure both. Schema changes run through a small numbered-migration
  runner keyed on `PRAGMA user_version`.
- **A store module between the pipeline and SQL.** `jobfit/store/` exposes intention-revealing
  functions (`upsert_jobs`, `jobs_needing_rescore`, `search_jobs`) and owns every SQL statement.
  The pipeline stages never see SQL. This is what lets the existing 1,354 tests keep testing
  behavior rather than being rewritten around a database.
- **WAL mode.** A scrape run and the server read and write concurrently; WAL makes readers
  non-blocking. The existing cross-process `PipelineLock` still guards whole stages.

## 4. Data model

Five tables. The two shape changes from today are deliberate and called out.

**`companies`** — one row per company; the primary key *is* company identity, replacing
`company_registry.json` and its duplicate-detection gate.

```
id TEXT PRIMARY KEY          -- snake_case slug, today's file stem
display_name TEXT NOT NULL
career_url TEXT              -- NULL = not scraped
review_decision TEXT         -- NULL | 'skip' | 'techmap'
host TEXT                    -- derived, for duplicate detection
industry TEXT, size TEXT, address_city TEXT
last_checked TEXT            -- ISO-8601 UTC
```

**`jobs`** — one row per job, `id` unchanged: base64url of the normalized URL, so every id in the
current store and in the browser's saved state survives migration untouched.

```
id TEXT PRIMARY KEY
company_id TEXT NOT NULL REFERENCES companies(id)
title TEXT NOT NULL, url TEXT, description TEXT
location TEXT, city TEXT, is_remote INTEGER
department TEXT, employment_type TEXT
status TEXT NOT NULL         -- 'new' | 'seen' | 'closed'
first_seen TEXT, last_seen TEXT, posted_at TEXT
years_required INTEGER
is_referral INTEGER, referral_contact TEXT
source_language TEXT, title_original TEXT, description_original TEXT
scrape_source TEXT, job_evidence TEXT   -- JSON blob, scrape-side diagnostics
```

**`job_scores`** — *shape change.* Today a job carries `score_default`, `score_infra`,
`matched_default`, `coverage_infra` and so on as parallel columns, so adding a third CV means a
schema change and touching every consumer. Here each (job, profile) pair is a row:

```
job_id TEXT REFERENCES jobs(id), profile_id TEXT,
score REAL, coverage REAL, confidence TEXT, matched TEXT (JSON),
cache_key TEXT,              -- scoring.score_cache_key; the rescore gate
PRIMARY KEY (job_id, profile_id)
```

`best_cv` and `best_score` are computed in the query, not stored.

**`job_state`** — the user's own data, the only table a scrape never touches:

```
job_id TEXT PRIMARY KEY REFERENCES jobs(id),
liked INTEGER, hidden INTEGER, sent INTEGER, reached_out INTEGER,
updated_at TEXT
```

**`runs`** — replaces `data/run_history.json`: id, started_at, finished_at, status, counts, error.

**`jobs_fts`** — *shape change.* An FTS5 external-content table over `title` and `description`,
kept in sync by triggers. This is what makes searching all 30,147 jobs (including the 15,868
closed ones) a query instead of a 77MB download.

Indices: `jobs(company_id)`, `jobs(status)`, `jobs(city)`, `job_scores(profile_id, score DESC)`.

### Files that move into the database

| Today | Becomes |
|---|---|
| `jobfit/companies/*.json` (1,695 files, 149MB) | `companies` + `jobs` + `job_scores` |
| `jobfit/data/jobs_v2.json` (77MB) | a query |
| `jobfit/data/company_registry.json` | the `companies` primary key |
| `jobfit/data/company_review.json` | `companies.review_decision` |
| `jobfit/data/company_addresses.json` | `companies.address_city` |
| `jobfit/companies_career_pages.json` | `companies.career_url` |
| `jobfit/data/run_history.json` | `runs` |
| browser `localStorage` | `job_state` |

`companies_career_pages.json` is the contentious one: it is hand-curated and committed, and the
project has months of history in it. It goes anyway, because the control panel *already* edits
career URLs and review decisions through the API (`POST /api/companies/{company}/career-url`), so
the file and the running system are already two sources of truth for the same fact — the condition
that produced 206 duplicate company records. Migration writes a one-time JSON snapshot of the
curated map, committed as `docs/migrations/2026-09-30-career-pages-snapshot.json`, so nothing is
unrecoverable.

### Files that stay files

Scrape plans (`data/scrape_plans/`) and listing snapshots (`cache/listing_snapshots/`): both are
hand-editable or test fixtures read directly by the replay suite, and neither is queried.
`data/link_rejects.json` is small scrape configuration. Personal uploads (CVs, connections CSV,
referral exports) stay gitignored files, as today.

## 5. API

Additive: every existing control-panel route keeps working. `GET /jobfit.html` is removed in
phase 4 with the page itself.

```
GET   /api/jobs
        q= full-text; company=; city=; status=; remote=; min_score=; profile=;
        liked=; hidden=; sent=; has_connection=; sort=score|date|company; page=; size=
     -> { total, page, size, jobs: [...] }   descriptions excluded from list rows
GET   /api/jobs/{id}          -> one job with description, scores per profile, connections
PATCH /api/jobs/{id}/state    -> { liked?, hidden?, sent?, reached_out? }
GET   /api/facets             -> counts per company / city / status for the current filter
GET   /api/companies          -> list with open-job counts, career URL, last checked
```

List rows carry no description: that single choice is most of the 77MB. A job's description is
fetched when its detail view opens.

## 6. Front end

React + TypeScript, built with Vite into `jobfit/server/static/app/`, served by FastAPI. In
development, Vite's dev server proxies `/api` to FastAPI.

v1 rebuilds what today's page does and nothing more: search box, filters (company, city, status,
remote, score range, connections, liked/hidden/sent), sort, company grouping, saved filter sets,
the four per-job toggles, and the job detail view. Filter state lives in the URL, so a filtered
view is a link — which today's page cannot do.

Two rules for the port: TanStack Query owns server state (no hand-rolled fetch caching), and the
result list is virtualized, because "show all matching jobs" must stay honest at 30,000 rows.

## 7. Migration and cutover

1. **Import.** `python -m jobfit.scripts.migrate_to_db` reads every company file, writes
   companies, jobs and scores, then verifies: total job count, per-company counts, and a
   field-by-field comparison of 50 jobs sampled with a fixed seed, so a failure is reproducible.
   It refuses to finish on any mismatch. Idempotent — safe to re-run.
2. **Rescue browser state.** The current page gets a one-line "Export my data" button that saves
   the six `jobfit_*` localStorage keys as JSON; `python -m jobfit.scripts.import_browser_state
   <file>` loads liked/hidden/sent/reached into `job_state`. Job ids are URL-derived and unchanged,
   so this is a direct mapping.
3. **Delete.** Only after the React app can do everything the page does: remove `build_html.py`,
   `jobfit.html`, `jobs_v2.json`, the company files, the four data files above, and the aggregate
   stage with its cache. One commit, reversible via git.

## 8. Testing

The bar: this replaces the storage layer of a tool holding months of scraped data, so "the tests
pass" must mean "the data is intact".

- **Store tests** against an in-memory SQLite: upsert semantics, the diff that marks jobs closed,
  the rescore gate, FTS behavior including Hebrew titles.
- **Migration test**, the important one: build a fixture tree of company JSON files, import it,
  and assert the database reproduces every field of every job. Plus an idempotency assertion —
  importing twice yields the same rows.
- **API tests** through FastAPI's `TestClient`: each filter narrows as expected, pagination totals
  are stable, sorting is deterministic, and a `PATCH` to state survives a re-read.
- **Existing suites unchanged.** `jobfit/scrape/` and `ats_scorer/` tests must not need edits; if
  they do, the store boundary is in the wrong place. The 856-test plan replay suite keeps reading
  snapshot files and is unaffected.
- **Front end:** Vitest over the filter-to-query-string logic, which is where port bugs will hide.
  No end-to-end browser suite in v1.
- **Cutover gate:** before deleting anything, one command compares the API's job counts per
  company against the files it replaced, and the numbers must match exactly.

## 9. Phases

Each phase ends with a working tool; nothing is deleted before its replacement works.

1. **Storage.** Schema, migration runner, store module, import command. `update_jobs` reads and
   writes the database; the aggregate stage and its cache are deleted. Four modules read
   `jobs_v2.json` today and all move to store queries in this phase: `build_html.py` (temporarily,
   so the page keeps working), `server/dashboard.py`, `scripts/check_urls.py` and
   `scripts/check_linkedin_closed.py`. The last two also *write* results back — closing jobs by
   URL — which becomes a store call rather than a rewrite of company files.
2. **API.** Query, detail, facet and state endpoints, with tests.
3. **React app.** The full port, served by FastAPI.
4. **Cutover.** Import browser state, delete the page and the files it needed, update
   `README.md`, root `CLAUDE.md` and `jobfit/server/CLAUDE.md`.

Phase 1 is planned in detail first. Phases 2-4 get their own plans once phase 1 ships, because
phase 1 will teach us things about the store boundary that would make a plan written today wrong.

## 10. Risks

- **The store boundary could leak.** If pipeline code starts needing SQL, the abstraction is
  wrong. Early signal: an existing scrape or scoring test needs rewriting. Stop and reshape.
- **Import fidelity.** A silent field drop would be discovered months later. Mitigated by the
  sampled field-by-field comparison and by keeping the files until phase 4.
- **SQLite write contention** between a long scrape and the server. WAL plus the existing pipeline
  lock should cover it; if a real `SQLITE_BUSY` appears, the write path gets a retry with backoff.
- **Scope creep into tracking and trends.** Both were explicitly cut. The schema accommodates them
  later; v1 does not build them.
