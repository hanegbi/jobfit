"""Rank stored jobs by how much they look like scraped junk, and record
"not a job" decisions as URL reject patterns the scraper enforces.

Usage:
  uv run python -m jobfit.scripts.audit_scrape [--limit 50] [--company NAME]
  uv run python -m jobfit.scripts.audit_scrape --reject '^https://copyleaks\\.com/[a-z0-9-]+$'
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import config  # noqa: E402
from jobfit.ats_scorer.taxonomy import load_role_families  # noqa: E402
from jobfit.atomic_io import write_json_atomic  # noqa: E402
from jobfit.scrape.candidates import href_shape  # noqa: E402
from jobfit.scrape.ids import plan_id_for  # noqa: E402
from jobfit.scrape.plan_store import FilePlanStore  # noqa: E402

SHARED_PREFIX_CHARS = 300
JACCARD_DUPLICATE = 0.8


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))


def _duplicates_sibling(description: str, siblings: list[str]) -> bool:
    if len(description or "") < SHARED_PREFIX_CHARS:
        return False
    mine = _words(description)
    for other in siblings:
        if other == description or not other:
            continue
        if description[:SHARED_PREFIX_CHARS] == other[:SHARED_PREFIX_CHARS]:
            return True
        theirs = _words(other)
        if mine and theirs and len(mine & theirs) / len(mine | theirs) >= JACCARD_DUPLICATE:
            return True
    return False


def suspicion_reasons(job: dict, sibling_descriptions: list[str], plan) -> list[str]:
    reasons = []
    evidence = job.get("job_evidence") or {}
    has_signal = any([evidence.get("jsonld_jobposting"), evidence.get("apply_cta"), (evidence.get("requirement_sections") or 0) >= 1, evidence.get("role_family_from_title")])
    if not has_signal:
        reasons.append("no evidence")
    if load_role_families().classify(job.get("title") or "") is None:
        reasons.append("title is not a role")
    expected = getattr(getattr(plan, "strategy", None), "url_shape", None) if plan is not None else None
    if expected and job.get("url") and href_shape(job["url"]) != expected:
        reasons.append("url off plan shape")
    if _duplicates_sibling(job.get("description") or "", sibling_descriptions):
        reasons.append("description duplicates sibling (site chrome)")
    return reasons


def audit(companies_dir: Path, store, limit: int, company: str | None = None) -> list[dict]:
    rows = []
    for path in sorted(companies_dir.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if company and record.get("name") != company:
            continue
        plan = store.get(plan_id_for(record.get("name", path.stem)))
        jobs = [j for j in record.get("jobs", []) if j.get("status") != "closed"]
        descriptions = [j.get("description") or "" for j in jobs]
        for job in jobs:
            reasons = suspicion_reasons(job, descriptions, plan)
            if reasons:
                rows.append({"company": record.get("name"), "title": job.get("title"), "url": job.get("url"), "reasons": reasons})
    rows.sort(key=lambda r: (-len(r["reasons"]), r["company"] or "", r["title"] or ""))
    return rows[:limit] if limit else rows


def add_reject_pattern(pattern: str, path: Path | None = None) -> list[str]:
    re.compile(pattern)  # fail fast on a bad regex
    path = path or config.LINK_REJECTS_PATH
    patterns: list[str] = []
    if path.exists():
        patterns = list(json.loads(path.read_text(encoding="utf-8")).get("patterns", []))
    if pattern not in patterns:
        patterns.append(pattern)
        write_json_atomic(path, {"patterns": patterns})
    return patterns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--company", type=str, default=None)
    parser.add_argument("--reject", type=str, default=None, help="append a URL regex to data/link_rejects.json and exit")
    args = parser.parse_args()
    if args.reject:
        patterns = add_reject_pattern(args.reject)
        print(f"reject patterns now: {len(patterns)}")
        return
    rows = audit(config.ROOT / "companies", FilePlanStore(config.SCRAPE_PLANS_DIR), args.limit, args.company)
    for row in rows:
        print(f"{row['company']!s:40} {row['title']!s:50} {', '.join(row['reasons'])}\n    {row['url']}")
    print(f"\n{len(rows)} suspicious job(s) shown")


if __name__ == "__main__":
    main()
