"""Pure functions computing the control panel's dashboard stats from what's on disk."""

import json
from collections import Counter

from jobfit import config, connections, cv


def get_dashboard_stats() -> dict:
    registry = cv.load_registry()
    profile_ids = list(registry)

    jobs = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8")) if config.JOBS_OUTPUT_JSON.exists() else []
    open_jobs = [j for j in jobs if j.get("status") != "closed"]

    score_distribution: dict[str, dict[int, int]] = {}
    for profile_id in profile_ids:
        buckets: Counter = Counter()
        for job in open_jobs:
            score = job.get(f"score_{profile_id}")
            if score is None:
                continue
            buckets[(score // 10) * 10] += 1
        score_distribution[profile_id] = dict(sorted(buckets.items()))

    conn_index = connections.load_connections_index() if config.CONNECTIONS_CSV.exists() else {}
    connections_count = sum(len(v) for v in conn_index.values())

    companies_dir = config.ROOT / "companies"
    company_count = (
        sum(1 for p in companies_dir.glob("*.json") if p.name != "_meta.json")
        if companies_dir.exists() else 0
    )

    return {
        "total_jobs_open": len(open_jobs),
        "total_jobs_all_time": len(jobs),
        "companies": company_count,
        "connections": connections_count,
        "profiles": [{"id": pid, "name": entry["name"]} for pid, entry in registry.items()],
        "score_distribution": score_distribution,
    }
