"""The store's connection and schema. Every other store module assumes a
connection from connect(): WAL, foreign keys on, rows accessible by name."""

import sqlite3

import pytest

from jobfit.store import db


def test_migrate_creates_the_schema_and_records_its_version():
    conn = db.connect(":memory:")
    version = db.migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"companies", "jobs", "job_scores", "jobs_fts", "job_state"} <= tables
    # The version reached is whatever the newest schema file says, so adding
    # one does not mean editing this test.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == version
    assert version >= 2


def test_migrate_is_idempotent():
    conn = db.connect(":memory:")
    version = db.migrate(conn)
    conn.execute("INSERT INTO companies (id, display_name) VALUES ('acme', 'Acme')")
    assert db.migrate(conn) == version
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


def test_the_search_index_is_populated_by_inserting_a_job():
    conn = db.connect(":memory:")
    db.migrate(conn)
    conn.execute("INSERT INTO companies (id, display_name) VALUES ('acme', 'Acme')")
    conn.execute(
        "INSERT INTO jobs (id, company_id, title, description, status) "
        "VALUES ('j1', 'acme', 'Senior Backend Engineer', 'Requirements: Kubernetes', 'new')"
    )
    hits = conn.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'kubernetes'").fetchall()
    assert len(hits) == 1


def test_a_file_backed_database_runs_in_wal_mode(tmp_path):
    conn = db.connect(tmp_path / "jobfit.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
