"""The scraper must stay model-free (CLAUDE.md, test_scrape_no_llm_at_runtime.py): jobfit never imports the agent."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_jobfit_never_imports_the_agent():
    offenders = [
        str(path.relative_to(REPO))
        for path in (REPO / "jobfit").rglob("*.py")
        if "jobfit_agent" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
