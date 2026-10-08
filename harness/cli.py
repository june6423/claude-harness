"""Command line interface: `python3 -m harness <command>` or `bin/harness <command>`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import journal, messages, tower
from .records import KINDS, RecordError, kind_of_id
from .store import ACTORS, Store, find_data_root, init_data_root


def cmd_init(args) -> int:
    path = init_data_root(Path(args.path), write_pointer=not args.no_pointer)
    print(path)
    return 0


def cmd_root(args) -> int:
    print(find_data_root(args.data_root))
    return 0


def cmd_publish(args) -> int:
    store = Store(find_data_root(args.data_root))
    text = Path(args.file).read_text(encoding="utf-8")
    record = store.publish(args.kind, text, args.actor)
    print(record.ref)
    return 0


def cmd_show(args) -> int:
    store = Store(find_data_root(args.data_root))
    record_id = args.record.split("@", 1)[0]
    record = store.resolve(args.record, kind_of_id(record_id)) if "@" in args.record else store.load(record_id)
    if args.ref_only:
        print(record.ref)
    else:
        sys.stdout.write(record.data.decode("utf-8"))
    return 0


def cmd_list(args) -> int:
    store = Store(find_data_root(args.data_root))
    for record in store.records(args.kind):
        if args.experiment and record.fields.get("experiment_id") not in (args.experiment, None):
            continue
        summary = {k: record.fields[k] for k in ("status", "authorization", "mode", "execution_class",
                                                 "phase", "overall", "outcome") if k in record.fields}
        print(record.ref, json.dumps(summary, ensure_ascii=False) if summary else "")
    return 0


def cmd_verify(args) -> int:
    store = Store(find_data_root(args.data_root))
    problems = store.verify()
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_message(args) -> int:
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else sys.stdin.read()
    sys.stdout.write(messages.format_message(args.type, args.ref or [], body))
    return 0


def cmd_messages(args) -> int:
    path = find_data_root(args.data_root) / "messages.jsonl"
    entries = list(journal.read(path))
    for entry in entries[-args.last:] if args.last else entries:
        print(json.dumps(entry, ensure_ascii=False))
    return 0


def cmd_tower(args) -> int:
    if args.action == "start":
        session = tower.start(args.name)
        print(json.dumps(session, ensure_ascii=False))
    elif args.action == "command":
        print(" ".join(tower.start_command(args.name)))
    else:
        for session in tower.sessions():
            if session.get("name") in tower.TOWERS:
                print(json.dumps(session, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    parser.add_argument("--data-root", help="data root path (default: HARNESS_DATA_ROOT or .harness-data-root)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create a data root and point this repository to it")
    p.add_argument("path")
    p.add_argument("--no-pointer", action="store_true", help="do not write .harness-data-root")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("root", help="print the data root path")
    p.set_defaults(func=cmd_root)

    p = sub.add_parser("publish", help="validate a draft and store it as an immutable record")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("file")
    p.add_argument("--actor", required=True, choices=ACTORS)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("show", help="print a record by ID or reference")
    p.add_argument("record")
    p.add_argument("--ref-only", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("list", help="list records")
    p.add_argument("kind", nargs="?", choices=KINDS)
    p.add_argument("--experiment")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("verify", help="check hashes, journal entries, schemas, and references")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("message", help="format a cross-session message")
    p.add_argument("--type", required=True, choices=messages.MESSAGE_TYPES)
    p.add_argument("--ref", action="append")
    p.add_argument("--body-file")
    p.set_defaults(func=cmd_message)

    p = sub.add_parser("messages", help="print the message ledger")
    p.add_argument("--last", type=int)
    p.set_defaults(func=cmd_messages)

    p = sub.add_parser("tower", help="start or list tower sessions")
    p.add_argument("action", choices=("start", "status", "command"))
    p.add_argument("name", nargs="?", choices=tower.TOWERS)
    p.set_defaults(func=cmd_tower)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tower" and args.action in ("start", "command") and not args.name:
        print("harness: tower start needs a tower name", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (RecordError, OSError) as error:
        messages_ = error.errors if isinstance(error, RecordError) else [str(error)]
        for message in messages_:
            print(f"harness: {message}", file=sys.stderr)
        return 2
