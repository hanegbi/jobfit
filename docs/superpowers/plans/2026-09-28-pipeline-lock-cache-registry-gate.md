# Pipeline Lock, Score-Cache Fingerprint, Identity Gate, Incremental Aggregate — Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make concurrent pipeline runs safe, make the score cache self-invalidate on scoring-code changes, stop new duplicate company files from being created, and make a scoped `update_jobs --company X` run finish in under a minute instead of six.

**Architecture:** Four independent, additive changes to `jobfit/scripts/update_jobs.py` and two new top-level modules (`jobfit/pipeline_lock.py`, `jobfit/company_registry.py`). No existing public function signature loses a parameter or changes meaning; every change is either a new module, a new optional parameter with a safe default, or a new field. `jobfit/server/singleton_lock.py` is deleted and its one caller (`jobfit/server/app.py`) is repointed at the new lock module.

**Tech Stack:** Python 3.13, pytest, no new third-party dependencies (pydantic is already a dependency via `jobfit.ats_scorer.config`, not otherwise needed here).

**Spec:** `docs/superpowers/specs/2026-09-28-scrape-compute-pipeline-redesign-design.md` — this plan implements spec section 2.1 (pipeline lock), section 2.5's identity gate only (not the full migration in section 5), section 2.2 (engine-fingerprinted cache), and section 2.3 (incremental aggregate). Spec section 8 calls these out as its build-sequencing steps 1–4, explicitly independent of steps 5–8 (the `jobfit/scrape/` package and the company-registry migration), which are separate plans.

## Global Constraints

- No LLM call anywhere in this plan's code (spec principle 1) — not applicable here since none of these four features touch scoring logic or scraping strategy, but stated for completeness.
- All file writes use `jobfit.atomic_io.write_json_atomic` (tmp-write-then-rename), never a plain `Path.write_text` for state files.
- Every new module lives at the top level of `jobfit/` (alongside `jobfit/scoring.py`, `jobfit/cv.py`), not under `jobfit/scripts/`, matching the existing convention that `scripts/` holds CLI entry points and top-level `jobfit/` holds importable library code.
- Tests live under `jobfit/server/tests/` and run via `uv run python -m pytest jobfit/server/tests -q` (existing convention; 276 tests currently pass — run the full suite after every task, not just the new file, since several tasks touch shared functions).
- Every test that exercises `save_company_file`, `diff_and_update`, `scrape_stage`, or `aggregate_to_jobs_v2` must monkeypatch `update_jobs.COMPANIES_DIR` (and, from Task 8 onward, `config.AGGREGATE_CACHE_DIR` / `config.JOBS_OUTPUT_META_JSON`) to a `tmp_path` — never let a test touch the real `jobfit/companies/`, `jobfit/data/`, or `jobfit/cache/` directories.
- Windows is the primary dev platform for this repo (see the environment note in every session) — the pid-liveness check must work on Windows (`tasklist`) and degrade sanely elsewhere (`os.kill(pid, 0)`), matching the existing pattern in `jobfit/server/singleton_lock.py`.

---

### Task 1: Pipeline lock core

**Files:**
- Create: `jobfit/pipeline_lock.py`
- Test: `jobfit/server/tests/test_pipeline_lock.py`

**Interfaces:**
- Produces: `class PipelineLock` (`__init__(self, path: Path, stage: str, scope: str = "all", wait: bool = False, poll_seconds: float = 5.0)`, usable as `with PipelineLock(...) as lock: ...`), `class PipelineBusy(RuntimeError)` (constructed as `PipelineBusy(holder: dict)`), module function `_pid_alive(pid: int) -> bool`. Later tasks import `from jobfit import pipeline_lock` and use `pipeline_lock.PipelineLock` / `pipeline_lock.PipelineBusy`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_pipeline_lock.py
"""PipelineLock guards jobfit/companies/*.json, data/jobs_v2.json and
jobfit.html from concurrent mutation across CLI runs, server-triggered
runs, and direct Python calls - see jobfit/pipeline_lock.py's own
docstring for the real bug this replaces (a scoped update_jobs run and a
concurrent recompute_stage() silently produced a stale jobs_v2.json for
one company, no error, no warning)."""

import json
import os

import pytest

from jobfit import pipeline_lock


