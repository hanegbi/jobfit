"""Background execution of the on-demand scrape run, with live-log fan-out and history."""

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

from jobfit import config
from jobfit.atomic_io import write_json_atomic
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
    write_json_atomic(config.RUN_HISTORY_PATH, history)


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


def start_run(force: bool, companies: list[str] | None = None) -> str:
    """companies=None scrapes every company in the bank (the normal "Run
    update" button); a list scopes the scrape to just those names - e.g. the
    companies a referral upload just added, so you don't have to re-check
    everything else to pick up a handful of new ones."""
    with _lock:
        if _state["running"]:
            raise RuntimeError(f"run {_state['run_id']} already active")
        run_id = uuid.uuid4().hex[:12]
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _state.update(running=True, run_id=run_id, started_at=started_at, queue=queue.Queue())

    history = _load_history()
    history.append({
        "id": run_id, "started_at": started_at, "finished_at": None, "trigger": "manual",
        "force": force, "companies": companies, "companies_checked": 0, "companies_skipped": 0,
        "new_jobs": 0, "closed_jobs": 0, "failures": [], "duration_s": None, "crashed": False,
    })
    _save_history(history)

    thread = threading.Thread(target=_run_worker, args=(run_id, force, companies), daemon=True)
    thread.start()
    return run_id


def _run_worker(run_id: str, force: bool, companies: list[str] | None) -> None:
    line_queue = log_queue()
    attached = attach(line_queue, _LOGGER_NAMES) if line_queue is not None else []
    started = time.time()
    try:
        scrape_targets = update_jobs.load_companies_to_scrape()
        if companies is not None:
            wanted = set(companies)
            scrape_targets = {name: url for name, url in scrape_targets.items() if name in wanted}
        profiles = update_jobs.cv.load_profiles()
        stats = update_jobs.scrape_stage(scrape_targets, profiles, force=force)
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
