# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A local, single-user job-fit tool. It scrapes ~780 curated company career pages, scores every posting
deterministically (no model call) against CV profiles, cross-references LinkedIn connections and
WhatsApp-referral leads, and renders one self-contained `jobfit.html` you open from disk. Everything runs
on one machine; there is no deployment, no server in production, no multi-user story.

## Commands

```
uv run python -m jobfit.scripts.update_jobs                   # the main entry point: scrape -> score -> jobfit.html
uv run python -m jobfit.scripts.update_jobs --limit 5         # first 5 companies only
uv run python -m jobfit.scripts.update_jobs --company Wiz     # one company, exact name match
uv run python -m jobfit.scripts.update_jobs --force-rescore   # ignore the score cache
uv run python -m jobfit.scripts.migrate_to_db                 # (one-off) load the company JSON files into the store
uv run python -m jobfit.scripts.update_jobs --plans           # print scrape-plan stats and exit (cheap, no network)
uv run python -m jobfit.scripts.update_jobs --discover        # derive missing scrape plans; THE ONLY MODE THAT CALLS A MODEL
uv run uvicorn jobfit.server.app:app --port 8787              # control panel at 127.0.0.1:8787
```

Tests (there is no linter, formatter or type checker — `ruff` and `mypy` are not installed):

```
PYTHONPATH=. uv run python -m pytest jobfit/server/tests --ignore=jobfit/server/tests/test_scrape_plans_replay.py -q   # ~10s, the working loop
PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q                                                            # ~3min, before committing
PYTHONPATH=. uv run python -m pytest jobfit/server/tests -k work_mode -q                                               # one test by name
```

Other scripts, all `uv run python -m jobfit.scripts.<name> --help`: `audit_scrape`, `check_urls`,
`check_linkedin_closed` (slow on purpose — LinkedIn rate-limits), `company_career_scrape <input.json>`,
`playwright_listings`, `update_techmap`, `import_workday_sources`.

## Architecture

`update_jobs.py` is the orchestrator and runs two stages, each under a cross-process lock
(`pipeline_lock.py`): **scrape** (per company, concurrent, writing to the store) → **recompute**
(rescore whatever the cache keys say is stale, then rebuild the page). There is no aggregate stage
any more — the page is built straight from the store, in about five seconds.

Where state lives:

| Path | Role |
|---|---|
| `jobfit/data/jobfit.db` | **The source of truth**: companies, jobs, scores, and an FTS index. SQLite, WAL, gitignored, ~206MB. Every query goes through `jobfit/store/` |
| `jobfit/data/scrape_plans/` | One plan per company: how to scrape it. Committed — see `jobfit/scrape/CLAUDE.md` |
| `jobfit/cache/listing_snapshots/` | The HTML each plan was derived from: the replay suite's fixtures. Committed |
| `jobfit/cache/` | Per-source caches with their own TTLs (see `config.py`) |
| `jobfit/companies/*.json`, `companies_career_pages.json`, `data/company_registry.json`, `data/company_review.json` | **Legacy.** The pre-migration source, kept as a rollback path until phase 4. Nothing writes them any more; nothing should read them except `scripts/migrate_to_db.py` |

`config.py` holds every path and tuning constant, including personal input paths (CVs, connections CSV,
referral export). Those inputs are gitignored and uploaded through the control panel — read the paths from
`config.py` rather than hardcoding any.

Sub-packages with their own CLAUDE.md: `jobfit/store/` (SQLite; every SQL statement), `jobfit/scrape/`
(plan-driven scraping), `jobfit/ats_scorer/` (the scoring engine), `jobfit/server/` (control panel —
and the whole project's test suite).

`jobfit.html` and `build_html.py` are scaffolding on the way out: the page is still generated from
the store so the tool keeps working, and both go away when the React application replaces them
(phase 3 of the spec above).

`jobfit/pipeline.py` is the superseded single-shot pipeline. It still exists because `update_jobs` imports
its `_infer_location_fields`; don't fork that logic, and don't reach for `pipeline.py` as a CLI.

## Conventions that are not obvious

- **A job's id is base64url of its normalized URL** (`scrape/ids.py`). Title and location are not part of it,
  so fixing a title never orphans a stored job.
- **Scoring is deterministic and offline.** `claude-agent-sdk` and `anthropic` are dependencies, but the only
  code allowed to call a model is plan discovery. `test_scrape_no_llm_at_runtime.py` enforces it.
- **Scores self-invalidate through a code fingerprint**: `scoring.score_cache_key` mixes
  `SCORING_ENGINE_FINGERPRINT` into every key, so a scoring change rescores on the next run without
  anyone passing `--force-rescore`. If you add a file that can change a score, make sure
  `_compute_scoring_engine_fingerprint` covers it.
- **The database is not committed; the fixtures are.** Scrape plans and listing snapshots are
  committed on purpose (a plan may have cost an API call, and the snapshots are the replay suite's
  fixtures). `jobfit.db` is rebuilt by a scrape, or from the legacy files via `migrate_to_db`.
- **A job listing only "Israel" gets a city** from its own text, then the company's registered address
  (`pipeline._infer_location_fields`). A job that states a foreign country keeps it and gets no city.
- Company names are matched by a normalized key (`connections.normalize_company`), shared by connection
  lookup, referral merging and duplicate detection.
- The repo root holds ~15 pre-`jobfit` scrapers (`scrape_*.py`) and their JSON output. They are inactive;
  the only live bridge is `scripts/import_workday_sources.py`. Don't touch the rest unless asked.

## Workflow

Before calling a change done:

1. Run the fast test subset. Add a test for any behavior you changed — this repo tests behavior, not
   implementation, and most tests are built from real scraped input.
2. If you touched scraping, scoring, or row building, run the full suite: `test_scrape_plans_replay.py`
   replays every committed plan against its saved HTML snapshot and fails **by company name** when a
   heuristic change breaks one.
3. If you changed data-shaping logic, rebuild and check the numbers moved as expected:
   `update_jobs.recompute_stage()`, or `--force-rescore` when the change is to scoring itself.
4. Verify claims by querying the store, not from memory — `jobfit/store/search.py` answers in
   milliseconds, so there is no excuse for guessing at counts.

Never run the test suite while an update is running: it holds the pipeline lock and writes the
database, which makes unrelated tests fail with `PipelineBusy` and trips the conftest guard. That
looks like dozens of real failures and is not.

## Do not

- Do not call a model, or add an import of `anthropic` / `claude-agent-sdk`, anywhere on the runtime path.
  Discovery (`scrape/planner.py`, via `bootstrap.build_discovery_planner`) is the only exception.
- Do not delete jobs to "clean up". Jobs are closed, never removed; a company that should stop being
  scraped gets its `career_url` cleared plus a `skip` review decision on its row.
- Do not write SQL outside `jobfit/store/`, and do not hand-edit `jobfit.db` or `jobfit.html`. Scrape
  plans are hand-editable; company career URLs and review decisions are edited through the control
  panel or the store, not the legacy JSON files.
- Do not commit anything under `jobfit/data/cvs/`, `data/connections.csv`, `data/profiles.json` or
  `data/referrals/` — personal data, gitignored.
- Do not introduce per-company special cases in the scrapers. Behavior belongs in a generic strategy plus
  that company's plan.
- Do not run `update_jobs` without a limit just to test something; use `--limit`, `--company`, or `--plans`.
- Do not delete the legacy company JSON files before phase 4. They are the only way back if the
  store loses something — which already happened once, and they are what restored it.

Committing is expected, not something to ask about: once a change is verified, commit it in logical commits
with a message explaining why. Pushing is still a separate, explicit decision.
