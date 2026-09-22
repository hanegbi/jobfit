# Jobfit Control Panel — Design

Status: approved for planning (approaches confirmed 2026-09-22)

## Problem

jobfit today is a CLI-only tool: you run `update_jobs.py`, it scrapes ~780
company career pages sequentially (no concurrency, no per-company freshness
check — every run re-checks every company), and you open the resulting
static `jobfit.html` in a browser. There is no way to:

- add/replace a CV without editing `config.py` and re-running the whole
  pipeline
- add a referral job ad without dropping a file at a hardcoded Downloads
  path
- update your LinkedIn connections export without touching a path that
  lives in a sibling repo
- see what a run is doing while it's running, or how many jobs/companies
  exist at a glance
- trigger any of the above without also re-scraping everything

This spec adds a small local control panel (new server + UI) for those
operational tasks, without changing how `jobfit.html` itself is opened or
browsed.

## Scope

In scope:
- CV profile management (upload/name/remove; arbitrary count, not fixed to
  two)
- Connections CSV upload
- Referral job-ad upload (existing JSON export format)
- An on-demand trigger for the network-bound scrape, with a live log and a
  per-company freshness skip
- A dashboard: totals, score distribution, run history, connections count,
  company count
- Auto-rebuild of `jobfit.html` after every action that changes scored data

