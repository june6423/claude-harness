"""Start and inspect tower sessions."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .records import RecordError
from .store import REPO_ROOT

TOWERS = ("dt", "et", "vt")
# Files that the Claude Code sandbox covers in the working directory (list from Claude Code 2.1.294).
# bubblewrap fails when it must create one of them itself and part of the directory is read-only.
SANDBOX_PLACEHOLDERS = (".gitconfig", ".gitmodules", ".bashrc", ".bash_profile", ".zshrc", ".zprofile",
                        ".profile", ".ripgreprc", ".mcp.json")
SANDBOX_PLACEHOLDER_DIRS = (".vscode", ".idea")


def render_settings(tower: str, data_root: Path, repo: Path = REPO_ROOT) -> Path:
    """Fill the settings template of a tower with absolute paths. Return the rendered file."""
    template = (repo / "settings" / f"{tower}.json").read_text(encoding="utf-8")
    text = template.replace("{{DATA_ROOT}}", str(data_root)).replace("{{HARNESS}}", str(repo))
    settings = json.loads(text)
    config_path = data_root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    if not config.get("towers", {}).get("sandbox", False):
        settings.pop("sandbox", None)
    else:
        # The sandbox must create placeholder files (such as .gitconfig) at the top of the
        # working directory, so deny writes to each repository entry, not to the directory.
        for name in SANDBOX_PLACEHOLDERS:
            placeholder = repo / name
            if not placeholder.exists():
                placeholder.write_text('{"mcpServers": {}}\n' if name == ".mcp.json" else "", encoding="utf-8")
        for name in SANDBOX_PLACEHOLDER_DIRS:
            (repo / name).mkdir(exist_ok=True)
        filesystem = settings["sandbox"]["filesystem"]
        filesystem["denyWrite"] = [p for p in filesystem["denyWrite"] if p != str(repo)] + sorted(
            str(entry) for entry in repo.iterdir() if entry.name not in SANDBOX_PLACEHOLDERS + SANDBOX_PLACEHOLDER_DIRS)
    settings["env"] = {**settings.get("env", {}), "HARNESS_DATA_ROOT": str(data_root)}
    # A tower can call the CLI as bin/harness, ./bin/harness, or by its absolute path. Permission
    # rules and sandbox exclusions match command text, so each rule gets all three forms.
    permissions = settings["permissions"]
    for key in ("allow", "deny"):
        rules = []
        for rule in permissions.get(key, []):
            rules.append(rule)
            if rule.startswith("Bash(bin/harness"):
                rest = rule[len("Bash(bin/harness"):]
                rules += [f"Bash(./bin/harness{rest}", f"Bash({repo}/bin/harness{rest}"]
        permissions[key] = rules
    text = json.dumps(settings, indent=2) + "\n"
    target = data_root / "runtime" / "settings" / f"{tower}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    (data_root / "runtime" / "drafts" / tower).mkdir(parents=True, exist_ok=True)
    return target


def transcript_dir(repo: Path = REPO_ROOT) -> Path:
    return Path.home() / ".claude" / "projects" / str(repo).replace("/", "-").replace(".", "-")


def resolve_session(prefix: str, repo: Path = REPO_ROOT) -> str:
    """The full session ID of a tower transcript whose ID starts with this prefix."""
    matches = sorted(p.stem for p in transcript_dir(repo).glob(f"{prefix}*.jsonl"))
    if len(matches) != 1:
        raise RecordError(f"session prefix {prefix!r} matches {len(matches)} transcripts in {transcript_dir(repo)}")
    return matches[0]


def start_command(tower: str, data_root: Path, model: str | None = None, name: str | None = None,
                  repo: Path = REPO_ROOT, resume: str | None = None) -> list[str]:
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
    if resume:
        # Continue the old conversation. Snapshot off: each request uses the current tower rules.
        command += ["--resume", resolve_session(resume, repo), "--system-prompt-snapshot", "off"]
    return command


def sessions(include_finished: bool = False) -> list[dict]:
    command = ["claude", "agents", "--json"] + (["--all"] if include_finished else [])
    output = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return json.loads(output or "[]")


def named(name: str) -> list[dict]:
    """Every background session with this name, including finished ones."""
    return [s for s in sessions(include_finished=True) if s.get("name") == name]


def running(name: str) -> dict | None:
    for session in named(name):
        if session.get("state") not in ("stopped", "failed"):
            return session
    return None


def stop(name: str) -> list[str]:
    """Stop and remove each session with this name. The transcripts stay on disk."""
    removed = []
    for session in named(name):
        subprocess.run(["claude", "stop", session["id"]], capture_output=True, text=True)
        done = subprocess.run(["claude", "rm", session["id"]], capture_output=True, text=True)
        if done.returncode != 0:
            raise RecordError(f"cannot remove session {session['id']}: {done.stderr.strip() or done.stdout.strip()}")
        removed.append(session["id"])
    return removed


def start(tower: str, data_root: Path, model: str | None = None, prompt: str | None = None,
          wait_seconds: float = 15.0, replace: bool = False, resume: str | None = None) -> dict:
    command = start_command(tower, data_root, model, resume=resume)
    if replace:
        stop(tower)
    else:
        existing = named(tower)
        if existing:
            ids = ", ".join(f"{s['id']} ({s.get('state')})" for s in existing)
            raise RecordError(f"a session named {tower!r} exists: {ids}. "
                              f"Run 'bin/harness tower restart {tower}' to stop and replace it")
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
