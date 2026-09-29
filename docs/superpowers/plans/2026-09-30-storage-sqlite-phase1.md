# Phase 1 (Storage): SQLite behind the pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the source of truth for company and job data, with the scrape and recompute stages reading and writing it through a store module, and the aggregate stage deleted.

**Architecture:** A `jobfit/store/` package owns every SQL statement; pipeline stages call intention-revealing functions and never see SQL. One SQLite file (`jobfit/data/jobfit.db`, WAL, gitignored) holds companies, jobs and scores, with an FTS5 index for search. The existing company JSON files stay on disk untouched until phase 4, so every step is reversible.

**Tech Stack:** Python 3.13, stdlib `sqlite3` (no ORM), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-30-jobfit-app-sqlite-api-react-design.md`

## Global Constraints

- Tests: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q` (full, ~3min). Fast loop adds `--ignore=jobfit/server/tests/test_scrape_plans_replay.py` (~10s). All new tests live in `jobfit/server/tests/`.
- `jobfit/server/tests/conftest.py` fails any test that writes to the real `companies/`, `jobs_v2.json`, `jobfit.html` or aggregate cache. New tests use `tmp_path` or `:memory:` and never the real DB path.
- No ORM, no new dependency. Explicit SQL in `jobfit/store/` only; any SQL outside that package is a bug.
- A job's id stays `base64url(normalized url)` — `jobfit.scrape.ids.normalize_job_url` + `update_jobs.compute_job_id`. Never re-derive ids during migration.
- Jobs are never deleted. A job that disappears from a listing becomes `status='closed'` and keeps its row.
- Existing suites (`test_scrape_*`, `test_ats_scorer_*`, the 856-test replay) must pass **unedited**. If a task needs to change one, stop: the store boundary is wrong.
- Nothing is deleted from disk in this phase except the aggregate stage and its cache directory.
- Commit after every task.
- The spec's `job_state` and `runs` tables are **not** created here. Nothing in phase 1 writes them; they arrive with their writers as migrations `002` and `003` in phases 2 and 4, which is also the first real exercise of the migration runner.

---

### Task 1: Database connection and migration runner

**Files:**
- Create: `jobfit/store/__init__.py`, `jobfit/store/db.py`, `jobfit/store/schema/001_initial.sql`
- Modify: `.gitignore`, `jobfit/config.py`
- Test: `jobfit/server/tests/test_store_db.py`

**Interfaces:**
- Consumes: `jobfit.config.ROOT`
- Produces: `db.connect(path: Path | str) -> sqlite3.Connection`, `db.migrate(conn) -> int` (returns the schema version reached), `config.DB_PATH: Path`

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_store_db.py
import sqlite3
from jobfit.store import db


def test_migrate_creates_the_schema_and_records_its_version():
    conn = db.connect(":memory:")
    assert db.migrate(conn) == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "jobs", "job_scores", "jobs_fts"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_migrate_is_idempotent():
    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute("INSERT INTO companies (id, display_name) VALUES ('acme', 'Acme')")
    assert db.migrate(conn) == 1
    assert conn.execute("SELECT count(*) FROM companies").fetchone()[0] == 1


def test_connect_enables_foreign_keys_and_row_access_by_name():
    conn = db.connect(":memory:")
    db.migrate(conn)
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("INSERT INTO companies (id, display_name) VALUES ('acme', 'Acme')")
    assert conn.execute("SELECT display_name FROM companies").fetchone()["display_name"] == "Acme"


def test_a_job_row_requires_a_company_that_exists():
    conn = db.connect(":memory:")
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (id, company_id, title, status) VALUES ('j1', 'ghost', 'Dev', 'new')")
```

Add `import pytest` at the top.

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobfit.store'`

- [ ] **Step 3: Write the schema**

Create `jobfit/store/schema/001_initial.sql`. Note `jobs_fts` is an external-content FTS5 table kept in sync by triggers — inserting into `jobs` alone must update the index.

```sql
CREATE TABLE companies (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    career_url      TEXT,
    review_decision TEXT,
    host            TEXT,
    industry        TEXT,
    size            TEXT,
    address_city    TEXT,
    last_checked    TEXT
);

CREATE TABLE jobs (
    id                  TEXT PRIMARY KEY,
    company_id          TEXT NOT NULL REFERENCES companies(id),
    title               TEXT NOT NULL,
    url                 TEXT,
    description         TEXT NOT NULL DEFAULT '',
    location            TEXT,
    city                TEXT,
    is_remote           INTEGER NOT NULL DEFAULT 0,
    department          TEXT,
    employment_type     TEXT,
    status              TEXT NOT NULL,
    first_seen          TEXT,
    last_seen           TEXT,
    posted_at           TEXT,
    closed_at           TEXT,
    closed_reason       TEXT,
    years_required      INTEGER,
    is_referral         INTEGER NOT NULL DEFAULT 0,
    referral_contact    TEXT,
    source_language     TEXT,
    title_original      TEXT,
    description_original TEXT,
    scrape_source       TEXT,
    job_evidence        TEXT
);

CREATE TABLE job_scores (
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    profile_id  TEXT NOT NULL,
    score       REAL,
    coverage    REAL,
    confidence  TEXT,
    matched     TEXT,
    cache_key   TEXT,
    PRIMARY KEY (job_id, profile_id)
);

CREATE INDEX idx_jobs_company ON jobs(company_id);
CREATE INDEX idx_jobs_status  ON jobs(status);
CREATE INDEX idx_jobs_city    ON jobs(city);
CREATE INDEX idx_scores_rank  ON job_scores(profile_id, score DESC);

CREATE VIRTUAL TABLE jobs_fts USING fts5(title, description, content='jobs', content_rowid='rowid');

CREATE TRIGGER jobs_fts_insert AFTER INSERT ON jobs BEGIN
    INSERT INTO jobs_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
CREATE TRIGGER jobs_fts_delete AFTER DELETE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description) VALUES('delete', old.rowid, old.title, old.description);
END;
CREATE TRIGGER jobs_fts_update AFTER UPDATE ON jobs BEGIN
    INSERT INTO jobs_fts(jobs_fts, rowid, title, description) VALUES('delete', old.rowid, old.title, old.description);
    INSERT INTO jobs_fts(rowid, title, description) VALUES (new.rowid, new.title, new.description);
END;
```

- [ ] **Step 4: Write the runner**

`jobfit/store/db.py`:

```python
"""Connection and schema migrations. Every other module in this package
assumes a connection from connect() - WAL, foreign keys on, rows by name."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schema"


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        # WAL lets the server read while a scrape writes. Meaningless for
        # an in-memory database, and sqlite refuses it there.
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every numbered .sql file above the database's user_version, in
    order, each in its own transaction. Returns the version reached."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        conn.execute("BEGIN")
        try:
            conn.executescript(path.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        current = version
    return current
```

`jobfit/store/__init__.py`:

```python
"""SQLite storage. Every SQL statement in jobfit lives in this package;
callers use the functions here and never write SQL of their own."""

from jobfit.store.db import connect, migrate

__all__ = ["connect", "migrate"]
```

- [ ] **Step 5: Register the path and ignore the database file**

In `jobfit/config.py`, next to `JOBS_OUTPUT_JSON`:

```python
DB_PATH = ROOT / "data" / "jobfit.db"
```