Out of scope (explicitly deferred):
- Scheduled/periodic runs (the trigger is on-demand only; the design
  doesn't block adding a scheduler later — see "Future: scheduling" below)
- Multi-user auth (this is a localhost, single-user tool)
- Rewriting `jobfit.html`'s browsing UI or its static/no-server delivery
  model
- Splitting the HTTP/Playwright fetch cascade into two separate batched
  phases (only pursued if freshness-skip + concurrency aren't enough — see
  "Scrape speed" below)

## Architecture

Two independently-triggerable pipelines share the same on-disk store
(`jobfit/companies/*.json`), matching the two different cost profiles of
the operations involved:

- **RECOMPUTE** — local only, no network, runs synchronously inline with
  the API request that triggered it (CV upload, connections upload,
  referral upload). Rescoring ~5-15k stored job records against a handful
  of CV profiles with a regex-based scorer is milliseconds of CPU work, not
  something that needs a background job.
- **SCRAPE** — network-bound (up to ~780 HTTP/Playwright requests), only
  ever started by an explicit `POST /api/run`, runs in a background thread,
  and streams its own log lines back to the browser over SSE.

Both stages read and write the same per-company JSON files that exist
today; nothing about that storage format or the closed/new/seen status
semantics changes.

```
Control Panel (browser)
  |-- upload CV / connections / referral --> RECOMPUTE (inline, sync)
  |-- POST /api/run (on demand)          --> SCRAPE (background thread)
                                                  |
                                     companies/*.json (per-company store)
                                                  |
                                          reaggregate -> data/jobs_v2.json
                                                  |
                                           rebuild -> jobfit.html
```

(Full diagram with labeled data flow: see the published sketch —
https://claude.ai/artifact/6XGtm8XBJM9vEwbYs7Cy4M)

## Components

### `jobfit/server/` (new)

- **`app.py`** — FastAPI application, route registration, serves the
  control-panel static page at `GET /`.
- **`runner.py`** — run orchestration. `start_run(force: bool) -> run_id`
  spawns a background thread running `scrape_stage()` then
  `recompute_stage()`; keeps a single in-memory "run active" flag (only one
  run at a time — a second `POST /api/run` while one is active returns
  409); appends a record to `jobfit/data/run_history.json` when the run
  finishes (or is marked `crashed: true` if the server restarts mid-run).
- **`logging_stream.py`** — `QueueLogHandler(logging.Handler)`: pushes
  formatted log records into a `queue.Queue`. Attached to the existing
  `jobfit.*` loggers (`jobfit.update_jobs`, `jobfit.ats`, `jobfit.techmap`,
  etc. — no changes needed to any existing `logger.info(...)` call site)
  only while a run is active; removed when it finishes. `GET
  /api/run/stream` (SSE) reads from this queue and forwards each line to
  the browser as it arrives.
- **`static/panel.html`** — the control panel UI: vanilla JS (same
  no-framework approach as `build_html.py`'s output), tabs for Dashboard /
  CV Profiles / Connections / Referrals / Run & Logs. Uses `EventSource`
  for the live log.

### Changes to existing modules

- **`jobfit/scripts/update_jobs.py`** — split the current monolithic
  `main()`/`run()` into two callable functions the server imports directly:
  - `scrape_stage(companies: dict[str, str], force: bool) -> RunStats` —
    today's fetch+diff loop, plus:
    - **freshness skip**: a company is skipped (no network call) if its
      stored `last_checked` is younger than
      `config.COMPANY_RECHECK_TTL_HOURS` (new config constant, default 12h),
      unless `force=True`.
    - **concurrency**: the per-company fetch+diff+save (currently a plain
      sequential `for` loop calling `asyncio.run()` once per company) moves
      into a `ThreadPoolExecutor(max_workers=WORKERS)`, same pattern
      already used in `pipeline.py`/`ats_fetchers.py` (`WORKERS = 8`). Each
      thread calls its own `asyncio.run()` for that company's fetch
      cascade — safe, since each thread gets its own event loop. Per-company
      try/except stays exactly as it is today (one bad company logs and is
      skipped, never aborts the run).
  - `recompute_stage() -> None` — rescan-and-rescore every stored job
    against the *current* profile registry, reaggregate
    `companies/*.json` → `data/jobs_v2.json`, then rebuild `jobfit.html`.
    Also strips any `score_<id>`/`matched_<id>`/`coverage_<id>`/
    `confidence_<id>`/`requirements_<id>` fields whose `<id>` is no longer
    in the profile registry (cleanup after a profile is deleted).
  - `update_jobs.main()` (the CLI entry point) becomes a thin wrapper:
    `scrape_stage(...)` then `recompute_stage()` then referral-merge, same
    end-to-end behavior as today, so `uv run python -m
    jobfit.scripts.update_jobs` keeps working unchanged.
  - `merge_referral_jobs(profiles, path)` gains an explicit `path`
    parameter (currently hardcoded to `config.REFERRAL_JOBS_PATH`) so the
    server can call it once per uploaded file, immediately, instead of only
    against one fixed path during a full run.

- **`jobfit/cv.py`** — `load_profiles()` becomes registry-driven: reads
  `jobfit/data/profiles.json` (`{id: {name, filename, uploaded_at}}`) and
  builds a `{must_have_keywords, text}` profile per registered CV under
  `jobfit/data/cvs/`, for however many are registered — no more hardcoded
  `CV_DEFAULT`/`CV_INFRA`.

- **`jobfit/scoring.py`** — drops the per-profile `ROLE_WEIGHTS_BY_PROFILE`
  distinction. A single shared `ROLE_WEIGHTS` table (the existing
  `ROLE_WEIGHTS_DEFAULT` values, since it's the less specialized of the
  two) is used for every profile. `score_job_both` already generalizes to
  N profiles as written (`for name, profile in profiles.items()`) — no
  change needed there beyond the weights lookup.

- **`jobfit/config.py`** — replace `CV_DEFAULT`/`CV_INFRA` with
  `CV_PROFILES_DIR = ROOT / "data" / "cvs"` and `CV_PROFILES_REGISTRY =
  ROOT / "data" / "profiles.json"`; replace `CONNECTIONS_CSV`'s external
  path with `ROOT / "data" / "connections.csv"`; add
  `COMPANY_RECHECK_TTL_HOURS = 12`; add `RUN_HISTORY_PATH`,
  `REFERRAL_UPLOADS_DIR`.

- **`jobfit/build_html.py`** — the embedded page currently hardcodes two
  `.cv-pill` blocks ("Default"/"Infra") and a two-option `<select>`. Both
  become data-driven: the template gets a new embedded `PROFILES` array
  (`[{id, name}]`, sourced from the registry at build time) alongside
  `JOBS`, and the JS renders one pill / one `<option>` per entry in it.

### Migration (existing data)

On first run under the new system: register the two existing CVs
(`CV_DEFAULT` → id `default`, `CV_INFRA` → id `infra`) in the new profile
registry, copying their files into `jobfit/data/cvs/`. This makes the
already-computed `score_default`/`score_infra` fields on all ~780
companies' stored jobs remain meaningful immediately — no forced full
rescore, no data loss, the two current CVs simply become the first two
entries in the N-profile system.

## Data model (new files, all under `jobfit/data/`)

- `data/cvs/<profile_id>.docx` — uploaded CV files
- `data/profiles.json` — `{ "<id>": {"name": str, "filename": str, "uploaded_at": iso8601} }`
- `data/connections.csv` — replaces the external `linkedin-match` path
- `data/referrals/<timestamp>-<original-filename>.json` — archived copies
  of every referral upload (audit trail; the merge itself happens
  synchronously at upload time, these are never re-read by the pipeline)
- `data/run_history.json` — append-only list:
  `{id, started_at, finished_at, trigger: "manual", force: bool, companies_checked, companies_skipped, new_jobs, closed_jobs, failures: [company, ...], duration_s, crashed: bool}`

No changes to `jobfit/companies/*.json`'s per-job record shape beyond the
existing dynamic `score_<name>`/`matched_<name>`/etc. fields now keying off
registry profile ids instead of the two hardcoded names.

## API surface

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | serve the control panel page |
| `/api/dashboard` | GET | totals, score distribution per profile, connections count, company count, last run summary |
| `/api/profiles` | GET | list registered CV profiles |
| `/api/profiles` | POST (multipart: name, file) | add a profile, copy file into `data/cvs/`, run `recompute_stage()` inline, return updated dashboard numbers |
| `/api/profiles/{id}` | DELETE | remove a profile, run `recompute_stage()` inline (drops its stale score fields) |
| `/api/connections` | POST (multipart: file) | replace `data/connections.csv`, run `recompute_stage()` inline |
| `/api/referrals` | POST (multipart: file) | archive upload, run `merge_referral_jobs(profiles, path=<upload>)`, then `recompute_stage()` inline; response includes the merge stats (`matched_existing_company`, `new_company`, `merged_into_existing_job`, `added_new_job`) so the UI can show e.g. "2 new jobs added, 3 already tracked" |
| `/api/run` | POST (json: `{force: bool}`) | start `scrape_stage()` in a background thread; 409 if a run is already active |
| `/api/run/status` | GET | `{running, run_id, started_at}` — lets the UI reconnect correctly after a page reload mid-run |
| `/api/run/stream` | GET (SSE) | live log lines for the active run; a final event carries the run's summary stats |
| `/api/run/history` | GET | past runs from `run_history.json` |

## Error handling

- Per-company scrape failures: unchanged from today — logged, that company
  skipped, run continues (already the existing behavior in
  `update_jobs.run()`).
- Upload validation: reject non-`.docx` CV uploads, non-`.csv` connections
  uploads, and referral JSON that doesn't match the expected
  `{"companies": [...]}` shape, with a 400 and a specific message shown in
  the UI (not a generic failure).
- Concurrent run attempts: `POST /api/run` while one is active returns 409
  with the active run's id; the UI disables the "Run" button and shows the
  in-progress state (polling `/api/run/status` on load) instead of letting
  a second run start.
- Server crash mid-run: `companies/*.json` writes are already atomic
  (`atomic_write_json` — write to `.tmp`, then rename), so a crash mid-run
  never corrupts existing data, only leaves the run incomplete. On the next
  server start, any `run_history.json` entry with no `finished_at` is
  marked `crashed: true`.

## Testing

The repo currently has no test suite anywhere, by convention. Given how
much of this subsystem is pure state mutation (uploads, merges, the
TTL-skip decision), I'll add a small, focused test module —
`jobfit/server/tests/` — covering only the parts that are cheap to get
wrong silently:
- the TTL-skip decision (`scrape_stage` given a company with a recent vs.
  stale `last_checked`)
- `merge_referral_jobs`'s duplicate-detection against a fixture company
  store (confirms the existing dedup logic still behaves the same once it
  takes an explicit `path` argument)
- the profile-registry CRUD (add/remove updates `profiles.json` and
  `data/cvs/` correctly, and `recompute_stage()` drops a removed profile's
  stale score fields)

No browser/UI test automation — manual verification of the panel through
the browser, consistent with how `jobfit.html` itself has always been
verified.

## Future: scheduling

Not built now, but the design doesn't block it: `POST /api/run` is already
a plain function call (`runner.start_run`), so a future periodic trigger
is just something else calling that same function on a timer (e.g. a
background `asyncio` task in `app.py`, or an OS-level scheduled task
calling a small CLI wrapper) — no architecture change needed to add it
later.

## Implementation order (phased, risk-first)

1. **Foundations, no server yet.** Refactor `update_jobs.py` into
   `scrape_stage()`/`recompute_stage()`; add TTL-skip + `ThreadPoolExecutor`
   concurrency; migrate the CV-profile data model to the registry (with
   `default`/`infra` auto-migrated); update `scoring.py`/`cv.py`/
   `config.py`; make `build_html.py` render N profiles dynamically. Verify
   entirely through the existing CLI (`update_jobs.py`'s own `main()` still
   works, same user-facing behavior) — this proves out the riskiest data
   model change (fixed-two → N profiles) before any server code exists.
2. **Server skeleton + CRUD.** FastAPI app, static panel shell,
   `/api/dashboard`, `/api/profiles` (list/add/delete), `/api/connections`,
   `/api/referrals`. All synchronous — no run-trigger yet. This alone
   already delivers "manage CVs/connections/referrals without touching
   config.py or fixed file paths."
3. **Run trigger + live log + history.** `runner.py`, `logging_stream.py`,
   `/api/run`, `/api/run/stream`, `/api/run/history`. Comes last because
   it's the highest-risk piece (background thread lifecycle, SSE,
   concurrent-run guarding) — built on top of the simpler, already-proven
   pieces from steps 1-2.

## New dependencies

`fastapi`, `uvicorn`, `python-multipart` (file uploads). SSE is hand-rolled
via a plain `StreamingResponse` yielding `text/event-stream` — not worth an
extra dependency for a few lines of formatting.
