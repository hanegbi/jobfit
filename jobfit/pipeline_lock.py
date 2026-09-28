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
