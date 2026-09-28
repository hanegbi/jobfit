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
