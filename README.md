# jobfit2

A local, searchable job-fit tool: scrapes real job postings from company career pages, scores each one against two CVs (general software/AI vs. infra/MLOps) with a deterministic, no-AI-calls scorer, cross-references your LinkedIn connections, and renders everything into one static `jobfit2.html` page you open directly in a browser.

## Keeping it up to date

```
uv run python -m jobfit2.scripts.update_jobs
```

Fetches every company in `jobfit2/companies_career_pages.json`, diffs the result against what's already saved in `jobfit2/companies/<company>.json`, and regenerates `jobfit2.html`. Safe to run anytime:

- Existing jobs are **not** re-scraped or re-scored on every run - only their `last_seen` timestamp updates, unless your CV files changed (detected by hash) or you pass `--force-rescore`.
- A job missing from a company's page on this run is marked `"closed"` (kept, never deleted) rather than silently disappearing.
- A company that fails (site down, blocked, etc.) is logged and skipped - it doesn't stop the rest of the run, and its saved file is left untouched.
- Each company fetch falls back through three tiers if the previous one finds nothing real: plain HTTP → a headless-browser render (for JS-rendered listing pages) → techmap's own data for that company (title/location only, no description).

Useful flags:

```
uv run python -m jobfit2.scripts.update_jobs --limit 5        # test on the first 5 companies
uv run python -m jobfit2.scripts.update_jobs --company Wiz    # just one company
uv run python -m jobfit2.scripts.update_jobs --force-rescore  # rescore everything regardless of CV hash
```

Then open `jobfit2.html` - no server needed. New jobs are tagged "New"; closed listings are hidden by default (toggle "Show closed jobs" in the sidebar to review them).
