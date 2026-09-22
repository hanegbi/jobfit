# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local, searchable job-fit tool (`jobfit/`) for one person's (Dan Hanegbi's) job search: it scrapes real job postings from ~780 curated company career pages, scores each one deterministically (no LLM calls) against two CVs, cross-references LinkedIn connections and WhatsApp-referral leads, and renders everything into a single static `jobfit.html` file with client-side search/filter/save — no server needed.

The repo root also contains a set of standalone, pre-`jobfit` scrapers (`scrape_amazon_jobs.py`, `scrape_google_jobs.py`, `scrape_nvidia_jobs.py`, `scrape_paloalto_jobs.py`, `scrape_workday_jobs.py`, `scrape_google_silicon.py`) and their raw JSON/HTML output, left over from before the project was split into `jobfit/` (see git log: "Initial commit: jobfit2 job-fit scraper/scorer, split out of playground" — the commit message predates this rename). The only live bridge from that era is `jobfit/scripts/import_workday_sources.py`, which one-off-imports `nvidia_workday_jobs.json`/`crowdstrike_jobs.json` into the `jobfit` store. Treat everything else at the repo root as legacy/inactive unless asked to touch it.

## Commands

```
uv run python -m jobfit.scripts.update_jobs                  # incremental update, all companies — the main entry point
uv run python -m jobfit.scripts.update_jobs --limit 5         # test on the first 5 companies
uv run python -m jobfit.scripts.update_jobs --company Wiz     # just one company
uv run python -m jobfit.scripts.update_jobs --force-rescore   # rescore every existing job regardless of CV hash
uv run python -m jobfit.scripts.update_jobs --skip-aggregate  # update companies/*.json but don't rebuild data/jobs_v2.json
```

Other scripts (all `uv run python -m jobfit.scripts.<name>`):
- `check_urls [--limit N] [--workers N]` — HEAD/GET-audits every job URL for validity, reports only (`cache/url_check_report.json`).
- `check_linkedin_closed [--limit N] [--delay SECONDS]` — deliberately slow, sequential check of LinkedIn-sourced jobs for the "no longer accepting applications" banner (LinkedIn rate-limits aggressively).
- `company_career_scrape <input.json> [--limit N] [--workers N]` — (re)builds `cache/company_career_pages.json`, the highest-priority job source, from a `{company: url}` map.
- `playwright_listings [--limit N] [--concurrency N]` — JS-rendered fallback for career pages plain HTTP can't parse; reads `cache/needs_playwright.json`, writes `cache/playwright_listings.json`.
- `import_workday_sources` — one-off import of the root-level Workday scrapes into `companies/*.json`.

No test suite, linter, or type checker is configured in this repo (no `tests/`, no ruff/mypy config in `pyproject.toml`).

There is no `pipeline.py` CLI you should reach for by default — see Architecture below for why it still exists.

## Architecture

### Two generations of the same pipeline

- **`jobfit/scripts/update_jobs.py`** is the current, incremental way to run everything (what the README documents). Per company: fetch → diff against `jobfit/companies/<snake_case_name>.json` by a stable job ID → mark jobs `new`/`seen`/`closed` (closed jobs are kept, never deleted) → rescore only if new/changed or the CV files' hash changed → merge referral jobs → flatten all `companies/*.json` into `jobfit/data/jobs_v2.json` (`aggregate_to_jobs_v2`).
- **`jobfit/pipeline.py`** is an older, single-shot rebuild (`techmap ingest -> ATS fetch -> score -> connections -> JSON`) that writes `data/jobs_v2.json` directly with no per-company persistence or diffing. It's mostly superseded, but `update_jobs.py` still imports its `_infer_location_fields` (the location-inference fallback logic) rather than duplicating it — don't fork that logic between the two files.

Company-level state (`jobfit/companies/*.json`, ~780 files, one per company + `_meta.json` holding the last CV hash) is the durable source of truth; `data/jobs_v2.json` is a disposable, regeneratable flattening of it for `build_html.py`.

### Fetch cascade (per company, in `update_jobs.fetch_company_jobs_async`)

