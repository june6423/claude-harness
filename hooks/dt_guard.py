#!/usr/bin/env python3
"""Claude Code PreToolUse hook for DT. Exit code 2 blocks the tool call.

1. `harness publish handoff <draft>` with status APPROVED, and `harness publish
   directive <draft>`: the `approval` text (handoff) or the `instruction` text
   (directive) must occur verbatim in a message that the researcher typed
   (transcript entry with origin kind "human"). Peer messages and tool output do
   not count. The check fails closed: if the transcript cannot be read, the
   call is blocked.
2. Write or Edit of state/PROJECT_STATE.md: the new file must not be longer
   than limits.project_state_chars.

Any other tool call passes. An unexpected error in a check that is not about
approval lets the call pass, so a hook bug cannot stop DT from working.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PUBLISH_RE = re.compile(r"(?:^|[\s;&|])(?:\S*/)?bin/harness\s+publish\s+(handoff|directive|grant|revoke)\s")


def block(message: str) -> int:
    print(f"dt_guard: {message}", file=sys.stderr)
    return 2


def normalize(text: str) -> str:
    return " ".join(text.split())


def typed_by_researcher(entry: dict) -> bool:
    """Interactive and background sessions tag a typed message with origin kind "human".
    A `claude -p` prompt has no origin but has a promptId. Other origins (peer,
    task-notification) and meta entries are never researcher input."""
    origin = entry.get("origin")
    if origin is not None:
        return origin.get("kind") == "human"
    return bool(entry.get("promptId")) and not entry.get("isMeta")


def researcher_messages(transcript_path: str) -> list[str]:
    messages = []
    with open(transcript_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "user" or not typed_by_researcher(entry):
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                messages.append(content)
            elif isinstance(content, list):
                messages.extend(block.get("text", "") for block in content
                                if isinstance(block, dict) and block.get("type") == "text")
    return messages


def draft_path(command: str) -> str | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    for index, word in enumerate(words[:-2]):
        if word.endswith("bin/harness") and words[index + 1] == "publish" \
                and words[index + 2] in ("handoff", "directive", "grant", "revoke"):
            return words[index + 3] if index + 3 < len(words) else None
    return None


def check_publish(event: dict) -> int:
    command = (event.get("tool_input") or {}).get("command", "")
    match = PUBLISH_RE.search(" " + command)
    if not match:
        return 0
    kind = match.group(1)
    if "--dry-run" in command.split():
        return 0
    path = draft_path(command)
    if not path:
        return block("cannot find the draft path in the publish command. Run one publish command alone.")
    from harness.records import RecordError, split_front_matter
    try:
        fields, _ = split_front_matter(Path(path).read_text(encoding="utf-8"))
    except (OSError, RecordError):
        return 0  # the CLI reports the bad draft itself
    if kind == "handoff":
        if fields.get("status") != "APPROVED":
            return 0
        field = "approval"
    else:
        field = "instruction"
    approval = normalize(str(fields.get(field, "")))
    if not approval or approval == "NONE":
        return 0  # the CLI refuses the draft itself
    try:
        messages = researcher_messages(event["transcript_path"])
    except (KeyError, OSError) as error:
        return block(f"cannot read the session transcript to check the '{field}' text ({error}). "
                     f"Ask the researcher to publish the {kind} with --actor RESEARCHER.")
    if any(approval in normalize(message) for message in messages):
        return 0
    return block(f"the '{field}' text does not occur verbatim in any message that the researcher typed "
                 f"in this session. Only the researcher can authorize a {kind}. Ask the researcher, "
                 "then copy the words verbatim.")


def check_state(event: dict) -> int:
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    target = tool_input.get("file_path", "")
    if not target.endswith("state/PROJECT_STATE.md"):
        return 0
    try:
        from harness.store import DEFAULT_LIMITS, Store, find_data_root
        root = find_data_root()
        if Path(target).resolve() != (root / "state" / "PROJECT_STATE.md").resolve():
            return 0
        limit = Store(root).limits().get("project_state_chars", DEFAULT_LIMITS["project_state_chars"])
        path = Path(target)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if tool == "Write":
            new = tool_input.get("content", "")
        elif tool == "Edit":
            old, replacement = tool_input.get("old_string", ""), tool_input.get("new_string", "")
            count = -1 if tool_input.get("replace_all") else 1
            new = current.replace(old, replacement, count)
        elif tool == "MultiEdit":
            new = current
            for edit in tool_input.get("edits", []):
                new = new.replace(edit.get("old_string", ""), edit.get("new_string", ""),
                                  -1 if edit.get("replace_all") else 1)
        else:
            return 0
    except Exception as error:  # a hook bug must not stop DT
        print(f"dt_guard: state check skipped: {error}", file=sys.stderr)
        return 0
    if len(new) > limit:
        return block(f"PROJECT_STATE.md would have {len(new)} characters. The limit is {limit}. "
                     "Keep only current decisions, live hypotheses, validated evidence, and open questions. "
                     "Refer to settled material by record reference.")
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if event.get("hook_event_name") != "PreToolUse":
        return 0
    if event.get("tool_name") == "Bash":
        return check_publish(event)
    return check_state(event)


if __name__ == "__main__":
    raise SystemExit(main())
