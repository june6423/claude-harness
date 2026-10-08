#!/usr/bin/env python3
"""Claude Code hook: record each sent and received cross-session message.

Events: PreToolUse (matcher SendMessage) and UserPromptSubmit.
The hook never blocks and never prints to stdout, because UserPromptSubmit
stdout goes into the model context.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def entries(event: dict, tower: str) -> list[dict]:
    from harness.messages import parse_header, parse_inbound
    from harness.records import RecordError

    def header(text: str) -> dict:
        try:
            parsed = parse_header(text)
            return {"header": {"type": parsed["type"], "refs": parsed["refs"]}, "header_error": None}
        except RecordError as error:
            return {"header": None, "header_error": str(error)}

    base = {"tower": tower, "session_id": event.get("session_id")}
    name = event.get("hook_event_name")
    if name == "PreToolUse" and event.get("tool_name") == "SendMessage":
        tool_input = event.get("tool_input") or {}
        text = tool_input.get("message") or tool_input.get("content") or ""
        return [{**base, "direction": "sent", "peer": tool_input.get("to"), "text": text, **header(text)}]
    if name == "UserPromptSubmit":
        return [{**base, "direction": "received", "peer": m["from_name"] or m["from"],
                 "text": m["text"], **header(m["text"])}
                for m in parse_inbound(event.get("prompt") or "")]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tower", required=True)
    args = parser.parse_args()
    try:
        from harness import journal
        from harness.store import find_data_root
        event = json.load(sys.stdin)
        found = entries(event, args.tower)
        if found:
            path = find_data_root() / "messages.jsonl"
            for entry in found:
                journal.append(path, entry)
    except Exception as error:  # a ledger failure must not stop the session
        print(f"message_ledger: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
