"""Make the stored text English and consistent: fold every department into
`departments.CANONICAL`, and retranslate the Hebrew titles and descriptions
whose translation failed and was cached as the Hebrew original.

Both are one-off repairs of data written before the rules existed, but both
are safe to re-run: the department fold is idempotent, and a title that is
already English is skipped without a network call.

    uv run python -m jobfit.scripts.normalize_text --departments
    uv run python -m jobfit.scripts.normalize_text --translate [--limit N]
"""

from __future__ import annotations

import argparse
import json
import logging

from jobfit import config, translation
from jobfit.atomic_io import write_json_atomic
from jobfit.departments import department_for
from jobfit.store import db

logger = logging.getLogger("jobfit.normalize")


def raw_departments() -> dict[str, str]:
    """{job id: the department string the ATS actually returned}, read from
    the pre-migration company files.

    Folding rewrites the column in place, so the raw value is gone from the
    database the first time this runs. Re-deriving a different taxonomy needs
    the original, and these files are the only place it still exists - which
    is the reason they are kept (see CLAUDE.md, "Do not delete the legacy
    company JSON files"). Job ids are base64url of the URL and did not change
    in the migration, so they still match.
    """
    raw: dict[str, str] = {}
    for path in sorted(config.ROOT.glob("companies/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for job in data.get("jobs") or []:
            if job.get("id") and job.get("department"):
                raw[job["id"]] = job["department"]
    return raw


def fold_departments(conn) -> dict:
    """Set every job's department from the raw ATS string where one survives,
    and from the job's own title where it does not."""
    raw = raw_departments()
    changed = 0
    rows = conn.execute("SELECT id, title, department FROM jobs").fetchall()
    with conn:
        for row in rows:
            # Prefer the raw ATS value; fall back to whatever is stored, which
            # for an already-folded row is a canonical name (idempotent).
            source = raw.get(row["id"], row["department"])
            wanted = department_for(row["title"], source)
            if wanted == row["department"]:
                continue
            conn.execute("UPDATE jobs SET department = ? WHERE id = ?", (wanted, row["id"]))
            changed += 1
    counts = conn.execute(
        "SELECT count(DISTINCT department) AS kinds, "
        "sum(CASE WHEN department IS NOT NULL THEN 1 ELSE 0 END) AS placed FROM jobs"
    ).fetchone()
    return {"raw_values_recovered": len(raw), "rows_changed": changed,
            "jobs_with_a_department": counts["placed"], "departments": counts["kinds"]}


def drop_poisoned_translations() -> int:
    """Forget cached "translations" that are still Hebrew, so the text they
    cover becomes eligible for a real translation again."""
    cache = translation._load_cache()
    poisoned = translation.poisoned_cache_keys(cache)
    if not poisoned:
        return 0
    for key in poisoned:
        cache.pop(key, None)
    write_json_atomic(translation.CACHE_PATH, cache)
    translation._cache_memo = None
    return len(poisoned)


def retranslate(conn, limit: int = 0) -> dict:
    """Translate the stored jobs whose title is still Hebrew, in place.

    Keeps the Hebrew under title_original, exactly as a fresh scrape would,
    so nothing distinguishes a job repaired here from one scraped after the
    cache fix. A title that comes back still Hebrew is left alone and will be
    retried next run rather than recorded as translated.
    """
    rows = conn.execute(
        "SELECT id, title, title_original FROM jobs "
        "WHERE title GLOB '*[֐-׿]*' ORDER BY id"
    ).fetchall()
    if limit:
        rows = rows[:limit]

    translated = failed = 0
    for row in rows:
        english = translation.translate_to_english(row["title"])
        if not english or translation.contains_hebrew(english):
            failed += 1
            continue
        with conn:
            conn.execute(
                "UPDATE jobs SET title = ?, title_original = COALESCE(title_original, ?), "
                "source_language = 'he' WHERE id = ?",
                (english, row["title"], row["id"]),
            )
        translated += 1
        if translated % 50 == 0:
            logger.info("translated %d/%d", translated, len(rows))
    return {"hebrew_titles": len(rows), "translated": translated, "failed": failed}


def clean_company_names(conn) -> dict:
    """Drop the Hebrew half of a bilingual company name, and translate the
    few that have no Latin half at all. Only display_name changes - the id is
    every job's foreign key and must not move."""
    rows = conn.execute(
        "SELECT id, display_name FROM companies WHERE display_name GLOB '*[֐-׿]*'"
    ).fetchall()
    renamed = 0
    for row in rows:
        name = translation.english_name(row["display_name"])
        if translation.contains_hebrew(name):
            name = translation.translate_to_english(name)
        if not name or name == row["display_name"] or translation.contains_hebrew(name):
            continue
        with conn:
            conn.execute("UPDATE companies SET display_name = ? WHERE id = ?", (name, row["id"]))
        renamed += 1
    return {"hebrew_names": len(rows), "renamed": renamed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--departments", action="store_true", help="fold departments into the canonical set")
    parser.add_argument("--translate", action="store_true", help="retranslate Hebrew titles")
    parser.add_argument("--companies", action="store_true", help="drop the Hebrew half of company names")
    parser.add_argument("--limit", type=int, default=0, help="translate at most N titles")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    conn = db.shared()
    if args.departments:
        print(fold_departments(conn))
    if args.companies:
        print(clean_company_names(conn))
    if args.translate:
        dropped = drop_poisoned_translations()
        print(f"dropped {dropped} poisoned cache entries")
        print(retranslate(conn, args.limit))
    if not (args.departments or args.translate or args.companies):
        parser.error("pick --departments, --companies, --translate, or a combination")


if __name__ == "__main__":
    main()
