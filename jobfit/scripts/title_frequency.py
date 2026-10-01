"""Normalized job-title frequency table, over the live store.

The taxonomy in jobfit/ats_scorer/data/role_families.json is authored
from this command's printed output, not from memory - every family and
split in the taxonomy is backed by a number this script prints. Same
normalization used for the one-off exploration that produced
docs/superpowers/specs/2026-10-01-scoring-redesign-design.md section 0,
now a real, rerunnable command rather than a scratch script.

Usage:
    uv run python -m jobfit.scripts.title_frequency                  # top 100
    uv run python -m jobfit.scripts.title_frequency --top 500
    uv run python -m jobfit.scripts.title_frequency --grep platform  # filter
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobfit import config  # noqa: E402
from jobfit.store import db  # noqa: E402

_SENIORITY_RE = re.compile(
    r"\b(senior|sr\.?|junior|jr\.?|lead|principal|staff|chief|head|director|vp|vice president|"
    r"mid[\s-]?level|mid|entry[\s-]?level|intern(ship)?|associate|experienced|expert|"
    r"i{1,3}|iv|v|[0-9]+)\b"
)
_ABBREV = {
    r"\bsw\b": "software", r"\bswe\b": "software engineer", r"\beng(ineer(ing)?)?\b": "engineer",
    r"\bdev\b": "developer", r"\bdevs\b": "developers",
    r"\bfull[\s-]?stack\b": "fullstack", r"\bfront[\s-]?end\b": "frontend",
    r"\bback[\s-]?end\b": "backend", r"\bops\b": "operations", r"\bqa\b": "qa",
    r"\bml\b": "ml", r"\bai\b": "ai", r"\bsre\b": "sre", r"\bdevops\b": "devops",
    r"\bpm\b": "product manager", r"\bux\b": "ux", r"\bui\b": "ui",
}


def normalize(raw: str) -> str:
    """Lowercase, strip Hebrew remnants and seniority words, expand common
    abbreviations. Same transform used to produce the spec's own numbers."""
    text = raw.lower()
    text = re.sub(r"[֐-׿]+", " ", text)
    text = re.sub(r"[^a-z0-9\s/&+-]", " ", text)
    for pattern, replacement in _ABBREV.items():
        text = re.sub(pattern, replacement, text)
    text = re.sub(_SENIORITY_RE, " ", text)
    text = re.sub(r"\b\d+\b", " ", text)
    text = re.sub(r"[-/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def frequency_table(conn, status_filter: str = "open") -> Counter:
    where = "status != 'closed'" if status_filter == "open" else "1=1"
    rows = conn.execute(f"SELECT title FROM jobs WHERE {where} AND title IS NOT NULL").fetchall()
    counts = Counter(n for n in (normalize(r["title"]) for r in rows) if n)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=100, help="how many rows to print")
    parser.add_argument("--grep", default=None, help="only normalized titles containing this substring")
    parser.add_argument("--all-statuses", action="store_true", help="include closed jobs")
    args = parser.parse_args()

    conn = db.connect(config.DB_PATH)
    counts = frequency_table(conn, "all" if args.all_statuses else "open")
    print(f"active jobs with a title, normalized -> {sum(counts.values()):,} total, {len(counts):,} unique")
    items = counts.most_common()
    if args.grep:
        items = [(t, n) for t, n in items if args.grep.lower() in t]
    for title, n in items[: args.top]:
        print(f"  {n:6,}  {title}")


if __name__ == "__main__":
    main()
