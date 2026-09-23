"""Atomic JSON writes, shared by every module that persists small state files.

A plain write_text() truncates the file before writing its new content - if
the process is killed (or crashes) between those two steps, the file is left
empty or half-written. This happened for real this session: jobfit/data/
profiles.json (cv.py's save_registry, before this module existed) ended up
as an empty {} after a server process got force-killed, silently dropping
every registered CV profile. tmp-write-then-rename is atomic on the same
filesystem, so a crash mid-write never corrupts the real file - it either
still has the old content, or the complete new content, never a partial one.
"""

import json
from pathlib import Path


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
