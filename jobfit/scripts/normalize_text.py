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
from pathlib import Path

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


def close_nav_junk(conn) -> dict:
    """Close open "jobs" whose title is site furniture - "About Us", "Terms &
    Conditions", "Careers". They were never postings; the denylist that
    should have rejected them had gaps (it matched 'about' but not 'about
    us', 'terms of service' but not 'terms and conditions').

    Closed rather than deleted, like every other job: the row is still
    evidence of what that career page served, and deleting it would just let
    the next scrape create it again.
    """
    from jobfit.scrape.filters import NAV_DENYLIST, looks_like_site_furniture

    rows = conn.execute("SELECT id, title FROM jobs WHERE status != 'closed'").fetchall()
    junk = [row["id"] for row in rows
            if NAV_DENYLIST.match((row["title"] or "").strip()) or looks_like_site_furniture(row["title"])]
    if junk:
        with conn:
            conn.executemany(
                "UPDATE jobs SET status = 'closed', closed_at = datetime('now'), "
                "closed_reason = 'not a job: site navigation' WHERE id = ?",
                [(job_id,) for job_id in junk])
    return {"open_jobs": len(rows), "closed_as_navigation": len(junk)}


def resplit_titles(conn) -> dict:
    """Re-peel card metadata off stored titles.

    split_card_text only trims what it recognises, so running it again is
    safe: it can shorten a title, never rename one. Needed whenever the strip
    learns a new kind of metadata - here, foreign places, which left "Senior
    DevOps Engineer Dallas HQ" in the store."""
    from jobfit.scrape import titles as titles_mod

    rows = conn.execute("SELECT id, title FROM jobs WHERE title IS NOT NULL AND title != ''").fetchall()
    changed = 0
    with conn:
        for row in rows:
            trimmed = titles_mod.split_card_text(row["title"]).title
            if not trimmed or trimmed == row["title"]:
                continue
            conn.execute("UPDATE jobs SET title = ? WHERE id = ?", (trimmed, row["id"]))
            changed += 1
    return {"titles_examined": len(rows), "titles_trimmed": changed}


def fix_remote_flags(conn) -> dict:
    """Re-derive is_remote for jobs that only looked remote because their
    description mentions distributed systems.

    "distributed" was in REMOTE_TERMS for "distributed team"; in an
    engineering description it means distributed computing, and it flagged
    172 office jobs - some of which say "this is a hybrid position" - as
    remote, so their card showed "Remote" instead of their real city.
    """
    from jobfit import scoring

    rows = conn.execute(
        "SELECT id, title, location, description FROM jobs WHERE is_remote = 1").fetchall()
    cleared = 0
    with conn:
        for row in rows:
            still_remote = (
                scoring.is_remote_location(row["location"])
                or scoring.is_remote_location(row["title"])
                or scoring.is_remote_location(row["description"] or "")
            )
            if still_remote:
                continue
            conn.execute("UPDATE jobs SET is_remote = 0 WHERE id = ?", (row["id"],))
            cleared += 1
    return {"was_remote": len(rows), "no_longer_remote": cleared}


def fix_locations(conn) -> dict:
    """Re-derive location/city for the rows the old rules got wrong.

    Two defects, both fixed in pipeline._infer_location_fields: the literal
    string "NaN" stood for "this job never said where", and a foreign place in
    the TITLE (rather than the location field) fell through to the company's
    Israeli address, so US and UK roles were served as Tel Aviv jobs.
    """
    from jobfit import pipeline
    from jobfit.scrape import titles as titles_mod

    cleared = relocated = 0
    with conn:
        cursor = conn.execute("UPDATE jobs SET location = NULL WHERE location IN ('NaN', 'nan')")
        cleared = cursor.rowcount

        rows = conn.execute(
            "SELECT id, title, location, city FROM jobs WHERE city IS NOT NULL").fetchall()
        for row in rows:
            if not titles_mod.names_foreign_place(row["title"] or ""):
                continue
            where = titles_mod.trailing_place(row["title"]) or "Outside Israel"
            conn.execute("UPDATE jobs SET location = ?, city = NULL WHERE id = ?", (where, row["id"]))
            relocated += 1
    return {"nan_locations_cleared": cleared, "foreign_jobs_un_israeled": relocated}


def apply_title_translations(conn, path) -> dict:
    """Apply a {hebrew title: english title} file to the store and the cache.

    A repair hatch for titles the translation service will not do - it rate
    limits, and 411 of these are Hebrew defence-industry job titles it
    handles badly even when it answers. The translations go into the same
    cache keyed the same way, so the scrape path finds them and never calls
    out for those strings again.
    """
    mapping = json.loads(path.read_text(encoding="utf-8"))
    hebrew = {
        row["title"]
        for row in conn.execute("SELECT DISTINCT title FROM jobs WHERE title GLOB '*[֐-׿]*'")
    }
    missing = sorted(hebrew - set(mapping))
    unused = sorted(set(mapping) - hebrew)

    cache = translation._load_cache()
    updated = 0
    with conn:
        for source, english in mapping.items():
            if not english or translation.contains_hebrew(english):
                continue
            cache[translation._cache_key(source)] = english
            cursor = conn.execute(
                "UPDATE jobs SET title = ?, title_original = COALESCE(title_original, ?), "
                "source_language = 'he' WHERE title = ?",
                (english, source, source))
            updated += cursor.rowcount
    write_json_atomic(translation.CACHE_PATH, cache)
    translation._cache_memo = None
    return {"in_file": len(mapping), "jobs_updated": updated,
            "still_hebrew_not_in_file": len(missing), "unused_entries": len(unused),
            "missing_sample": missing[:10]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--departments", action="store_true", help="fold departments into the canonical set")
    parser.add_argument("--translate", action="store_true", help="retranslate Hebrew titles")
    parser.add_argument("--companies", action="store_true", help="drop the Hebrew half of company names")
    parser.add_argument("--nav-junk", action="store_true", help="close open jobs that are site navigation")
    parser.add_argument("--locations", action="store_true", help="re-derive locations the old rules got wrong")
    parser.add_argument("--titles", action="store_true", help="re-peel card metadata off stored titles")
    parser.add_argument("--remote", action="store_true", help="re-derive the remote flag")
    parser.add_argument("--apply-titles", type=Path, help="a {hebrew: english} JSON file of title translations")
    parser.add_argument("--limit", type=int, default=0, help="translate at most N titles")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    conn = db.shared()
    if args.departments:
        print(fold_departments(conn))
    if args.companies:
        print(clean_company_names(conn))
    if args.nav_junk:
        print(close_nav_junk(conn))
    if args.titles:
        print(resplit_titles(conn))
    if args.locations:
        print(fix_locations(conn))
    if args.remote:
        print(fix_remote_flags(conn))
    if args.apply_titles:
        print(apply_title_translations(conn, args.apply_titles))
    if args.translate:
        dropped = drop_poisoned_translations()
        print(f"dropped {dropped} poisoned cache entries")
        print(retranslate(conn, args.limit))
    if not (args.departments or args.translate or args.companies or args.nav_junk
            or args.apply_titles or args.locations or args.titles or args.remote):
        parser.error("pick --departments, --companies, --nav-junk, --translate, or a combination")


if __name__ == "__main__":
    main()
