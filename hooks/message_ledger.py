#!/usr/bin/env python3
"""Claude Code hook: record each sent and received cross-session message.

Events: PreToolUse (matcher SendMessage) and UserPromptSubmit.
The hook never prints to stdout, because UserPromptSubmit stdout goes into
the model context. It blocks (exit 2) only a sent message over the rate
limit limits.messages_per_peer_per_10_minutes, to stop a message loop.
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
            root = find_data_root()
            path = root / "messages.jsonl"
            if found[0]["direction"] == "sent" and wrong_cycle(root, found[0]):
                journal.append(path, {**found[0], "blocked": "other cycle"})
                print(f"message_ledger: {found[0]['peer']!r} is a tower of another project. The towers of this "
                      f"project are {', '.join(sorted(set(wrong_cycle.names.values())))}. Send the message "
                      "to one of these.", file=sys.stderr)
                return 2
            if found[0]["direction"] == "sent" and over_rate_limit(root, path, found[0]):
                journal.append(path, {**found[0], "blocked": "rate limit"})
                print("message_ledger: too many messages to this peer in 10 minutes. This can be a message "
                      "loop. Stop and report the situation to the researcher.", file=sys.stderr)
                return 2
            for entry in found:
                journal.append(path, entry)
    except Exception as error:  # a ledger failure must not stop the session
        print(f"message_ledger: {error}", file=sys.stderr)
    return 0


def wrong_cycle(root: Path, entry: dict) -> bool:
    """True when the message goes to a tower name of another data root (another suffix).
    A name that is not a tower name, such as the maintenance session, passes."""
    import re
    from harness.tower import TOWERS, peers
    wrong_cycle.names = peers(root)
    peer = str(entry.get("peer") or "").split(" ")[0]
    match = re.fullmatch("(" + "|".join(sorted(TOWERS, key=len, reverse=True)) + r")(_[A-Za-z0-9_-]+)?", peer)
    return bool(match) and peer != wrong_cycle.names[match.group(1)]


def over_rate_limit(root: Path, path: Path, entry: dict) -> bool:
    from datetime import datetime, timedelta, timezone
    from harness import journal
    from harness.store import DEFAULT_LIMITS
    try:
        configured = json.loads((root / "config.json").read_text(encoding="utf-8")).get("limits") or {}
    except (OSError, json.JSONDecodeError):
        configured = {}
    limit = {**DEFAULT_LIMITS, **configured}["messages_per_peer_per_10_minutes"]
    since = datetime.now(timezone.utc) - timedelta(minutes=10)
    count = sum(1 for e in journal.read(path)
                if e.get("direction") == "sent" and not e.get("blocked") and e.get("tower") == entry["tower"]
                and e.get("peer") == entry["peer"] and datetime.fromisoformat(e["at"]) >= since)
    return count >= limit


if __name__ == "__main__":
    raise SystemExit(main())
