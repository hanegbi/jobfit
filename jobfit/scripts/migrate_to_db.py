"""One-off: load the JSON company files into SQLite, then prove the result
matches them field by field.

Idempotent - safe to re-run at any point. The files are left untouched, so
until they are deleted (phase 4) this can always be redone from scratch.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
from pathlib import Path

from jobfit import config, connections
from jobfit.scripts.update_jobs import _snake_case, load_company_address_cities, load_techmap_index, location_fields_for
from jobfit.store import companies as companies_store
from jobfit.store import db
from jobfit.store import jobs as jobs_store
from jobfit.store import scores as scores_store

# Every job field that exists in both the files and the jobs table. The
# verification below walks exactly this list, so a column added to one side
# and not the other shows up as a mismatch rather than silent data loss.
JOB_FIELDS = (
    "title", "url", "description", "location", "city", "is_remote", "department", "employment_type",
    "status", "first_seen", "last_seen", "posted_at", "closed_at", "closed_reason", "years_required",
    "is_referral", "referral_contact", "source_language", "title_original", "description_original",
    "scrape_source",
)
_BOOLEAN_FIELDS = ("is_remote", "is_referral")
# Not copied from the file but derived when the job is written, so they are
# checked by recomputing them rather than by comparing against a file that
# never carried them.
_DERIVED_FIELDS = ("location", "city", "is_remote")
_COPIED_FIELDS = tuple(f for f in JOB_FIELDS if f not in _DERIVED_FIELDS)


def _profiles_in(job: dict) -> set[str]:
    return {key[len("score_"):] for key in job if key.startswith("score_")}


def _score_rows(job: dict) -> dict[str, dict]:
    cache_keys = job.get("_score_cache_keys") or {}
    return {
        profile: {
            "score": job.get(f"score_{profile}"),
            "coverage": job.get(f"coverage_{profile}"),
            "confidence": job.get(f"confidence_{profile}"),
            "matched": job.get(f"matched_{profile}") or [],
            "cache_key": cache_keys.get(profile),
        }
        for profile in _profiles_in(job)
    }


def _upsert_job(conn: sqlite3.Connection, company_id: str, job: dict) -> None:
    values = {field: job.get(field) for field in JOB_FIELDS}
    values.update(
        id=job["id"],
        company_id=company_id,
        description=job.get("description") or "",
        status=job.get("status") or "seen",
        is_remote=int(bool(job.get("is_remote"))),
        is_referral=int(bool(job.get("is_referral"))),
        job_evidence=json.dumps(job["job_evidence"]) if job.get("job_evidence") else None,
    )
    columns = ", ".join(values)
    assignments = ", ".join(f"{c} = :{c}" for c in values if c != "id")
    conn.execute(
        f"INSERT INTO jobs ({columns}) VALUES ({', '.join(':' + c for c in values)}) "
        f"ON CONFLICT(id) DO UPDATE SET {assignments}",
        values,
    )


def import_all(conn: sqlite3.Connection, companies_dir: Path, career_pages: dict,
               review: dict, addresses: dict, techmap_index: dict | None = None) -> dict:
    """career_pages is {display_name: url|None}, review {display_name:
    decision}, addresses {normalized_key: city}, techmap_index the
    {normalized_key: rows} map that supplies industry, size and a location
    hint."""
    counts = {"companies": 0, "jobs": 0, "scores": 0}
    techmap_index = techmap_index or {}
    review_by_key = {connections.normalize_company(name): decision for name, decision in review.items()}

    def _company_fields(name: str) -> dict:
        key = connections.normalize_company(name)
        rows = techmap_index.get(key, [])
        return {
            "review_decision": review_by_key.get(key),
            "address_city": addresses.get(key),
            "industry": rows[0]["industry"] if rows else None,
            "size": rows[0]["size"] if rows else None,
        }

    # The career-pages map is the scrape list: every name in it is a company,
    # whether or not it has ever been scraped into a file.
    for name, url in career_pages.items():
        companies_store.upsert_company(conn, _snake_case(name), name, career_url=url, **_company_fields(name))
        counts["companies"] += 1

    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        name = record.get("name") or path.stem
        company_id = _snake_case(name)
        if companies_store.get_company(conn, company_id) is None:
            counts["companies"] += 1
        existing_url = career_pages.get(name)
        companies_store.upsert_company(
            conn, company_id, name,
            career_url=existing_url if name in career_pages else record.get("career_url"),
            last_checked=record.get("last_checked"),
            **_company_fields(name),
        )
        for job in record.get("jobs", []):
            # city/is_remote never existed in the files: the aggregate stage
            # derived them at the end of every run. They are columns now.
            _upsert_job(conn, company_id, {**job, **location_fields_for(job, name, techmap_index, addresses)})
            counts["jobs"] += 1
            rows = _score_rows(job)
            if rows:
                scores_store.write_scores(conn, job["id"], rows)
                counts["scores"] += len(rows)
    return counts


def verify(conn: sqlite3.Connection, companies_dir: Path, addresses: dict | None = None,
           techmap_index: dict | None = None, sample: int = 50, seed: int = 20260930) -> list[str]:
    """Counts first, then a fixed-seed sample compared field by field. The
    seed is fixed so a failure is reproducible rather than a dice roll.

    Copied fields are compared against the file. Derived ones (location,
    city, remote) are recomputed from the file and compared to what was
    stored, which catches an import that derived them differently."""
    problems: list[str] = []
    file_jobs: dict[str, dict] = {}
    company_of: dict[str, str] = {}
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for job in record.get("jobs", []):
            file_jobs[job["id"]] = job
            company_of[job["id"]] = record.get("name") or path.stem

    db_count = conn.execute("SELECT count(*) FROM jobs").fetchone()[0]
    if db_count != len(file_jobs):
        problems.append(f"job count: {len(file_jobs)} in files, {db_count} in the database")

    if not file_jobs:
        return problems
    chosen = random.Random(seed).sample(sorted(file_jobs), min(sample, len(file_jobs)))
    for job_id in chosen:
        row = jobs_store.get_job(conn, job_id)
        if row is None:
            problems.append(f"{job_id}: missing from the database")
            continue
        source = file_jobs[job_id]
        expected_derived = location_fields_for(source, company_of[job_id], techmap_index or {}, addresses or {})
        for field in _DERIVED_FIELDS:
            want = int(bool(expected_derived[field])) if field == "is_remote" else expected_derived[field]
            if (row[field] or None) != (want or None):
                problems.append(f"{job_id}: {field} is {row[field]!r}, recomputing gives {want!r}")
        for field in _COPIED_FIELDS:
            expected = source.get(field)
            actual = row[field]
            if field in _BOOLEAN_FIELDS:
                expected = int(bool(expected))
            if field == "description":
                expected = expected or ""
            if field == "status":
                expected = expected or "seen"
            if (expected if expected not in ("", None) else None) != (actual if actual not in ("", None) else None):
                problems.append(f"{job_id}: {field} is {actual!r}, the file says {expected!r}")
    return problems


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the company JSON files into the SQLite store.")
    parser.add_argument("--db", default=str(config.DB_PATH))
    args = parser.parse_args()

    conn = db.connect(args.db)
    db.migrate(conn)

    career_pages = _load_json(config.COMPANIES_CAREER_PAGES_PATH, {})
    review_raw = _load_json(config.COMPANY_REVIEW_PATH, {})
    review = {
        name: (entry.get("decision") if isinstance(entry, dict) else entry)
        for name, entry in review_raw.items()
    }
    counts = import_all(conn, config.ROOT / "companies", career_pages, review,
                        load_company_address_cities(), load_techmap_index())
    print(f"imported: {counts}")

    problems = verify(conn, config.ROOT / "companies", load_company_address_cities(), load_techmap_index())
    if not problems:
        print("verification: clean")
        raise SystemExit(0)
    print(f"VERIFICATION FAILED ({len(problems)} problem(s)):")
    for problem in problems[:20]:
        print(f"  {problem}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
