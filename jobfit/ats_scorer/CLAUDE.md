# jobfit/ats_scorer

The deterministic scoring engine: CV text + job description → sub-scores, hard gates, a 0-100 score and a
band. No model calls, no network, no randomness. `jobfit/scoring.py` is the app-facing wrapper; this package
is the engine underneath it.

`pipeline.score_cv_against_job(cv, jd, job_title=..., now=...)` is the entry point. `cli.py` scores one pair
from the shell: `uv run python -m jobfit.ats_scorer.cli score --cv <path> --jd <path> [--title "..."]`.

## Where the behavior lives

- `config.py` — every weight, gate and band threshold in one pydantic object, so tuning never means hunting
  constants. Change values here, not in the modules that read them.
- `cv_extractor.py` / `jd_extractor.py` — text → structured skills, years, level.
- `matcher.py`, `scorer.py` — coverage and family matching, then the weighted sum and gates.
- `taxonomy.py` + `data/skills_taxonomy.json`, `data/role_families.json` — the curated vocabulary. Both data
  files are inputs to the engine fingerprint; edit them like code, not like cache.

## Conventions

- **Any change here silently invalidates every cached score** — by design.
  `scoring.SCORING_ENGINE_FINGERPRINT` hashes this package's sources, its data files and the serialized
  config, so the next run rescores without anyone passing `--force-rescore`. If you add a file that can
  change a score, make sure `_compute_scoring_engine_fingerprint` covers it.
- Four test files guard four different properties, and a tuning change usually has to satisfy all of them:
  `test_ats_scorer_golden.py` (real CV/JD pairs land in the expected *band*, never an exact score),
  `test_ats_scorer_gates.py` (each hard gate caps the score), `test_ats_scorer_monotonicity.py` (more
  evidence never scores lower), `test_ats_scorer_determinism.py` (same input, same output across processes —
  never use Python's randomized `hash()` here).
- Scores are bands, not precision. Don't assert an exact number in a new test; assert the band or a direction.
- This package must stay independent of the rest of `jobfit` — it takes strings, not job dicts or config
  paths. Keep the dependency arrow pointing one way.
