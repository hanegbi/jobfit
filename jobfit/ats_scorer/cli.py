"""Command-line entry point: score --cv path --jd path [--title "..."],
compute-idf (rebuild jobfit/ats_scorer/data/skill_idf.json from the live
job corpus)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jobfit.ats_scorer import cv_extractor
from jobfit.ats_scorer.idf import compute_idf
from jobfit.ats_scorer.pipeline import score_cv_against_job
from jobfit.ats_scorer.taxonomy import SKILL_IDF_PATH


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

    idf_parser = subparsers.add_parser(
        "compute-idf", help="Rebuild skill_idf.json from the live job corpus (writes the file; no args)."
    )
    idf_parser.add_argument("--min-description-len", type=int, default=50,
                             help="Skip jobs with a shorter description (default: 50, matches scoring's own full/title_only cutoff).")

    args = parser.parse_args(argv)

    if args.command == "score":
        cv_text = cv_extractor.extract_text(args.cv)
        jd_text = _read_jd(args.jd)
        result = score_cv_against_job(cv_text, jd_text, job_title=args.title)
        print(json.dumps(result.model_dump(), indent=2))
        return 0
    if args.command == "compute-idf":
        # Reaches into jobfit.store for the corpus - the one place in this
        # package allowed to, since it's the CLI's job to gather real data
        # for a committed artifact, not the engine's. idf.compute_idf()
        # itself only ever sees strings.
        from jobfit import config
        from jobfit.store import db, jobs as store_jobs

        conn = db.connect(config.DB_PATH)
        texts = store_jobs.corpus_texts(conn, min_description_len=args.min_description_len)
        weights = compute_idf(texts)
        payload = {
            "corpus_size": len(texts),
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "weights": weights,
        }
        SKILL_IDF_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {SKILL_IDF_PATH} - corpus_size={payload['corpus_size']}, {len(weights)} skills")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
