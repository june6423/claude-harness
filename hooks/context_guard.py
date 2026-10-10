#!/usr/bin/env python3
"""Claude Code Stop hook for each tower: rotate the session when the context is large.

After each turn, the hook reads the context size of the latest model request
from the transcript. When it is at least limits.context_rotate_tokens and the
tower has not written a handover, the hook blocks the stop (exit 2) and tells
the tower to write one with `bin/harness handover`. The executor daemon then
starts a new session for the tower. The hook never blocks twice in a row
(stop_hook_active), so it cannot keep a tower in a loop.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def context_tokens(transcript_path: str) -> int:
    tokens = 0
    with open(transcript_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (entry.get("message") or {}).get("usage") if entry.get("type") == "assistant" else None
            if usage:
                tokens = (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                          + usage.get("cache_read_input_tokens", 0))
    return tokens


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tower", required=True)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        if event.get("hook_event_name") != "Stop" or event.get("stop_hook_active"):
            return 0
        from harness.store import DEFAULT_LIMITS, find_data_root
        root = find_data_root()
        if (root / "runtime" / "rotate" / f"{args.tower}.json").exists():
            return 0  # the handover is written; the daemon restarts the session
        config = json.loads((root / "config.json").read_text(encoding="utf-8"))
        limit = {**DEFAULT_LIMITS, **(config.get("limits") or {})}["context_rotate_tokens"]
        tokens = context_tokens(event["transcript_path"])
        if tokens < limit:
            return 0
    except Exception as error:  # a hook bug must not stop the tower
        print(f"context_guard: {error}", file=sys.stderr)
        return 0
    print(f"context_guard: the context has {tokens} tokens. The limit is {limit}. Write a handover now: "
          f"load the harness-writing skill, write the handover draft in "
          f"{root}/runtime/drafts/{args.tower}/handover.md, and run "
          f"'bin/harness handover {args.tower} {root}/runtime/drafts/{args.tower}/handover.md'. "
          "Then end your turn. The harness starts a new session that reads your worklog.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
