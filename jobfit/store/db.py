"""Connection and schema migrations.

Every other module in this package assumes a connection from connect():
WAL so the server can read while a scrape writes, foreign keys enforced so
a job can never point at a company that isn't there, and rows accessible
by column name.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schema"

_shared: sqlite3.Connection | None = None


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        # WAL is meaningless for an in-memory database and sqlite refuses it.
        conn.execute("PRAGMA journal_mode = WAL")
    # A scrape run and the server can collide on a write; wait rather than
    # raising SQLITE_BUSY at the caller.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every numbered .sql file above the database's user_version, in
    order, each in its own transaction. Returns the version reached.

    Schema changes are new files. An applied file is never edited - the
    version already recorded means it will never run again."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for path in sorted(SCHEMA_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        # executescript() COMMITs anything pending before it runs, so the
        # transaction has to live inside the script rather than around it.
        script = f"BEGIN;\n{path.read_text(encoding='utf-8')}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass  # nothing open; don't mask the real failure
            raise
        current = version
    return current


def shared() -> sqlite3.Connection:
    """The process's connection to the configured database, opened and
    migrated on first use. Tests replace it through the store_conn fixture."""
    global _shared
    if _shared is None:
        from jobfit import config

        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(config.DB_PATH)
        migrate(conn)
        _shared = conn
    return _shared