In `.gitignore`, under the existing generated-data section:

```
# SQLite store - regenerated from a scrape, never committed.
jobfit/data/jobfit.db
jobfit/data/jobfit.db-wal
jobfit/data/jobfit.db-shm
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_db.py -q`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add jobfit/store .gitignore jobfit/config.py jobfit/server/tests/test_store_db.py
git commit -m "feat(store): sqlite connection, schema and migration runner"
```

---

### Task 2: Company store

**Files:**
- Create: `jobfit/store/companies.py`
- Test: `jobfit/server/tests/test_store_companies.py`

**Interfaces:**
- Consumes: `db.connect`, `db.migrate`
- Produces: `upsert_company(conn, id, display_name, **fields) -> None`, `get_company(conn, id) -> sqlite3.Row | None`, `list_companies(conn) -> list[Row]`, `companies_to_scrape(conn) -> dict[str, str | None]`, `mark_checked(conn, id, when: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_store_companies.py
from jobfit.store import companies, db


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    return conn


def test_upsert_inserts_then_updates_without_losing_untouched_fields():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers", industry="Software")
    companies.upsert_company(conn, "acme", "Acme Ltd.", career_url="https://acme.com/jobs")
    row = companies.get_company(conn, "acme")
    assert row["display_name"] == "Acme Ltd."
    assert row["career_url"] == "https://acme.com/jobs"
    assert row["industry"] == "Software"


def test_companies_to_scrape_matches_the_old_file_rules():
    """A company with a URL is scraped. A company without one is scraped
    only when a review decided 'techmap'; 'skip' and undecided are out."""
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme", career_url="https://acme.com/careers")
    companies.upsert_company(conn, "beta", "Beta")
    companies.upsert_company(conn, "gamma", "Gamma", review_decision="techmap")
    companies.upsert_company(conn, "delta", "Delta", review_decision="skip")
    assert companies.companies_to_scrape(conn) == {"Acme": "https://acme.com/careers", "Gamma": None}


def test_mark_checked_records_the_timestamp():
    conn = _conn()
    companies.upsert_company(conn, "acme", "Acme")
    companies.mark_checked(conn, "acme", "2026-09-30T10:00:00Z")
    assert companies.get_company(conn, "acme")["last_checked"] == "2026-09-30T10:00:00Z"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_companies.py -q`
Expected: FAIL — `ImportError: cannot import name 'companies'`

- [ ] **Step 3: Implement**

`jobfit/store/companies.py`:

```python
"""Company rows. The primary key IS company identity - what
company_registry.json used to guard with a duplicate-detection gate."""

from __future__ import annotations

import sqlite3

_FIELDS = ("display_name", "career_url", "review_decision", "host", "industry", "size", "address_city", "last_checked")


def upsert_company(conn: sqlite3.Connection, company_id: str, display_name: str, **fields) -> None:
    unknown = set(fields) - set(_FIELDS)
    if unknown:
        raise ValueError(f"unknown company field(s): {sorted(unknown)}")
    values = {"display_name": display_name, **fields}
    assignments = ", ".join(f"{k} = :{k}" for k in values)
    columns = ", ".join(["id", *values])
    placeholders = ", ".join([":id", *(f":{k}" for k in values)])
    conn.execute(
        f"INSERT INTO companies ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        {"id": company_id, **values},
    )


def get_company(conn: sqlite3.Connection, company_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()


def list_companies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM companies ORDER BY display_name COLLATE NOCASE").fetchall()


def companies_to_scrape(conn: sqlite3.Connection) -> dict[str, str | None]:
    """{display_name: career_url|None} for every company a run should check -
    the rule update_jobs.load_companies_to_scrape() applied to the files."""
    rows = conn.execute(
        "SELECT display_name, career_url FROM companies "
        "WHERE career_url IS NOT NULL OR review_decision = 'techmap' "
        "ORDER BY display_name COLLATE NOCASE"
    ).fetchall()
    return {r["display_name"]: r["career_url"] for r in rows}


def mark_checked(conn: sqlite3.Connection, company_id: str, when: str) -> None:
    conn.execute("UPDATE companies SET last_checked = ? WHERE id = ?", (when, company_id))
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_companies.py -q`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/store/companies.py jobfit/server/tests/test_store_companies.py
git commit -m "feat(store): company rows and the scrape-selection rule"
```

---

### Task 3: Job store — upsert, diff and close

This is the heart of the phase: `diff_and_update`'s semantics, in SQL. Read `jobfit/scripts/update_jobs.py:171-260` before starting, and `jobfit/server/tests/test_diff_and_update.py` for the behavior being preserved.

**Files:**
- Create: `jobfit/store/jobs.py`
- Test: `jobfit/server/tests/test_store_jobs.py`

**Interfaces:**
- Consumes: `db`, `companies`
- Produces: `upsert_scraped(conn, company_id, jobs: list[dict], now: str, may_close: bool = True) -> tuple[int, int]` returning `(new_count, closed_count)`; `close_by_url(conn, {url: reason}, now) -> dict[str, int]`; `get_job(conn, job_id) -> Row | None`; `jobs_for_company(conn, company_id) -> list[Row]`

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_store_jobs.py
from jobfit.store import companies, db, jobs

NOW = "2026-09-30T10:00:00Z"
LATER = "2026-10-01T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    return conn


def _job(url, title="Senior Backend Engineer", **extra):
    return {"id": url.rsplit("/", 1)[-1], "title": title, "url": url, **extra}


def test_a_first_scrape_stores_every_job_as_new():
    conn = _conn()
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    assert (new, closed) == (1, 0)
    row = jobs.get_job(conn, "1")
    assert row["status"] == "new" and row["first_seen"] == NOW and row["title"] == "Senior Backend Engineer"


def test_seeing_a_job_again_marks_it_seen_and_bumps_last_seen():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    row = jobs.get_job(conn, "1")
    assert (new, closed) == (0, 0)
    assert row["status"] == "seen" and row["first_seen"] == NOW and row["last_seen"] == LATER


def test_a_job_missing_from_the_scrape_is_closed_not_deleted():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1"), _job("https://acme.com/jobs/2")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    assert (new, closed) == (0, 1)
    assert jobs.get_job(conn, "2")["status"] == "closed"
    assert jobs.get_job(conn, "2")["closed_at"] == LATER


def test_may_close_false_leaves_missing_jobs_open():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    new, closed = jobs.upsert_scraped(conn, "acme", [], LATER, may_close=False)
    assert closed == 0 and jobs.get_job(conn, "1")["status"] == "new"


def test_a_reappearing_closed_job_opens_again():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    jobs.upsert_scraped(conn, "acme", [], LATER)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], LATER)
    assert jobs.get_job(conn, "1")["status"] == "seen"


def test_a_stored_title_is_trimmed_but_never_replaced():
    """Same rule as update_jobs.diff_and_update: a re-scrape may cut card
    metadata off a stored title, never rename the job."""
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer Full-time Tel Aviv")], NOW)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer")], LATER)
    assert jobs.get_job(conn, "1")["title"] == "Senior MLOps Engineer"
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Office Manager")], LATER)
    assert jobs.get_job(conn, "1")["title"] == "Senior MLOps Engineer"


