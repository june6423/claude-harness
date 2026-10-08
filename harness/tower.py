"""Start and inspect tower sessions."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .records import RecordError
from .store import REPO_ROOT

TOWERS = ("dt", "et", "vt")


def start_command(tower: str, repo: Path = REPO_ROOT) -> list[str]:
    if tower not in TOWERS:
        raise RecordError(f"tower must be one of {', '.join(TOWERS)}")
    for path in (repo / ".claude" / "agents" / f"{tower}.md", repo / "towers" / f"{tower}.md",
                 repo / "settings" / f"{tower}.json"):
        if not path.exists():
            raise RecordError(f"missing file: {path}")
    return ["claude", "--bg", "--agent", tower, "--name", tower,
            "--append-system-prompt-file", str(repo / "towers" / f"{tower}.md"),
            "--settings", str(repo / "settings" / f"{tower}.json")]


def sessions() -> list[dict]:
    output = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True,
                            check=True).stdout
    return json.loads(output or "[]")


def running(tower: str) -> dict | None:
    for session in sessions():
        if session.get("name") == tower and session.get("state") not in ("stopped", "failed"):
            return session
    return None


def start(tower: str, wait_seconds: float = 10.0) -> dict:
    command = start_command(tower)
    if running(tower):
        raise RecordError(f"a session named {tower!r} already runs")
    subprocess.run(command, cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        session = running(tower)
        if session:
            return session
        time.sleep(1)
    raise RecordError(f"tower {tower} did not appear in 'claude agents --json'")
