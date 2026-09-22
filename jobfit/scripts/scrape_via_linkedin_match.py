"""Run inside linkedin-match's own venv (invoked with cwd=linkedin-match root).

Scrapes real career-page jobs for a given list of company names, using that
project's own discovery + ATS-detection scraper, WITHOUT touching its own
data/jobs_cache.json (we only read the returned dict, never call save_cache
on their path).

Usage: uv run python <this file> <input_names.json> <output_entries.json>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from backend.cli.scraper import scrape_companies  # noqa: E402
from backend.core.models import Company  # noqa: E402


def main() -> None:
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    names = json.loads(input_path.read_text(encoding="utf-8"))
    companies = [Company(name=n) for n in names]
    updated = scrape_companies(companies, {}, workers=8, force=False)

    result = {name: updated[name] for name in names if name in updated}
    output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(f"scraped {len(result)}/{len(names)} companies live", file=sys.stderr)


if __name__ == "__main__":
    main()
