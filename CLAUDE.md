# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A local, single-user job-fit tool. It scrapes ~780 curated company career pages, scores every posting
deterministically (no model call) against CV profiles, cross-references LinkedIn connections and
WhatsApp-referral leads, and renders one self-contained `jobfit.html` you open from disk. Everything runs
on one machine; there is no deployment, no server in production, no multi-user story.

## Commands

```
uv run python -m jobfit.scripts.update_jobs                   # the main entry point: scrape -> score -> aggregate -> jobfit.html
uv run python -m jobfit.scripts.update_jobs --limit 5         # first 5 companies only
uv run python -m jobfit.scripts.update_jobs --company Wiz     # one company, exact name match
uv run python -m jobfit.scripts.update_jobs --force-rescore   # ignore the score cache
uv run python -m jobfit.scripts.update_jobs --force-aggregate # ignore the per-company aggregate cache
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

`update_jobs.py` is the orchestrator and runs three stages, each under a cross-process lock
(`pipeline_lock.py`): **scrape** (per company, concurrent) → **recompute** (rescore changed jobs,
multiprocess) → **aggregate** (flatten to `data/jobs_v2.json`, then `build_html.py` writes `jobfit.html`).

Where state lives:

| Path | Role |
|---|---|
| `jobfit/companies_career_pages.json` | The curated `{company: url\|null}` map that decides which companies get scraped |
| `jobfit/companies/<snake_case>.json` | Durable source of truth: one file per company, jobs marked `new`/`seen`/`closed` (closed jobs are kept forever) |
| `jobfit/data/scrape_plans/` | One plan per company: how to scrape it. Committed — see `jobfit/scrape/CLAUDE.md` |
| `jobfit/data/company_registry.json` | Company identity; blocks a second file for a company already known |
| `jobfit/data/jobs_v2.json` | Disposable flattening of all company files, the only input to `build_html.py` |
| `jobfit/cache/` | Per-source caches with their own TTLs (see `config.py`) |

`config.py` holds every path and tuning constant, including personal input paths (CVs, connections CSV,
referral export). Those inputs are gitignored and uploaded through the control panel — read the paths from
`config.py` rather than hardcoding any.

Sub-packages with their own CLAUDE.md: `jobfit/scrape/` (plan-driven scraping), `jobfit/ats_scorer/`
(the scoring engine), `jobfit/server/` (control panel — and the whole project's test suite).

`jobfit/pipeline.py` is the superseded single-shot pipeline. It still exists because `update_jobs` imports
its `_infer_location_fields`; don't fork that logic, and don't reach for `pipeline.py` as a CLI.

## Conventions that are not obvious

- **A job's id is base64url of its normalized URL** (`scrape/ids.py`). Title and location are not part of it,
  so fixing a title never orphans a stored job.
- **Scoring is deterministic and offline.** `claude-agent-sdk` and `anthropic` are dependencies, but the only
  code allowed to call a model is plan discovery. `test_scrape_no_llm_at_runtime.py` enforces it.
- **Caches self-invalidate through code fingerprints**, so a logic fix actually changes the output:
  `scoring.SCORING_ENGINE_FINGERPRINT` for scores and `update_jobs._row_engine_fingerprint()` for aggregate
  rows. If you change what shapes a row or a score, make sure its fingerprint covers the file you touched.
- **Generated data is committed on purpose**: `companies/*.json`, `data/jobs_v2.json`, `jobfit.html`, scrape
  plans and listing snapshots. A normal run is expected to produce a large diff.
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
   `update_jobs.recompute_stage()` (or `update_jobs --skip-aggregate` off, `--force-aggregate` on).
4. Verify claims against the store, not from memory — `jobfit/companies/*.json` is the truth.

Never run the test suite while an update is running: it holds the pipeline lock and writes the aggregate
cache, which makes unrelated tests fail with `PipelineBusy` and trips the conftest guard. That looks like
dozens of real failures and is not.

## Do not

- Do not call a model, or add an import of `anthropic` / `claude-agent-sdk`, anywhere on the runtime path.
  Discovery (`scrape/planner.py`, via `bootstrap.build_discovery_planner`) is the only exception.
- Do not delete jobs or company files to "clean up". Jobs are closed, never removed; a company that should
  stop being scraped gets its URL set to `null` plus a `skip` decision in `company_review.py`.
- Do not hand-edit `data/jobs_v2.json` or `jobfit.html`; both are regenerated. Company files, scrape plans
  and `companies_career_pages.json` are hand-editable.
- Do not commit anything under `jobfit/data/cvs/`, `data/connections.csv`, `data/profiles.json` or
  `data/referrals/` — personal data, gitignored.
- Do not introduce per-company special cases in the scrapers. Behavior belongs in a generic strategy plus
  that company's plan.
- Do not run `update_jobs` without a limit just to test something; use `--limit`, `--company`, or `--plans`.

Committing is expected, not something to ask about: once a change is verified, commit it in logical commits
with a message explaining why. Pushing is still a separate, explicit decision.
