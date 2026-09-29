# jobfit

A local, searchable job-fit tool: scrapes real job postings from company career pages, scores each one against your CV profiles with a deterministic, no-AI-calls scorer, cross-references your LinkedIn connections, and renders everything into one static `jobfit.html` page you open directly in a browser.

**Latest results:** [jobfit.html](./jobfit.html) - GitHub shows this as source; use the "Download raw file" button (or `git pull`) to open it locally as a page.

## Keeping it up to date

```
uv run python -m jobfit.scripts.update_jobs
```

Fetches every company in `jobfit/companies_career_pages.json`, diffs the result against what's already saved in `jobfit/companies/<company>.json`, and regenerates `jobfit.html`. Safe to run anytime:

- A company checked within the last 12 hours is skipped unless you pass `--force`. Jobs are rescored whenever the scoring engine, your CVs, or the job itself changed - the cache notices on its own, so there is nothing to remember to invalidate.
- A job missing from a company's page on this run is marked `"closed"` (kept, never deleted) rather than silently disappearing.
- A company that fails (site down, blocked, etc.) is logged and skipped - it doesn't stop the rest of the run, and its saved file is left untouched.
- Each company is scraped by its own stored *plan* - its ATS board's API where it has one, otherwise its listing page, with a headless-browser render and techmap's own data as fallbacks.

Useful flags:

```
uv run python -m jobfit.scripts.update_jobs --limit 5        # test on the first 5 companies
uv run python -m jobfit.scripts.update_jobs --company Wiz    # just one company
uv run python -m jobfit.scripts.update_jobs --force          # re-check companies even if recently checked
uv run python -m jobfit.scripts.update_jobs --plans          # what each company's scrape plan looks like, no network
```

Then open `jobfit.html` - no server needed. New jobs are tagged "New"; closed listings are hidden by default (toggle "Show closed jobs" in the sidebar to review them).

## Control panel

```
uv run uvicorn jobfit.server.app:app --port 8787
```

Then open `http://127.0.0.1:8787/` - a local admin page for managing CV profiles, your connections CSV, and referral job ads (all with upload dates), plus an on-demand "Run update" button with a live log and run history. Uploads apply instantly (no scraping); the run button is the only thing that hits the network.

## Adding a company

Add it to `jobfit/companies_career_pages.json` as `"Company Name": "https://.../careers"`, then run `--company "Company Name"`. The first run derives a scrape plan for it. Setting the URL to `null` stops it being scraped without losing its saved jobs.

## Scraping a company you've just added

The scraper reads career pages in plain Python - no model is called while scraping, ever. Working out *how* to read a new company's page (`--discover`) is the one step that may ask a small model, and it runs once per company and stores the answer.

## Tests

```
PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q
```

~1,350 tests, about three minutes. Most of the runtime is `test_scrape_plans_replay.py`, which replays every stored scrape plan against a saved copy of that company's careers page - it tells you, by company name, when a site changed shape or a heuristic regressed. While developing, `--ignore` that one file for a ten-second run.

Working on this repo with Claude Code? `CLAUDE.md` has the architecture map and conventions.