def test_close_by_url_closes_open_jobs_and_records_the_reason():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1")], NOW)
    stats = jobs.close_by_url(conn, {"https://acme.com/jobs/1": "http 404"}, LATER)
    row = jobs.get_job(conn, "1")
    assert stats["jobs_closed"] == 1
    assert row["status"] == "closed" and row["closed_reason"] == "http 404"
    assert jobs.close_by_url(conn, {"https://acme.com/jobs/1": "http 404"}, LATER)["already_closed"] == 1


def test_the_search_index_follows_a_title_change():
    conn = _conn()
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer Tel Aviv")], NOW)
    jobs.upsert_scraped(conn, "acme", [_job("https://acme.com/jobs/1", "Senior MLOps Engineer")], LATER)
    hits = conn.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'Aviv'").fetchall()
    assert hits == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_jobs.py -q`
Expected: FAIL — `ImportError: cannot import name 'jobs'`

- [ ] **Step 3: Implement**

`jobfit/store/jobs.py`. The title rule reuses `jobfit.scrape.titles.authoritative_title`, exactly as `diff_and_update` does today, so the two paths cannot drift.

```python
"""Job rows and the scrape diff: what is new, what is still there, what is
gone. Nothing is ever deleted - a job that disappears from a listing is
closed and keeps its row, because a closed listing is still evidence."""

from __future__ import annotations

import json
import sqlite3

from jobfit.scrape import titles
from jobfit.scrape.ids import normalize_job_url

