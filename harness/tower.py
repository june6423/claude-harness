"""Start and inspect tower sessions."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .records import RecordError
from .store import REPO_ROOT

TOWERS = ("dt", "et", "vt")


def render_settings(tower: str, data_root: Path, repo: Path = REPO_ROOT) -> Path:
    """Fill the settings template of a tower with absolute paths. Return the rendered file."""
    template = (repo / "settings" / f"{tower}.json").read_text(encoding="utf-8")
    text = template.replace("{{DATA_ROOT}}", str(data_root)).replace("{{HARNESS}}", str(repo))
    settings = json.loads(text)
    config_path = data_root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not config.get("towers", {}).get("sandbox", False):
        settings.pop("sandbox", None)
    settings["env"] = {**settings.get("env", {}), "HARNESS_DATA_ROOT": str(data_root)}
    text = json.dumps(settings, indent=2) + "\n"
    target = data_root / "runtime" / "settings" / f"{tower}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    (data_root / "runtime" / "drafts" / tower).mkdir(parents=True, exist_ok=True)
    return target


def start_command(tower: str, data_root: Path, model: str | None = None, name: str | None = None,
                  repo: Path = REPO_ROOT) -> list[str]:
    if tower not in TOWERS:
        raise RecordError(f"tower must be one of {', '.join(TOWERS)}")
    for path in (repo / ".claude" / "agents" / f"{tower}.md", repo / "towers" / f"{tower}.md",
                 repo / "settings" / f"{tower}.json"):
        if not path.exists():
            raise RecordError(f"missing file: {path}")
    settings = render_settings(tower, data_root, repo)
    command = ["claude", "--bg", "--agent", tower, "--name", name or tower,
               "--append-system-prompt-file", str(repo / "towers" / f"{tower}.md"),
               "--settings", str(settings)]
    if model:
        command += ["--model", model]
    return command


def sessions() -> list[dict]:
    output = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True,
                            check=True).stdout
    return json.loads(output or "[]")


def running(name: str) -> dict | None:
    for session in sessions():
        if session.get("name") == name and session.get("state") not in ("stopped", "failed"):
            return session
    return None


def start(tower: str, data_root: Path, model: str | None = None, prompt: str | None = None,
          wait_seconds: float = 15.0) -> dict:
    command = start_command(tower, data_root, model)
    if running(tower):
        raise RecordError(f"a session named {tower!r} already runs")
    if prompt:
        command.append(prompt)
    subprocess.run(command, cwd=REPO_ROOT, check=True, stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        session = running(tower)
        if session:
            return session
        time.sleep(1)
    raise RecordError(f"tower {tower} did not appear in 'claude agents --json'")