def test_first_acquire_creates_lock_file_with_holder_info(tmp_path):
    path = tmp_path / ".pipeline.lock"
    with pipeline_lock.PipelineLock(path, stage="scrape", scope="all") as lock:
        assert lock is not None
        holder = json.loads(path.read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
        assert holder["stage"] == "scrape"
        assert holder["scope"] == "all"
        assert "started_at" in holder
        assert isinstance(holder["argv"], list)


def test_exit_releases_a_lock_this_process_owns(tmp_path):
    path = tmp_path / ".pipeline.lock"
    with pipeline_lock.PipelineLock(path, stage="scrape"):
        pass
    assert not path.exists()


def test_reentrant_nested_lock_same_pid_is_a_noop(tmp_path):
    path = tmp_path / ".pipeline.lock"
    with pipeline_lock.PipelineLock(path, stage="update") as outer:
        with pipeline_lock.PipelineLock(path, stage="scrape") as inner:
            assert inner is not None
            holder = json.loads(path.read_text(encoding="utf-8"))
            assert holder["stage"] == "update"  # inner never overwrote the outer's holder
        # inner's __exit__ must not have released the lock
        assert path.exists()
    assert not path.exists()  # outer's __exit__ releases it


def test_raises_busy_when_held_by_a_different_live_process(tmp_path, monkeypatch):
    path = tmp_path / ".pipeline.lock"
    path.write_text(json.dumps({
        "pid": 999999, "stage": "recompute", "scope": "all",
        "started_at": "2026-09-28T04:31:00Z", "argv": ["update_jobs", "--force-rescore"],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)

    with pytest.raises(pipeline_lock.PipelineBusy) as excinfo:
        with pipeline_lock.PipelineLock(path, stage="scrape"):
            pass
    message = str(excinfo.value)
    assert "recompute" in message
    assert "999999" in message
    assert "--wait" in message


def test_takes_over_a_stale_lock_from_a_dead_process(tmp_path, monkeypatch):
    path = tmp_path / ".pipeline.lock"
    path.write_text(json.dumps({
        "pid": 999999, "stage": "recompute", "scope": "all",
        "started_at": "2026-09-28T04:31:00Z", "argv": [],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid != 999999)

    with pipeline_lock.PipelineLock(path, stage="scrape"):
        holder = json.loads(path.read_text(encoding="utf-8"))
        assert holder["pid"] == os.getpid()
        assert holder["stage"] == "scrape"


def test_wait_true_polls_until_the_lock_is_released(tmp_path, monkeypatch):
    path = tmp_path / ".pipeline.lock"
    path.write_text(json.dumps({
        "pid": 999999, "stage": "recompute", "scope": "all",
        "started_at": "2026-09-28T04:31:00Z", "argv": [],
    }), encoding="utf-8")
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)

    calls = {"n": 0}
    real_sleep = pipeline_lock.time.sleep

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] >= 2:
            path.unlink()  # the "other process" finishes and releases
        # no real sleeping - keep the test fast

    monkeypatch.setattr(pipeline_lock.time, "sleep", fake_sleep)

    with pipeline_lock.PipelineLock(path, stage="scrape", wait=True, poll_seconds=0.01):
        pass
    assert calls["n"] >= 2


def test_exit_does_not_release_a_lock_owned_by_a_different_process(tmp_path):
    path = tmp_path / ".pipeline.lock"
    lock = pipeline_lock.PipelineLock(path, stage="scrape")
    lock.__enter__()
    # simulate another process having taken over between enter and exit -
    # exercise the guard directly rather than racing a real process
    path.write_text(json.dumps({"pid": 999999, "stage": "x", "scope": "x", "started_at": "x", "argv": []}), encoding="utf-8")
    lock.__exit__(None, None, None)
    assert path.exists()  # not ours anymore - must not delete it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_pipeline_lock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.pipeline_lock'`

- [ ] **Step 3: Write the implementation**

```python
# jobfit/pipeline_lock.py
"""Cross-process lock guarding jobfit's mutating pipeline stages (scrape,
referral merge, recompute, aggregate, the Workday import) so at most one
of them touches jobfit/companies/*.json, data/jobs_v2.json, or
jobfit.html at a time - whether it was started from the CLI, the control
panel server, or a direct Python call.

Real bug this replaces: a scoped `update_jobs --company X --force` ran
while a full recompute_stage() was still mid-flight. The scoped run
correctly wrote status: closed into companies/x.json, but
recompute_stage()'s own single-threaded aggregate_to_jobs_v2() had
already read that file before the edit landed, so data/jobs_v2.json
silently carried stale data for that one company - no error, no
warning, just wrong output. jobfit/server/singleton_lock.py only
protected two server processes against each other; the CLI and any
direct Python call to recompute_stage() bypassed it entirely.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


class PipelineBusy(RuntimeError):
    """Raised when a PipelineLock's file is held by a different, live process."""

    def __init__(self, holder: dict):
        self.holder = holder
        argv = " ".join(holder.get("argv") or [])
        super().__init__(
            f"pipeline busy: {holder.get('stage')} (scope={holder.get('scope')}) "
            f"started {holder.get('started_at')} by pid {holder.get('pid')}"
            + (f" ({argv})" if argv else "")
            + ". Wait for it to finish, or pass --wait to queue behind it. "
              "The lock file self-heals once that process exits - never delete it by hand."
        )


def _pid_alive(pid: int) -> bool:
    """True if a process with this pid currently exists. Fails safe (returns
    True, "assume alive, refuse to take over") when liveness can't be
    determined at all."""
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
        except OSError:
            return True
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    except OSError:
        return True
    return True


class PipelineLock:
    """Context manager guarding one lock file.

    Re-entrant within the same process: a nested `with PipelineLock(...)`
    at the same path, while this process already holds it, is a no-op -
    it does not rewrite the holder info and its __exit__ does not
    release. Only the outermost acquire in a process actually writes and
    later removes the file.
    """

    def __init__(self, path: Path, stage: str, scope: str = "all", wait: bool = False, poll_seconds: float = 5.0):
        self.path = path
        self.stage = stage
        self.scope = scope
        self.wait = wait
        self.poll_seconds = poll_seconds
        self._owns = False

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        holder = {
            "pid": os.getpid(),
            "stage": self.stage,
            "scope": self.scope,
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "argv": sys.argv,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(holder), encoding="utf-8")
        tmp.replace(self.path)

    def __enter__(self) -> "PipelineLock":
        last_log = 0.0
        while True:
            holder = self._read()
            if holder is None:
                self._write()
                self._owns = True
                return self
            if holder.get("pid") == os.getpid():
                return self  # re-entrant: already held by this process
            if not _pid_alive(holder.get("pid", -1)):
                self._write()
                self._owns = True
                return self
            if not self.wait:
                raise PipelineBusy(holder)
            now = time.time()
            if now - last_log >= 60:
                print(
                    f"pipeline busy ({holder.get('stage')}, pid {holder.get('pid')}) - waiting...",
                    file=sys.stderr,
                )
                last_log = now
            time.sleep(self.poll_seconds)

    def __exit__(self, *exc) -> None:
        if not self._owns:
            return
        try:
            if self.path.exists():
                holder = self._read()
                if holder is not None and holder.get("pid") == os.getpid():
                    self.path.unlink()
        except OSError:
            pass
        self._owns = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_pipeline_lock.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/pipeline_lock.py jobfit/server/tests/test_pipeline_lock.py
git commit -m "feat: add PipelineLock, a re-entrant cross-process lock for jobfit's mutating pipeline stages"
```

---

### Task 2: Wire the pipeline lock into update_jobs.py

**Files:**
- Modify: `jobfit/scripts/update_jobs.py` (`scrape_stage` at line 517, `merge_referral_jobs` at line 674, `recompute_stage` at line 612, `aggregate_to_jobs_v2` at line 797, `main` at line 847)
- Modify: `jobfit/config.py` (add `PIPELINE_LOCK_PATH`)
- Test: `jobfit/server/tests/test_pipeline_lock_wiring.py`

**Interfaces:**
- Consumes: `pipeline_lock.PipelineLock`, `pipeline_lock.PipelineBusy` (Task 1).
- Produces: `config.PIPELINE_LOCK_PATH: Path`; `main()` gains a `--wait` CLI flag; the four stage functions now raise `pipeline_lock.PipelineBusy` (a `RuntimeError`) instead of running when another pipeline stage is active elsewhere.

- [ ] **Step 1: Write the failing test**

```python
# jobfit/server/tests/test_pipeline_lock_wiring.py
"""Each mutating stage function takes the pipeline lock itself, not just
main() - so a scoped/standalone call (a REPL, a scratch script, another
module) is covered too, which is the whole point: the real incident this
guards against was exactly a standalone recompute_stage() call racing a
CLI run, not two CLI invocations."""

import json

import pytest

from jobfit import config, pipeline_lock
from jobfit.scripts import update_jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    return companies_dir


def test_aggregate_to_jobs_v2_raises_busy_when_lock_already_held(isolated, monkeypatch):
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)
    config.PIPELINE_LOCK_PATH.write_text(json.dumps({
        "pid": 999999, "stage": "recompute", "scope": "all",
        "started_at": "x", "argv": [],
    }), encoding="utf-8")

    with pytest.raises(pipeline_lock.PipelineBusy):
        update_jobs.aggregate_to_jobs_v2()


def test_recompute_stage_holds_the_lock_around_its_own_aggregate_call(isolated, monkeypatch):
    """recompute_stage() calls aggregate_to_jobs_v2() internally - that
    nested call must re-enter the SAME lock, not deadlock or double-acquire."""
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    monkeypatch.setattr(update_jobs, "RECOMPUTE_WORKERS", 1)

    update_jobs.recompute_stage()  # must not raise, must not hang

    assert not config.PIPELINE_LOCK_PATH.exists()  # released on exit


def test_scrape_stage_releases_lock_after_completion(isolated, monkeypatch):
    monkeypatch.setattr(update_jobs.ats_fetchers, "make_session", lambda: None)
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    monkeypatch.setattr(update_jobs, "_process_company", lambda *a, **kw: (a[0], 0, 0, True, None))

    update_jobs.scrape_stage({}, {})

    assert not config.PIPELINE_LOCK_PATH.exists()


def test_main_wait_flag_is_parsed(monkeypatch):
    monkeypatch.setattr(update_jobs, "load_companies_to_scrape", lambda: {})
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    monkeypatch.setattr(update_jobs, "scrape_stage", lambda *a, **kw: update_jobs.RunStats())
    monkeypatch.setattr(update_jobs, "load_meta", lambda: {})
    monkeypatch.setattr(update_jobs, "save_meta", lambda meta: None)
    monkeypatch.setattr("sys.argv", ["update_jobs", "--skip-aggregate", "--wait"])

    update_jobs.main()  # must not raise (argparse must accept --wait)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest jobfit/server/tests/test_pipeline_lock_wiring.py -v`
Expected: FAIL - `test_aggregate_to_jobs_v2_raises_busy_when_lock_already_held` fails because nothing raises `PipelineBusy` yet; `test_main_wait_flag_is_parsed` fails with `SystemExit` (argparse rejects unknown `--wait`).

- [ ] **Step 3: Add the config constant**

In `jobfit/config.py`, after the existing `RUN_HISTORY_PATH = ROOT / "data" / "run_history.json"` line (line 31):

```python
PIPELINE_LOCK_PATH = ROOT / "data" / ".pipeline.lock"
```

- [ ] **Step 4: Wrap the four stage functions and add the CLI flag**

In `jobfit/scripts/update_jobs.py`, add the import at the top (with the other `from jobfit import ...` line, line 37):

```python
from jobfit import ats_fetchers, company_review, config, connections, cv, pipeline_lock, scoring, techmap_source, translation  # noqa: E402
```

Wrap `scrape_stage`'s body (currently lines 530-559) by indenting it one level under a `with` block. Change the function from:

```python
def scrape_stage(
    companies: dict[str, str], profiles: dict, force: bool = False, cancel_event=None
) -> RunStats:
    """..."""
    session = ats_fetchers.make_session()
    ...
    return stats
```

to:

```python
def scrape_stage(
    companies: dict[str, str], profiles: dict, force: bool = False, cancel_event=None
) -> RunStats:
    """... (docstring unchanged)"""
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="scrape", scope=f"{len(companies)} companies"):
        session = ats_fetchers.make_session()
        ...
        return stats
```

(i.e. indent the existing body one level and put it under the `with`; do not change any line's content, only its indentation.)

Do the same for `recompute_stage` (currently lines 612-671): wrap its body under `with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="recompute", scope="all" if not force else "all (forced)"):`.

Do the same for `aggregate_to_jobs_v2` (currently lines 797-844): wrap its body under `with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="aggregate", scope="all"):`.

Do the same for `merge_referral_jobs` (currently lines 674-781): wrap its body under `with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="referral-merge", scope="all"):`.

In `main()` (line 847), add the flag and wrap the whole body:

```python
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process the first N companies (testing)")
    parser.add_argument("--company", type=str, default=None, help="only process this one company (exact name match)")
    parser.add_argument("--force", action="store_true", help="re-check companies even if checked recently")
    parser.add_argument("--skip-aggregate", action="store_true", help="don't rescore/rebuild after updating")
    parser.add_argument("--wait", action="store_true", help="wait for another pipeline run to finish instead of exiting")
    args = parser.parse_args()

    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="update", scope=args.company or "all", wait=args.wait):
        companies = load_companies_to_scrape()

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

(Only change from the current body: the new `--wait` argument, and indenting everything from `companies = load_companies_to_scrape()` onward one level under the new `with` block.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_pipeline_lock_wiring.py -v`
Expected: 4 passed

- [ ] **Step 6: Run the full test suite to check for regressions**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all tests pass (276 previously-existing + new ones). If any existing test that calls `scrape_stage`/`recompute_stage`/`aggregate_to_jobs_v2`/`merge_referral_jobs` fails, it is missing a `monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")` - add that line to its fixture (do not skip or weaken the test).

- [ ] **Step 7: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/config.py jobfit/server/tests/test_pipeline_lock_wiring.py
git commit -m "feat: wrap scrape_stage/merge_referral_jobs/recompute_stage/aggregate_to_jobs_v2 in the pipeline lock"
```

---

### Task 3: Migrate the server to the pipeline lock; delete singleton_lock.py

**Files:**
- Modify: `jobfit/server/app.py` (lines 13, 23, 26)
- Modify: `jobfit/server/runner.py` (`start_run` at line 81, `_run_worker` at line 107)
- Modify: `jobfit/scripts/import_workday_sources.py`
- Delete: `jobfit/server/singleton_lock.py`
- Delete: `jobfit/server/tests/test_singleton_lock.py`
- Test: extend `jobfit/server/tests/test_runner.py`, `jobfit/server/tests/test_app.py`

**Interfaces:**
- Consumes: `pipeline_lock.PipelineLock`, `pipeline_lock.PipelineBusy` (Task 1); `config.PIPELINE_LOCK_PATH` (Task 2).
- Produces: `POST /api/run` returns HTTP 409 with the `PipelineBusy` message when a CLI run holds the lock, in addition to its existing 409 for "a server run is already active".

- [ ] **Step 1: Read the current runner/app wiring to confirm exact lines**

Run: `grep -n "singleton_lock\|_run_worker\|start_run" jobfit/server/app.py jobfit/server/runner.py`
Expected output should match: `app.py` lines 13 (import), 23 (`acquire`), 26 (`release`); `runner.py` `start_run` at line 81, `_run_worker` at line 107. If line numbers differ (this file may have moved since this plan was written), locate the functions by name instead - the edits below are described by what to change, not only by line number.

- [ ] **Step 2: Write the failing tests**

Add to `jobfit/server/tests/test_runner.py` (read the existing file first to match its fixture style - it will already have a way to isolate `runner._state`/history paths; reuse that pattern):

```python
def test_start_run_raises_when_another_pipeline_run_holds_the_lock(monkeypatch, tmp_path):
    """A CLI `update_jobs` run holding the pipeline lock must be visible to
    the server as a conflict, not just other server-triggered runs."""
    import json
    from jobfit import config, pipeline_lock
    from jobfit.server import runner

    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)
    config.PIPELINE_LOCK_PATH.write_text(json.dumps({
        "pid": 999999, "stage": "update", "scope": "all", "started_at": "x", "argv": ["update_jobs"],
    }), encoding="utf-8")

    with pytest.raises(RuntimeError, match="pipeline busy"):
        runner.start_run(force=False)
```

Add to `jobfit/server/tests/test_app.py` (read the existing file first for its `TestClient` setup pattern):

```python
def test_api_run_returns_409_when_pipeline_lock_is_held(client, monkeypatch, tmp_path):
    import json
    from jobfit import config, pipeline_lock

    monkeypatch.setattr(config, "PIPELINE_LOCK_PATH", tmp_path / ".pipeline.lock")
    monkeypatch.setattr(pipeline_lock, "_pid_alive", lambda pid: pid == 999999)
    config.PIPELINE_LOCK_PATH.write_text(json.dumps({
        "pid": 999999, "stage": "update", "scope": "all", "started_at": "x", "argv": ["update_jobs"],
    }), encoding="utf-8")

    response = client.post("/api/run", json={})
    assert response.status_code == 409
    assert "pipeline busy" in response.json()["detail"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_runner.py jobfit/server/tests/test_app.py -v -k "pipeline"`
Expected: FAIL - `start_run` does not check the pipeline lock yet, so no `RuntimeError` is raised.

- [ ] **Step 4: Update runner.py**

In `jobfit/server/runner.py`, add the import at the top of the file (alongside the existing imports):

```python
from jobfit import config, pipeline_lock
```

In `start_run` (the function starting at line 81), add an eager, fail-fast check before the existing `with _lock:` block, so a busy CLI run is reported synchronously (mapped to 409 by `app.py`'s existing `except RuntimeError` handler) instead of only failing invisibly inside the background thread:

```python
def start_run(force: bool, companies: list[str] | None = None) -> str:
    """companies=None scrapes every company in the bank (the normal "Run
    update" button); a list scopes the scrape to just those names - e.g. the
    companies a referral upload just added, so you don't have to re-check
    everything else to pick up a handful of new ones."""
    # Fail fast and synchronously if a CLI run (or another process) holds
    # the pipeline lock - PipelineBusy is a RuntimeError, so app.py's
    # existing `except RuntimeError` -> 409 handles it with no extra code.
    # This is a best-effort check (a TOCTOU race with the thread started
    # below is possible and acceptable); the real enforcement is the lock
    # scrape_stage/recompute_stage take themselves, inside _run_worker.
    probe = pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="update", scope="probe")
    probe.__enter__()
    probe.__exit__(None, None, None)

    with _lock:
        if _state["running"]:
            raise RuntimeError(f"run {_state['run_id']} already active")
        run_id = uuid.uuid4().hex[:12]
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cancel_event = threading.Event()
        _state.update(running=True, run_id=run_id, started_at=started_at, queue=queue.Queue(), cancel_event=cancel_event)

    history = _load_history()
    history.append({
        "id": run_id, "started_at": started_at, "finished_at": None, "trigger": "manual",
        "force": force, "companies": companies, "companies_checked": 0, "companies_skipped": 0,
        "new_jobs": 0, "closed_jobs": 0, "failures": [], "duration_s": None, "crashed": False, "stopped": False,
    })
    _save_history(history)

    thread = threading.Thread(target=_run_worker, args=(run_id, force, companies, cancel_event), daemon=True)
    thread.start()
    return run_id
```

In `_run_worker` (the function starting at line 107), catch `PipelineBusy` so an unlikely race (the probe above passed, but the lock was taken between the probe and `scrape_stage`'s own acquire) still records a finished/crashed run instead of leaving `_state["running"]` stuck `True` forever:

```python
def _run_worker(run_id: str, force: bool, companies: list[str] | None, cancel_event: threading.Event) -> None:
    line_queue = log_queue()
    attached = attach(line_queue, _LOGGER_NAMES) if line_queue is not None else []
    started = time.time()
    try:
        scrape_targets = update_jobs.load_companies_to_scrape()
        if companies is not None:
            wanted = set(companies)
            scrape_targets = {name: url for name, url in scrape_targets.items() if name in wanted}
        profiles = update_jobs.cv.load_profiles()
        stats = update_jobs.scrape_stage(scrape_targets, profiles, force=force, cancel_event=cancel_event)
        update_jobs.recompute_stage()
        _finish_run(run_id, started, stats, stopped=cancel_event.is_set())
    except pipeline_lock.PipelineBusy as error:
        logger.warning("run %s aborted: %s", run_id, error)
        _finish_run(run_id, started, update_jobs.RunStats(failures=[str(error)]), stopped=True)
    finally:
        detach(attached)
        if line_queue is not None:
            line_queue.put(None)
        with _lock:
            _state.update(running=False, run_id=None, started_at=None, queue=None, cancel_event=None)
```

(This assumes `runner.py` already has a module-level `logger` - check with `grep -n "^logger" jobfit/server/runner.py`; if it uses a different name, e.g. imports `logging` directly, match that file's existing logging call style instead of introducing a new one.)

- [ ] **Step 5: Delete singleton_lock.py and repoint app.py**

Delete `jobfit/server/singleton_lock.py` and `jobfit/server/tests/test_singleton_lock.py`.

In `jobfit/server/app.py`, change line 13 from:

```python
from jobfit.server import dashboard, runner, singleton_lock
```

to:

```python
from jobfit.server import dashboard, runner
from jobfit import pipeline_lock
```

Change the `_lifespan` function (lines 19-26) from:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Two server processes writing companies/*.json and profiles.json at once
    # silently corrupt/lose data - see singleton_lock's own docstring.
    singleton_lock.acquire(LOCK_PATH)
    runner.mark_orphaned_runs_crashed()
    yield
    singleton_lock.release(LOCK_PATH)
```

to:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Two server processes writing companies/*.json and profiles.json at once
    # silently corrupt/lose data - see pipeline_lock's own docstring. This
    # reuses the same lock mechanism as the pipeline stages, at a different
    # path and for a different purpose (one whole server instance, for its
    # entire lifetime, rather than one mutating stage at a time).
    server_lock = pipeline_lock.PipelineLock(LOCK_PATH, stage="server", scope="instance")
    server_lock.__enter__()
    runner.mark_orphaned_runs_crashed()
    yield
    server_lock.__exit__(None, None, None)
```

`LOCK_PATH` (line 16, `config.ROOT / "data" / ".server.lock"`) is unchanged - it stays a separate file from `config.PIPELINE_LOCK_PATH`.

- [ ] **Step 6: Wire import_workday_sources.py**

Read `jobfit/scripts/import_workday_sources.py` to find its `main()` (or module-level script body). Wrap the mutating part of it (the part that calls `save_company_file`/writes to `companies/*.json`) in `with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="import-workday", scope="all"):`, importing `from jobfit import config, pipeline_lock` at the top. If the script has no single `main()` function and instead runs at module level under `if __name__ == "__main__":`, wrap that block's body instead.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all tests pass, `test_singleton_lock.py` no longer collected (deleted), the two new pipeline-conflict tests pass.

- [ ] **Step 8: Commit**

```bash
git add jobfit/server/app.py jobfit/server/runner.py jobfit/scripts/import_workday_sources.py jobfit/server/tests/test_runner.py jobfit/server/tests/test_app.py
git rm jobfit/server/singleton_lock.py jobfit/server/tests/test_singleton_lock.py
git commit -m "feat: migrate the server-instance lock and import_workday_sources to PipelineLock, delete singleton_lock.py"
```

---

### Task 4: CompanyRegistry core

**Files:**
- Create: `jobfit/company_registry.py`
- Test: `jobfit/server/tests/test_company_registry.py`

**Interfaces:**
- Consumes: `connections.normalize_company` (existing, `jobfit/connections.py:16`).
- Produces: `class CompanyRegistry` with `load(cls, registry_path: Path, companies_dir: Path) -> "CompanyRegistry"` (classmethod), `resolve(self, name: str, url: str | None = None) -> str | None`, `register_if_new(self, company_id: str, display_name: str, career_url: str | None) -> str | None` (returns the conflicting id, or `None` on success), `check_invariants(self) -> list[str]`, `save(self) -> None`; module function `get_registry(companies_dir: Path, registry_path: Path) -> CompanyRegistry` (path-keyed cache); `class DuplicateCompany(RuntimeError)`. Task 5 imports `from jobfit import company_registry` and calls `company_registry.get_registry(...)`.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_company_registry.py
"""CompanyRegistry gives every company exactly one id, derived by one of
four resolution tiers (exact normalized name, career-page host, a looser
name key that also strips parentheticals and .com/.io/.ai/.co, or - not
implemented until the full migration - a hand-reviewed alias). Measured
live on the real data (2026-09-28): 111 groups of company files already
collapse under the normalized-name key alone, 168 under the loose key,
174 by shared career-page host - this class is what a future migration
uses to actually merge them; for now (this plan) it only prevents NEW
collisions via register_if_new, seeded read-only from whatever already
exists on disk."""

import json

import pytest

from jobfit import company_registry


@pytest.fixture
def companies_dir(tmp_path):
    d = tmp_path / "companies"
    d.mkdir()
    return d


def _write_company(companies_dir, stem, name, career_url=None):
    (companies_dir / f"{stem}.json").write_text(
        json.dumps({"name": name, "career_url": career_url, "last_checked": None, "jobs": []}),
        encoding="utf-8",
    )


def test_load_seeds_one_entry_per_existing_company_file(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "wiz", "Wiz", "https://wiz.io/careers")

    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Acme Corp") == "acme"
    assert registry.resolve("Wiz") == "wiz"


def test_load_persists_the_seed_so_a_second_load_does_not_reread_company_files(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    registry_path = tmp_path / "registry.json"

    company_registry.CompanyRegistry.load(registry_path, companies_dir)
    assert registry_path.exists()

    # Remove the company file; a second load must still know about "acme"
    # because it now reads from the persisted registry, not the (now-empty)
    # companies_dir.
    (companies_dir / "acme.json").unlink()
    registry = company_registry.CompanyRegistry.load(registry_path, companies_dir)
    assert registry.resolve("Acme Corp") == "acme"


def test_resolve_matches_by_normalized_name(companies_dir, tmp_path):
    _write_company(companies_dir, "apiiro", "Apiiro", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Apiiro Ltd.") == "apiiro"  # "Ltd." stripped by normalize_company


def test_resolve_matches_by_career_url_host(companies_dir, tmp_path):
    _write_company(companies_dir, "mondaycom", "monday.com", "https://monday.com/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Monday.com Ltd. (Formerly DaPulse)", "https://monday.com/careers/some-job") == "mondaycom"


def test_resolve_matches_by_loose_key_stripping_parentheticals(companies_dir, tmp_path):
    _write_company(companies_dir, "mondaycom", "monday.com", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Monday.com Ltd. (Formerly DaPulse)") == "mondaycom"


def test_resolve_returns_none_for_a_genuinely_new_company(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.resolve("Totally Different Co", "https://totallydifferent.example/careers") is None


def test_register_if_new_adds_a_genuinely_new_company(companies_dir, tmp_path):
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("newco", "New Co", "https://newco.example/careers")

    assert conflict is None
    assert registry.resolve("New Co") == "newco"


def test_register_if_new_returns_the_conflicting_id_without_registering(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("acme_corp_ltd", "Acme Corp Ltd.", "https://acme.com/careers/jobs")

    assert conflict == "acme"
    assert registry.resolve("Acme Corp Ltd.") == "acme"  # still resolves via normalize_company, no new entry


def test_register_if_new_is_a_noop_when_the_id_is_already_registered(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", None)
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    conflict = registry.register_if_new("acme", "Acme Corp", None)

    assert conflict is None  # updating the existing entry, not creating a new one


def test_check_invariants_is_empty_for_a_clean_registry(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "wiz", "Wiz", "https://wiz.io/careers")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    assert registry.check_invariants() == []


def test_check_invariants_reports_a_shared_host(companies_dir, tmp_path):
    _write_company(companies_dir, "acme", "Acme Corp", "https://acme.com/careers")
    _write_company(companies_dir, "acme_other", "Acme Other Spelling", "https://acme.com/jobs")
    registry = company_registry.CompanyRegistry.load(tmp_path / "registry.json", companies_dir)

    problems = registry.check_invariants()

    assert len(problems) == 1
    assert "acme" in problems[0] and "acme_other" in problems[0]


def test_get_registry_caches_by_registry_path(tmp_path, companies_dir):
    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"

    reg_a1 = company_registry.get_registry(companies_dir, path_a)
    reg_a2 = company_registry.get_registry(companies_dir, path_a)
    reg_b = company_registry.get_registry(companies_dir, path_b)

    assert reg_a1 is reg_a2  # same path -> cached instance
    assert reg_a1 is not reg_b  # different path -> independent instance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_company_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobfit.company_registry'`

- [ ] **Step 3: Write the implementation**

```python
# jobfit/company_registry.py
"""One company, one id. Resolves a display name (and optionally a
career-page URL) to a stable company id through exactly one function
(resolve), so nothing else in the codebase invents its own ad hoc
name-matching. Backed by jobfit/data/company_registry.json, seeded
read-only from whatever companies/*.json files already exist the first
time it's loaded.

This module does NOT merge existing duplicate company files - that's a
separate, higher-risk migration (see the design spec, section 5) because
it re-keys every job id and needs a localStorage migration in the SPA.
What this module DOES do, today: register_if_new refuses to let a NEW
company file be created when it would collide (by normalized name, by
career-page host, or by a looser name key) with a DIFFERENT company that
already exists - so the ~110-170 duplicate pairs already in the corpus
don't keep growing while the real migration is designed and reviewed.

Real bug this is scoped around: 'monday.com' and 'Monday.com Ltd.
(Formerly DaPulse)' are the same real company, split into two files
because they entered the pipeline from two different sources
(a curated career-page URL and techmap's own company registry) with two
different spellings, and nothing checked whether a "new" company was
actually new.
"""

import json
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from jobfit import connections
from jobfit.atomic_io import write_json_atomic

_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")
_DOMAIN_SUFFIX_RE = re.compile(r"\.(com|io|ai|co)\b", re.IGNORECASE)


def _loose_key(name: str) -> str:
    """A looser match than connections.normalize_company alone: also drops
    parenthetical asides ("(Formerly DaPulse)") and common domain suffixes
    that sometimes leak into a company's display name."""
    stripped = _PARENTHETICAL_RE.sub(" ", name or "")
    stripped = _DOMAIN_SUFFIX_RE.sub(" ", stripped)
    return connections.normalize_company(stripped)


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None


@dataclass
class CompanyEntry:
    id: str
    display_name: str
    career_url: str | None
    host: str | None


class DuplicateCompany(RuntimeError):
    def __init__(self, name: str, attempted_id: str, conflict_id: str):
        self.name = name
        self.attempted_id = attempted_id
        self.conflict_id = conflict_id
        super().__init__(
            f"refusing to create a new company file {attempted_id!r} for {name!r}: "
            f"it looks like the same company as the existing {conflict_id!r} "
            "(same normalized name or career-page host). "
            f"Check jobfit/companies/{conflict_id}.json - if this really is a "
            "different company, this gate has no manual override yet (that "
            "lands with the full registry migration)."
        )


class CompanyRegistry:
    def __init__(self, path: Path, entries: dict[str, CompanyEntry]):
        self.path = path
        self._entries = entries
        self._by_key: dict[str, str] = {}
        self._by_host: dict[str, str] = {}
        self._by_loose_key: dict[str, str] = {}
        for entry in entries.values():
            self._index(entry)
        self._lock = threading.Lock()

    def _index(self, entry: CompanyEntry) -> None:
        key = connections.normalize_company(entry.display_name)
        if key:
            self._by_key.setdefault(key, entry.id)
        if entry.host:
            self._by_host.setdefault(entry.host, entry.id)
        loose = _loose_key(entry.display_name)
        if loose:
            self._by_loose_key.setdefault(loose, entry.id)

    @classmethod
    def load(cls, registry_path: Path, companies_dir: Path) -> "CompanyRegistry":
        if registry_path.exists():
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            entries = {e["id"]: CompanyEntry(**e) for e in data.get("companies", [])}
            return cls(registry_path, entries)
        registry = cls._seed_from_company_files(registry_path, companies_dir)
        registry.save()
        return registry

    @classmethod
    def _seed_from_company_files(cls, registry_path: Path, companies_dir: Path) -> "CompanyRegistry":
        entries: dict[str, CompanyEntry] = {}
        if companies_dir.exists():
            for path in sorted(companies_dir.glob("*.json")):
                if path.name == "_meta.json":
                    continue
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                career_url = record.get("career_url")
                entries[path.stem] = CompanyEntry(
                    id=path.stem, display_name=record.get("name", path.stem),
                    career_url=career_url, host=_host_of(career_url),
                )
        return cls(registry_path, entries)

    def save(self) -> None:
        write_json_atomic(self.path, {
            "schema_version": 1,
            "companies": [asdict(e) for e in self._entries.values()],
        })

    def resolve(self, name: str, url: str | None = None) -> str | None:
        key = connections.normalize_company(name)
        if key and key in self._by_key:
            return self._by_key[key]
        host = _host_of(url)
        if host and host in self._by_host:
            return self._by_host[host]
        loose = _loose_key(name)
        if loose and loose in self._by_loose_key:
            return self._by_loose_key[loose]
        return None

    def register_if_new(self, company_id: str, display_name: str, career_url: str | None) -> str | None:
        """If company_id is not yet registered, register it unless doing so
        would collide with a DIFFERENT existing entry - in which case,
        return that entry's id without registering anything. Returns None
        on success (including when company_id was already registered)."""
        with self._lock:
            if company_id in self._entries:
                return None
            conflict = self.resolve(display_name, career_url)
            if conflict is not None:
                return conflict
            entry = CompanyEntry(company_id, display_name, career_url, _host_of(career_url))
            self._entries[company_id] = entry
            self._index(entry)
            self.save()
            return None

    def check_invariants(self) -> list[str]:
        problems = []
        seen_keys: dict[str, str] = {}
        seen_hosts: dict[str, str] = {}
        for entry in self._entries.values():
            key = connections.normalize_company(entry.display_name)
            if key:
                if key in seen_keys and seen_keys[key] != entry.id:
                    problems.append(f"{entry.id!r} and {seen_keys[key]!r} share normalized name key {key!r}")
                else:
                    seen_keys.setdefault(key, entry.id)
            if entry.host:
                if entry.host in seen_hosts and seen_hosts[entry.host] != entry.id:
                    problems.append(f"{entry.id!r} and {seen_hosts[entry.host]!r} share career-page host {entry.host!r}")
                else:
                    seen_hosts.setdefault(entry.host, entry.id)
        return problems


_registry_cache: dict[Path, CompanyRegistry] = {}
_cache_lock = threading.Lock()


def get_registry(companies_dir: Path, registry_path: Path) -> CompanyRegistry:
    """Path-keyed cache: a distinct registry_path always gets its own
    instance, so tests (each using a distinct tmp_path) never share state
    with each other or with production, while production - which always
    derives the same registry_path from the real COMPANIES_DIR - only
    pays the seed-from-disk cost once per process."""
    with _cache_lock:
        if registry_path not in _registry_cache:
            _registry_cache[registry_path] = CompanyRegistry.load(registry_path, companies_dir)
        return _registry_cache[registry_path]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_company_registry.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add jobfit/company_registry.py jobfit/server/tests/test_company_registry.py
git commit -m "feat: add CompanyRegistry - one company, one id, via exactly one resolve() function"
```

---

### Task 5: Wire the identity gate into save_company_file

**Files:**
- Modify: `jobfit/scripts/update_jobs.py` (`save_company_file` at line 119)
- Test: `jobfit/server/tests/test_company_registry_gate.py`

**Interfaces:**
- Consumes: `company_registry.get_registry`, `company_registry.DuplicateCompany` (Task 4).
- Produces: `save_company_file(company, data)` now raises `company_registry.DuplicateCompany` instead of silently creating a second file for a company that already exists under a different spelling or the same career-page host.

- [ ] **Step 1: Write the failing tests**

```python
# jobfit/server/tests/test_company_registry_gate.py
"""save_company_file is the single place every new companies/*.json file
gets created (directly, and via merge_referral_jobs -> load_company_file
+ save_company_file). Gating it here - rather than each caller
separately - means merge_referral_jobs benefits from host-based and
loose-key matching too, even though its own inline matching (existing
code, unchanged by this plan) only ever checked normalized names."""

import json

import pytest

from jobfit import company_registry
from jobfit.scripts import update_jobs


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    company_registry._registry_cache.clear()
    return companies_dir


def _write_company(companies_dir, stem, name, career_url=None):
    (companies_dir / f"{stem}.json").write_text(
        json.dumps({"name": name, "career_url": career_url, "last_checked": None, "jobs": []}),
        encoding="utf-8",
    )


def test_save_company_file_allows_updating_an_existing_company(isolated):
    _write_company(isolated, "acme", "Acme Corp", "https://acme.com/careers")

    update_jobs.save_company_file("Acme Corp", {"name": "Acme Corp", "career_url": "https://acme.com/careers", "jobs": []})

    assert (isolated / "acme.json").exists()


def test_save_company_file_allows_a_genuinely_new_company(isolated):
    update_jobs.save_company_file("Brand New Co", {"name": "Brand New Co", "career_url": None, "jobs": []})

    assert (isolated / "brand_new_co.json").exists()


def test_save_company_file_raises_for_a_new_spelling_of_an_existing_host(isolated):
    _write_company(isolated, "mondaycom", "monday.com", "https://monday.com/careers")

    with pytest.raises(company_registry.DuplicateCompany) as excinfo:
        update_jobs.save_company_file("Monday.com Ltd. (Formerly DaPulse)", {
            "name": "Monday.com Ltd. (Formerly DaPulse)", "career_url": "https://monday.com/careers", "jobs": [],
        })
    assert excinfo.value.conflict_id == "mondaycom"
    assert not (isolated / "mondaycom_ltd_formerly_dapulse.json").exists()


def test_save_company_file_raises_for_a_new_spelling_matching_only_by_normalized_name(isolated):
    _write_company(isolated, "apiiro", "Apiiro", None)

    with pytest.raises(company_registry.DuplicateCompany):
        update_jobs.save_company_file("Apiiro Ltd.", {"name": "Apiiro Ltd.", "career_url": None, "jobs": []})
    assert not (isolated / "apiiro_ltd.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_company_registry_gate.py -v`
Expected: FAIL - the two `DuplicateCompany`-expecting tests fail because `save_company_file` currently creates the second file with no check.

- [ ] **Step 3: Write the implementation**

In `jobfit/scripts/update_jobs.py`, add the import (alongside the Task 2 import line):

```python
from jobfit import ats_fetchers, company_registry, company_review, config, connections, cv, pipeline_lock, scoring, techmap_source, translation  # noqa: E402
```

Change `save_company_file` (currently line 119-120) from:

```python
def save_company_file(company: str, data: dict) -> None:
    atomic_write_json(COMPANIES_DIR / f"{_snake_case(company)}.json", data)
```

to:

```python
def save_company_file(company: str, data: dict) -> None:
    target_id = _snake_case(company)
    target_path = COMPANIES_DIR / f"{target_id}.json"
    if not target_path.exists():
        registry = company_registry.get_registry(COMPANIES_DIR, COMPANIES_DIR.parent / "data" / "company_registry.json")
        conflict_id = registry.register_if_new(target_id, company, data.get("career_url"))
        if conflict_id is not None:
            raise company_registry.DuplicateCompany(company, target_id, conflict_id)
    atomic_write_json(target_path, data)
```

(`COMPANIES_DIR.parent / "data" / "company_registry.json"` is derived from `COMPANIES_DIR` itself - not a separate `config.COMPANY_REGISTRY_PATH` reference - specifically so that every existing test which already does `monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path / "companies")` automatically gets an isolated registry path too, with no changes needed to those other tests.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_company_registry_gate.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all tests pass. This step matters most for `test_diff_and_update.py` and `test_referral_merge.py`, which call `save_company_file` indirectly and must still pass unchanged now that it can raise.

- [ ] **Step 6: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/server/tests/test_company_registry_gate.py
git commit -m "feat: gate save_company_file against creating a new file for an already-known company"
```

---

### Task 6: Engine-fingerprinted score cache

**Files:**
- Modify: `jobfit/scoring.py` (imports at line 13-20, `score_cache_key` at line 137-149)
- Test: extend `jobfit/server/tests/test_recompute_score_cache.py`

**Interfaces:**
- Produces: `scoring.SCORING_ENGINE_FINGERPRINT: str` (module-level, computed once at import); `scoring.score_cache_key(job, profile)` now also depends on `job["title"]`, `job["department"]`, `job["location"]`, `job["employment_type"]`, and the fingerprint, in addition to the existing `description`/CV text. Every existing caller of `score_cache_key` (`update_jobs._recompute_one_company`) is unaffected - same signature, same return type (a 16-hex-char string).

- [ ] **Step 1: Write the failing tests**

Add to `jobfit/server/tests/test_recompute_score_cache.py` (after the existing `test_score_cache_key_changes_when_the_cv_text_changes` test):

```python
def test_score_cache_key_changes_when_the_scoring_engine_fingerprint_changes(monkeypatch):
    job = {"title": "Backend Engineer", "description": "Python required"}
    profile = {"text": "Backend engineer with Python experience"}
    key_before = scoring.score_cache_key(job, profile)

    monkeypatch.setattr(scoring, "SCORING_ENGINE_FINGERPRINT", "different000")
    key_after = scoring.score_cache_key(job, profile)

    assert key_before != key_after


def test_score_cache_key_changes_when_the_job_title_changes():
    profile = {"text": "Backend engineer with Python experience"}
    key1 = scoring.score_cache_key({"title": "Backend Engineer", "description": "Python required"}, profile)
    key2 = scoring.score_cache_key({"title": "Frontend Engineer", "description": "Python required"}, profile)
    assert key1 != key2


def test_score_cache_key_changes_when_department_location_or_employment_type_changes():
    profile = {"text": "Backend engineer with Python experience"}
    base = {"title": "Backend Engineer", "description": "Python required", "department": "R&D", "location": "Tel Aviv", "employment_type": "Full-time"}
    base_key = scoring.score_cache_key(base, profile)

    for field, new_value in [("department", "Sales"), ("location", "Haifa"), ("employment_type", "Part-time")]:
        changed = dict(base)
        changed[field] = new_value
        assert scoring.score_cache_key(changed, profile) != base_key, f"{field} change did not affect the cache key"


def test_score_cache_key_is_stable_for_identical_inputs_including_new_fields():
    job = {"title": "Backend Engineer", "description": "Python required", "department": "R&D", "location": "Tel Aviv", "employment_type": "Full-time"}
    profile = {"text": "Backend engineer with Python experience"}
    assert scoring.score_cache_key(job, profile) == scoring.score_cache_key(job, profile)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_recompute_score_cache.py -v -k fingerprint`
Expected: FAIL with `AttributeError: module 'jobfit.scoring' has no attribute 'SCORING_ENGINE_FINGERPRINT'`

- [ ] **Step 3: Write the implementation**

In `jobfit/scoring.py`, add the import (with the existing imports at the top of the file):

```python
from jobfit.ats_scorer.config import DEFAULT_CONFIG
```

After the imports and before `MIN_DESCRIPTION_LEN_FOR_FULL_CONFIDENCE = 50`, add:

```python
def _compute_scoring_engine_fingerprint() -> str:
    """sha256 over every file that can change a job's score for the same
    inputs: this module, every ats_scorer source file, every ats_scorer
    taxonomy/config data file, and the scoring config's own serialized
    values. Computed once at import time - a scoring-formula fix changes
    this on the next process start, which is what makes
    score_cache_key() below self-invalidate without anyone having to
    remember to pass force=True."""
    hasher = hashlib.sha256()
    ats_scorer_dir = Path(__file__).parent / "ats_scorer"
    paths = [Path(__file__)]
    paths.extend(sorted(ats_scorer_dir.glob("*.py")))
    paths.extend(sorted((ats_scorer_dir / "data").glob("*.json")))
    for path in paths:
        hasher.update(path.read_bytes())
    hasher.update(DEFAULT_CONFIG.model_dump_json().encode("utf-8"))
    return hasher.hexdigest()[:12]


SCORING_ENGINE_FINGERPRINT = _compute_scoring_engine_fingerprint()
```

Add `from pathlib import Path` to the imports if not already present (check first - `re` and `hashlib` are already imported per the existing file).

Change `score_cache_key` (currently lines 137-149) from:

```python
def score_cache_key(job: dict, profile: dict) -> str:
    """..."""
    description = job.get("description") or ""
    cv_text = _cv_text_for_profile(profile)
    combined = f"{description}\x00{cv_text}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]
```

to:

```python
def score_cache_key(job: dict, profile: dict) -> str:
    """Deterministic (stable across processes and runs - unlike Python's
    built-in hash(), which is randomized per-process) short hash of
    everything a job's score against one profile is actually computed
    from: the current scoring engine's own fingerprint (so a scoring-code
    or taxonomy-data change invalidates every cached score automatically,
    with no force=True needed), the job's title/description/department/
    location/employment_type (everything score_job's extraction and
    matching actually reads), and that profile's CV text.
    update_jobs.recompute_stage stores this per job/profile pair and
    skips rescoring when it's unchanged, so a rerun only does real work
    for jobs whose description changed (a rescrape), whose CV changed (a
    re-upload), or whose scoring logic changed (a code fix) - not every
    job every time."""
    cv_text = _cv_text_for_profile(profile)
    parts = [
        SCORING_ENGINE_FINGERPRINT,
        job.get("title") or "",
        job.get("description") or "",
        job.get("department") or "",
        job.get("location") or "",
        job.get("employment_type") or "",
        cv_text,
    ]
    combined = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_recompute_score_cache.py -v`
Expected: all pass (existing + 4 new)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all pass. (No other test should depend on a specific cache-key value, only on equality/inequality, so this should be a clean pass.)

- [ ] **Step 6: Commit**

```bash
git add jobfit/scoring.py jobfit/server/tests/test_recompute_score_cache.py
git commit -m "feat: fingerprint the scoring engine and fold it plus title/department/location/employment_type into the score cache key"
```

---

### Task 7: Store years_required at score time; expose the engine fingerprint

**Files:**
- Modify: `jobfit/scripts/update_jobs.py` (`diff_and_update` line 405, `_recompute_one_company` line 566, `recompute_stage` line 612, `main` line 847)
- Modify: `jobfit/config.py` (add `JOBS_OUTPUT_META_JSON`)
- Modify: `jobfit/build_html.py` (`render`/`build` at lines 1158-1179, template constant near line 332, footer template near line 1132)
- Test: extend `jobfit/server/tests/test_diff_and_update.py`, `jobfit/server/tests/test_recompute_score_cache.py`; new `jobfit/server/tests/test_build_html_footer.py`

**Interfaces:**
- Consumes: `scoring.SCORING_ENGINE_FINGERPRINT` (Task 6).
- Produces: every job record gains a `years_required: int | None` field, computed once when the job is first scored rather than recomputed from a regex on every aggregate; `config.JOBS_OUTPUT_META_JSON: Path`; `recompute_stage(force: bool = False)` gains no new required parameter but now writes `_meta.json["scoring_engine"]`; `build_html.build()`/`render()` embed the fingerprint in the page footer; `main()` gains `--force-rescore`.

- [ ] **Step 1: Write the failing tests**

Add to `jobfit/server/tests/test_diff_and_update.py` (read the existing file first to match its fixture style; it should already isolate `COMPANIES_DIR` - reuse that fixture):

```python
def test_diff_and_update_stores_years_required_on_a_new_job(isolated):  # reuse whatever the file's isolation fixture is named
    fetched = [{
        "title": "Backend Engineer", "location": "Tel Aviv",
        "description": "Requirements: 5+ years of experience with Python",
        "url": "https://acme.com/careers/1",
    }]
    record, new_count, closed_count = update_jobs.diff_and_update("Acme Corp", "https://acme.com/careers", fetched, {})

    assert new_count == 1
    assert record["jobs"][0]["years_required"] == 5
```

Add to `jobfit/server/tests/test_recompute_score_cache.py`:

```python
def test_recompute_stores_years_required_when_a_job_is_rescored(tmp_path, monkeypatch):
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", tmp_path)
    profiles = {"default": {"text": "Backend engineer with Python experience"}}
    job = {
        "id": "1", "title": "Backend Engineer",
        "description": "Requirements: 5+ years of experience with Python",
    }
    path = tmp_path / "acme.json"
    path.write_text(json.dumps({"name": "Acme", "jobs": [job]}), encoding="utf-8")

    update_jobs._recompute_one_company(
        str(path), profiles, {"default"}, ("score_", "matched_", "coverage_", "confidence_", "requirements_"),
    )

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["jobs"][0]["years_required"] == 5


def test_recompute_stage_writes_scoring_engine_to_meta(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(update_jobs, "META_PATH", companies_dir / "_meta.json")
    monkeypatch.setattr(update_jobs.cv, "load_profiles", lambda: {})
    monkeypatch.setattr(update_jobs, "RECOMPUTE_WORKERS", 1)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})

    update_jobs.recompute_stage()

    meta = json.loads((companies_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["scoring_engine"] == scoring.SCORING_ENGINE_FINGERPRINT
```

(This test file's existing imports already include `json`, `update_jobs`, `scoring`, `config` per the prior tasks in this plan - add any that are missing.)

Create `jobfit/server/tests/test_build_html_footer.py`:

```python
"""build_html embeds the scoring engine's fingerprint in the page footer
(next to "generated at") so a page built before a scoring fix is visibly
different from one built after - see jobs_v2.meta.json, written by
aggregate_to_jobs_v2."""

import json

from jobfit import build_html, config


def test_build_reads_scoring_engine_from_meta_file_and_embeds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.JOBS_OUTPUT_JSON.write_text("[]", encoding="utf-8")
    config.JOBS_OUTPUT_META_JSON.write_text(json.dumps({"scoring_engine": "abc123def456"}), encoding="utf-8")
    monkeypatch.setattr(build_html.cv, "load_registry", lambda: {})

    build_html.build()

    html = config.OUTPUT_HTML.read_text(encoding="utf-8")
    assert "abc123def456" in html


def test_build_tolerates_a_missing_meta_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.JOBS_OUTPUT_JSON.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(build_html.cv, "load_registry", lambda: {})

    build_html.build()  # must not raise even though jobs_v2.meta.json doesn't exist

    assert config.OUTPUT_HTML.exists()
```

(`build_html.py` must already do `from jobfit import cv` inside `build()` per the existing code shown in this plan's research - if it's a top-level import instead, adjust `monkeypatch.setattr(build_html.cv, ...)` to whatever import style the file actually uses; check with `grep -n "^from jobfit import cv\|import cv" jobfit/build_html.py` first.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_diff_and_update.py jobfit/server/tests/test_recompute_score_cache.py jobfit/server/tests/test_build_html_footer.py -v -k "years_required or scoring_engine"`
Expected: FAIL - `years_required` not present on new/rescored jobs yet; `config.JOBS_OUTPUT_META_JSON` doesn't exist yet; `build_html.build()` doesn't embed anything yet.

- [ ] **Step 3: Add the config constant**

In `jobfit/config.py`, after `JOBS_OUTPUT_JSON = ROOT / "data" / "jobs_v2.json"` (line 20):

```python
JOBS_OUTPUT_META_JSON = ROOT / "data" / "jobs_v2.meta.json"
```

- [ ] **Step 4: Store years_required in diff_and_update and _recompute_one_company**

In `jobfit/scripts/update_jobs.py`'s `diff_and_update` (around line 458, right after `new_job.update(scores)`):

```python
        else:
            scores = scoring.score_job_both(job, profiles)
            new_job = {
                "id": job_id,
                "title": title,
                "location": job.get("location"),
                "url": job.get("url"),
                "description": ats_fetchers.strip_html(job.get("description")),
                "department": job.get("department"),
                "employment_type": job.get("employment_type"),
                "title_original": job.get("title_original"),
                "description_original": job.get("description_original"),
                "source_language": job.get("source_language"),
                "first_seen": job.get("posted_at") or now,
                "last_seen": now,
                "status": "new",
            }
            new_job.update(scores)
            new_job["years_required"] = scoring.required_years(f"{title}\n{new_job['description'] or ''}")
            existing_by_id[job_id] = new_job
            new_count += 1
```

(Only new line: `new_job["years_required"] = ...`, placed after `new_job.update(scores)`.)

In `_recompute_one_company` (around line 601-603):

```python
    for job in record["jobs"]:
        current_keys = {name: scoring.score_cache_key(job, profile) for name, profile in profiles.items()}
        if not force and job.get("_score_cache_keys") == current_keys:
            skipped += 1
            continue
        job.update(scoring.score_job_both(job, profiles))
        job["years_required"] = scoring.required_years(f"{job.get('title') or ''}\n{job.get('description') or ''}")
        job["_score_cache_keys"] = current_keys
        rescored += 1
```

(Only new line: `job["years_required"] = ...`, placed between the existing `job.update(...)` and `job["_score_cache_keys"] = ...` lines.)

- [ ] **Step 5: Write scoring_engine into _meta.json from recompute_stage**

In `recompute_stage` (around line 665-671), change:

```python
    count = aggregate_to_jobs_v2()
    logger.info(
        "recompute: rescored %d jobs (%d unchanged, skipped) against %d profile(s), aggregated %d jobs",
        jobs_rescored, jobs_skipped, len(profiles), count,
    )
    from jobfit import build_html
    build_html.build()
```

to:

```python
    count = aggregate_to_jobs_v2()
    meta = load_meta()
    meta["scoring_engine"] = scoring.SCORING_ENGINE_FINGERPRINT
    save_meta(meta)
    logger.info(
        "recompute: rescored %d jobs (%d unchanged, skipped) against %d profile(s), aggregated %d jobs, engine %s",
        jobs_rescored, jobs_skipped, len(profiles), count, scoring.SCORING_ENGINE_FINGERPRINT,
    )
    from jobfit import build_html
    build_html.build()
```

- [ ] **Step 6: Write jobs_v2.meta.json from aggregate_to_jobs_v2**

In `aggregate_to_jobs_v2` (around line 842-844), change:

```python
    dataset.sort(key=lambda r: r.get("best_score") or 0, reverse=True)
    atomic_write_json(config.JOBS_OUTPUT_JSON, dataset)
    return len(dataset)
```

to:

```python
    dataset.sort(key=lambda r: r.get("best_score") or 0, reverse=True)
    atomic_write_json(config.JOBS_OUTPUT_JSON, dataset)
    atomic_write_json(config.JOBS_OUTPUT_META_JSON, {
        "scoring_engine": scoring.SCORING_ENGINE_FINGERPRINT,
        "aggregated_at": _now_iso(),
        "job_count": len(dataset),
        "company_count": len({r["company"] for r in dataset}),
    })
    return len(dataset)
```

- [ ] **Step 7: Add --force-rescore to main()**

In `main()`, add the argument (alongside `--wait` from Task 2) and use it:

```python
    parser.add_argument("--force-rescore", action="store_true", help="rescore every job regardless of the score cache")
```

and change:

```python
        if not args.skip_aggregate:
            recompute_stage()
```

to:

```python
        if not args.skip_aggregate:
            recompute_stage(force=args.force_rescore)
```

- [ ] **Step 8: Embed the fingerprint in build_html's footer**

First, run `grep -n "^from jobfit import cv\|^    from jobfit import cv" jobfit/build_html.py` to confirm whether `cv` is imported at module level or inside `build()`; match whichever style the file already uses when writing the code below (the snippets assume it's imported inside `build()`, matching what this plan's research found at line 1174).

In `jobfit/build_html.py`, near `const GENERATED_AT = __GENERATED_AT_JSON__;` (line 332), add directly after it:

```javascript
const SCORING_ENGINE = __SCORING_ENGINE_JSON__;
```

At the footer text (line 1132), change:

```javascript
    `${JOBS.length} jobs &middot; ${companiesCount} companies<br>${connCount} with a connection<br>${descCount} with full description<br>${referralCount} referral jobs<br>${likedIds.size} liked &middot; ${hiddenIds.size} hidden &middot; ${sentIds.size} CV sent &middot; ${reachedIds.size} reached out<br>generated ${GENERATED_AT}`;
```

to:

```javascript
    `${JOBS.length} jobs &middot; ${companiesCount} companies<br>${connCount} with a connection<br>${descCount} with full description<br>${referralCount} referral jobs<br>${likedIds.size} liked &middot; ${hiddenIds.size} hidden &middot; ${sentIds.size} CV sent &middot; ${reachedIds.size} reached out<br>generated ${GENERATED_AT}${SCORING_ENGINE ? ` &middot; engine ${SCORING_ENGINE}` : ""}`;
```

Change `_load_company_addresses` and `render`/`build` (lines 1143-1179) from:

```python
def render(dataset: list[dict], profiles: list[dict]) -> str:
    jobs_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    profiles_json = json.dumps(profiles, ensure_ascii=False)
    addresses_json = json.dumps(_load_company_addresses(), ensure_ascii=False).replace("</", "<\\/")
    generated_at = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    return (
        PAGE_TEMPLATE.replace("__JOBS_JSON__", jobs_json)
        .replace("__PROFILES_JSON__", profiles_json)
        .replace("__COMPANY_ADDRESSES_JSON__", addresses_json)
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

to:

```python
def _load_scoring_engine_fingerprint() -> str | None:
    if not config.JOBS_OUTPUT_META_JSON.exists():
        return None
    try:
        return json.loads(config.JOBS_OUTPUT_META_JSON.read_text(encoding="utf-8")).get("scoring_engine")
    except (json.JSONDecodeError, OSError):
        return None


def render(dataset: list[dict], profiles: list[dict], scoring_engine: str | None = None) -> str:
    jobs_json = json.dumps(dataset, ensure_ascii=False).replace("</", "<\\/")
    profiles_json = json.dumps(profiles, ensure_ascii=False)
    addresses_json = json.dumps(_load_company_addresses(), ensure_ascii=False).replace("</", "<\\/")
    generated_at = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    scoring_engine_json = json.dumps(scoring_engine)
    return (
        PAGE_TEMPLATE.replace("__JOBS_JSON__", jobs_json)
        .replace("__PROFILES_JSON__", profiles_json)
        .replace("__COMPANY_ADDRESSES_JSON__", addresses_json)
        .replace("__GENERATED_AT_JSON__", generated_at)
        .replace("__SCORING_ENGINE_JSON__", scoring_engine_json)
    )


def build(dataset: list[dict] | None = None) -> None:
    if dataset is None:
        dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    from jobfit import cv
    profiles = [{"id": pid, "name": entry["name"]} for pid, entry in cv.load_registry().items()]
    html = render(dataset, profiles, _load_scoring_engine_fingerprint())
    config.OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"wrote {config.OUTPUT_HTML} ({len(dataset)} jobs)")
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_diff_and_update.py jobfit/server/tests/test_recompute_score_cache.py jobfit/server/tests/test_build_html_footer.py -v`
Expected: all pass.

- [ ] **Step 10: Run the full test suite to check for regressions**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add jobfit/scripts/update_jobs.py jobfit/config.py jobfit/build_html.py jobfit/server/tests/test_diff_and_update.py jobfit/server/tests/test_recompute_score_cache.py jobfit/server/tests/test_build_html_footer.py
git commit -m "feat: store years_required at score time, write jobs_v2.meta.json, embed the scoring engine fingerprint in the page footer"
```

---

### Task 8: Incremental aggregate

**Files:**
- Modify: `jobfit/atomic_io.py` (`write_json_atomic`)
- Modify: `jobfit/scripts/update_jobs.py` (`atomic_write_json` line 108, `aggregate_to_jobs_v2` line 797, `recompute_stage` line 612, `main` line 847)
- Modify: `jobfit/config.py` (add `AGGREGATE_CACHE_DIR`)
- Modify: `jobfit/server/tests/test_aggregate_to_jobs_v2.py` (extend the `isolated` fixture)
- Test: new cases in `jobfit/server/tests/test_aggregate_to_jobs_v2.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `config.AGGREGATE_CACHE_DIR: Path`; `aggregate_to_jobs_v2(force: bool = False) -> int` (new optional parameter, default preserves current behavior of always re-flattening - wait, see Step 3: default changes to "use cache when valid", `force=True` is the new "always re-flatten" path); `recompute_stage(force: bool = False, force_aggregate: bool = False)`; `main()` gains `--force-aggregate`; `jobs_v2.json` is now written with no indentation.

- [ ] **Step 1: Write the failing tests**

First, update the existing `isolated` fixture in `jobfit/server/tests/test_aggregate_to_jobs_v2.py` (read the current file - it's reproduced in this plan's research above) from:

```python
@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    return companies_dir
```

to:

```python
@pytest.fixture
def isolated(tmp_path, monkeypatch):
    companies_dir = tmp_path / "companies"
    companies_dir.mkdir()
    monkeypatch.setattr(update_jobs, "COMPANIES_DIR", companies_dir)
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "AGGREGATE_CACHE_DIR", tmp_path / "cache" / "aggregate")
    monkeypatch.setattr(config, "CONNECTIONS_CSV", tmp_path / "connections.csv")
    monkeypatch.setattr(config, "TECHMAP_CACHE_DIR", tmp_path / "cache" / "techmap")
    monkeypatch.setattr(update_jobs, "load_techmap_index", lambda: {})
    return companies_dir
```

Then append these tests to the same file:

```python
def test_second_run_is_a_cache_hit_and_does_not_reflatten(isolated, monkeypatch):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    def _boom(*a, **kw):
        raise AssertionError("should not re-flatten on a cache hit")
    monkeypatch.setattr(update_jobs, "_flatten_company", _boom)

    count = update_jobs.aggregate_to_jobs_v2()
    assert count == 1


def test_changing_a_company_file_invalidates_only_its_own_cache_entry(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    _write_company(isolated, "wiz", jobs=[{
        "id": "j2", "title": "Frontend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "closed",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    dataset = json.loads(config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8"))
    acme_job = next(j for j in dataset if j["id"] == "j1")
    assert acme_job["status"] == "closed"


def test_force_true_ignores_the_cache(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])
    update_jobs.aggregate_to_jobs_v2()

    calls = {"n": 0}
    real_flatten = update_jobs._flatten_company

    def counting_flatten(*args, **kwargs):
        calls["n"] += 1
        return real_flatten(*args, **kwargs)

    import jobfit.scripts.update_jobs as uj_module
    uj_module._flatten_company = counting_flatten
    try:
        update_jobs.aggregate_to_jobs_v2(force=True)
    finally:
        uj_module._flatten_company = real_flatten

    assert calls["n"] == 1


def test_orphaned_cache_entries_for_deleted_companies_are_removed(isolated):
    _write_company(isolated, "acme", jobs=[])
    update_jobs.aggregate_to_jobs_v2()
    assert (config.AGGREGATE_CACHE_DIR / "acme.json").exists()

    (isolated / "acme.json").unlink()
    update_jobs.aggregate_to_jobs_v2()

    assert not (config.AGGREGATE_CACHE_DIR / "acme.json").exists()


def test_jobs_v2_json_has_no_indentation(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    raw = config.JOBS_OUTPUT_JSON.read_text(encoding="utf-8")
    assert "\n" not in raw  # indent=None -> single line
    assert json.loads(raw)  # still valid JSON


def test_jobs_v2_meta_json_written(isolated):
    _write_company(isolated, "acme", jobs=[{
        "id": "j1", "title": "Backend Engineer", "status": "seen",
        "first_seen": "2026-06-15T10:00:00Z", "last_seen": "2026-09-24T10:00:00Z",
    }])

    update_jobs.aggregate_to_jobs_v2()

    meta = json.loads(config.JOBS_OUTPUT_META_JSON.read_text(encoding="utf-8"))
    assert meta["job_count"] == 1
    assert meta["company_count"] == 1
    assert "scoring_engine" in meta
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest jobfit/server/tests/test_aggregate_to_jobs_v2.py -v`
Expected: FAIL - no caching exists yet (`_flatten_company` doesn't exist, `config.AGGREGATE_CACHE_DIR` doesn't exist, output has `indent=2`).

- [ ] **Step 3: Add config constants and widen write_json_atomic**

In `jobfit/config.py`, after `JOBS_OUTPUT_META_JSON` (added in Task 7):

```python
AGGREGATE_CACHE_DIR = ROOT / "cache" / "aggregate"
```

In `jobfit/atomic_io.py`, change:

```python
def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
```

to:

```python
def write_json_atomic(path: Path, data, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    tmp.replace(path)
```

In `jobfit/scripts/update_jobs.py`, change `atomic_write_json` (line 108-109) from:

```python
def atomic_write_json(path: Path, data) -> None:
    write_json_atomic(path, data)
```

to:

```python
def atomic_write_json(path: Path, data, indent: int | None = 2) -> None:
    write_json_atomic(path, data, indent=indent)
```

- [ ] **Step 4: Rewrite aggregate_to_jobs_v2 with the per-company cache**

In `jobfit/scripts/update_jobs.py`, change `aggregate_to_jobs_v2` (currently lines 797-844) from its current body to:

```python
def _context_sha1() -> str:
    """Covers every input that a flattened row embeds besides the company
    file's own bytes: LinkedIn connections (contacts_for_company) and
    techmap (industry/size/location hint) both feed into every row, so a
    change to either must invalidate every company's cache entry, not
    just the one company file that happened to change."""
    hasher = hashlib.sha1()
    if config.CONNECTIONS_CSV.exists():
        hasher.update(config.CONNECTIONS_CSV.read_bytes())
    if config.TECHMAP_CACHE_DIR.exists():
        for path in sorted(config.TECHMAP_CACHE_DIR.glob("*")):
            if path.is_file():
                hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _flatten_company(record: dict, company: str, contacts: list, industry, size, techmap_location_hint) -> list[dict]:
    from jobfit import pipeline as _pipeline  # reuse its already-debugged location-inference logic, not a copy

    rows = []
    for job in record["jobs"]:
        loc, city, is_remote = _pipeline._infer_location_fields(job, techmap_location_hint)
        out = dict(job)  # carries id/title/url/description/status/first_seen/last_seen/score_*/matched_*/coverage_*/confidence_*/requirements_*/best_*
        out["company"] = company
        out["industry"] = industry
        out["company_size"] = size
        out["location"] = loc
        out["city"] = city
        out["is_remote"] = is_remote
        out["department"] = job.get("department")
        out["employment_type"] = job.get("employment_type")
        out["posted_at"] = job.get("first_seen")
        out["connections"] = contacts
        out["has_connection"] = bool(contacts)
        out["has_description"] = bool(job.get("description"))
        if "years_required" in job:
            years_required = job["years_required"]
        else:
            years_required = scoring.required_years(f"{job['title']}\n{job.get('description') or ''}")
        out["years_required"] = years_required
        out["is_referral"] = bool(job.get("is_referral"))
        out["referral_contact"] = job.get("referral_contact")
        rows.append(out)
    return rows


def aggregate_to_jobs_v2(force: bool = False) -> int:
    """Flatten companies/*.json into the record shape build_html.py already
    expects, and write it to config.JOBS_OUTPUT_JSON - so build_html needs no
    changes at all, it just picks up whatever's there.

    Each company's flattened rows are cached under config.AGGREGATE_CACHE_DIR,
    keyed by the sha1 of its own companies/*.json bytes plus a shared
    context_sha1 (connections + techmap, which also feed every row). A
    company whose file and the shared context are both unchanged since its
    last flatten is a cache hit - this is what makes a scoped `--company X`
    run's aggregate step cost seconds instead of the ~6 minutes a full
    re-flatten of ~1900 companies takes. force=True ignores the cache
    entirely (e.g. after a bulk edit that touched context but you want to
    be sure).
    """
    with pipeline_lock.PipelineLock(config.PIPELINE_LOCK_PATH, stage="aggregate", scope="all"):
        conn_index = connections.load_connections_index()
        techmap_index = load_techmap_index()
        context_sha1 = _context_sha1()

        cache_dir = config.AGGREGATE_CACHE_DIR
        cache_dir.mkdir(parents=True, exist_ok=True)
        live_stems: set[str] = set()

        dataset = []
        for path in sorted(COMPANIES_DIR.glob("*.json")):
            if path.name == "_meta.json":
                continue
            stem = path.stem
            live_stems.add(stem)
            source_bytes = path.read_bytes()
            source_sha1 = hashlib.sha1(source_bytes).hexdigest()
            cache_path = cache_dir / f"{stem}.json"

            rows = None
            if not force and cache_path.exists():
                try:
                    cached = json.loads(cache_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    cached = None
                if cached is not None and cached.get("source_sha1") == source_sha1 and cached.get("context_sha1") == context_sha1:
                    rows = cached["rows"]

            if rows is None:
                record = json.loads(source_bytes.decode("utf-8"))
                company = record["name"]
                contacts = connections.contacts_for_company(conn_index, company)
                techmap_rows = techmap_index.get(connections.normalize_company(company), [])
                industry = techmap_rows[0]["industry"] if techmap_rows else None
                size = techmap_rows[0]["size"] if techmap_rows else None
                techmap_location_hint = techmap_rows[0]["location"] if techmap_rows else None
                rows = _flatten_company(record, company, contacts, industry, size, techmap_location_hint)
                atomic_write_json(cache_path, {"source_sha1": source_sha1, "context_sha1": context_sha1, "rows": rows})

            dataset.extend(rows)

        for stale in cache_dir.glob("*.json"):
            if stale.stem not in live_stems:
                stale.unlink()

        dataset.sort(key=lambda r: r.get("best_score") or 0, reverse=True)
        atomic_write_json(config.JOBS_OUTPUT_JSON, dataset, indent=None)
        atomic_write_json(config.JOBS_OUTPUT_META_JSON, {
            "scoring_engine": scoring.SCORING_ENGINE_FINGERPRINT,
            "aggregated_at": _now_iso(),
            "job_count": len(dataset),
            "company_count": len({r["company"] for r in dataset}),
        })
        return len(dataset)
```

(This replaces both the plain `aggregate_to_jobs_v2` body from before Task 7 AND the `jobs_v2.meta.json`-writing addition made in Task 7 Step 6 - the meta write moves into this version unchanged. The `with pipeline_lock.PipelineLock(...)` wrapper from Task 2 is preserved here, now wrapping the new body.)

- [ ] **Step 5: Add --force-aggregate**

In `recompute_stage` (signature at line 612), change:

```python
def recompute_stage(force: bool = False) -> None:
```

to:

```python
def recompute_stage(force: bool = False, force_aggregate: bool = False) -> None:
```

and change the line `count = aggregate_to_jobs_v2()` (from Task 7 Step 5) to:

```python
    count = aggregate_to_jobs_v2(force=force_aggregate)
```

In `main()`, add the argument:

```python
    parser.add_argument("--force-aggregate", action="store_true", help="ignore the per-company aggregate cache")
```

and change:

```python
        if not args.skip_aggregate:
            recompute_stage(force=args.force_rescore)
```

to:

```python
        if not args.skip_aggregate:
            recompute_stage(force=args.force_rescore, force_aggregate=args.force_aggregate)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest jobfit/server/tests/test_aggregate_to_jobs_v2.py -v`
Expected: all pass (1 previously-existing + 6 new).

- [ ] **Step 7: Run the full test suite to check for regressions**

Run: `uv run python -m pytest jobfit/server/tests -q`
Expected: all pass. Pay particular attention to any test that reads `jobs_v2.json` and expects pretty-printed (multi-line) JSON on disk directly (rather than through `json.loads`) - `indent=None` will break a raw-text assertion like that; if found, fix the test to parse before asserting rather than reverting the indent change.

- [ ] **Step 8: Manual sanity check against the real corpus**

Run: `uv run python -m jobfit.scripts.update_jobs --company "<any single real company name from jobfit/companies_career_pages.json>" --force`
Expected: completes in well under a minute (previously ~6+ minutes for the aggregate step alone), logs show `recompute: rescored ... aggregated ... jobs, engine <12-hex-fingerprint>`, and `jobfit/cache/aggregate/` now contains one `.json` file per company.

- [ ] **Step 9: Commit**

```bash
git add jobfit/atomic_io.py jobfit/scripts/update_jobs.py jobfit/config.py jobfit/server/tests/test_aggregate_to_jobs_v2.py
git commit -m "feat: cache aggregate_to_jobs_v2 per company, write jobs_v2.json without indentation"
```

---

## Self-review notes (completed before handoff)

**Spec coverage:** Section 2.1 (pipeline lock) → Tasks 1-3. Section 2.5's identity gate (not the full migration) → Tasks 4-5. Section 2.2 (engine fingerprint) → Task 6, plus its "stored years_required" and "visibility" sub-points → Task 7. Section 2.3 (incremental aggregate) → Task 8. Every numbered item in spec sections 2.1-2.3 and the identity-gate paragraph of 2.5 has a corresponding task; the rest of section 2.5 (the persisted rich schema with `aliases`/`sources`/`review`/`merged_from`, and section 5's migration) is explicitly out of scope for this plan per the spec's own three-plan split.

**Placeholder scan:** No TBD/TODO; every step has real, complete code or an exact `grep`/`pytest` command with expected output.

**Type/name consistency check:** `pipeline_lock.PipelineLock`/`PipelineBusy` used identically across Tasks 1-3, 8. `company_registry.get_registry(companies_dir, registry_path)`/`register_if_new`/`DuplicateCompany` used identically across Tasks 4-5. `scoring.SCORING_ENGINE_FINGERPRINT` referenced identically across Tasks 6-8. `config.JOBS_OUTPUT_META_JSON`/`config.AGGREGATE_CACHE_DIR`/`config.PIPELINE_LOCK_PATH` each defined once (Tasks 2, 7, 8 respectively) and only referenced, never redefined, afterward. `aggregate_to_jobs_v2`'s signature is introduced as `-> int` (existing), gains `force: bool = False` only in Task 8 (Task 2 wraps its *existing* zero-arg body in the lock without changing its signature; Task 7 does not touch its signature, only its body) - double-checked that Task 2's lock-wrapping edit and Task 8's full-body replacement don't conflict: Task 8's Step 4 code block is the complete post-Task-7, post-Task-2 body (it includes both the `with pipeline_lock...` wrapper and the `jobs_v2.meta.json` write), so applying Task 8 after Tasks 2 and 7 as written produces one consistent function, not three overlapping partial edits.