_COLUMNS = (
    "title", "url", "description", "location", "city", "is_remote", "department", "employment_type",
    "status", "first_seen", "last_seen", "posted_at", "years_required", "is_referral", "referral_contact",
    "source_language", "title_original", "description_original", "scrape_source", "job_evidence",
)


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def jobs_for_company(conn: sqlite3.Connection, company_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE company_id = ? ORDER BY id", (company_id,)).fetchall()


def upsert_scraped(conn: sqlite3.Connection, company_id: str, scraped: list[dict], now: str,
                   may_close: bool = True) -> tuple[int, int]:
    """Returns (new_count, closed_count). may_close=False records the visit
    without closing anything - for a fetch too weak to prove absence."""
    stored = {r["id"]: r for r in jobs_for_company(conn, company_id)}
    seen_ids: set[str] = set()
    new_count = 0

    for job in scraped:
        job_id = job["id"]
        seen_ids.add(job_id)
        existing = stored.get(job_id)
        if existing is None:
            values = {c: job.get(c) for c in _COLUMNS}
            values.update(
                id=job_id, company_id=company_id, status="new",
                first_seen=job.get("posted_at") or now, last_seen=now,
                description=job.get("description") or "",
                is_remote=int(bool(job.get("is_remote"))), is_referral=int(bool(job.get("is_referral"))),
                job_evidence=json.dumps(job["job_evidence"]) if job.get("job_evidence") else None,
            )
            columns = ", ".join(values)
            conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({', '.join(':' + c for c in values)})", values)
            new_count += 1
            continue

        # A re-scrape may trim card metadata off a stored title; it may never
        # rename the job. Same rule, same function, as the file path used.
        trimmed = titles.authoritative_title([job.get("title") or ""], existing["title"] or "")
        updates = {"last_seen": now, "status": "seen" if existing["status"] in ("new", "closed") else existing["status"]}
        if trimmed:
            updates["title"] = trimmed
        if job.get("location") and not (existing["location"] or "").strip():
            updates["location"] = job["location"]
        if job.get("description") and not (existing["description"] or "").strip():
            updates["description"] = job["description"]
        if job.get("job_evidence") is not None:
            updates["job_evidence"] = json.dumps(job["job_evidence"])
        conn.execute(
            f"UPDATE jobs SET {', '.join(f'{k} = :{k}' for k in updates)} WHERE id = :id",
            {**updates, "id": job_id},
        )

    closed_count = 0
    if may_close:
        gone = [jid for jid, row in stored.items() if jid not in seen_ids and row["status"] != "closed"]
        for job_id in gone:
            conn.execute(
                "UPDATE jobs SET status = 'closed', closed_at = ?, closed_reason = 'not on the listing' WHERE id = ?",
                (now, job_id),
            )
        closed_count = len(gone)
    return new_count, closed_count


def close_by_url(conn: sqlite3.Connection, closed_urls: dict[str, str], now: str) -> dict[str, int]:
    """Close open jobs by URL, recording why - how jobs no company scrape
    re-verifies (LinkedIn, referrals) age out."""
    stats = {"jobs_closed": 0, "already_closed": 0}
    wanted = {normalize_job_url(u): reason for u, reason in closed_urls.items() if normalize_job_url(u)}
    if not wanted:
        return stats
    for row in conn.execute("SELECT id, url, status FROM jobs WHERE url IS NOT NULL").fetchall():
        reason = wanted.get(normalize_job_url(row["url"]))
        if reason is None:
            continue
        if row["status"] == "closed":
            stats["already_closed"] += 1
            continue
        conn.execute(
            "UPDATE jobs SET status = 'closed', closed_at = ?, closed_reason = ? WHERE id = ?",
            (now, reason, row["id"]),
        )
        stats["jobs_closed"] += 1
    return stats
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_jobs.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/store/jobs.py jobfit/server/tests/test_store_jobs.py
git commit -m "feat(store): job upsert, the scrape diff and closing by url"
```

---

### Task 4: Score store and the rescore gate

**Files:**
- Create: `jobfit/store/scores.py`
- Test: `jobfit/server/tests/test_store_scores.py`

**Interfaces:**
- Consumes: `db`, `jobs`
- Produces: `write_scores(conn, job_id, {profile_id: {...}}) -> None`, `scores_for_job(conn, job_id) -> dict[str, dict]`, `drop_scores_for_missing_profiles(conn, profile_ids: set[str]) -> int`

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_store_scores.py
from jobfit.store import companies, db, jobs, scores

NOW = "2026-09-30T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    jobs.upsert_scraped(conn, "acme", [{"id": "j1", "title": "Dev", "url": "https://acme.com/1"}], NOW)
    return conn


def test_scores_round_trip_per_profile():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 82.5, "coverage": 0.7, "confidence": "full",
                                                 "matched": ["python"], "cache_key": "k1"}})
    stored = scores.scores_for_job(conn, "j1")
    assert stored["default"]["score"] == 82.5
    assert stored["default"]["matched"] == ["python"]


def test_writing_again_replaces_that_profiles_score_only():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 10, "cache_key": "k1"},
                                     "infra": {"score": 20, "cache_key": "k2"}})
    scores.write_scores(conn, "j1", {"default": {"score": 99, "cache_key": "k9"}})
    stored = scores.scores_for_job(conn, "j1")
    assert stored["default"]["score"] == 99 and stored["infra"]["score"] == 20


def test_a_job_with_no_row_for_a_profile_reports_no_cache_key():
    """The rescore gate in recompute_stage reads cache keys through this;
    a missing profile row must read as "not scored", never as up to date."""
    conn = _conn()
    assert scores.scores_for_job(conn, "j1").get("default") is None
    scores.write_scores(conn, "j1", {"default": {"score": 1, "cache_key": "k1"}})
    assert scores.scores_for_job(conn, "j1")["default"]["cache_key"] == "k1"


def test_scores_for_removed_profiles_are_dropped():
    conn = _conn()
    scores.write_scores(conn, "j1", {"default": {"score": 1, "cache_key": "k1"},
                                     "gone": {"score": 2, "cache_key": "k2"}})
    assert scores.drop_scores_for_missing_profiles(conn, {"default"}) == 1
    assert set(scores.scores_for_job(conn, "j1")) == {"default"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_scores.py -q`
Expected: FAIL — `ImportError: cannot import name 'scores'`

- [ ] **Step 3: Implement**

```python
"""Per-(job, profile) scores. One row per profile rather than parallel
score_default/score_infra columns, so a third CV is data, not a migration."""

from __future__ import annotations

import json
import sqlite3


def write_scores(conn: sqlite3.Connection, job_id: str, by_profile: dict[str, dict]) -> None:
    for profile_id, values in by_profile.items():
        conn.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, coverage, confidence, matched, cache_key) "
            "VALUES (:job_id, :profile_id, :score, :coverage, :confidence, :matched, :cache_key) "
            "ON CONFLICT(job_id, profile_id) DO UPDATE SET "
            "score = :score, coverage = :coverage, confidence = :confidence, matched = :matched, cache_key = :cache_key",
            {
                "job_id": job_id, "profile_id": profile_id,
                "score": values.get("score"), "coverage": values.get("coverage"),
                "confidence": values.get("confidence"),
                "matched": json.dumps(values.get("matched") or [], ensure_ascii=False),
                "cache_key": values.get("cache_key"),
            },
        )


def scores_for_job(conn: sqlite3.Connection, job_id: str) -> dict[str, dict]:
    out = {}
    for row in conn.execute("SELECT * FROM job_scores WHERE job_id = ?", (job_id,)):
        entry = dict(row)
        entry["matched"] = json.loads(entry["matched"]) if entry["matched"] else []
        out[row["profile_id"]] = entry
    return out


def drop_scores_for_missing_profiles(conn: sqlite3.Connection, profile_ids: set[str]) -> int:
    if not profile_ids:
        return conn.execute("DELETE FROM job_scores").rowcount
    placeholders = ", ".join("?" * len(profile_ids))
    return conn.execute(
        f"DELETE FROM job_scores WHERE profile_id NOT IN ({placeholders})", tuple(sorted(profile_ids))
    ).rowcount
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_scores.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/store/scores.py jobfit/server/tests/test_store_scores.py
git commit -m "feat(store): per-profile job scores and the rescore gate"
```

---

### Task 5: The search query

The one read the whole application is built on: filter, sort, paginate, count — over all 30,147 jobs.

**Files:**
- Create: `jobfit/store/search.py`
- Test: `jobfit/server/tests/test_store_search.py`

**Interfaces:**
- Consumes: `db`, `jobs`, `scores`
- Produces: `search_jobs(conn, *, q=None, company_id=None, city=None, status=None, is_remote=None, min_score=None, profile="best", sort="score", page=1, size=50) -> dict` with keys `total`, `page`, `size`, `jobs` (list of dicts, **no description**)

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_store_search.py
from jobfit.store import companies, db, jobs, scores, search

NOW = "2026-09-30T10:00:00Z"


def _conn():
    conn = db.connect(":memory:")
    db.migrate(conn)
    companies.upsert_company(conn, "acme", "Acme")
    companies.upsert_company(conn, "beta", "Beta")
    jobs.upsert_scraped(conn, "acme", [
        {"id": "j1", "title": "Senior Backend Engineer", "url": "u1", "city": "Tel Aviv",
         "description": "Requirements: Python and Kubernetes"},
        {"id": "j2", "title": "Data Scientist", "url": "u2", "city": "Haifa", "description": "Requirements: pandas"},
    ], NOW)
    jobs.upsert_scraped(conn, "beta", [
        {"id": "j3", "title": "Platform Engineer", "url": "u3", "city": "Tel Aviv",
         "description": "Requirements: Kubernetes"},
    ], NOW)
    scores.write_scores(conn, "j1", {"default": {"score": 90, "cache_key": "a"}})
    scores.write_scores(conn, "j2", {"default": {"score": 40, "cache_key": "b"}})
    scores.write_scores(conn, "j3", {"default": {"score": 70, "cache_key": "c"}})
    return conn


def test_no_filters_returns_every_job_best_score_first():
    result = search.search_jobs(_conn())
    assert result["total"] == 3
    assert [j["id"] for j in result["jobs"]] == ["j1", "j3", "j2"]


def test_list_rows_never_carry_the_description():
    result = search.search_jobs(_conn())
    assert "description" not in result["jobs"][0]


def test_full_text_search_matches_title_and_description():
    conn = _conn()
    assert {j["id"] for j in search.search_jobs(conn, q="kubernetes")["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, q="scientist")["jobs"]} == {"j2"}


def test_filters_narrow_and_combine():
    conn = _conn()
    assert {j["id"] for j in search.search_jobs(conn, city="Tel Aviv")["jobs"]} == {"j1", "j3"}
    assert {j["id"] for j in search.search_jobs(conn, company_id="beta")["jobs"]} == {"j3"}
    assert {j["id"] for j in search.search_jobs(conn, city="Tel Aviv", min_score=80)["jobs"]} == {"j1"}


def test_pagination_reports_the_full_total():
    result = search.search_jobs(_conn(), page=2, size=2)
    assert result["total"] == 3 and [j["id"] for j in result["jobs"]] == ["j2"]


def test_a_query_with_no_matches_is_empty_not_an_error():
    assert search.search_jobs(_conn(), q="nonexistentterm")["total"] == 0


def test_fts_special_characters_do_not_crash_the_query():
    """A user typing "C++" or a quote must not produce an FTS syntax error."""
    for query in ['C++', 'senior "backend', "it's", "AND"]:
        assert search.search_jobs(_conn(), q=query)["total"] >= 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_search.py -q`
Expected: FAIL — `ImportError: cannot import name 'search'`

- [ ] **Step 3: Implement**

```python
"""The read the application is built on. One query answers "which jobs match"
with a total, so the client never holds the dataset."""

from __future__ import annotations

import re
import sqlite3

_LIST_COLUMNS = (
    "j.id, j.company_id, j.title, j.url, j.location, j.city, j.is_remote, j.department, "
    "j.employment_type, j.status, j.first_seen, j.last_seen, j.posted_at, j.years_required, "
    "j.is_referral, j.referral_contact, c.display_name AS company"
)
_SORTS = {
    "score": "best_score DESC, j.id",
    "date": "COALESCE(j.posted_at, j.first_seen) DESC, j.id",
    "company": "c.display_name COLLATE NOCASE, j.id",
}


def _fts_query(raw: str) -> str:
    """FTS5 has its own syntax, and a user typing C++ or a stray quote must
    not be a syntax error. Every run of word characters becomes one quoted
    term, and the terms are ANDed."""
    terms = re.findall(r"[0-9A-Za-z֐-׿#+.]+", raw)
    return " AND ".join(f'"{t}"' for t in terms if t)


def search_jobs(conn: sqlite3.Connection, *, q=None, company_id=None, city=None, status=None,
                is_remote=None, min_score=None, profile="best", sort="score", page=1, size=50) -> dict:
    where, params = [], {}
    if q:
        match = _fts_query(q)
        if not match:
            return {"total": 0, "page": page, "size": size, "jobs": []}
        where.append("j.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH :match)")
        params["match"] = match
    if company_id:
        where.append("j.company_id = :company_id"); params["company_id"] = company_id
    if city:
        where.append("j.city = :city"); params["city"] = city
    if status:
        where.append("j.status = :status"); params["status"] = status
    if is_remote is not None:
        where.append("j.is_remote = :is_remote"); params["is_remote"] = int(is_remote)

    if profile == "best":
        score_sql = "(SELECT max(score) FROM job_scores s WHERE s.job_id = j.id)"
    else:
        score_sql = "(SELECT score FROM job_scores s WHERE s.job_id = j.id AND s.profile_id = :profile)"
        params["profile"] = profile
    if min_score is not None:
        where.append(f"{score_sql} >= :min_score"); params["min_score"] = min_score

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    joins = "FROM jobs j JOIN companies c ON c.id = j.company_id"
    total = conn.execute(f"SELECT count(*) {joins} {clause}", params).fetchone()[0]

    params["limit"] = max(1, size)
    params["offset"] = max(0, (max(1, page) - 1) * max(1, size))
    rows = conn.execute(
        f"SELECT {_LIST_COLUMNS}, {score_sql} AS best_score {joins} {clause} "
        f"ORDER BY {_SORTS.get(sort, _SORTS['score'])} LIMIT :limit OFFSET :offset",
        params,
    ).fetchall()
    return {"total": total, "page": page, "size": size, "jobs": [dict(r) for r in rows]}
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_store_search.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/store/search.py jobfit/server/tests/test_store_search.py
git commit -m "feat(store): the job search query - filters, sort, pagination, totals"
```

---

### Task 6: Import the existing files into the database

**Files:**
- Create: `jobfit/scripts/migrate_to_db.py`
- Test: `jobfit/server/tests/test_migrate_to_db.py`

**Interfaces:**
- Consumes: every store module
- Produces: `import_all(conn, companies_dir: Path, career_pages: dict, review: dict, addresses: dict) -> dict` (counts), `verify(conn, companies_dir: Path, sample: int = 50, seed: int = 20260930) -> list[str]` (list of mismatches; empty means clean)

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_migrate_to_db.py
import json

from jobfit.scripts import migrate_to_db
from jobfit.store import db, jobs, scores


def _company_file(tmp_path, stem, record):
    (tmp_path / f"{stem}.json").write_text(json.dumps(record), encoding="utf-8")


def _record():
    return {"name": "Acme Ltd.", "career_url": "https://acme.com/careers", "last_checked": "2026-09-30T10:00:00Z",
            "jobs": [{"id": "j1", "title": "Senior Backend Engineer", "url": "https://acme.com/1",
                      "description": "Requirements: Python", "location": "Tel Aviv", "status": "seen",
                      "first_seen": "2026-09-01T00:00:00Z", "last_seen": "2026-09-30T00:00:00Z",
                      "score_default": 88.0, "coverage_default": 0.8, "confidence_default": "full",
                      "matched_default": ["python"], "_score_cache_keys": {"default": "k1"}}]}


def test_import_creates_the_company_its_jobs_and_its_scores(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    counts = migrate_to_db.import_all(conn, tmp_path, {"Acme Ltd.": "https://acme.com/careers"}, {}, {})
    assert counts == {"companies": 1, "jobs": 1, "scores": 1}
    job = jobs.get_job(conn, "j1")
    assert job["title"] == "Senior Backend Engineer" and job["company_id"] == "acme"
    assert scores.scores_for_job(conn, "j1")["default"]["score"] == 88.0


def test_import_is_idempotent(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM job_scores").fetchone()[0] == 1


def test_review_decisions_and_addresses_land_on_the_company(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    migrate_to_db.import_all(conn, tmp_path, {}, {"Acme Ltd.": "skip"}, {"acme": "Herzliya"})
    row = conn.execute("SELECT review_decision, address_city FROM companies").fetchone()
    assert row["review_decision"] == "skip" and row["address_city"] == "Herzliya"


def test_verify_is_silent_on_a_faithful_import(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    assert migrate_to_db.verify(conn, tmp_path) == []


def test_verify_reports_a_dropped_field(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    conn.execute("UPDATE jobs SET title = 'Something Else' WHERE id = 'j1'")
    problems = migrate_to_db.verify(conn, tmp_path)
    assert any("title" in p for p in problems)


def test_verify_reports_a_missing_job(tmp_path):
    _company_file(tmp_path, "acme", _record())
    conn = db.connect(":memory:"); db.migrate(conn)
    migrate_to_db.import_all(conn, tmp_path, {}, {}, {})
    conn.execute("DELETE FROM job_scores"); conn.execute("DELETE FROM jobs")
    assert any("count" in p for p in migrate_to_db.verify(conn, tmp_path))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_migrate_to_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobfit.scripts.migrate_to_db'`

- [ ] **Step 3: Implement**

Key detail: the old per-job score fields are `score_<profile>`, `coverage_<profile>`, `confidence_<profile>`, `matched_<profile>`, and `_score_cache_keys[<profile>]`. Profile ids are whatever suffixes appear.

```python
"""One-off: load the JSON company files into SQLite, then prove the result
matches them field by field. Idempotent - safe to re-run at any time."""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

from jobfit import config, connections
from jobfit.scripts.update_jobs import _snake_case
from jobfit.store import companies as companies_store
from jobfit.store import db, jobs as jobs_store, scores as scores_store

_JOB_FIELDS = ("title", "url", "description", "location", "city", "is_remote", "department", "employment_type",
               "status", "first_seen", "last_seen", "posted_at", "closed_at", "closed_reason", "years_required",
               "is_referral", "referral_contact", "source_language", "title_original", "description_original",
               "scrape_source")


def _profiles_in(job: dict) -> set[str]:
    return {k[len("score_"):] for k in job if k.startswith("score_")}


def import_all(conn: sqlite3.Connection, companies_dir: Path, career_pages: dict,
               review: dict, addresses: dict) -> dict:
    counts = {"companies": 0, "jobs": 0, "scores": 0}
    review_by_key = {connections.normalize_company(n): d for n, d in review.items()}
    url_by_key = {connections.normalize_company(n): u for n, u in career_pages.items()}
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        name = record.get("name") or path.stem
        company_id = _snake_case(name)
        key = connections.normalize_company(name)
        companies_store.upsert_company(
            conn, company_id, name,
            career_url=url_by_key.get(key, record.get("career_url")),
            review_decision=review_by_key.get(key),
            address_city=addresses.get(company_id) or addresses.get(key),
            last_checked=record.get("last_checked"),
        )
        counts["companies"] += 1
        for job in record.get("jobs", []):
            values = {f: job.get(f) for f in _JOB_FIELDS}
            values.update(
                id=job["id"], company_id=company_id, description=job.get("description") or "",
                status=job.get("status") or "seen",
                is_remote=int(bool(job.get("is_remote"))), is_referral=int(bool(job.get("is_referral"))),
                job_evidence=json.dumps(job["job_evidence"]) if job.get("job_evidence") else None,
            )
            columns = ", ".join(values)
            conn.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({', '.join(':' + c for c in values)}) "
                f"ON CONFLICT(id) DO UPDATE SET {', '.join(f'{c} = :{c}' for c in values if c != 'id')}",
                values,
            )
            counts["jobs"] += 1
            cache_keys = job.get("_score_cache_keys") or {}
            by_profile = {
                profile: {
                    "score": job.get(f"score_{profile}"), "coverage": job.get(f"coverage_{profile}"),
                    "confidence": job.get(f"confidence_{profile}"), "matched": job.get(f"matched_{profile}") or [],
                    "cache_key": cache_keys.get(profile),
                }
                for profile in _profiles_in(job)
            }
            if by_profile:
                scores_store.write_scores(conn, job["id"], by_profile)
                counts["scores"] += len(by_profile)
    return counts


def verify(conn: sqlite3.Connection, companies_dir: Path, sample: int = 50, seed: int = 20260930) -> list[str]:
    """Counts first, then a fixed-seed sample compared field by field. A
    fixed seed means a failure here is reproducible."""
    problems = []
    file_jobs = {}
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        for job in json.loads(path.read_text(encoding="utf-8")).get("jobs", []):
            file_jobs[job["id"]] = job
    db_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    if db_count != len(file_jobs):
        problems.append(f"job count: {len(file_jobs)} in files, {db_count} in the database")

    for job_id in random.Random(seed).sample(sorted(file_jobs), min(sample, len(file_jobs))):
        row = jobs_store.get_job(conn, job_id)
        if row is None:
            problems.append(f"{job_id}: missing from the database")
            continue
        source = file_jobs[job_id]
        for field in _JOB_FIELDS:
            expected = source.get(field)
            actual = row[field]
            if field in ("is_remote", "is_referral"):
                expected = int(bool(expected))
            if field == "description":
                expected = expected or ""
            if (expected or None) != (actual or None):
                problems.append(f"{job_id}: {field} is {actual!r}, files say {expected!r}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(config.DB_PATH))
    args = parser.parse_args()
    conn = db.connect(args.db)
    db.migrate(conn)
    career_pages = json.loads(config.COMPANIES_CAREER_PAGES_PATH.read_text(encoding="utf-8"))
    review_path = config.COMPANY_REVIEW_PATH
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    addresses_path = config.ROOT / "data" / "company_addresses.json"
    addresses = json.loads(addresses_path.read_text(encoding="utf-8")) if addresses_path.exists() else {}
    counts = import_all(conn, config.ROOT / "companies", career_pages,
                        {k: v.get("decision") if isinstance(v, dict) else v for k, v in review.items()}, addresses)
    print(f"imported: {counts}")
    problems = verify(conn, config.ROOT / "companies")
    print("verification: clean" if not problems else f"VERIFICATION FAILED ({len(problems)}):")
    for problem in problems[:20]:
        print(f"  {problem}")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_migrate_to_db.py -q`
Expected: 6 passed

- [ ] **Step 5: Run it for real and read the output**

Run: `PYTHONPATH=. uv run python -m jobfit.scripts.migrate_to_db`
Expected: `imported: {'companies': 1695, 'jobs': 30147, ...}` then `verification: clean`. If the review file's shape differs from the `{name: {"decision": ...}}` assumption, fix the adapter in `main()`, not the store.

- [ ] **Step 6: Commit**

```bash
git add jobfit/scripts/migrate_to_db.py jobfit/server/tests/test_migrate_to_db.py
git commit -m "feat(store): import the company JSON files into sqlite, with verification"
```

---

### Task 7: The scrape stage writes to the store

`diff_and_update` keeps its signature and its tests; its body becomes a store call. Read `update_jobs.py:171-260` and `_process_company` at `:284-303` first.

**Files:**
- Modify: `jobfit/scripts/update_jobs.py` (`diff_and_update`, `_process_company`, `load_companies_to_scrape`, `_should_skip_company`)
- Test: `jobfit/server/tests/test_diff_and_update.py` (existing — adapt its fixture, not its assertions), `jobfit/server/tests/test_scrape_stage.py` (existing)

**Interfaces:**
- Consumes: `store.jobs.upsert_scraped`, `store.companies.*`
- Produces: `diff_and_update(company, career_url, fetched, profiles, may_close=True) -> tuple[dict, int, int]` — same signature, now persisting through the store

- [ ] **Step 1: Add a shared test fixture for a temporary database**

In `jobfit/server/tests/conftest.py`, append:

```python
@pytest.fixture
def store_conn(tmp_path, monkeypatch):
    """A migrated, throwaway database wired in as the process-wide store."""
    from jobfit import config
    from jobfit.store import db

    path = tmp_path / "jobfit.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = db.connect(path)
    db.migrate(conn)
    monkeypatch.setattr(db, "_shared", conn, raising=False)
    return conn
```

- [ ] **Step 2: Run the existing scrape tests to see what breaks**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_diff_and_update.py jobfit/server/tests/test_scrape_stage.py -q`
Expected: PASS for now — this is the before-picture. Note the count.

- [ ] **Step 3: Give the store a process-wide connection**

Add to `jobfit/store/db.py`:

```python
_shared: sqlite3.Connection | None = None


def shared() -> sqlite3.Connection:
    """The process's connection to the configured database, opened and
    migrated on first use. Tests replace it through the store_conn fixture."""
    global _shared
    if _shared is None:
        from jobfit import config
        _shared = connect(config.DB_PATH)
        migrate(_shared)
    return _shared
```

- [ ] **Step 4: Rewrite `diff_and_update` over the store**

Replace the body (keep the docstring's first paragraph, add why it changed):

```python
def diff_and_update(company: str, career_url: str, fetched: list[dict], profiles: dict,
                    may_close: bool = True) -> tuple[dict, int, int]:
    """Returns (company_record, new_count, closed_count). Only newly-seen
    jobs are scored here; recompute_stage owns rescoring everything else.

    Persists through jobfit.store - the per-company JSON file it used to
    write is no longer the source of truth."""
    conn = db.shared()
    company_id = _snake_case(company)
    now = _now_iso()
    store_companies.upsert_company(conn, company_id, company, career_url=career_url, last_checked=now)

    relevant = []
    for job in fetched:
        title = (job.get("title") or "").strip()
        if not title or not scoring.is_relevant_location(job.get("location")):
            continue
        job = dict(job, title=title,
                   id=compute_job_id(company, title, job.get("location"), job.get("url")),
                   description=ats_fetchers.strip_html(job.get("description")))
        relevant.append(job)

    new_count, closed_count = store_jobs.upsert_scraped(conn, company_id, relevant, now, may_close=may_close)

    for job in relevant:
        if store_scores.scores_for_job(conn, job["id"]):
            continue
        scored = scoring.score_job_both(job, profiles)
        store_scores.write_scores(conn, job["id"], _score_rows(scored, job, profiles))

    record = {"name": company, "career_url": career_url, "last_checked": now,
              "jobs": [dict(r) for r in store_jobs.jobs_for_company(conn, company_id)]}
    return record, new_count, closed_count
```

Add the helper next to it, which converts `scoring.score_job_both`'s flat dict into per-profile rows:

```python
def _score_rows(scored: dict, job: dict, profiles: dict) -> dict[str, dict]:
    return {
        profile_id: {
            "score": scored.get(f"score_{profile_id}"),
            "coverage": scored.get(f"coverage_{profile_id}"),
            "confidence": scored.get(f"confidence_{profile_id}"),
            "matched": scored.get(f"matched_{profile_id}") or [],
            "cache_key": scoring.score_cache_key(job, profile),
        }
        for profile_id, profile in profiles.items()
    }
```

Imports at the top: `from jobfit.store import companies as store_companies, db, jobs as store_jobs, scores as store_scores`.

- [ ] **Step 5: Point company selection and the TTL check at the store**

`load_companies_to_scrape()` becomes `return store_companies.companies_to_scrape(db.shared())`. `_should_skip_company` keeps its signature (it takes a record dict with `last_checked`) — `_process_company` now builds that dict from `store_companies.get_company`.

- [ ] **Step 6: Adapt the existing tests' fixtures only**

In `test_diff_and_update.py` and `test_scrape_stage.py`, replace `monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)` with the `store_conn` fixture. **Assertions must not change.** Where a test read `record["jobs"][0]["title"]`, it still does — `diff_and_update` still returns a record.

- [ ] **Step 7: Run the fast suite**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests --ignore=jobfit/server/tests/test_scrape_plans_replay.py -q`
Expected: every test passes. If a `test_scrape_*` test other than the two above needs editing, stop — the boundary is wrong.

- [ ] **Step 8: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/store/db.py jobfit/server/tests
git commit -m "feat(store): the scrape stage persists through sqlite"
```

---

### Task 8: Recompute over the store, and delete the aggregate stage

**Files:**
- Modify: `jobfit/scripts/update_jobs.py` (`recompute_stage`, `_recompute_one_company`, `close_jobs_by_url`, `merge_referral_jobs`); delete `aggregate_to_jobs_v2`, `_flatten_company`, `_context_sha1`, `_row_engine_fingerprint`, `load_company_address_cities`, `PAGE_IRRELEVANT_FIELDS`
- Delete: `jobfit/cache/aggregate/` (directory, gitignored already)
- Test: `jobfit/server/tests/test_recompute_score_cache.py`, `test_aggregate_to_jobs_v2.py` (the aggregate tests move or go)

**Interfaces:**
- Consumes: `store.scores.jobs_needing_rescore`, `store.jobs`
- Produces: `recompute_stage(force: bool = False) -> None` — note `force_aggregate` is gone with the aggregate

- [ ] **Step 1: Write the failing test for the new rescore gate**

```python
# append to jobfit/server/tests/test_recompute_score_cache.py
def test_recompute_rescores_only_jobs_whose_cache_key_changed(store_conn, monkeypatch):
    from jobfit.scripts import update_jobs
    from jobfit.store import companies, jobs, scores

    companies.upsert_company(store_conn, "acme", "Acme")
    jobs.upsert_scraped(store_conn, "acme", [
        {"id": "j1", "title": "Backend Engineer", "url": "u1", "description": "Requirements: Python"},
        {"id": "j2", "title": "Data Scientist", "url": "u2", "description": "Requirements: pandas"},
    ], "2026-09-30T10:00:00Z")
    profiles = {"default": {"text": "python developer"}}
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: profiles)

    update_jobs.recompute_stage()
    first = {jid: scores.scores_for_job(store_conn, jid)["default"]["cache_key"] for jid in ("j1", "j2")}

    calls = []
    original = update_jobs.scoring.score_job_both
    monkeypatch.setattr(update_jobs.scoring, "score_job_both",
                        lambda job, profs: calls.append(job["id"]) or original(job, profs))
    update_jobs.recompute_stage()
    assert calls == []   # nothing changed, nothing rescored

    store_conn.execute("UPDATE jobs SET description = 'Requirements: Go' WHERE id = 'j1'")
    update_jobs.recompute_stage()
    assert calls == ["j1"]
    assert scores.scores_for_job(store_conn, "j1")["default"]["cache_key"] != first["j1"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_recompute_score_cache.py -q -k cache_key_changed`
Expected: FAIL — `recompute_stage` still walks company files.

- [ ] **Step 3: Rewrite `recompute_stage`**

The parallelism changes shape: work is now per batch of jobs, not per company file. Keep `ProcessPoolExecutor`, but hand each worker a list of job dicts and get scores back — workers must not hold the connection (SQLite connections are not shareable across processes).

```python
def recompute_stage(force: bool = False) -> None:
    """Rescore every stored job against the current profile registry.
    force=True ignores the per-job cache key - needed after a scoring
    logic change, though scoring.SCORING_ENGINE_FINGERPRINT usually
    invalidates the keys on its own."""
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="recompute", scope="all"):
        conn = db.shared()
        profiles = cv.load_profiles()
        dropped = store_scores.drop_scores_for_missing_profiles(conn, set(profiles))
        if dropped:
            logger.info("recompute: dropped %d score row(s) for removed profiles", dropped)

        pending = []
        for row in conn.execute("SELECT * FROM jobs ORDER BY id"):
            job = dict(row)
            stored = store_scores.scores_for_job(conn, job["id"])
            wanted = {p: scoring.score_cache_key(job, prof) for p, prof in profiles.items()}
            if not force and all(stored.get(p, {}).get("cache_key") == k for p, k in wanted.items()):
                continue
            pending.append(job)

        logger.info("recompute: %d job(s) to rescore against %d profile(s)", len(pending), len(profiles))
        for chunk in _chunks(pending, 500):
            for job, scored in zip(chunk, _score_chunk(chunk, profiles)):
                store_scores.write_scores(conn, job["id"], _score_rows(scored, job, profiles))
```

Add the two helpers it calls. Workers get plain dicts and return plain dicts — a SQLite connection cannot cross a process boundary, so all writing happens in the parent:

```python
def _chunks(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _score_one(args):
    job, profiles = args
    return scoring.score_job_both(job, profiles)


def _score_chunk(chunk: list[dict], profiles: dict) -> list[dict]:
    if len(chunk) < 50:  # process startup costs more than the work
        return [scoring.score_job_both(job, profiles) for job in chunk]
    with ProcessPoolExecutor(max_workers=RECOMPUTE_WORKERS) as pool:
        return list(pool.map(_score_one, [(job, profiles) for job in chunk]))
```

Then delete `aggregate_to_jobs_v2`, `_flatten_company`, `_context_sha1`, `_row_engine_fingerprint`, `load_company_address_cities` and `PAGE_IRRELEVANT_FIELDS`, and remove `--force-aggregate` and `--skip-aggregate` from `main()`'s parser.

- [ ] **Step 4: Move `close_jobs_by_url` and `merge_referral_jobs` onto the store**

`close_jobs_by_url` keeps its signature and its `test_close_jobs_by_url.py` assertions:

```python
def close_jobs_by_url(closed_urls: dict[str, str]) -> dict[str, int]:
    """Close every open job whose normalized URL is a key, recording the
    reason - how jobs no company scrape re-verifies (LinkedIn matches,
    referrals) age out. Nothing is deleted."""
    conn = db.shared()
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="close-stale", scope=f"{len(closed_urls)} urls"):
        before = {r["company_id"] for r in conn.execute(
            "SELECT DISTINCT company_id FROM jobs WHERE status = 'closed'")}
        stats = store_jobs.close_by_url(conn, closed_urls, _now_iso())
        after = {r["company_id"] for r in conn.execute(
            "SELECT DISTINCT company_id FROM jobs WHERE status = 'closed'")}
    stats["companies_touched"] = len(after - before)
    return stats
```

`merge_referral_jobs` keeps its matching logic (canonical company name, `difflib` title similarity) and changes only where it writes: referral jobs go through `store_jobs.upsert_scraped(conn, company_id, [job], now, may_close=False)` — `may_close=False` matters, because a referral export is not evidence that a company's other jobs are gone.

- [ ] **Step 5: Retire the aggregate tests**

`test_aggregate_to_jobs_v2.py` tests a function that no longer exists. The two tests worth keeping move to `test_store_search.py` as search assertions: closed jobs are still findable by filter, and a job's company name comes back with it. Delete the rest of the file along with the aggregate cache directory.

- [ ] **Step 6: Run the fast suite**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests --ignore=jobfit/server/tests/test_scrape_plans_replay.py -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git rm -r --cached jobfit/cache/aggregate 2>/dev/null; true
git add -A jobfit/scripts/update_jobs.py jobfit/server/tests
git commit -m "feat(store): rescore from sqlite and delete the aggregate stage"
```

---

### Task 9: Move the four readers of jobs_v2.json

**Files:**
- Modify: `jobfit/build_html.py:1185`, `jobfit/server/dashboard.py:24`, `jobfit/scripts/check_urls.py:59`, `jobfit/scripts/check_linkedin_closed.py:89`
- Test: `jobfit/server/tests/test_dashboard.py` (existing), `jobfit/server/tests/test_build_html_footer.py` (existing)

**Interfaces:**
- Consumes: `store.search.search_jobs`, `store.db.shared`
- Produces: no new public functions; four call sites change

- [ ] **Step 1: Write the failing test for the dashboard**

```python
# jobfit/server/tests/test_dashboard.py - replace the jobs_v2.json fixture
def test_dashboard_counts_come_from_the_store(store_conn):
    from jobfit.server import dashboard
    from jobfit.store import companies, jobs

    companies.upsert_company(store_conn, "acme", "Acme")
    jobs.upsert_scraped(store_conn, "acme", [
        {"id": "j1", "title": "Open Role", "url": "u1"},
        {"id": "j2", "title": "Gone Role", "url": "u2"},
    ], "2026-09-30T10:00:00Z")
    jobs.upsert_scraped(store_conn, "acme", [{"id": "j1", "title": "Open Role", "url": "u1"}],
                        "2026-10-01T10:00:00Z")
    stats = dashboard.get_dashboard_stats()
    assert stats["jobs_total"] == 2 and stats["jobs_open"] == 1 and stats["companies"] == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests/test_dashboard.py -q`
Expected: FAIL — the dashboard still reads `config.JOBS_OUTPUT_JSON`.

- [ ] **Step 3: Change the four call sites**

Each replaces a `json.loads(config.JOBS_OUTPUT_JSON.read_text(...))` with a store query.

`dashboard.get_dashboard_stats` — keep every key the panel already renders; only the source changes:

```python
def get_dashboard_stats() -> dict:
    conn = db.shared()
    counts = conn.execute(
        "SELECT count(*) AS total, "
        "sum(CASE WHEN status != 'closed' THEN 1 ELSE 0 END) AS open, "
        "sum(CASE WHEN status = 'new' THEN 1 ELSE 0 END) AS new FROM jobs"
    ).fetchone()
    return {
        "jobs_total": counts["total"],
        "jobs_open": counts["open"] or 0,
        "jobs_new": counts["new"] or 0,
        "companies": conn.execute("SELECT count(*) FROM companies").fetchone()[0],
        "connections_uploaded_at": _connections_uploaded_at(),
        "html_updated_at": _html_updated_at(),
    }
```

The remaining three:
- `check_urls.main` and `check_linkedin_closed.main`: read candidate URLs with `search_jobs(conn, status=...)` (or a direct `SELECT id, url, status FROM jobs`), and write results back through `store_jobs.close_by_url`, which they already do via `close_jobs_by_url`.
- `build_html.build`: takes its dataset from `search_jobs(conn, size=1_000_000)["jobs"]` plus descriptions for open jobs. This is temporary scaffolding, deleted in phase 4 — mark it with a comment saying so.

- [ ] **Step 4: Run the fast suite**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests --ignore=jobfit/server/tests/test_scrape_plans_replay.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jobfit/build_html.py jobfit/server/dashboard.py jobfit/scripts/check_urls.py jobfit/scripts/check_linkedin_closed.py jobfit/server/tests
git commit -m "feat(store): dashboard, url checks and the page read from sqlite"
```

---

### Task 10: End-to-end verification and documentation

**Files:**
- Modify: `CLAUDE.md`, `jobfit/server/CLAUDE.md`, `README.md`
- Create: `jobfit/store/CLAUDE.md`

- [ ] **Step 1: Run the full suite**

Run: `PYTHONPATH=. uv run python -m pytest jobfit/server/tests -q`
Expected: all pass, including all 856 replay tests unedited.

- [ ] **Step 2: Rebuild from a fresh database and compare against the files**

```bash
rm -f jobfit/data/jobfit.db*
PYTHONPATH=. uv run python -m jobfit.scripts.migrate_to_db
PYTHONPATH=. uv run python -c "
from jobfit.store import db, search
conn = db.connect('jobfit/data/jobfit.db')
print('total', search.search_jobs(conn)['total'])
print('open', search.search_jobs(conn, status='seen')['total'] + search.search_jobs(conn, status='new')['total'])
print('tel aviv', search.search_jobs(conn, city='Tel Aviv')['total'])
print('search kubernetes', search.search_jobs(conn, q='kubernetes')['total'])
"
```

Expected: total 30,147 (matching `jobs_v2.json` before this phase), and every query returns in well under a second.

- [ ] **Step 3: Run one real company end to end**

Run: `PYTHONPATH=. uv run python -m jobfit.scripts.update_jobs --company Wiz --force`
Expected: the run completes, and re-querying that company shows its jobs with `last_checked` updated. Verify the company JSON file on disk is now stale — it is no longer written, which is the point.

- [ ] **Step 4: Write `jobfit/store/CLAUDE.md`**

Under 60 lines, covering only what this package imposes: every SQL statement lives here and nowhere else; schema changes are a new numbered file in `schema/`, never an edit to an applied one; `db.shared()` is the process connection and tests swap it through the `store_conn` fixture; jobs are closed, never deleted; the job id rule is unchanged.

- [ ] **Step 5: Update the root CLAUDE.md**

The "Where state lives" table changes: company files are legacy-until-phase-4, `jobs_v2.json` is gone, `jobfit/data/jobfit.db` is the source of truth. Remove `--skip-aggregate` and `--force-aggregate` from the commands block. Note in the architecture section that the aggregate stage no longer exists.

- [ ] **Step 6: Note the phase in the README**

One line under "Keeping it up to date": data now lives in a local SQLite database; the static page is still generated, and is replaced by the app in a later phase.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md README.md jobfit/store/CLAUDE.md jobfit/server/CLAUDE.md
git commit -m "docs: sqlite is the store - commands, layout and the store package's rules"
```

---

## Done when

- Every test passes, and no test outside `test_diff_and_update.py`, `test_scrape_stage.py`, `test_dashboard.py`, `test_recompute_score_cache.py` and the deleted `test_aggregate_to_jobs_v2.py` was edited.
- `migrate_to_db` reports a clean verification against the 1,695 files.
- A scrape of one company writes to the database and leaves the files untouched.
- `jobs_v2.json` is no longer written by anything.
- The company JSON files are still on disk, untouched, ready to be deleted in phase 4.
