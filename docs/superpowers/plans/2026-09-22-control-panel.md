# Jobfit Control Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a local FastAPI control panel to jobfit for managing CV profiles, the LinkedIn connections CSV, and referral job ads, plus an on-demand scrape trigger with a live log and a stats dashboard — without changing how `jobfit.html` itself is generated or opened.

**Architecture:** Two independently-triggerable pipelines sharing the existing `jobfit/companies/*.json` store: a cheap, synchronous **RECOMPUTE** stage (rescoring + reaggregation + HTML rebuild, run inline after any CV/connections/referral edit) and a network-bound **SCRAPE** stage (per-company fetch+diff, with a freshness skip and thread-pool concurrency, run only via an explicit on-demand trigger in a background thread, streaming its log to the browser over SSE).

**Tech Stack:** Python 3.13, FastAPI + Uvicorn (new), pytest (new, for the new server-side state-mutation logic only), vanilla JS for the panel UI (no framework, matching `build_html.py`'s existing approach).

**Spec:** `docs/superpowers/specs/2026-09-22-control-panel-design.md`

## Global Constraints

- Localhost only, single user, no authentication.
- No new heavy test infrastructure: pytest covers only the TTL-skip decision, the referral-merge dedup logic, and the profile-registry CRUD (per the spec's Testing section) — not the HTTP layer or the UI.
- SSE is hand-rolled via `StreamingResponse` — no `sse-starlette` dependency.
- `jobfit/companies/*.json`'s existing per-job record shape and new/seen/closed status semantics do not change.
- `jobfit.html` stays a static, no-server file, generated the same way (`build_html.build()`), just triggered from more places.
- Personal data (CV files, the connections CSV, referral uploads) must never be committed to git — gitignored from the start.
- Every test/verification command in this plan is run with `uv run python -m pytest ...` (not bare `pytest`) so `jobfit` package imports resolve without extra `sys.path` plumbing, matching how the rest of this repo invokes itself (`uv run python -m jobfit.scripts.<name>`).

---

## Phase 1 — Foundations (no server yet)

### Task 1: Gitignore the new personal-data paths

**Files:**
- Modify: `.gitignore`

**Interfaces:** none (infrastructure only).

- [x] **Step 1: Append the new ignore rules**

Add to the end of `.gitignore`:

```gitignore

# Personal data uploaded through the control panel (never committed)
jobfit/data/cvs/
jobfit/data/connections.csv
jobfit/data/referrals/
jobfit/data/profiles.json
```

- [x] **Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore control-panel personal-data paths before they exist"
```

---

### Task 2: CV profile registry (load/save/register/remove)

**Files:**
- Modify: `jobfit/cv.py`
- Modify: `jobfit/config.py` (additive only — old `CV_DEFAULT`/`CV_INFRA` stay for now, removed in Task 5)
- Test: `jobfit/server/tests/test_cv_registry.py`

**Interfaces:**
- Produces: `cv.load_registry() -> dict[str, dict]`, `cv.save_registry(registry: dict) -> None`, `cv.register_profile(name: str, source_path: Path) -> str`, `cv.remove_profile(profile_id: str) -> None`.

- [x] **Step 1: Add the new config constants**

In `jobfit/config.py`, after the `CONNECTIONS_CSV` line, add:

```python
CV_PROFILES_DIR = ROOT / "data" / "cvs"
CV_PROFILES_REGISTRY = ROOT / "data" / "profiles.json"
```

- [x] **Step 2: Write the failing tests**

Create `jobfit/server/tests/test_cv_registry.py`:

```python
import json

import docx as docx_lib
import pytest

from jobfit import config, cv


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CV_PROFILES_DIR", tmp_path / "cvs")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    yield tmp_path


def _make_docx(path, text="Python engineer with Kubernetes experience."):
    document = docx_lib.Document()
    document.add_paragraph(text)
    document.save(str(path))


def test_load_registry_returns_empty_dict_when_no_file(tmp_path):
    assert cv.load_registry() == {}


def test_save_then_load_registry_round_trips(tmp_path):
    cv.save_registry({"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}})
    assert cv.load_registry() == {"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}}


def test_register_profile_copies_file_and_adds_registry_entry(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)

    profile_id = cv.register_profile("Data Engineering", source)

    assert profile_id == "data_engineering"
    registry = cv.load_registry()
    assert registry[profile_id]["name"] == "Data Engineering"
    assert (config.CV_PROFILES_DIR / registry[profile_id]["filename"]).exists()


def test_register_profile_dedupes_id_on_name_collision(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)

    first_id = cv.register_profile("Infra", source)
    second_id = cv.register_profile("Infra", source)

    assert first_id == "infra"
    assert second_id == "infra_2"
    assert set(cv.load_registry()) == {"infra", "infra_2"}


def test_remove_profile_deletes_file_and_registry_entry(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)
    profile_id = cv.register_profile("Default", source)
    file_path = config.CV_PROFILES_DIR / cv.load_registry()[profile_id]["filename"]
    assert file_path.exists()

    cv.remove_profile(profile_id)

    assert profile_id not in cv.load_registry()
    assert not file_path.exists()


def test_remove_profile_is_a_noop_for_unknown_id():
    cv.remove_profile("does-not-exist")  # must not raise
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_cv_registry.py -v`
Expected: FAIL — `AttributeError: module 'jobfit.cv' has no attribute 'load_registry'`

- [x] **Step 4: Implement the registry functions**

In `jobfit/cv.py`, add these imports at the top (alongside the existing `import re` / `import docx`):

```python
import json
import re
from datetime import datetime, timezone
from pathlib import Path
```

Then add, after `extract_skills`:

```python
def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "profile"


def _unique_id(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}_{n}" in existing:
        n += 1
    return f"{base}_{n}"


def load_registry() -> dict[str, dict]:
    if config.CV_PROFILES_REGISTRY.exists():
        return json.loads(config.CV_PROFILES_REGISTRY.read_text(encoding="utf-8"))
    return {}


def save_registry(registry: dict[str, dict]) -> None:
    config.CV_PROFILES_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    config.CV_PROFILES_REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def register_profile(name: str, source_path: Path) -> str:
    """Copy source_path's CV into the registry under a new profile id. Returns that id."""
    source_path = Path(source_path)
    registry = load_registry()
    profile_id = _unique_id(_slugify(name), set(registry))
    config.CV_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.CV_PROFILES_DIR / f"{profile_id}{source_path.suffix}"
    dest.write_bytes(source_path.read_bytes())
    registry[profile_id] = {
        "name": name,
        "filename": dest.name,
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    save_registry(registry)
    return profile_id


def remove_profile(profile_id: str) -> None:
    registry = load_registry()
    entry = registry.pop(profile_id, None)
    if entry is None:
        return
    file_path = config.CV_PROFILES_DIR / entry["filename"]
    if file_path.exists():
        file_path.unlink()
    save_registry(registry)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_cv_registry.py -v`
Expected: PASS (6 passed)

- [x] **Step 6: Commit**

```bash
git add jobfit/cv.py jobfit/config.py jobfit/server/tests/test_cv_registry.py
git commit -m "feat: add CV profile registry (register/remove/load) to cv.py"
```

---

### Task 3: `cv.load_profiles()` becomes registry-driven

**Files:**
- Modify: `jobfit/cv.py:38-43`
- Test: `jobfit/server/tests/test_cv_registry.py` (extend)

**Interfaces:**
- Consumes: `cv.load_registry()`, `cv.build_profile(cv_path) -> dict` (existing, unchanged) from Task 2.
- Produces: `cv.load_profiles() -> dict[str, dict]` — now returns one entry per **registered** profile instead of exactly `{"default": ..., "infra": ...}`.

- [x] **Step 1: Write the failing test**

Append to `jobfit/server/tests/test_cv_registry.py`:

```python
def test_load_profiles_builds_one_entry_per_registered_cv(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source, "Kubernetes and Python and MLOps experience.")
    profile_id = cv.register_profile("Platform", source)

    profiles = cv.load_profiles()

    assert set(profiles) == {profile_id}
    assert "kubernetes" in profiles[profile_id]["must_have_keywords"]


def test_load_profiles_is_empty_when_no_profiles_registered():
    assert cv.load_profiles() == {}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_cv_registry.py::test_load_profiles_builds_one_entry_per_registered_cv -v`
Expected: FAIL — old `load_profiles()` still reads `config.CV_DEFAULT`/`config.CV_INFRA`, which don't exist under the monkeypatched tmp_path.

- [x] **Step 3: Replace `load_profiles()`**

In `jobfit/cv.py`, replace the existing `load_profiles` function (lines 38-43):

```python
def load_profiles() -> dict[str, dict]:
    """Build {must_have_keywords, text} for every registered CV profile."""
    return {
        profile_id: build_profile(config.CV_PROFILES_DIR / entry["filename"])
        for profile_id, entry in load_registry().items()
    }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_cv_registry.py -v`
Expected: PASS (8 passed)

- [x] **Step 5: Commit**

```bash
git add jobfit/cv.py jobfit/server/tests/test_cv_registry.py
git commit -m "feat: make cv.load_profiles() registry-driven instead of fixed default/infra"
```

---

### Task 4: Migrate the two existing CVs and the connections CSV

This is a one-time data migration, run by hand now (not application code) — it must run *before* Task 5 removes `config.CV_DEFAULT`/`CV_INFRA`/the old `CONNECTIONS_CSV` value.

**Files:** none changed — this only creates data files under `jobfit/data/`.

- [x] **Step 1: Register the two existing CVs under ids that match the scores already stored**

Run from the repo root (this must produce ids `default` and `infra` exactly, since ~780 files under `jobfit/companies/` already have `score_default`/`score_infra` fields keyed on those names):

```bash
uv run python -c "
from jobfit import cv, config
print(cv.register_profile('default', config.CV_DEFAULT))
print(cv.register_profile('infra', config.CV_INFRA))
"
```

Expected output: `default` then `infra`.

- [x] **Step 2: Verify the registry and copied files**

```bash
uv run python -c "
from jobfit import cv, config
import json
print(json.dumps(cv.load_registry(), indent=2))
print(list(config.CV_PROFILES_DIR.iterdir()))
"
```

Expected: a registry with `default` and `infra` entries, and two `.docx` files under `jobfit/data/cvs/`.

- [x] **Step 3: Copy the connections CSV into its new location**

```bash
mkdir -p jobfit/data
cp "/c/Users/user/Code/linkedin-match/Connections.csv" "jobfit/data/connections.csv"
```

(If that source path doesn't exist on this machine, skip this step — `jobfit/data/connections.csv` simply won't exist yet, and `connections.load_connections_index()` already returns `{}` for a missing file, so nothing breaks; you can upload connections later through the panel once Task 13 exists.)

- [x] **Step 4: Confirm none of the new data files are tracked by git**

```bash
git status --short jobfit/data/
```

Expected: no output (everything under `jobfit/data/cvs/`, `jobfit/data/connections.csv` is covered by Task 1's `.gitignore` rules). `jobfit/data/jobs_v2.json` (if it shows up) is fine — that one was already tracked before this plan and isn't part of the new ignore rules.

No commit for this task — nothing it creates is meant to be tracked.

---

### Task 5: Remove the old fixed-CV config, add remaining new constants

**Files:**
- Modify: `jobfit/config.py:10-13, 26, 51-81`

**Interfaces:**
- Consumes: nothing new.
- Produces: `config.ROLE_WEIGHTS` (renamed from `ROLE_WEIGHTS_DEFAULT`), `config.COMPANY_RECHECK_TTL_HOURS`, `config.RUN_HISTORY_PATH`, `config.REFERRAL_UPLOADS_DIR`. Removes `config.CV_DEFAULT`, `config.CV_INFRA`, `config.ROLE_WEIGHTS_INFRA`, `config.ROLE_WEIGHTS_BY_PROFILE`.

- [x] **Step 1: Update the personal-inputs block**

Replace lines 10-13:

```python
# --- Personal inputs ---
CV_DEFAULT = Path(r"C:\Users\user\Documents\Job\2026\Dan_Hanegbi_Resume.docx")
CV_INFRA = Path(r"C:\Users\user\Documents\Job\2026\Infra\Dan_Hanegbi_Resume.docx")
CONNECTIONS_CSV = Path(r"C:\Users\user\Code\linkedin-match\Connections.csv")
```

with:

```python
# --- Personal inputs (uploaded through the control panel, gitignored) ---
CONNECTIONS_CSV = ROOT / "data" / "connections.csv"  # ROOT is defined below; see note
```

Since `CONNECTIONS_CSV` now depends on `ROOT`, move this line down so it comes *after* the `ROOT = Path(__file__).parent` line instead (line 16 in the current file). The block from line 8 through line 28 should read, in order:

```python
from pathlib import Path

# --- Local cache/output ---
ROOT = Path(__file__).parent
TECHMAP_CACHE_DIR = ROOT / "cache" / "techmap"
COMPANY_JOBS_CACHE = ROOT / "cache" / "company_jobs.json"
LM_LIVE_SCRAPED_CACHE = ROOT / "cache" / "lm_live_scraped.json"
GENERIC_DESC_CACHE = ROOT / "cache" / "generic_descriptions.json"
COMPANY_CAREER_PAGES_CACHE = ROOT / "cache" / "company_career_pages.json"
REFERRAL_JOBS_PATH = Path(r"C:\Users\user\Downloads\jobs_by_company.json")
GENERIC_DESC_TTL_HOURS = 24 * 14
CONNECTIONS_CACHE = ROOT / "cache" / "connections_index.json"
JOBS_OUTPUT_JSON = ROOT / "data" / "jobs_v2.json"
OUTPUT_HTML = ROOT.parent / "jobfit.html"

# --- Personal inputs (uploaded through the control panel, gitignored) ---
CONNECTIONS_CSV = ROOT / "data" / "connections.csv"
CV_PROFILES_DIR = ROOT / "data" / "cvs"
CV_PROFILES_REGISTRY = ROOT / "data" / "profiles.json"
REFERRAL_UPLOADS_DIR = ROOT / "data" / "referrals"

COMPANY_JOBS_TTL_HOURS = 24
COMPANY_RECHECK_TTL_HOURS = 12
RUN_HISTORY_PATH = ROOT / "data" / "run_history.json"
```

(`CV_PROFILES_DIR`/`CV_PROFILES_REGISTRY` already exist from Task 2 — this step is what relocates them next to the other personal-input constants and removes the now-dead `CV_DEFAULT`/`CV_INFRA`. If Task 2 already placed them elsewhere in the file, just make sure they end up matching this block and aren't duplicated.)

- [x] **Step 2: Drop the per-profile role-weight split**

Replace lines 51-81 (the `ROLE_WEIGHTS_DEFAULT` / `ROLE_WEIGHTS_INFRA` / `ROLE_WEIGHTS_BY_PROFILE` block):

```python
# One shared role/title weighting for every CV profile. Profiles no longer get
# their own hand-tuned table — what differentiates them is the skill vocabulary
# extracted from each CV's own text (see cv.py), and coverage against a job's
# real description already dominates the score (see scoring.FULL_WEIGHTS).
ROLE_WEIGHTS: dict[str, int] = {
    "software engineer": 58, "python engineer": 55, "backend engineer": 54,
    "ai engineer": 53, "ml engineer": 52, "machine learning engineer": 52,
    "ai infrastructure": 52, "backend developer": 50, "software developer": 50,
    "platform engineer": 48, "infrastructure engineer": 48, "distributed systems engineer": 48,
    "site reliability engineer": 45, "full stack engineer": 42, "full-stack engineer": 42,
    "fullstack engineer": 42, "full stack developer": 40, "devops engineer": 40,
    "data engineer": 38, "mlops engineer": 56, "ml infrastructure engineer": 56,
    "ml platform engineer": 55, "ai platform engineer": 55, "llm infrastructure engineer": 57,
    "inference engineer": 55, "machine learning infrastructure engineer": 56, "ml systems engineer": 53,
}
```

- [x] **Step 2: Verify nothing else in the module references the removed names**

Run: `grep -n "CV_DEFAULT\|CV_INFRA\|ROLE_WEIGHTS_INFRA\|ROLE_WEIGHTS_BY_PROFILE\|ROLE_WEIGHTS_DEFAULT" jobfit/config.py`
Expected: no matches.

- [x] **Step 3: Commit**

(This commit will show `config.py` as changed but the rest of the codebase still referencing the old names — that's expected and fixed in Tasks 6-7 next; commit anyway since this is a clean, reviewable unit.)

```bash
git add jobfit/config.py
git commit -m "refactor: drop fixed CV_DEFAULT/CV_INFRA and per-profile role weights from config"
```

---

### Task 6: `scoring.py` uses the single shared `ROLE_WEIGHTS`

**Files:**
- Modify: `jobfit/scoring.py:126-147, 172-243`
- Test: `jobfit/server/tests/test_scoring_shared_weights.py`

**Interfaces:**
- Consumes: `config.ROLE_WEIGHTS` from Task 5.
- Produces: `scoring.score_job(job, must_have_keywords, role_weights=None)` (default now `config.ROLE_WEIGHTS`), `scoring.score_job_both(job, profiles)` (drops the per-profile weights lookup).

- [x] **Step 1: Write the failing test**

Create `jobfit/server/tests/test_scoring_shared_weights.py`:

```python
from jobfit import config, scoring


def test_score_job_defaults_to_shared_role_weights():
    job = {"title": "Platform Engineer", "description": "", "department": None, "employment_type": None}
    result = scoring.score_job(job, must_have_keywords=[])
    assert result["notes"][0] == "platform engineer"


def test_score_job_both_scores_every_profile_the_same_way_for_role_fit():
    job = {"title": "Platform Engineer", "description": "", "department": None, "employment_type": None}
    profiles = {
        "a": {"must_have_keywords": []},
        "b": {"must_have_keywords": []},
    }
    result = scoring.score_job_both(job, profiles)
    # No per-profile role-weight tuning left: identical must_have_keywords -> identical scores.
    assert result["score_a"] == result["score_b"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scoring_shared_weights.py -v`
Expected: FAIL — `AttributeError: module 'jobfit.config' has no attribute 'ROLE_WEIGHTS'` is already fixed by Task 5, so this should instead fail because `score_job_both` still calls `config.ROLE_WEIGHTS_BY_PROFILE.get(...)`, which no longer exists.

- [x] **Step 3: Update `scoring.py`**

Replace line 183 (`role_weights = role_weights or config.ROLE_WEIGHTS_DEFAULT`) with:

```python
    role_weights = role_weights or config.ROLE_WEIGHTS
```

Replace the `score_job_both` function (lines 227-242):

```python
def score_job_both(job: dict, profiles: dict[str, dict]) -> dict:
    """Return score/coverage fields for every profile plus a best-of pick."""
    result = {}
    for name, profile in profiles.items():
        outcome = score_job(job, profile["must_have_keywords"])
        result[f"score_{name}"] = outcome["score"]
        result[f"matched_{name}"] = outcome["matched"] + outcome["notes"]
        result[f"coverage_{name}"] = outcome["coverage_pct"]
        result[f"confidence_{name}"] = outcome["confidence"]
        result[f"requirements_{name}"] = outcome["requirements"]
    best_name = max(profiles, key=lambda n: result[f"score_{n}"])
    result["best_cv"] = best_name
    result["best_score"] = result[f"score_{best_name}"]
    result["best_confidence"] = result[f"confidence_{best_name}"]
    return result
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_scoring_shared_weights.py -v`
Expected: PASS (2 passed)

- [x] **Step 5: Run the full test suite so far**

Run: `uv run python -m pytest jobfit/server/tests/ -v`
Expected: all passing (Tasks 2, 3, 6's tests together).

- [x] **Step 6: Commit**

```bash
git add jobfit/scoring.py jobfit/server/tests/test_scoring_shared_weights.py
git commit -m "refactor: score_job_both uses one shared role-weight table for every profile"
```

---

### Task 7: `update_jobs.py` — split into `scrape_stage()`/`recompute_stage()`, add TTL-skip + concurrency

This is the biggest single change in the plan. It also removes the CV-hash-based "only rescore if the CV changed" machinery (`cv_hash()`, `meta["cv_hash"]`, the `rescore_all` parameter threaded through `diff_and_update`/`run`/`main`) — now that `recompute_stage()` cheaply rescands and rescoring *every* stored job on every call, that hash-gate is redundant (it existed only to avoid the cost this design now does unconditionally, correctly, and for more triggers than just "the CV changed").

**Files:**
- Modify: `jobfit/scripts/update_jobs.py:83-88, 217-298, 301, 324, 433-485`
- Test: `jobfit/server/tests/test_scrape_stage.py`

**Interfaces:**
- Produces: `update_jobs.RunStats` (dataclass: `companies_checked`, `companies_skipped`, `new_jobs`, `closed_jobs`, `failures: list[str]`), `update_jobs._should_skip_company(record: dict, force: bool) -> bool`, `update_jobs.scrape_stage(companies: dict[str, str], profiles: dict, force: bool = False) -> RunStats`, `update_jobs.recompute_stage() -> None`, `update_jobs.diff_and_update(company, career_url, fetched, profiles) -> tuple[dict, int, int]` (drops the old `rescore_all` parameter), `update_jobs.merge_referral_jobs(profiles, path=None)` (adds `path`, addressed in Task 9 — leave its signature alone here).
- Consumes: `cv.load_profiles()`, `cv.load_registry()` (Tasks 2-3), `scoring.score_job_both` (Task 6), `config.COMPANY_RECHECK_TTL_HOURS` (Task 5), `build_html.build()` (existing, unchanged).

- [x] **Step 1: Write the failing tests for the TTL-skip decision**

Create `jobfit/server/tests/test_scrape_stage.py`:

```python
from datetime import datetime, timedelta, timezone

from jobfit import config
from jobfit.scripts import update_jobs


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_skips_a_company_checked_within_the_ttl():
    recent = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=False) is True


def test_does_not_skip_a_stale_company():
    stale = _iso(datetime.now(timezone.utc) - timedelta(hours=config.COMPANY_RECHECK_TTL_HOURS + 1))
    assert update_jobs._should_skip_company({"last_checked": stale}, force=False) is False


def test_force_never_skips_even_if_recent():
    recent = _iso(datetime.now(timezone.utc))
    assert update_jobs._should_skip_company({"last_checked": recent}, force=True) is False


def test_never_checked_company_is_not_skipped():
    assert update_jobs._should_skip_company({}, force=False) is False
    assert update_jobs._should_skip_company({"last_checked": None}, force=False) is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_scrape_stage.py -v`
Expected: FAIL — `AttributeError: module 'jobfit.scripts.update_jobs' has no attribute '_should_skip_company'`

- [x] **Step 3: Replace `cv_hash()` (lines 83-88)**

```python
def cv_hash() -> str:
    """Kept for meta.json bookkeeping only — no longer gates rescoring (recompute_stage
    always rescores everything; see Task 7 of the control-panel plan for why)."""
    h = hashlib.sha1()
    registry = cv.load_registry()
    for profile_id in sorted(registry):
        path = config.CV_PROFILES_DIR / registry[profile_id]["filename"]
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()
```

(This keeps `cv_hash()` around in case something outside this plan still wants a fingerprint of the current CV set, but nothing in this plan reads it for gating anymore.)

- [x] **Step 4: Replace `diff_and_update` (lines 217-266) — drop `rescore_all`**

```python
def diff_and_update(company: str, career_url: str, fetched: list[dict], profiles: dict) -> tuple[dict, int, int]:
    """Returns (updated_company_record, new_count, closed_count).

    Only newly-seen jobs get scored here (so they have a sane score immediately).
    Existing jobs' scores are left alone — recompute_stage() is the single place
    that rescands and rescoring every stored job, on every trigger that could
    change a score (a new CV, a removed profile, or just periodically).
    """
    record = load_company_file(company)
    existing_by_id = {j["id"]: j for j in record["jobs"]}
    fetched_ids: set[str] = set()
    now = _now_iso()
    new_count = 0

    for job in fetched:
        title = (job.get("title") or "").strip()
        if not title:
            continue
        job_id = compute_job_id(company, title, job.get("location"), job.get("url"))
        fetched_ids.add(job_id)

        if job_id in existing_by_id:
            existing = existing_by_id[job_id]
            existing["last_seen"] = now
            if existing.get("status") in ("new", "closed"):
                existing["status"] = "seen"
        else:
            scores = scoring.score_job_both(job, profiles)
            new_job = {
                "id": job_id,
                "title": title,
                "location": job.get("location"),
                "url": job.get("url"),
                "description": ats_fetchers.strip_html(job.get("description")),
                "first_seen": now,
                "last_seen": now,
                "status": "new",
            }
            new_job.update(scores)
            existing_by_id[job_id] = new_job
            new_count += 1

    closed_count = 0
    for job_id, existing in existing_by_id.items():
        if job_id not in fetched_ids and existing.get("status") != "closed":
            existing["status"] = "closed"
            closed_count += 1

    record["name"] = company
    record["career_url"] = career_url
    record["last_checked"] = now
    record["jobs"] = list(existing_by_id.values())
    return record, new_count, closed_count
```

- [x] **Step 5: Replace `run()` (lines 269-298) with `_should_skip_company`, `_process_company`, `scrape_stage`, and `recompute_stage`**

Add these imports at the top of the file (alongside the existing ones):

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
```

Then replace the `run()` function entirely:

```python
WORKERS = 8


@dataclass
class RunStats:
    companies_checked: int = 0
    companies_skipped: int = 0
    new_jobs: int = 0
    closed_jobs: int = 0
    failures: list[str] = field(default_factory=list)


def _should_skip_company(record: dict, force: bool) -> bool:
    if force:
        return False
    last_checked = record.get("last_checked")
    if not last_checked:
        return False
    try:
        checked_at = datetime.strptime(last_checked, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    age_hours = (datetime.now(timezone.utc) - checked_at).total_seconds() / 3600
    return age_hours < config.COMPANY_RECHECK_TTL_HOURS


def _process_company(company, url, session, profiles, techmap_index, force):
    """Runs in a worker thread. Returns (company, new_count, closed_count, skipped, error)."""
    record = load_company_file(company)
    if _should_skip_company(record, force):
        return company, 0, 0, True, None
    try:
        fetched = asyncio.run(fetch_company_jobs_async(company, url, session, profiles, techmap_index))
        updated, new_count, closed_count = diff_and_update(company, url, fetched, profiles)
        save_company_file(company, updated)
        return company, new_count, closed_count, False, None
    except Exception as error:  # noqa: BLE001 - one bad company must never abort the run
        return company, 0, 0, False, error


def scrape_stage(companies: dict[str, str], profiles: dict, force: bool = False) -> RunStats:
    """The network-bound half of an update: fetch + diff every company, concurrently,
    skipping anything checked within COMPANY_RECHECK_TTL_HOURS unless force=True."""
    session = ats_fetchers.make_session()
    logger.info("loading techmap data (fallback source for companies whose own site can't be parsed)...")
    techmap_index = load_techmap_index()

    stats = RunStats()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [
            pool.submit(_process_company, company, url, session, profiles, techmap_index, force)
            for company, url in companies.items()
        ]
        for future in as_completed(futures):
            company, new_count, closed_count, skipped, error = future.result()
            if skipped:
                stats.companies_skipped += 1
                continue
            if error is not None:
                stats.failures.append(company)
                logger.warning("%s: FAILED - %s: %s", company, type(error).__name__, error)
                continue
            stats.companies_checked += 1
            stats.new_jobs += new_count
            stats.closed_jobs += closed_count
            logger.info("%s: %d new, %d closed", company, new_count, closed_count)

    logger.info(
        "scrape done: %d checked, %d skipped (recently checked), %d new, %d closed, %d failures",
        stats.companies_checked, stats.companies_skipped, stats.new_jobs, stats.closed_jobs, len(stats.failures),
    )
    return stats


def recompute_stage() -> None:
    """The local-only half of an update: rescore every stored job against the
    *current* profile registry, drop any score fields for profiles that no
    longer exist, reaggregate, and rebuild jobfit.html. Cheap (pure regex
    scoring over already-fetched text) - safe to call after any admin edit,
    not just after a scrape."""
    profiles = cv.load_profiles()
    profile_ids = set(profiles)
    stale_prefixes = ("score_", "matched_", "coverage_", "confidence_", "requirements_")

    for path in sorted(COMPANIES_DIR.glob("*.json")):
        if path.name == "_meta.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        for job in record["jobs"]:
            job.update(scoring.score_job_both(job, profiles))
            for key in list(job):
                for prefix in stale_prefixes:
                    if key.startswith(prefix) and key[len(prefix):] not in profile_ids:
                        del job[key]
        save_company_file(record["name"], record)

    count = aggregate_to_jobs_v2()
    logger.info("recompute: rescored against %d profile(s), aggregated %d jobs", len(profiles), count)
    from jobfit import build_html
    build_html.build()
```

- [x] **Step 6: Update `main()` (lines 433-485) to use the two new stages**

```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process the first N companies (testing)")
    parser.add_argument("--company", type=str, default=None, help="only process this one company (exact name match)")
    parser.add_argument("--force", action="store_true", help="re-check companies even if checked recently")
    parser.add_argument("--skip-aggregate", action="store_true", help="don't rescore/rebuild after updating")
    args = parser.parse_args()

    all_companies = json.loads(config.ROOT.joinpath("companies_career_pages.json").read_text(encoding="utf-8"))
    companies = {name: url for name, url in all_companies.items() if url}

    if args.company:
        if args.company not in companies:
            logger.error("company %r not found (or has no URL) in companies_career_pages.json", args.company)
            return
        companies = {args.company: companies[args.company]}
    elif args.limit:
        companies = dict(list(companies.items())[: args.limit])

    started = time.time()
    profiles = cv.load_profiles()
    stats = scrape_stage(companies, profiles, force=args.force)

    print()
    print("=== Update summary ===")
    print(f"companies checked: {stats.companies_checked}")
    print(f"companies skipped (recently checked): {stats.companies_skipped}")
    print(f"new jobs: {stats.new_jobs}")
    print(f"closed jobs: {stats.closed_jobs}")
    print(f"failures: {len(stats.failures)}" + (f" ({', '.join(stats.failures)})" if stats.failures else ""))

    if args.company or args.limit:
        logger.info("skipping referral-jobs merge (scoped run via --company/--limit)")
    else:
        referral_stats = merge_referral_jobs(profiles)
        logger.info(
            "referral jobs: %d matched to existing companies, %d new companies, "
            "%d merged into existing jobs (referral-tagged), %d added as new jobs",
            referral_stats["matched_existing_company"], referral_stats["new_company"],
            referral_stats["merged_into_existing_job"], referral_stats["added_new_job"],
        )

    meta = load_meta()
    meta["last_run"] = _now_iso()
    save_meta(meta)

    if not args.skip_aggregate:
        recompute_stage()

    logger.info("done in %.1fs", time.time() - started)
```

Note the CLI flags changed: `--force-rescore` is gone (superseded — `recompute_stage()` always rescands everything now), and `--force` is new (means "re-check companies even if recently checked", previously the default and only behavior). `--skip-aggregate` now also skips rescoring, since that's bundled into the same cheap step.

- [x] **Step 7: Run all tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/ -v`
Expected: all passing, including the 4 new tests from Step 1.

- [x] **Step 8: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/server/tests/test_scrape_stage.py
git commit -m "refactor: split update_jobs into scrape_stage/recompute_stage with TTL-skip and concurrency"
```

---

### Task 8: `build_html.py` renders N profiles dynamically

**Files:**
- Modify: `jobfit/build_html.py` (PAGE_TEMPLATE's `cvSelect` options, `scoreFor`/`cvLabelFor`, `jobCardHtml`'s CV-pill block; `render()`)

**Interfaces:**
- Consumes: `cv.load_registry()` (Task 2).
- Produces: `build_html.render(dataset, profiles)` (adds a `profiles` parameter), embeds a `PROFILES` JS array alongside `JOBS`.

- [x] **Step 1: Replace the hardcoded `cvSelect` options**

In the `PAGE_TEMPLATE` string, replace:

```html
      <select id="cvSelect">
        <option value="best">Best of both CVs</option>
        <option value="default">Default CV</option>
        <option value="infra">Infra CV</option>
      </select>
```

with:

```html
      <select id="cvSelect">
        <option value="best">Best of all CVs</option>
      </select>
```

(The per-profile `<option>`s are now added by JS at load time — see Step 4.)

- [x] **Step 2: Add a `PROFILES` constant next to `JOBS`**

Replace:

```javascript
const JOBS = __JOBS_JSON__;
const GENERATED_AT = __GENERATED_AT_JSON__;
```

with:

```javascript
const JOBS = __JOBS_JSON__;
const PROFILES = __PROFILES_JSON__;
const GENERATED_AT = __GENERATED_AT_JSON__;
```

- [x] **Step 3: Replace the hardcoded `scoreFor`/`cvLabelFor` functions**

Replace:

```javascript
function scoreFor(job) {
  if (state.cv === "default") return job.score_default;
  if (state.cv === "infra") return job.score_infra;
  return job.best_score;
}
function cvLabelFor(job) {
  if (state.cv === "best") return job.best_cv;
  return state.cv;
}
```

with:

```javascript
function scoreFor(job) {
  if (state.cv === "best") return job.best_score;
  return job[`score_${state.cv}`];
}
function cvLabelFor(job) {
  if (state.cv === "best") return job.best_cv;
  return state.cv;
}
function profileName(id) {
  const p = PROFILES.find(p => p.id === id);
  return p ? p.name : id;
}
```

- [x] **Step 4: Populate `cvSelect`'s options from `PROFILES` at load**

Add this near the top of the `<script>` block, right after the `PROFILES` constant is read (before `applyFilterState(loadFilterState())` is called, since a saved filter might reference a profile id that needs to already be a valid `<option>`):

```javascript
const cvSelectEl = document.getElementById("cvSelect");
for (const p of PROFILES) {
  const opt = document.createElement("option");
  opt.value = p.id;
  opt.textContent = p.name;
  cvSelectEl.appendChild(opt);
}
```

- [x] **Step 5: Replace the hardcoded two-pill block in `jobCardHtml`**

Replace:

```javascript
      <div class="score-both">
        ${cvPillHtml("default", "Default", job.score_default, job.confidence_default, job.coverage_default, job.best_cv === "default")}
        ${cvPillHtml("infra", "Infra", job.score_infra, job.confidence_infra, job.coverage_infra, job.best_cv === "infra")}
      </div>
```

with:

```javascript
      <div class="score-both">
        ${PROFILES.map(p => cvPillHtml(p.id, p.name, job[`score_${p.id}`], job[`confidence_${p.id}`], job[`coverage_${p.id}`], job.best_cv === p.id)).join("")}
      </div>
```

- [x] **Step 6: Update `render()`/`build()` to embed `PROFILES`**

Replace:

```python
def render(dataset: list[dict]) -> str:
    jobs_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    generated_at = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return PAGE_TEMPLATE.replace("__JOBS_JSON__", jobs_json).replace("__GENERATED_AT_JSON__", generated_at)


def build(dataset: list[dict] | None = None) -> None:
    if dataset is None:
        dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    html = render(dataset)
    config.OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {config.OUTPUT_HTML} ({len(dataset)} jobs)")
```

with:

```python
def render(dataset: list[dict], profiles: list[dict]) -> str:
    jobs_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    profiles_json = json.dumps(profiles, ensure_ascii=False)
    generated_at = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return (
        PAGE_TEMPLATE.replace("__JOBS_JSON__", jobs_json)
        .replace("__PROFILES_JSON__", profiles_json)
        .replace("__GENERATED_AT_JSON__", generated_at)
    )


def build(dataset: list[dict] | None = None) -> None:
    if dataset is None:
        dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    from jobfit import cv
    profiles = [{"id": pid, "name": entry["name"]} for pid, entry in cv.load_registry().items()]
    html = render(dataset, profiles)
    config.OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {config.OUTPUT_HTML} ({len(dataset)} jobs)")
```

- [x] **Step 7: Commit**

```bash
git add jobfit/build_html.py
git commit -m "feat: render CV profile pills/select dynamically from the profile registry"
```

---

### Task 9: Manual end-to-end verification of Phase 1

No new files — this confirms Tasks 2-8 work together before any server code exists.

- [x] **Step 1: Run a small-scope update through the CLI**

```bash
uv run python -m jobfit.scripts.update_jobs --company Wiz
```

Expected: completes without error, logs `Wiz: N new, M closed` (or `0, 0` if nothing changed), and the summary block prints `companies checked: 1`.

- [x] **Step 2: Run the recompute stage directly and confirm the page rebuilds**

```bash
uv run python -c "
from jobfit.scripts import update_jobs
update_jobs.recompute_stage()
"
```

Expected: prints `wrote .../jobfit.html (N jobs)`.

- [x] **Step 3: Confirm the rebuilt page shows two CV pills, now driven by the registry**

```bash
grep -o '"id": "[a-z_]*"' jobfit.html | sort -u
```

Expected: `"id": "default"` and `"id": "infra"` (from the migrated registry in Task 4) — confirming `PROFILES` made it into the embedded JSON.

- [x] **Step 4: Re-run the same company immediately and confirm the TTL-skip fires**

```bash
uv run python -m jobfit.scripts.update_jobs --company Wiz
```

Since `--company` bypasses the batch skip check only at the CLI-arg-filtering level, not inside `scrape_stage` itself, this should still show `companies checked: 1` (a single explicitly-named company is still checked) — confirm this manually, then verify the skip logic on a *batch* run:

```bash
uv run python -m jobfit.scripts.update_jobs --limit 3
uv run python -m jobfit.scripts.update_jobs --limit 3
```

Expected: the second run's summary shows `companies skipped (recently checked): 3` (or close to it, depending on which 3 companies alphabetically-first happen to already be within the TTL from the first run).

---

## Phase 2 — Server skeleton + CRUD

### Task 10: Add server dependencies

**Files:**
- Modify: `pyproject.toml`

- [x] **Step 1: Add the new dependencies**

```toml
dependencies = [
    "beautifulsoup4>=4.15.0",
    "claude-agent-sdk>=0.2.128",
    "fastapi>=0.115.0",
    "playwright>=1.62.0",
    "python-docx>=1.2.0",
    "python-multipart>=0.0.12",
    "pytest>=8.3.0",
    "requests>=2.34.2",
    "uvicorn>=0.32.0",
]
```

- [x] **Step 2: Install and verify**

```bash
uv sync
uv run python -c "import fastapi, uvicorn, multipart; print('ok')"
```

Expected: `ok`

- [x] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastapi/uvicorn/python-multipart/pytest for the control panel"
```

---

### Task 11: Dashboard stats + FastAPI app skeleton

**Files:**
- Create: `jobfit/server/__init__.py` (empty)
- Create: `jobfit/server/dashboard.py`
- Create: `jobfit/server/app.py`
- Test: `jobfit/server/tests/test_dashboard.py`

**Interfaces:**
- Produces: `dashboard.get_dashboard_stats() -> dict`, FastAPI `app` object in `jobfit/server/app.py` with `GET /` and `GET /api/dashboard`.

- [x] **Step 1: Write the failing test**

Create `jobfit/server/tests/test_dashboard.py`:

```python
import json

from jobfit import config, cv
from jobfit.server import dashboard


def test_dashboard_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")

    (tmp_path / "companies").mkdir()
    (tmp_path / "companies" / "wiz.json").write_text("{}", encoding="utf-8")
    (tmp_path / "companies" / "_meta.json").write_text("{}", encoding="utf-8")

    cv.save_registry({"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}})
    config.JOBS_OUTPUT_JSON.write_text(json.dumps([
        {"status": "seen", "score_default": 72},
        {"status": "new", "score_default": 45},
        {"status": "closed", "score_default": 90},
    ]), encoding="utf-8")

    stats = dashboard.get_dashboard_stats()

    assert stats["total_jobs_open"] == 2
    assert stats["total_jobs_all_time"] == 3
    assert stats["companies"] == 1
    assert stats["connections"] == 0
    assert stats["profiles"] == [{"id": "default", "name": "Default"}]
    assert stats["score_distribution"] == {"default": {40: 1, 70: 1}}


def test_dashboard_stats_with_no_data_yet(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")

    stats = dashboard.get_dashboard_stats()

    assert stats == {
        "total_jobs_open": 0, "total_jobs_all_time": 0, "companies": 0,
        "connections": 0, "profiles": [], "score_distribution": {},
    }
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobfit.server'`

- [x] **Step 3: Implement `dashboard.py`**

Create `jobfit/server/__init__.py` (empty file).

Create `jobfit/server/dashboard.py`:

```python
"""Pure functions computing the control panel's dashboard stats from what's on disk."""

import json
from collections import Counter

from jobfit import config, connections, cv


def get_dashboard_stats() -> dict:
    registry = cv.load_registry()
    profile_ids = list(registry)

    jobs = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8")) if config.JOBS_OUTPUT_JSON.exists() else []
    open_jobs = [j for j in jobs if j.get("status") != "closed"]

    score_distribution: dict[str, dict[int, int]] = {}
    for profile_id in profile_ids:
        buckets: Counter = Counter()
        for job in open_jobs:
            score = job.get(f"score_{profile_id}")
            if score is None:
                continue
            buckets[(score // 10) * 10] += 1
        score_distribution[profile_id] = dict(sorted(buckets.items()))

    conn_index = connections.load_connections_index() if config.CONNECTIONS_CSV.exists() else {}
    connections_count = sum(len(v) for v in conn_index.values())

    companies_dir = config.ROOT / "companies"
    company_count = (
        sum(1 for p in companies_dir.glob("*.json") if p.name != "_meta.json")
        if companies_dir.exists() else 0
    )

    return {
        "total_jobs_open": len(open_jobs),
        "total_jobs_all_time": len(jobs),
        "companies": company_count,
        "connections": connections_count,
        "profiles": [{"id": pid, "name": entry["name"]} for pid, entry in registry.items()],
        "score_distribution": score_distribution,
    }
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_dashboard.py -v`
Expected: PASS (2 passed)

- [x] **Step 5: Create the FastAPI app skeleton**

Create `jobfit/server/app.py`:

```python
"""FastAPI app for the jobfit control panel — localhost only, no auth."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from jobfit.server import dashboard

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="jobfit control panel")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "panel.html")


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    return dashboard.get_dashboard_stats()
```

Create an empty placeholder so `FileResponse` has something to serve for now:

```bash
mkdir -p jobfit/server/static
```

Create `jobfit/server/static/panel.html` with a minimal placeholder (replaced for real in Task 15):

```html
<!doctype html>
<html><body><h1>jobfit control panel</h1><p>under construction</p></body></html>
```

- [x] **Step 6: Verify the server starts and responds**

```bash
uv run uvicorn jobfit.server.app:app --port 8787 &
sleep 1
curl -s http://127.0.0.1:8787/api/dashboard
kill %1
```

Expected: a JSON dashboard payload (numbers will reflect whatever's currently in `jobfit/data/jobs_v2.json` and `jobfit/companies/`).

- [x] **Step 7: Commit**

```bash
git add jobfit/server/__init__.py jobfit/server/dashboard.py jobfit/server/app.py jobfit/server/static/panel.html jobfit/server/tests/test_dashboard.py
git commit -m "feat: add FastAPI app skeleton with a dashboard-stats endpoint"
```

---

### Task 12: `/api/profiles` — list, add, delete

**Files:**
- Modify: `jobfit/server/app.py`

**Interfaces:**
- Consumes: `cv.load_registry()`, `cv.register_profile()`, `cv.remove_profile()` (Task 2), `update_jobs.recompute_stage()` (Task 7).

- [x] **Step 1: Add the routes**

In `jobfit/server/app.py`, add imports:

```python
from fastapi import File, Form, HTTPException, UploadFile

from jobfit import config, cv
from jobfit.scripts import update_jobs
```

Add routes after `api_dashboard`:

```python
@app.get("/api/profiles")
def api_list_profiles() -> list[dict]:
    return [{"id": pid, **entry} for pid, entry in cv.load_registry().items()]


@app.post("/api/profiles")
async def api_add_profile(name: str = Form(...), file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".docx"):
        raise HTTPException(400, "CV must be a .docx file")
    config.CV_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.CV_PROFILES_DIR / f"_upload_{file.filename}"
    tmp_path.write_bytes(await file.read())
    try:
        profile_id = cv.register_profile(name, tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    update_jobs.recompute_stage()
    return {"id": profile_id, **dashboard.get_dashboard_stats()}


@app.delete("/api/profiles/{profile_id}")
def api_delete_profile(profile_id: str) -> dict:
    cv.remove_profile(profile_id)
    update_jobs.recompute_stage()
    return dashboard.get_dashboard_stats()
```

- [x] **Step 2: Verify manually**

```bash
uv run uvicorn jobfit.server.app:app --port 8787 &
sleep 1
curl -s http://127.0.0.1:8787/api/profiles
curl -s -X DELETE http://127.0.0.1:8787/api/profiles/does-not-exist
kill %1
```

Expected: the first call lists whatever profiles are currently registered (e.g. `default`, `infra` from Task 4's migration); the delete call returns dashboard stats without error (no-op delete, per `cv.remove_profile`'s tested behavior).

- [x] **Step 3: Commit**

```bash
git add jobfit/server/app.py
git commit -m "feat: add /api/profiles list/add/delete routes"
```

---

### Task 13: `/api/connections` — upload

**Files:**
- Modify: `jobfit/server/app.py`

- [x] **Step 1: Add the route**

```python
@app.post("/api/connections")
async def api_upload_connections(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Connections export must be a .csv file")
    config.CONNECTIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    config.CONNECTIONS_CSV.write_bytes(await file.read())
    update_jobs.recompute_stage()
    return dashboard.get_dashboard_stats()
```

- [x] **Step 2: Verify manually**

```bash
uv run uvicorn jobfit.server.app:app --port 8787 &
sleep 1
curl -s -X POST http://127.0.0.1:8787/api/connections -F "file=@jobfit/data/connections.csv;type=text/csv"
kill %1
```

Expected: JSON dashboard stats with a non-zero `connections` count (assuming Task 4's migration copied a real connections export).

- [x] **Step 3: Commit**

```bash
git add jobfit/server/app.py
git commit -m "feat: add /api/connections upload route"
```

---

### Task 14: `merge_referral_jobs` gains a `path` argument; `/api/referrals` upload

**Files:**
- Modify: `jobfit/scripts/update_jobs.py:301-383`
- Modify: `jobfit/server/app.py`
- Test: `jobfit/server/tests/test_referral_merge.py`

**Interfaces:**
- Produces: `update_jobs.merge_referral_jobs(profiles: dict, path: Path | None = None) -> dict[str, int]` (was: `merge_referral_jobs(profiles)`, hardcoded to `config.REFERRAL_JOBS_PATH`).

- [x] **Step 1: Write the failing test**

Create `jobfit/server/tests/test_referral_merge.py`:

```python
import json

from jobfit import config
from jobfit.scripts import update_jobs


def _write_company(companies_dir, name, jobs):
    companies_dir.mkdir(parents=True, exist_ok=True)
    (companies_dir / f"{name}.json").write_text(
        json.dumps({"name": name.title(), "career_url": None, "last_checked": None, "jobs": jobs}),
        encoding="utf-8",
    )


def test_merge_referral_jobs_reads_from_an_explicit_path_not_the_global_default(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    _write_company(companies_dir, "acme", jobs=[])

    upload_path = tmp_path / "my_referral_upload.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe", "requirements": ["python"]}],
        }],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["added_new_job"] == 1
    saved = json.loads((companies_dir / "acme.json").read_text(encoding="utf-8"))
    assert saved["jobs"][0]["title"] == "Backend Engineer"
    assert saved["jobs"][0]["is_referral"] is True


def test_merge_referral_jobs_dedupes_against_an_existing_similar_title(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    _write_company(companies_dir, "acme", jobs=[{
        "id": "existing1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "x", "last_seen": "x",
    }])

    upload_path = tmp_path / "my_referral_upload.json"
    upload_path.write_text(json.dumps({
        "companies": [{
            "company": "Acme",
            "jobs": [{"title": "Backend Engineer", "contact": "Jane Doe"}],
        }],
    }), encoding="utf-8")

    stats = update_jobs.merge_referral_jobs(profiles={}, path=upload_path)

    assert stats["merged_into_existing_job"] == 1
    assert stats["added_new_job"] == 0
    saved = json.loads((companies_dir / "acme.json").read_text(encoding="utf-8"))
    assert len(saved["jobs"]) == 1
    assert saved["jobs"][0]["is_referral"] is True
    assert saved["jobs"][0]["referral_contact"] == "Jane Doe"


def test_merge_referral_jobs_returns_empty_stats_when_the_file_is_missing(tmp_path):
    stats = update_jobs.merge_referral_jobs(profiles={}, path=tmp_path / "does_not_exist.json")
    assert stats == {
        "matched_existing_company": 0, "new_company": 0,
        "merged_into_existing_job": 0, "added_new_job": 0,
    }
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_referral_merge.py -v`
Expected: FAIL — `TypeError: merge_referral_jobs() got an unexpected keyword argument 'path'`

- [x] **Step 3: Add the `path` parameter**

In `jobfit/scripts/update_jobs.py`, replace the `merge_referral_jobs` signature and its body's two references to `config.REFERRAL_JOBS_PATH`:

```python
def merge_referral_jobs(profiles: dict, path: "Path | None" = None) -> dict[str, int]:
    """Merge WhatsApp-referral-sourced jobs into companies/*.json - same
    canonical-company + title-similarity matching as before. `path` defaults
    to config.REFERRAL_JOBS_PATH (the CLI's fixed Downloads-folder file);
    the control panel passes an explicit uploaded-file path instead.
    """
    from jobfit import referral_source  # noqa: E402

    path = path or config.REFERRAL_JOBS_PATH
    stats = {"matched_existing_company": 0, "new_company": 0, "merged_into_existing_job": 0, "added_new_job": 0}
    if not path.exists():
        return stats

    existing_names = [
        json.loads(p.read_text(encoding="utf-8"))["name"]
        for p in COMPANIES_DIR.glob("*.json") if p.name != "_meta.json"
    ]
    canonical_by_key = {connections.normalize_company(c): c for c in existing_names if connections.normalize_company(c)}
    now = _now_iso()

    for company_entry in referral_source.load_referral_companies(path):
```

(Everything below that `for` line stays exactly as it already is — only the function signature, the docstring, and the `path = path or config.REFERRAL_JOBS_PATH` / `load_referral_companies(path)` lines change.)

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_referral_merge.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Add the `/api/referrals` route**

In `jobfit/server/app.py`, add:

```python
import json
from datetime import datetime, timezone


@app.post("/api/referrals")
async def api_upload_referral(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".json"):
        raise HTTPException(400, "Referral export must be a .json file")
    raw = await file.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Not valid JSON")
    if "companies" not in payload:
        raise HTTPException(400, 'Expected a top-level "companies" key')

    config.REFERRAL_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = config.REFERRAL_UPLOADS_DIR / f"{timestamp}-{file.filename}"
    archive_path.write_bytes(raw)

    profiles = cv.load_profiles()
    stats = update_jobs.merge_referral_jobs(profiles, path=archive_path)
    update_jobs.recompute_stage()
    return {**stats, **dashboard.get_dashboard_stats()}
```

- [x] **Step 6: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/server/app.py jobfit/server/tests/test_referral_merge.py
git commit -m "feat: merge_referral_jobs takes an explicit path; add /api/referrals upload"
```

---

### Task 15: Panel UI — Dashboard, CV Profiles, Connections, Referrals tabs

**Files:**
- Modify: `jobfit/server/static/panel.html` (replaces the Task 11 placeholder)

- [x] **Step 1: Write the panel page**

Replace `jobfit/server/static/panel.html` entirely:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>jobfit control panel</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background: #0b0e11; color: #e6e9ec; }
  .tabs { display: flex; gap: 4px; padding: 12px 16px; border-bottom: 1px solid #232a32; }
  .tab-btn { background: #171c22; border: 1px solid #232a32; color: #9aa4ad; padding: 8px 14px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  .tab-btn.active { color: #76b900; border-color: #4d7a00; }
  .panel { padding: 20px; max-width: 720px; }
  .panel[hidden] { display: none; }
  .stat-row { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 18px; }
  .stat { background: #12161b; border: 1px solid #232a32; border-radius: 8px; padding: 12px 16px; min-width: 120px; }
  .stat b { display: block; font-size: 20px; }
  .list-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #232a32; }
  form { display: flex; flex-direction: column; gap: 8px; max-width: 360px; margin-bottom: 20px; }
  input, button { background: #171c22; border: 1px solid #232a32; color: #e6e9ec; padding: 7px 10px; border-radius: 6px; }
  button { cursor: pointer; }
  .msg { font-size: 13px; color: #76b900; min-height: 18px; }
  .msg.error { color: #ff6b6b; }
</style>
</head>
<body>
  <div class="tabs">
    <button class="tab-btn" data-tab="dashboard">Dashboard</button>
    <button class="tab-btn" data-tab="profiles">CV Profiles</button>
    <button class="tab-btn" data-tab="connections">Connections</button>
    <button class="tab-btn" data-tab="referrals">Referrals</button>
  </div>

  <section id="tab-dashboard" class="panel">
    <div class="stat-row" id="statRow"></div>
    <div id="scoreDistribution"></div>
  </section>

  <section id="tab-profiles" class="panel" hidden>
    <form id="profileForm">
      <input type="text" name="name" placeholder="Profile name" required>
      <input type="file" name="file" accept=".docx" required>
      <button type="submit">Add CV profile</button>
    </form>
    <div class="msg" id="profileMsg"></div>
    <div id="profileList"></div>
  </section>

  <section id="tab-connections" class="panel" hidden>
    <form id="connectionsForm">
      <input type="file" name="file" accept=".csv" required>
      <button type="submit">Upload connections CSV</button>
    </form>
    <div class="msg" id="connectionsMsg"></div>
  </section>

  <section id="tab-referrals" class="panel" hidden>
    <form id="referralForm">
      <input type="file" name="file" accept=".json" required>
      <button type="submit">Upload referral export</button>
    </form>
    <div class="msg" id="referralMsg"></div>
  </section>

<script>
function switchTab(name) {
  document.querySelectorAll(".panel").forEach(el => el.hidden = el.id !== `tab-${name}`);
  document.querySelectorAll(".tab-btn").forEach(el => el.classList.toggle("active", el.dataset.tab === name));
}
document.querySelectorAll(".tab-btn").forEach(btn => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));
switchTab("dashboard");

async function refreshDashboard() {
  const stats = await (await fetch("/api/dashboard")).json();
  document.getElementById("statRow").innerHTML = `
    <div class="stat"><b>${stats.total_jobs_open}</b>open jobs</div>
    <div class="stat"><b>${stats.total_jobs_all_time}</b>jobs all-time</div>
    <div class="stat"><b>${stats.companies}</b>companies</div>
    <div class="stat"><b>${stats.connections}</b>connections</div>
  `;
  document.getElementById("scoreDistribution").innerHTML = Object.entries(stats.score_distribution).map(([id, buckets]) => {
    const profile = stats.profiles.find(p => p.id === id);
    const rows = Object.entries(buckets).map(([bucket, count]) => `<div>${bucket}-${Number(bucket) + 9}: ${count}</div>`).join("");
    return `<h3>${profile ? profile.name : id}</h3>${rows}`;
  }).join("");
}

async function refreshProfiles() {
  const profiles = await (await fetch("/api/profiles")).json();
  document.getElementById("profileList").innerHTML = profiles.map(p => `
    <div class="list-row">
      <span>${p.name}</span>
      <button data-id="${p.id}" class="delete-profile">Remove</button>
    </div>`).join("");
  document.querySelectorAll(".delete-profile").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/profiles/${btn.dataset.id}`, { method: "DELETE" });
      await refreshProfiles();
      await refreshDashboard();
    });
  });
}

document.getElementById("profileForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("profileMsg");
  msg.textContent = "Uploading...";
  msg.classList.remove("error");
  try {
    const res = await fetch("/api/profiles", { method: "POST", body: new FormData(e.target) });
    if (!res.ok) throw new Error((await res.json()).detail || "upload failed");
    msg.textContent = "Added.";
    e.target.reset();
    await refreshProfiles();
    await refreshDashboard();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
});

document.getElementById("connectionsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("connectionsMsg");
  msg.textContent = "Uploading...";
  msg.classList.remove("error");
  try {
    const res = await fetch("/api/connections", { method: "POST", body: new FormData(e.target) });
    if (!res.ok) throw new Error((await res.json()).detail || "upload failed");
    msg.textContent = "Connections updated.";
    e.target.reset();
    await refreshDashboard();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
});

document.getElementById("referralForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = document.getElementById("referralMsg");
  msg.textContent = "Uploading...";
  msg.classList.remove("error");
  try {
    const res = await fetch("/api/referrals", { method: "POST", body: new FormData(e.target) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "upload failed");
    msg.textContent = `${data.added_new_job} new, ${data.merged_into_existing_job} already tracked (marked as referral).`;
    e.target.reset();
    await refreshDashboard();
  } catch (err) {
    msg.textContent = err.message;
    msg.classList.add("error");
  }
});

refreshDashboard();
refreshProfiles();
</script>
</body>
</html>
```

- [x] **Step 2: Verify manually in a browser**

```bash
uv run uvicorn jobfit.server.app:app --port 8787
```

Open `http://127.0.0.1:8787/` — confirm the Dashboard tab shows real numbers, the CV Profiles tab lists the migrated `default`/`infra` profiles and can add/remove one, Connections and Referrals tabs accept an upload and show a result message. Stop the server (Ctrl+C) when done.

- [x] **Step 3: Commit**

```bash
git add jobfit/server/static/panel.html
git commit -m "feat: build the control panel UI for dashboard, CV profiles, connections, referrals"
```

---

## Phase 3 — Run trigger + live log + history

### Task 16: `logging_stream.py` — queue-backed log handler

**Files:**
- Create: `jobfit/server/logging_stream.py`
- Test: `jobfit/server/tests/test_logging_stream.py`

**Interfaces:**
- Produces: `logging_stream.QueueLogHandler(line_queue)`, `logging_stream.attach(line_queue, logger_names) -> list[tuple[Logger, QueueLogHandler]]`, `logging_stream.detach(attached) -> None`.

- [x] **Step 1: Write the failing test**

Create `jobfit/server/tests/test_logging_stream.py`:

```python
import logging
import queue

from jobfit.server import logging_stream


def test_attach_forwards_log_lines_to_the_queue_and_detach_stops_it():
    line_queue: queue.Queue = queue.Queue()
    logger = logging.getLogger("jobfit.tests.logging_stream")
    logger.setLevel(logging.INFO)

    attached = logging_stream.attach(line_queue, ["jobfit.tests.logging_stream"])
    logger.info("hello %s", "world")

    line = line_queue.get(timeout=1)
    assert "hello world" in line

    logging_stream.detach(attached)
    logger.info("should not appear")
    assert line_queue.empty()
```

- [x] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest jobfit/server/tests/test_logging_stream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobfit.server.logging_stream'`

- [x] **Step 3: Implement `logging_stream.py`**

```python
"""In-memory log fan-out: attach a queue-backed handler to the jobfit loggers
for the duration of a background run, so an SSE endpoint can stream it live."""

import logging
import queue


class QueueLogHandler(logging.Handler):
    def __init__(self, line_queue: "queue.Queue[str | None]"):
        super().__init__()
        self.line_queue = line_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.line_queue.put(self.format(record))


def attach(
    line_queue: "queue.Queue[str | None]", logger_names: list[str]
) -> list[tuple[logging.Logger, QueueLogHandler]]:
    handler = QueueLogHandler(line_queue)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    attached = []
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        attached.append((logger, handler))
    return attached


def detach(attached: list[tuple[logging.Logger, QueueLogHandler]]) -> None:
    for logger, handler in attached:
        logger.removeHandler(handler)
```

- [x] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest jobfit/server/tests/test_logging_stream.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add jobfit/server/logging_stream.py jobfit/server/tests/test_logging_stream.py
git commit -m "feat: add queue-backed logging handler for streaming a run's log live"
```

---

### Task 17: `runner.py` — background run orchestration + history

**Files:**
- Create: `jobfit/server/runner.py`
- Test: `jobfit/server/tests/test_runner.py`

**Interfaces:**
- Consumes: `logging_stream.attach/detach` (Task 16), `update_jobs.scrape_stage`/`recompute_stage` (Task 7), `cv.load_profiles()` (Task 3), `config.RUN_HISTORY_PATH` (Task 5).
- Produces: `runner.start_run(force: bool) -> str`, `runner.status() -> dict`, `runner.log_queue() -> queue.Queue | None`, `runner.get_history() -> list[dict]`, `runner.mark_orphaned_runs_crashed() -> None`, `runner.is_running() -> bool`.

- [x] **Step 1: Write the failing tests**

Create `jobfit/server/tests/test_runner.py`:

```python
import json
import time

from jobfit import config
from jobfit.server import runner


def test_start_run_marks_running_then_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    monkeypatch.setattr(runner, "_LOGGER_NAMES", [])

    def _fake_scrape_stage(companies, profiles, force=False):
        from jobfit.scripts.update_jobs import RunStats
        return RunStats(companies_checked=1, companies_skipped=0, new_jobs=2, closed_jobs=0, failures=[])

    monkeypatch.setattr("jobfit.scripts.update_jobs.scrape_stage", _fake_scrape_stage)
    monkeypatch.setattr("jobfit.scripts.update_jobs.recompute_stage", lambda: None)
    monkeypatch.setattr("jobfit.scripts.update_jobs.cv.load_profiles", lambda: {})
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "companies_career_pages.json").write_text("{}", encoding="utf-8")

    run_id = runner.start_run(force=False)
    assert runner.status()["running"] is True
    assert runner.status()["run_id"] == run_id

    for _ in range(50):
        if not runner.is_running():
            break
        time.sleep(0.05)
    assert runner.is_running() is False

    history = runner.get_history()
    assert history[-1]["id"] == run_id
    assert history[-1]["new_jobs"] == 2
    assert history[-1]["finished_at"] is not None


def test_start_run_rejects_a_second_concurrent_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    with runner._lock:
        runner._state.update(running=True, run_id="already-running", started_at="x", queue=None)
    try:
        try:
            runner.start_run(force=False)
            assert False, "expected RuntimeError"
        except RuntimeError as error:
            assert "already-running" in str(error)
    finally:
        with runner._lock:
            runner._state.update(running=False, run_id=None, started_at=None, queue=None)


def test_mark_orphaned_runs_crashed_flags_unfinished_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUN_HISTORY_PATH", tmp_path / "run_history.json")
    config.RUN_HISTORY_PATH.write_text(json.dumps([
        {"id": "a", "started_at": "t1", "finished_at": None, "crashed": False},
        {"id": "b", "started_at": "t2", "finished_at": "t3", "crashed": False},
    ]), encoding="utf-8")

    runner.mark_orphaned_runs_crashed()

    history = runner.get_history()
    assert history[0]["crashed"] is True
    assert history[0]["finished_at"] == "t1"
    assert history[1]["crashed"] is False
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobfit.server.runner'`

- [x] **Step 3: Implement `runner.py`**

```python
"""Background execution of the on-demand scrape run, with live-log fan-out and history."""

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from jobfit import config
from jobfit.scripts import update_jobs
from jobfit.server.logging_stream import attach, detach

_LOGGER_NAMES = ["jobfit.update_jobs", "jobfit.ats", "jobfit.techmap", "jobfit.lm_bridge"]

_lock = threading.Lock()
_state = {"running": False, "run_id": None, "started_at": None, "queue": None}


def is_running() -> bool:
    with _lock:
        return _state["running"]


def status() -> dict:
    with _lock:
        return {"running": _state["running"], "run_id": _state["run_id"], "started_at": _state["started_at"]}


def log_queue():
    with _lock:
        return _state["queue"]


def _load_history() -> list[dict]:
    if config.RUN_HISTORY_PATH.exists():
        return json.loads(config.RUN_HISTORY_PATH.read_text(encoding="utf-8"))
    return []


def _save_history(history: list[dict]) -> None:
    config.RUN_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.RUN_HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def get_history() -> list[dict]:
    return _load_history()


def mark_orphaned_runs_crashed() -> None:
    """Call once at server startup: a history entry with no finished_at predates
    this process, so the server must have restarted mid-run."""
    history = _load_history()
    changed = False
    for entry in history:
        if entry.get("finished_at") is None:
            entry["finished_at"] = entry["started_at"]
            entry["crashed"] = True
            changed = True
    if changed:
        _save_history(history)


def start_run(force: bool) -> str:
    with _lock:
        if _state["running"]:
            raise RuntimeError(f"run {_state['run_id']} already active")
        run_id = uuid.uuid4().hex[:12]
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _state.update(running=True, run_id=run_id, started_at=started_at, queue=queue.Queue())

    history = _load_history()
    history.append({
        "id": run_id, "started_at": started_at, "finished_at": None, "trigger": "manual",
        "force": force, "companies_checked": 0, "companies_skipped": 0, "new_jobs": 0,
        "closed_jobs": 0, "failures": [], "duration_s": None, "crashed": False,
    })
    _save_history(history)

    thread = threading.Thread(target=_run_worker, args=(run_id, force), daemon=True)
    thread.start()
    return run_id


def _run_worker(run_id: str, force: bool) -> None:
    line_queue = log_queue()
    attached = attach(line_queue, _LOGGER_NAMES) if line_queue is not None else []
    started = time.time()
    try:
        all_companies = json.loads(config.ROOT.joinpath("companies_career_pages.json").read_text(encoding="utf-8"))
        companies = {name: url for name, url in all_companies.items() if url}
        profiles = update_jobs.cv.load_profiles()
        stats = update_jobs.scrape_stage(companies, profiles, force=force)
        update_jobs.recompute_stage()
        _finish_run(run_id, started, stats)
    finally:
        detach(attached)
        if line_queue is not None:
            line_queue.put(None)
        with _lock:
            _state.update(running=False, run_id=None, started_at=None, queue=None)


def _finish_run(run_id: str, started: float, stats) -> None:
    history = _load_history()
    for entry in history:
        if entry["id"] == run_id:
            entry.update(
                finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                companies_checked=stats.companies_checked,
                companies_skipped=stats.companies_skipped,
                new_jobs=stats.new_jobs,
                closed_jobs=stats.closed_jobs,
                failures=stats.failures,
                duration_s=round(time.time() - started, 1),
            )
            break
    _save_history(history)
```

Note `update_jobs.cv.load_profiles()` relies on `update_jobs.py` already importing `cv` at module level (it does — see its existing `from jobfit import ats_fetchers, config, connections, cv, scoring, techmap_source` import).

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_runner.py -v`
Expected: PASS (3 passed)

- [x] **Step 5: Commit**

```bash
git add jobfit/server/runner.py jobfit/server/tests/test_runner.py
git commit -m "feat: add background run orchestration with history and crash detection"
```

---

### Task 18: Wire `/api/run`, `/api/run/status`, `/api/run/stream`, `/api/run/history`

**Files:**
- Modify: `jobfit/server/app.py`

- [x] **Step 1: Add the routes**

```python
from fastapi.responses import StreamingResponse

from jobfit.server import runner


@app.on_event("startup")
def _on_startup() -> None:
    runner.mark_orphaned_runs_crashed()


@app.post("/api/run")
def api_start_run(payload: dict) -> dict:
    force = bool(payload.get("force", False))
    try:
        run_id = runner.start_run(force)
    except RuntimeError as error:
        raise HTTPException(409, str(error))
    return {"run_id": run_id, "status": "started"}


@app.get("/api/run/status")
def api_run_status() -> dict:
    return runner.status()


@app.get("/api/run/history")
def api_run_history() -> list[dict]:
    return runner.get_history()


@app.get("/api/run/stream")
def api_run_stream() -> StreamingResponse:
    line_queue = runner.log_queue()
    if line_queue is None:
        def _idle():
            yield "event: idle\ndata: no run active\n\n"
        return StreamingResponse(_idle(), media_type="text/event-stream")

    def _stream():
        while True:
            line = line_queue.get()
            if line is None:
                yield "event: done\ndata: run finished\n\n"
                break
            yield f"data: {line}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")
```

- [x] **Step 2: Verify manually**

```bash
uv run uvicorn jobfit.server.app:app --port 8787 &
sleep 1
curl -s http://127.0.0.1:8787/api/run/status
curl -s -X POST http://127.0.0.1:8787/api/run -H "Content-Type: application/json" -d '{"force": false}'
curl -s -N http://127.0.0.1:8787/api/run/stream --max-time 5
curl -s -X POST http://127.0.0.1:8787/api/run -H "Content-Type: application/json" -d '{"force": false}'
kill %1
```

Expected: first call shows `{"running": false, ...}`; the `POST /api/run` starts a run and returns a `run_id`; the SSE curl streams log lines for a few seconds; the second `POST /api/run` (while the first is likely still running against ~780 companies) returns HTTP 409.

- [x] **Step 3: Commit**

```bash
git add jobfit/server/app.py
git commit -m "feat: wire /api/run, /api/run/status, /api/run/stream, /api/run/history"
```

---

### Task 19: Panel UI — Run & Logs tab

**Files:**
- Modify: `jobfit/server/static/panel.html`

- [x] **Step 1: Add the tab button**

In the `.tabs` block, add:

```html
    <button class="tab-btn" data-tab="run">Run &amp; Logs</button>
```

- [x] **Step 2: Add the tab panel**

Before the closing `</body>`'s `<script>` tag's content ends, add this new `<section>` alongside the existing ones (e.g. after `#tab-referrals`):

```html
  <section id="tab-run" class="panel" hidden>
    <button id="runBtn">Run update</button>
    <label><input type="checkbox" id="runForce"> force (ignore recently-checked skip)</label>
    <div id="runStatus" class="msg"></div>
    <h3>Live log</h3>
    <pre id="runLog" style="background:#12161b;border:1px solid #232a32;border-radius:8px;padding:10px;height:260px;overflow-y:auto;font-size:12px;"></pre>
    <h3>History</h3>
    <div id="runHistory"></div>
  </section>
```

- [x] **Step 3: Add the run/log/history JS**

Add before the final `refreshDashboard(); refreshProfiles();` lines:

```javascript
async function refreshRunStatus() {
  const s = await (await fetch("/api/run/status")).json();
  document.getElementById("runStatus").textContent = s.running ? `Running (started ${s.started_at})` : "Idle";
  document.getElementById("runBtn").disabled = s.running;
  return s.running;
}

async function refreshRunHistory() {
  const history = await (await fetch("/api/run/history")).json();
  document.getElementById("runHistory").innerHTML = history.slice().reverse().map(r => `
    <div class="list-row">
      <span>${r.started_at} ${r.crashed ? "(crashed)" : ""}</span>
      <span>${r.new_jobs ?? 0} new, ${r.closed_jobs ?? 0} closed, ${(r.failures || []).length} failed</span>
    </div>`).join("");
}

let runEventSource = null;
function streamRunLog() {
  if (runEventSource) runEventSource.close();
  const logEl = document.getElementById("runLog");
  runEventSource = new EventSource("/api/run/stream");
  runEventSource.onmessage = (e) => {
    logEl.textContent += e.data + "\n";
    logEl.scrollTop = logEl.scrollHeight;
  };
  runEventSource.addEventListener("done", async () => {
    runEventSource.close();
    await refreshRunStatus();
    await refreshRunHistory();
    await refreshDashboard();
  });
}

document.getElementById("runBtn").addEventListener("click", async () => {
  const force = document.getElementById("runForce").checked;
  document.getElementById("runLog").textContent = "";
  const res = await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ force }),
  });
  if (res.status === 409) {
    document.getElementById("runStatus").textContent = "A run is already active.";
    return;
  }
  await refreshRunStatus();
  streamRunLog();
});

refreshRunStatus();
refreshRunHistory();
```

- [x] **Step 4: Verify manually in a browser**

```bash
uv run uvicorn jobfit.server.app:app --port 8787
```

Open `http://127.0.0.1:8787/`, go to "Run & Logs", click "Run update" with a small enough scope to watch end-to-end (see Task 20 for a way to point it at a tiny company set), confirm the log streams live and history/dashboard update when it finishes. Stop the server when done.

- [x] **Step 5: Commit**

```bash
git add jobfit/server/static/panel.html
git commit -m "feat: add Run & Logs tab with live SSE log and run history"
```

---

### Task 20: End-to-end manual verification of Phase 3

- [x] **Step 1: Point a throwaway `companies_career_pages.json` at 2-3 companies for a fast test run**

```bash
cp jobfit/companies_career_pages.json /tmp/companies_career_pages.full.json.bak
uv run python -c "
import json
from jobfit import config
all_companies = json.loads(config.ROOT.joinpath('companies_career_pages.json').read_text(encoding='utf-8'))
small = dict(list(all_companies.items())[:3])
config.ROOT.joinpath('companies_career_pages.json').write_text(json.dumps(small), encoding='utf-8')
"
```

- [x] **Step 2: Start the server and trigger a run from the browser**

```bash
uv run uvicorn jobfit.server.app:app --port 8787
```

In the browser: Run & Logs tab → "Run update" → confirm log lines stream for those 2-3 companies, the run finishes, history shows the new entry, and the Dashboard tab's numbers update.

- [x] **Step 3: Confirm `jobfit.html` was rebuilt**

```bash
ls -la jobfit.html
```

Expected: modification time matches when the run just finished.

- [x] **Step 4: Restore the full company list**

```bash
cp /tmp/companies_career_pages.full.json.bak jobfit/companies_career_pages.json
rm /tmp/companies_career_pages.full.json.bak
```

- [x] **Step 5: Run the full test suite one more time**

Run: `uv run python -m pytest jobfit/server/tests/ -v`
Expected: all tests passing.

No commit for this task (verification only; Step 4 restores tracked state so `git status` should show no changes from this task).

---

## Self-Review Notes

- **Spec coverage:** every spec section has a task — profile registry (2-3), migration (4), config/scoring (5-6), scrape/recompute split + TTL-skip + concurrency (7), dynamic HTML profiles (8), dependencies (10), dashboard (11), profiles/connections/referrals CRUD (12-14), panel UI (15, 19), run trigger/SSE/history (16-18), error handling (400s in 12-14, 409 in 18, crash-marking in 17), testing scope exactly matches the spec's three call-outs (TTL-skip, referral dedup, profile CRUD — Tasks 7, 14, 2/3).
- **Placeholder scan:** no TBD/TODO; every step has real code or a real shell command.
- **Type consistency:** `RunStats` (Task 7) is used identically in Task 17's `runner.py`; `cv.load_registry()`'s `{id: {name, filename, uploaded_at}}` shape is used identically in Tasks 3, 8, 11, 12; `merge_referral_jobs(profiles, path=None)` (Task 14) matches its call site in Task 14's own `/api/referrals` route.
- **Deviation flagged:** Task 7 removes `cv_hash()`-based rescore-gating and the `--force-rescore` CLI flag, which the spec didn't call out explicitly — this is a direct, necessary consequence of `recompute_stage()` unconditionally rescoring everything (as the spec's Components section describes it), not an unrelated scope change; documented inline in Task 7 and in its commit message.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-22-control-panel.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
