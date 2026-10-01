"""Command-line entry point: score --cv path --jd path [--title "..."],
compute-idf (rebuild jobfit/ats_scorer/data/skill_idf.json from the live
job corpus)."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jobfit.ats_scorer import cv_extractor, jd_extractor, matcher, scorer as scorer_engine
from jobfit.ats_scorer.adjacency import compute_adjacency_matrix
from jobfit.ats_scorer.config import FIT_WEIGHTS_RESULT_PATH
from jobfit.ats_scorer.fit_weights import LabeledExample, run_search
from jobfit.ats_scorer.idf import compute_idf
from jobfit.ats_scorer.pipeline import score_cv_against_job
from jobfit.ats_scorer.profile import apply_family_overrides, build_profile
from jobfit.ats_scorer.taxonomy import AFFINITY_FLOOR, FAMILY_ADJACENCY_PATH, SKILL_IDF_PATH, load_role_families, load_skill_idf


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

    profile_parser = subparsers.add_parser(
        "profile", help="Print a CV's full family-affinity vector and signature skill list."
    )
    profile_parser.add_argument("--cv", required=True, help="Path to the CV file (.txt, .pdf, or .docx).")
    profile_parser.add_argument("--overrides", default=None,
                                 help="Path to a {family: \"boost\"|\"block\"} JSON file, applied as the last step.")

    adjacency_parser = subparsers.add_parser(
        "compute-adjacency", help="Rebuild family_adjacency.json from the live job corpus (writes the file; no args)."
    )
    adjacency_parser.add_argument("--min-description-len", type=int, default=50,
                                   help="Skip jobs with a shorter description (default: 50).")

    fit_weights_parser = subparsers.add_parser(
        "fit-weights", help="Search WeightConfig/FamilyFitConfig against a labeled set (writes fit_weights.json)."
    )
    fit_weights_parser.add_argument(
        "--labels", required=True,
        help='Path to a JSON array of {"profile_id", "job_id", "label": "fit"|"maybe"|"not_fit", '
             '"min_score"?, "max_score"?}. profile_id resolves through the CV registry (jobfit.cv); '
             "job_id resolves through the store.",
    )
    fit_weights_parser.add_argument("--trials", type=int, default=500, help="Random-search trial count (default: 500).")
    fit_weights_parser.add_argument("--seed", type=int, default=0, help="RNG seed, for a reproducible search (default: 0).")

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
    if args.command == "profile":
        cv_text = cv_extractor.extract_text(args.cv)
        profile = build_profile(cv_text)
        if args.overrides:
            overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
            profile = apply_family_overrides(profile, overrides)
        print(json.dumps({
            "roles": [{"title": r.title, "company": r.company, "start": r.start, "end": r.end, "family": r.family}
                      for r in profile.roles],
            "family_affinity": {k: v for k, v in sorted(profile.family_affinity.items(), key=lambda kv: -kv[1]) if v},
            "signature_skills": profile.signature_skills,
            "seniority": profile.seniority.value,
        }, indent=2))
        return 0
    if args.command == "compute-adjacency":
        # Same split as compute-idf: the store read lives here, the pure
        # math lives in adjacency.py.
        from jobfit import config
        from jobfit.store import db, jobs as store_jobs

        conn = db.connect(config.DB_PATH)
        texts_by_family = store_jobs.corpus_texts_by_family(conn, min_description_len=args.min_description_len)
        idf = load_skill_idf()
        families = sorted(load_role_families().families)
        matrix = compute_adjacency_matrix(texts_by_family, idf, families)

        # A hand-edited "overrides" block survives a recompute - see this
        # command's docstring in the design spec (section 1, "Adjacency"):
        # a data-file edit like any other taxonomy change, never silently
        # re-decided per run. {"family_a|family_b": {"value": v, "reason": r}}.
        existing_overrides = {}
        if FAMILY_ADJACENCY_PATH.exists():
            existing_overrides = json.loads(FAMILY_ADJACENCY_PATH.read_text(encoding="utf-8")).get("overrides", {})
        for key, entry in existing_overrides.items():
            a, b = key.split("|")
            matrix.setdefault(a, {})[b] = entry["value"]
            matrix.setdefault(b, {})[a] = entry["value"]

        payload = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "floor": AFFINITY_FLOOR,
            "families_in_corpus": sorted(texts_by_family),
            "overrides": existing_overrides,
            "matrix": matrix,
        }
        FAMILY_ADJACENCY_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {FAMILY_ADJACENCY_PATH} - {len(families)} families, "
              f"{len(texts_by_family)} with corpus presence, {len(existing_overrides)} override(s) preserved")
        return 0
    if args.command == "fit-weights":
        from datetime import date

        from jobfit import config as app_config
        from jobfit import cv as cv_module
        from jobfit.store import db, jobs as store_jobs

        raw_labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        registry = cv_module.load_registry()
        conn = db.connect(app_config.DB_PATH)
        now = date.today()

        # Each (profile, job) pair is matched once - match_requirement()
        # doesn't depend on anything this search varies, only on
        # stale_skill_years, which isn't a search parameter - so every
        # trial reuses the same MatchResult and just re-weights it.
        examples: list[LabeledExample] = []
        matched_pairs: dict[tuple[str, str], tuple] = {}
        for entry in raw_labels:
            key = (entry["profile_id"], entry["job_id"])
            if key not in matched_pairs:
                cv_entry = registry.get(entry["profile_id"])
                if cv_entry is None:
                    raise SystemExit(f"unknown profile_id in labels file: {entry['profile_id']!r}")
                cv_text = cv_extractor.extract_text(app_config.CV_PROFILES_DIR / cv_entry["filename"])
                profile = build_profile(cv_text, reference_date=now)
                overrides = cv_module.load_family_overrides(entry["profile_id"])
                if overrides:
                    profile = apply_family_overrides(profile, overrides)

                job_row = store_jobs.get_job(conn, entry["job_id"])
                if job_row is None:
                    raise SystemExit(f"unknown job_id in labels file: {entry['job_id']!r}")
                job = jd_extractor.extract_job_requirements(
                    job_row["description"] or "", title=job_row["title"], role_family=job_row["family"],
                )
                match_result = matcher.match(profile, job, now=now)
                matched_pairs[key] = (profile, job, match_result)
            examples.append(LabeledExample(
                profile_id=entry["profile_id"], job_id=entry["job_id"], label=entry["label"],
                min_score=entry.get("min_score"), max_score=entry.get("max_score"),
            ))

        def score_fn(example, trial_config):
            profile, job, match_result = matched_pairs[(example.profile_id, example.job_id)]
            return scorer_engine.score(profile, job, match_result, config=trial_config).score

        result = run_search(examples, score_fn, trials=args.trials, seed=args.seed)
        if result is None:
            print(f"no configuration in {args.trials} trials satisfied every min_score/max_score constraint - "
                  "the formula's shape may be wrong, not just its weights. Nothing written.")
            return 1

        payload = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "labeled_examples": len(examples), "trials_evaluated": result.trials_evaluated,
            "trials_rejected": result.trials_rejected,
            "precision_at_20": round(result.precision_at_20, 4), "precision_at_50": round(result.precision_at_50, 4),
            "weights": result.config.weights.model_dump(),
            "family_fit": result.config.family_fit.model_dump(),
        }
        FIT_WEIGHTS_RESULT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
