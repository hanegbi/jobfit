"""Per-(job, profile) scores.

One row per profile rather than the parallel score_default/score_infra/
matched_default/... columns the JSON records carried: adding a third CV is
then data, not a schema change and an edit to every consumer.
"""

from __future__ import annotations

import json
import sqlite3


def write_scores(conn: sqlite3.Connection, job_id: str, by_profile: dict[str, dict]) -> None:
    for profile_id, values in by_profile.items():
        conn.execute(
            "INSERT INTO job_scores (job_id, profile_id, score, coverage, confidence, matched, cache_key) "
            "VALUES (:job_id, :profile_id, :score, :coverage, :confidence, :matched, :cache_key) "
            "ON CONFLICT(job_id, profile_id) DO UPDATE SET "
            "score = :score, coverage = :coverage, confidence = :confidence, "
            "matched = :matched, cache_key = :cache_key",
            {
                "job_id": job_id,
                "profile_id": profile_id,
                "score": values.get("score"),
                "coverage": values.get("coverage"),
                "confidence": values.get("confidence"),
                "matched": json.dumps(values.get("matched") or [], ensure_ascii=False),
                "cache_key": values.get("cache_key"),
            },
        )


def scores_for_job(conn: sqlite3.Connection, job_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM job_scores WHERE job_id = ?", (job_id,)):
        entry = dict(row)
        entry["matched"] = json.loads(entry["matched"]) if entry["matched"] else []
        out[row["profile_id"]] = entry
    return out


def drop_scores_for_missing_profiles(conn: sqlite3.Connection, profile_ids: set[str]) -> int:
    """Scores for a profile that no longer exists are not just stale, they
    are wrong - a deleted CV must stop influencing what ranks highest.

    An empty set does nothing. "I know of no profiles" is what a failed or
    not-yet-loaded CV registry looks like, and treating it as "delete every
    score" cost a real database all 60,294 of its score rows."""
    if not profile_ids:
        return 0
    placeholders = ", ".join("?" * len(profile_ids))
    return conn.execute(
        f"DELETE FROM job_scores WHERE profile_id NOT IN ({placeholders})",
        tuple(sorted(profile_ids)),
    ).rowcount
