"""Start and inspect tower sessions."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from .records import RecordError
from .store import REPO_ROOT

TOWERS = ("dt", "et", "vt", "dt2")
# A fork tower starts from the handover of its source tower and adds its own rules to the source rules.
# Researcher decision (2026-10-20): DT2 is a discussion-only copy of DT.
FORKS = {"dt2": "dt"}
# Researcher decision (2026-10-10): every model call is Opus 5.5 or stronger.
MODEL_IDS = {"opus": "claude-opus-5-5", "fable": "claude-fable-5-1"}
DEFAULT_TOWER_MODEL = "opus"
DEFAULT_BACKGROUND_MODEL = "claude-opus-5-5"


def tower_config(data_root: Path) -> dict:
    path = data_root / "config.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return config.get("towers") or {}


def remote_control(tower: str, data_root: Path) -> bool:
    """config.json towers.remote_control: true for every tower, or a list of tower names."""
    value = tower_config(data_root).get("remote_control", False)
    return value is True or (isinstance(value, list) and tower in value)


def tower_model(tower: str, data_root: Path) -> str:
    models = tower_config(data_root).get("models") or {}
    return models.get(tower) or models.get(FORKS.get(tower, tower), DEFAULT_TOWER_MODEL)
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
    model = tower_model(tower, data_root)
    settings["env"] = {
        **settings.get("env", {}),
        "HARNESS_DATA_ROOT": str(data_root),
        # Subagents use the model of their tower, whatever the Agent tool asks for.
        "CLAUDE_CODE_SUBAGENT_MODEL": MODEL_IDS.get(model, model),
        "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1",
        # Background calls (title and similar) and the haiku alias also use Opus or stronger.
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": tower_config(data_root).get("background_model", DEFAULT_BACKGROUND_MODEL),
    }
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


def render_rules(tower: str, data_root: Path, repo: Path = REPO_ROOT) -> Path:
    """The rules file of a tower. A fork tower gets the rules of its source tower, then its own rules."""
    own = repo / "towers" / f"{tower}.md"
    if tower not in FORKS:
        return own
    source = repo / "towers" / f"{FORKS[tower]}.md"
    target = data_root / "runtime" / "rules" / f"{tower}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8").rstrip("\n") + "\n\n" + own.read_text(encoding="utf-8"),
                      encoding="utf-8")
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
               "--append-system-prompt-file", str(render_rules(tower, data_root, repo)),
               "--settings", str(settings)]
    if remote_control(tower, data_root):
        # Remote Control: the session is reachable from claude.ai/code and the mobile app. The link
        # is bound to the session ID, so each new session gets a new link (see remote_url).
        command += ["--remote-control", name or tower]
    if model:
        command += ["--model", model]
    if resume:
        # Continue the old conversation. Snapshot off: each request uses the current tower rules.
        command += ["--resume", resolve_session(resume, repo), "--system-prompt-snapshot", "off"]
    return command


JOBS_DIR = Path.home() / ".claude" / "jobs"


def remote_url(session: dict, jobs_dir: Path = JOBS_DIR) -> str | None:
    """The claude.ai/code link of a background session with Remote Control, from its job state
    (undocumented file ~/.claude/jobs/<id>/state.json, key bridgeSessionId 'cse_...')."""
    path = jobs_dir / str(session.get("id", "")) / "state.json"
    try:
        bridge = json.loads(path.read_text(encoding="utf-8")).get("bridgeSessionId")
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(bridge, str) or not bridge.startswith("cse_"):
        return None
    return "https://claude.ai/code/session_" + bridge[len("cse_"):]


def last_model(session_id: str, repo: Path = REPO_ROOT) -> str | None:
    """The model of the latest assistant message in a tower transcript."""
    path = transcript_dir(repo) / f"{session_id}.jsonl"
    model = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") == "assistant":
                model = (entry.get("message") or {}).get("model") or model
    return model


def rotation_prompt(tower: str, data_root: Path) -> str:
    worklog = data_root / "worklog"
    return (f"This is a new {tower.upper()} session after a context rotation. Your earlier session wrote a handover.\n"
            f"1. Read {worklog}/{tower}-summary.md if it exists.\n"
            f"2. Read the last Handover section in {worklog}/{tower}.md.\n"
            "3. Run bin/harness status and bin/harness messages --last 20.\n"
            + ("4. Continue the open discussion.\n" if tower in FORKS else
               "4. Continue the open work. Start again each background wait for a task that has no result.\n")
            + "5. Tell the researcher in Korean that a new session started and what you continue.")


def fork_prompt(tower: str, data_root: Path) -> str:
    worklog = data_root / "worklog"
    return (f"This is a new {tower.upper()} session. It is a discussion-only copy of {FORKS[tower].upper()}. "
            f"The harness copied the {FORKS[tower].upper()} worklog into your worklog.\n"
            f"1. Read {worklog}/{tower}-summary.md if it exists.\n"
            f"2. Read the last Handover section in {worklog}/{tower}.md.\n"
            "3. Run bin/harness status and bin/harness messages --last 20.\n"
            "4. Do not continue the open work of the source tower. Wait for the researcher.\n"
            f"5. Tell the researcher in Korean that {tower.upper()} started, what context you have, "
            "and that you are discussion-only.")


def fork(tower: str, data_root: Path, model: str | None = None, replace: bool = False) -> dict:
    """Copy the worklog of the source tower to the fork tower, then start the fork tower."""
    if tower not in FORKS:
        raise RecordError(f"tower must be one of {', '.join(FORKS)}")
    if not replace and named(tower):
        raise RecordError(f"a session named {tower!r} exists. Use --replace to stop it and fork again")
    source = FORKS[tower]
    worklog = data_root / "worklog"
    stamp = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
    for suffix in ("", "-summary"):
        origin = worklog / f"{source}{suffix}.md"
        if origin.exists():
            text = origin.read_text(encoding="utf-8")
            note = f"<!-- Copied from worklog/{source}{suffix}.md at {stamp} for the fork {tower}. -->\n"
            (worklog / f"{tower}{suffix}.md").write_text(note + text, encoding="utf-8")
    return start(tower, data_root, model, fork_prompt(tower, data_root), replace=replace)


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


def respawn(session_id: str) -> None:
    done = subprocess.run(["claude", "respawn", session_id], capture_output=True, text=True)
    if done.returncode != 0:
        raise RecordError(f"cannot respawn {session_id}: {done.stderr.strip() or done.stdout.strip()}")


def reaped(tower: str, repo: Path = REPO_ROOT) -> dict | None:
    """The latest session of a tower when its process is gone. Claude Code stops the process of a
    done or blocked session after about an hour without attachment (seen 2026-10-11 for done and
    2026-10-12 for blocked). 'harness tower stop' removes the session, so a session stopped that
    way is not here."""
    rows = [s for s in named(tower) if s.get("cwd") == str(repo)]
    if not rows or any(s.get("pid") for s in rows):
        return None
    return max(rows, key=lambda s: s.get("startedAt", 0))


def start(tower: str, data_root: Path, model: str | None = None, prompt: str | None = None,
          wait_seconds: float = 15.0, replace: bool = False, resume: str | None = None) -> dict:
    command = start_command(tower, data_root, model or tower_model(tower, data_root), resume=resume)
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
