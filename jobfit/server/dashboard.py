"""Pure functions computing the control panel's dashboard stats from what's on disk."""

from datetime import datetime, timezone

from jobfit import config, connections, cv
from jobfit.store import companies as store_companies
from jobfit.store import db
from jobfit.store import jobs as store_jobs
from jobfit.store import scores as store_scores


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
    counts = store_jobs.counts(conn)
    # One grouped query per profile, instead of bucketing every job in Python.
    score_distribution = {pid: store_scores.distribution(conn, pid) for pid in profile_ids}

    conn_index = connections.load_connections_index() if config.CONNECTIONS_CSV.exists() else {}
    connections_count = sum(len(v) for v in conn_index.values())

    return {
        "total_jobs_open": counts["open"],
        "total_jobs_all_time": counts["total"],
        "companies": len(store_companies.list_companies(conn)),
        "connections": connections_count,
        "connections_uploaded_at": _connections_uploaded_at(),
        "html_updated_at": _html_updated_at(),
        "profiles": [
            {"id": pid, "name": entry["name"], "uploaded_at": entry["uploaded_at"]}
            for pid, entry in registry.items()
        ],
        "score_distribution": score_distribution,
    }
