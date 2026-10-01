# jobfit/store

SQLite is the source of truth for companies and jobs. `jobfit/data/jobfit.db` (WAL, gitignored,
~206MB) holds 29,000 jobs; the company JSON files still on disk are the pre-migration source, kept
until phase 4 as a rollback path. Design: @docs/superpowers/specs/2026-09-30-jobfit-app-sqlite-api-react-design.md

## Rules

- **Every SQL statement in jobfit lives here.** A `SELECT` anywhere else is a bug — add a function
  to this package instead. That boundary is what let the storage layer change without rewriting the
  scrape, scoring or plan-replay suites. The one exception is `scripts/migrate_to_db.py`, the
  one-off importer, which goes away with the files.
- **Schema changes are a new numbered file** in `schema/`. Never edit an applied one: `migrate()`
  keys on `PRAGMA user_version` and will not run it again. `executescript()` commits whatever is
  pending before it runs, so the transaction lives *inside* the script, not around it.
- **Jobs are closed, never deleted.** A listing that used to exist is still evidence. `closed_at`
  and `closed_reason` say when and why.
- **A job's id is base64url of its normalized URL** (`scrape/ids.py`), unchanged by the migration,
  which is why the browser's saved likes still match.
- **`db.shared()` is the process connection**, opened and migrated on first use. Tests swap it
  through the `store_conn` fixture — a test that forgets it reads and writes the real 206MB
  database, which the conftest guard now catches.
- **A row handed out must look like a job**, not like storage. `job_evidence` is a TEXT column
  holding JSON; `row_to_job` decodes it, because scoring calls `.get()` on it. Returning the raw
  string crashed a whole recompute run.

## What is where

- `db.py` — connect, migrate, the shared connection. **`mmap_size`, not `cache_size`**: a filtered
  page costs 0.24s on the 2MB default and 0.06s mapped, while a 256MB `cache_size` gets only halfway
  (0.12s) and adds nothing on top of mmap. Sizes in between are *worse* than the default (64MB
  measured 0.62s, thrashing). Mapped pages are the OS page cache, so one copy is shared by the
  server and any scrape beside it and the OS can reclaim it; a `cache_size` is per connection and is
  neither. Don't "tune" this by raising `cache_size`.
- `companies.py` — company rows, `companies_to_scrape` (the rule that used to read
  `companies_career_pages.json` plus `company_review.json`), and the user's LinkedIn contacts.
  `refresh_connection_counts` writes the names and the count together from one source, so a card
  saying "3 contacts" can never list two.
- `jobs.py` — `upsert_scraped` is the scrape diff: new/seen/closed, titles trimmed but never
  renamed, `may_close=False` for a fetch too weak to prove absence.
- `scores.py` — one row per (job, profile). An empty profile set means "I know of no profiles", not
  "delete every score": reading it the other way once destroyed all 60,294 rows.
- `search.py` — the read the application is built on. List rows carry `SNIPPET_CHARS` of the
  description and never the whole of it, and a user's query is treated as data, not FTS5 syntax.
  `build_filter` is the shared WHERE clause, and
  the only place a filter is defined. Two rules that look like bugs and are not: `status="open"`
  means `!= 'closed'` rather than an enumeration of new+seen, and `max_years` keeps jobs with no
  stated years — a job that never said is not evidence of wanting more experience than you have.
- `state.py` — the user's own flags (liked, hidden, sent, reached out). The only table a scrape never
  writes, and the one whose rows must survive a re-scrape and a job closing.
- `facets.py` — counts per dimension, built from `search.build_filter` so a count can never disagree
  with the list it annotates. **One `MATERIALIZED` CTE, not seven queries**: every dimension groups
  the same filtered set, and without `MATERIALIZED` SQLite re-runs the CTE per branch and the single
  statement costs exactly what the seven did (1.09s against 0.23s on 13,448 rows). Each dimension is
  capped at `MAX_PER_DIMENSION`, because shipping all 1,492 companies to a list that shows eight was
  85KB of a 104KB response.
