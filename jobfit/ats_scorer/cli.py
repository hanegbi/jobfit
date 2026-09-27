"""Command-line entry point: score --cv path --jd path [--title "..."]."""

import argparse
import json
import sys
from pathlib import Path

from jobfit.ats_scorer import cv_extractor
from jobfit.ats_scorer.pipeline import score_cv_against_job


def _read_jd(path: str) -> str:
    p = Path(path)
    if p.suffix.lower() in (".pdf", ".docx"):
        return cv_extractor.extract_text(p)
    return p.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the scoring pipeline, and print the JSON result.

    Args:
        argv: Argument list to parse; defaults to sys.argv[1:].

    Returns:
        Process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(prog="ats-scorer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_parser = subparsers.add_parser("score", help="Score a CV against a job description.")
    score_parser.add_argument("--cv", required=True, help="Path to the CV file (.txt, .pdf, or .docx).")
    score_parser.add_argument("--jd", required=True, help="Path to the job description file (.txt, .pdf, or .docx).")
    score_parser.add_argument("--title", default=None, help="Job title, if not already in the JD text.")

    args = parser.parse_args(argv)

    if args.command == "score":
        cv_text = cv_extractor.extract_text(args.cv)
        jd_text = _read_jd(args.jd)
        result = score_cv_against_job(cv_text, jd_text, job_title=args.title)
        print(json.dumps(result.model_dump(), indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
