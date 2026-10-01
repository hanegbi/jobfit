# jobfit/ats_scorer

The deterministic scoring engine: CV text + job description → `score = family_fit × job_fit`, hard gates, a
0-100 score and a band. No model calls, no network, no randomness. `jobfit/scoring.py` is the app-facing
wrapper; this package is the engine underneath it. Design:
@docs/superpowers/specs/2026-10-01-scoring-redesign-design.md

`pipeline.score_cv_against_job(cv, jd, job_title=..., now=...)` is the entry point. `cli.py` has five
subcommands: `score --cv <path> --jd <path> [--title "..."]` (one pair), `profile --cv <path> [--overrides
<path>]` (a CV's family-affinity vector and signature skills), `compute-idf`, `compute-adjacency`, and
`fit-weights --labels <path>` (each rebuilds one of the `data/*.json` files below from the live job corpus).

## Where the behavior lives

- `config.py` — every weight, gate and band threshold in one pydantic object, so tuning never means hunting
  constants. Change values here, not in the modules that read them. `WeightConfig`/`FamilyFitConfig`'s
  values are seeds — `fit-weights` overwrites `data/fit_weights.json`, which `DEFAULT_CONFIG` loads on top
  of the seeds when present.
- `cv_extractor.py` / `jd_extractor.py` — text → structured skills, years, level, role family.
  `job_classifier.classify_job(title, description)` is the one classifier both sides call — title rule
  first, JD/bullet fallback only when the title is generic, confidence tracked, `None` ("unknown") when
  neither matched. Never re-run at score time for a *stored* job: pass its `family` column through
  `extract_job_requirements(..., role_family=...)` instead of leaving that argument to re-derive from text.
- `profile.py` — `build_profile(cv_text)` wraps `cv_extractor.extract_candidate_profile()` and adds
  `family_affinity` (recency/duration-weighted vector over every family) and `signature_skills`
  (role-bullet-backed skills clearing `ProfileConfig.signature_idf_threshold`). `jobfit/scoring.py` must call
  this, not `cv_extractor.extract_candidate_profile()` directly — the latter leaves both fields empty, which
  makes `family_fit` 0.0 and every score 0 (a real bug caught live once `scorer.score()` started multiplying
  by it). `apply_family_overrides(profile, {family: "boost"|"block"})` is the last step, a pure function —
  file I/O for overrides lives in `jobfit/cv.py` (`load_family_overrides(profile_id)`), not here.
- `matcher.py` — per-requirement match strength. `scorer.py` — the weighted sum, `family_fit` multiply, both
  gate sets, the signature bonus and negative-evidence penalty.
- `family_fit.py` — `family_fit(candidate_vector, job_family, adjacency)`: the candidate's strongest
  weighted path to the job's family, crediting adjacent families via `adjacency.json` rather than scoring
  anything but an exact match 0.
- `idf.py` / `adjacency.py` / `fit_weights.py` — pure computation for the three `compute-*`/`fit-weights`
  commands. Each stays pure (strings/dicts in, numbers out); the store query and the file write live in
  `cli.py`, the one place in this package allowed to import `jobfit.store`/`jobfit.cv` — gathering real data
  for a committed artifact is the CLI's job, not the engine's.
- `taxonomy.py` + `data/skills_taxonomy.json`, `data/role_families.json`, `data/canonical_titles.json`,
  `data/skill_idf.json`, `data/family_adjacency.json` — the curated/computed vocabulary. Every data file is
  an input to the engine fingerprint; edit the hand-curated ones like code, not like cache. `family_adjacency
  .json`'s `"overrides"` block is also hand-curated — `compute-adjacency` preserves it across a recompute.

## Conventions

- **Any change here silently invalidates every cached score** — by design.
  `scoring.SCORING_ENGINE_FINGERPRINT` hashes this package's sources, its data files and the serialized
  config, so the next run rescores without anyone passing `--force-rescore`. If you add a file that can
  change a score, make sure `_compute_scoring_engine_fingerprint` covers it (it globs `*.py` and
  `data/*.json`, so a new file is usually covered for free).
- Test files guard distinct properties, and a tuning change usually has to satisfy all of them:
  `test_ats_scorer_golden.py` (real CV/JD pairs land in the expected *band*, never an exact score),
  `test_ats_scorer_gates.py` (each job_fit hard gate caps job_fit), `test_ats_scorer_family_fit.py` (the
  family_fit multiplier itself), `test_ats_scorer_formula.py` (signature bonus, negative evidence),
  `test_ats_scorer_monotonicity.py` (more evidence never scores lower), `test_ats_scorer_determinism.py`
  (same input, same output across processes — never use Python's randomized `hash()` here).
- Scores are bands, not precision. Don't assert an exact number in a new test; assert the band or a direction.
- This package must stay independent of the rest of `jobfit` — it takes strings, not job dicts or config
  paths. Keep the dependency arrow pointing one way. `cli.py`'s `compute-idf`/`compute-adjacency`/
  `fit-weights` subcommands are the one named exception (see above); nothing else in this package may import
  `jobfit.store` or `jobfit.cv`.
