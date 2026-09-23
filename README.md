# jobfit

A local, searchable job-fit tool: scrapes real job postings from company career pages, scores each one against your CV profiles with a deterministic, no-AI-calls scorer, cross-references your LinkedIn connections, and renders everything into one static `jobfit.html` page you open directly in a browser.

**Latest results:** [jobfit.html](./jobfit.html) - GitHub shows this as source; use the "Download raw file" button (or `git pull`) to open it locally as a page.

## Keeping it up to date

```
uv run python -m jobfit.scripts.update_jobs
```

Fetches every company in `jobfit/companies_career_pages.json`, diffs the result against what's already saved in `jobfit/companies/<company>.json`, and regenerates `jobfit.html`. Safe to run anytime:

- A company checked within the last 12 hours is skipped unless you pass `--force`. Existing jobs are rescored on every run (cheap, local) against whatever CV profiles are currently registered - see the control panel below.
- A job missing from a company's page on this run is marked `"closed"` (kept, never deleted) rather than silently disappearing.
- A company that fails (site down, blocked, etc.) is logged and skipped - it doesn't stop the rest of the run, and its saved file is left untouched.
- Each company fetch falls back through three tiers if the previous one finds nothing real: plain HTTP → a headless-browser render (for JS-rendered listing pages) → techmap's own data for that company (title/location only, no description).

Useful flags:

```
uv run python -m jobfit.scripts.update_jobs --limit 5        # test on the first 5 companies
uv run python -m jobfit.scripts.update_jobs --company Wiz    # just one company
uv run python -m jobfit.scripts.update_jobs --force          # re-check companies even if recently checked
```

Then open `jobfit.html` - no server needed. New jobs are tagged "New"; closed listings are hidden by default (toggle "Show closed jobs" in the sidebar to review them).

## Control panel

```
uv run uvicorn jobfit.server.app:app --port 8787
```

Then open `http://127.0.0.1:8787/` - a local admin page for managing CV profiles, your connections CSV, and referral job ads (all with upload dates), plus an on-demand "Run update" button with a live log and run history. Uploads apply instantly (no scraping); the run button is the only thing that hits the network.
