"""Refuse to start a second jobfit server against the same data directory.

Root cause this exists for: two server processes running concurrently each
call recompute_stage()/register_profile() with their own in-memory state,
racing to write the *same* companies/*.json and profiles.json files. Each
individual write is atomic (tmp + rename), but two racing writers can still
silently clobber each other's changes - this is how a registered CV profile
and part of a company's job record were lost in practice, not a hypothetical.
The in-process threading.Lock in runner.py only protects against concurrent
requests within *one* process; it does nothing across two OS processes.
"""

import os
import subprocess
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    """Windows-only: True if a process with this PID currently exists."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
    except OSError:
        return True  # can't tell - fail safe (assume alive, refuse to start)
    return str(pid) in result.stdout


def acquire(lock_path: Path) -> None:
    """Raise RuntimeError if another live jobfit server already holds the lock."""
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid is not None and existing_pid != os.getpid() and _pid_alive(existing_pid):
            raise RuntimeError(
                f"Another jobfit server is already running (pid {existing_pid}). "
                "Running two at once corrupts companies/*.json and profiles.json via concurrent writes - "
                "stop that process first, or delete this lock file if you're sure it's gone: "
                f"{lock_path}"
            )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")


def release(lock_path: Path) -> None:
    try:
        if lock_path.exists() and lock_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            lock_path.unlink()
    except OSError:
        pass
