"""Pure functions computing the control panel's dashboard stats from what's on disk."""

from datetime import datetime, timezone

from jobfit import config, connections, cv
from jobfit.store import db


def _connections_uploaded_at() -> str | None:
    if not config.CONNECTIONS_CSV.exists():
        return None
    mtime = config.CONNECTIONS_CSV.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _html_updated_at() -> str | None:
    if not config.OUTPUT_HTML.exists():
        return None
    mtime = config.OUTPUT_HTML.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_dashboard_stats() -> dict:
    registry = cv.load_registry()
    profile_ids = list(registry)

    conn = db.shared()
    total_jobs = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    open_count = conn.execute("SELECT count(*) FROM jobs WHERE status != 'closed'").fetchone()[0]

    # One grouped query per profile instead of bucketing every job in Python.
    score_distribution: dict[str, dict[int, int]] = {}
    for profile_id in profile_ids:
        buckets = conn.execute(
            "SELECT CAST(s.score / 10 AS INTEGER) * 10 AS bucket, count(*) AS n "
            "FROM job_scores s JOIN jobs j ON j.id = s.job_id "
            "WHERE s.profile_id = ? AND s.score IS NOT NULL AND j.status != 'closed' "
            "GROUP BY bucket ORDER BY bucket",
            (profile_id,),
        ).fetchall()
        score_distribution[profile_id] = {row["bucket"]: row["n"] for row in buckets}

    conn_index = connections.load_connections_index() if config.CONNECTIONS_CSV.exists() else {}
    connections_count = sum(len(v) for v in conn_index.values())

    company_count = conn.execute("SELECT count(*) FROM companies").fetchone()[0]

    return {
        "total_jobs_open": open_count,
        "total_jobs_all_time": total_jobs,
        "companies": company_count,
        "connections": connections_count,
        "connections_uploaded_at": _connections_uploaded_at(),
        "html_updated_at": _html_updated_at(),
        "profiles": [
            {"id": pid, "name": entry["name"], "uploaded_at": entry["uploaded_at"]}
            for pid, entry in registry.items()
        ],
        "score_distribution": score_distribution,
    }