Each tier is only tried if the previous one found nothing that actually *scores* as a real job (not just "zero results" — a scrape of pure nav-link junk still returns entries, so `_any_job_scores_positive` is the real gate):
1. Plain HTTP listing scrape (`ats_fetchers.fetch_listing_links` + `listing_heuristics.py`'s link-text heuristics) against the URL in `companies_career_pages.json`.
2. Playwright headless-browser render (`scripts/playwright_listings.py`) for JS-only listing pages.
3. techmap's own CSV row for that company (title/location/level only, no description) as last resort.

`jobfit/pipeline.py`'s `fetch_all_company_jobs` describes a richer 5-tier version of this same idea (career-pages cache → linkedin-match cache → own ATS resolution → linkedin-match live scraper → techmap fallback) used by the older single-shot path.

### ATS API fetchers (`ats_fetchers.py`)

Direct API fetchers for 5 known ATS providers (Greenhouse, Lever, Ashby, Workable, Comeet), resolved from a known job URL via regex token extraction (`resolve_ats`/`TOKEN_PATTERNS`) — no blind homepage discovery needed once one job URL for a company is known. Also does generic plain-HTML description scraping with cookie-consent-widget and boilerplate stripping for everything else.

### Scoring (`scoring.py`, tuned by `config.py`)

Deterministic, no AI/LLM calls (despite `claude-agent-sdk` being a listed dependency — it isn't used anywhere in this package currently). A job is scored against each CV as a blend of:
- **Coverage** (dominant, 70%, when the description yields ≥3 real requirement terms): % of the job's own extracted requirement vocabulary (`config.SKILLS_VOCAB`) that the CV also contains.
- **Role/title match** (20%, or 75% when there's no real description to check — `TITLE_ONLY_SCALE` caps how high a title-only guess can score so it never outranks a description-verified match).
- **Years-of-experience fit** (10%/25%).
- Penalties for off-target titles, seniority terms ("principal", "staff", "director", ...), and excluded keywords.

Two CV profiles are scored independently and stored as `score_default`/`score_infra` plus a `best_*` pick — same person/resume text, different role-title weighting (`ROLE_WEIGHTS_DEFAULT` leans AI/software, `ROLE_WEIGHTS_INFRA` leans platform/SRE/MLOps). `config.py` explicitly documents that this role/keyword/location tuning mirrors a sibling project, `linkedin-match`'s `backend/core/keywords.py` — copied rather than imported so this stays a self-contained, independently runnable project.

### External inputs, all local/personal paths hardcoded in `config.py`

- Two `.docx` CVs (`CV_DEFAULT`, `CV_INFRA`) under `C:\Users\user\Documents\Job\2026\...`.
- A LinkedIn `Connections.csv` export (`connections.py` matches contacts to companies by a normalized-name key shared with referral matching).
- An optional WhatsApp-referral jobs export (`config.REFERRAL_JOBS_PATH`, under Downloads) — `referral_source.py` merges these in, matching companies by canonical name and jobs by title similarity (`difflib`, 0.75 threshold) rather than exact match, since referral jobs carry no stable ID of their own.
- A sibling project, `linkedin-match` (`C:\Users\user\Code\linkedin-match`), whose own scraped-jobs cache and live scraper `linkedin_match_bridge.py` reads/shells out to (in *its* venv, via `subprocess`) as one of the older pipeline's fetch tiers. `update_jobs.py`'s current cascade doesn't depend on it being present.

### Output (`build_html.py`)

Renders `data/jobs_v2.json` into one self-contained `jobfit.html` (JSON embedded inline, no fetch/server). The page is a vanilla-JS SPA: multi-field filter/search/sort, per-company grouping, saved filter sets, and per-job like/hide/sent/reached-out state — all persisted client-side in `localStorage` only (nothing round-trips back into the Python-side data). If you change a field name or shape in the job record (`pipeline.py`'s `build_jobs_dataset` / `update_jobs.py`'s `aggregate_to_jobs_v2`), update the corresponding read in `build_html.py`'s embedded JS too — they aren't type-checked against each other.

### Caching

Everything fetched is cached under `jobfit/cache/` (techmap CSVs, ATS API responses, generic-description scrapes, linkedin-match bridge results) with its own TTL, so reruns only chase gaps or expired entries — see `config.py` for the specific TTL per cache (`COMPANY_JOBS_TTL_HOURS`, `GENERIC_DESC_TTL_HOURS`). `jobfit/companies/*.json` and `jobfit/data/jobs_v2.json` are committed to git (tracked generated data/cache, per the most recent commit), not gitignored — a normal `update_jobs` run is expected to produce a real diff there.
