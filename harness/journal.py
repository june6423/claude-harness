"""Append-only JSONL files with an exclusive file lock."""

from __future__ import annotations

import fcntl
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    entry = {"at": now(), **entry}
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(line)
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return entry


def read(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)
