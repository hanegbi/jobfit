"""What the user thinks of each job: liked, hidden, CV sent, reached out.

The only table a scrape never writes. This lived in one browser's
localStorage until now, which meant it could not be queried, backed up, or
seen from any other device - and would have been lost by clearing site data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

STATE_FIELDS = ("liked", "hidden", "sent", "reached_out")


def get_state(conn: sqlite3.Connection, job_id: str) -> dict:
    """All four flags. A job with no row reads as all false rather than as
    missing - "not liked" and "never considered" are the same to a caller."""
    row = conn.execute("SELECT * FROM job_state WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return {field: False for field in STATE_FIELDS}
    return {field: bool(row[field]) for field in STATE_FIELDS}


def set_state(conn: sqlite3.Connection, job_id: str, **flags) -> dict:
    """Update the named flags, leaving the rest as they were. Returns the
    whole new state, so a caller never has to guess what it now is."""
    unknown = set(flags) - set(STATE_FIELDS)
    if unknown:
        raise ValueError(f"unknown state field(s): {sorted(unknown)}")
    if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        raise KeyError(job_id)
    merged = {**get_state(conn, job_id), **{field: bool(value) for field, value in flags.items()}}
    conn.execute(
        "INSERT INTO job_state (job_id, liked, hidden, sent, reached_out, updated_at) "
        "VALUES (:job_id, :liked, :hidden, :sent, :reached_out, :updated_at) "
        "ON CONFLICT(job_id) DO UPDATE SET liked = :liked, hidden = :hidden, sent = :sent, "
        "reached_out = :reached_out, updated_at = :updated_at",
        {
            "job_id": job_id,
            **{field: int(value) for field, value in merged.items()},
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    return merged


def ids_with(conn: sqlite3.Connection, field: str) -> set[str]:
    if field not in STATE_FIELDS:
        raise ValueError(f"unknown state field: {field}")
    return {row["job_id"] for row in conn.execute(f"SELECT job_id FROM job_state WHERE {field} = 1")}
