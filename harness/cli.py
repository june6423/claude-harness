"""Command line interface: `python3 -m harness <command>` or `bin/harness <command>`."""

from __future__ import annotations

import argparse
import json
import os
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
    record = store.publish(args.kind, text, args.actor, dry_run=args.dry_run)
    print(f"VALID: would publish {record.record_id}" if args.dry_run else record.ref)
    return 0


def cmd_handoff_draft(args) -> int:
    store = Store(find_data_root(args.data_root))
    text = store.handoff_draft(args.experiment)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    else:
        sys.stdout.write(text)
    return 0


def cmd_status(args) -> int:
    """A short overview for the researcher: each experiment, its latest handoff, tasks, and verdicts."""
    store = Store(find_data_root(args.data_root))
    experiments: dict[str, dict] = {}
    for record in store.records("handoff"):
        experiments.setdefault(record.fields["experiment_id"], {})["handoff"] = record
    for record in store.records("task"):
        experiments.setdefault(record.fields["experiment_id"], {}).setdefault("tasks", []).append(record)
    verdicts = list(store.records("verdict"))
    for experiment, items in sorted(experiments.items()):
        if args.experiment and experiment != args.experiment:
            continue
        handoff = items.get("handoff")
        head = (f"R{handoff.fields['revision']} {handoff.fields['status']} {handoff.fields['authorization']}"
                if handoff else "no handoff")
        print(f"{experiment}: {head}")
        if handoff:
            usage, limits = store.budget_usage(handoff.ref), store.limits()
            print(f"  budget R{handoff.fields['revision']}: run tasks {usage['run_tasks']}/{limits['run_tasks_per_handoff']}, "
                  f"FULL {usage['full_tasks']}/{limits['full_tasks_per_handoff']}, "
                  f"max recovery attempt {usage['max_recovery_attempt']}/{limits['recovery_attempts']}")
        for task in items.get("tasks", []):
            result_id = f"RESULT-{task.record_id}"
            outcome = store.load(result_id).fields["outcome"] if store.path_of(result_id).exists() else "WAITING"
            print(f"  {task.record_id} {task.fields['mode']} {task.fields['execution_class']} {outcome}")
        refs = {r.ref for r in store.records("handoff") if r.fields["experiment_id"] == experiment}
        for verdict in verdicts:
            if verdict.fields["handoff"] in refs:
                print(f"  {verdict.record_id} {verdict.fields['phase']} {verdict.fields['overall']}")
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


def cmd_executor(args) -> int:
    from .executor import Executor, Worker
    store = Store(find_data_root(args.data_root))
    if args.action == "watch":
        Executor(store).watch(once=args.once)
    elif args.action == "status":
        for row in Executor(store).status():
            print(json.dumps(row, ensure_ascii=False))
    else:
        if not args.task:
            raise RecordError("executor worker needs a task ID")
        Worker(store, args.task).run()
    return 0


def cmd_wait(args) -> int:
    import time
    store = Store(find_data_root(args.data_root))
    task_id = args.task.split("@", 1)[0]
    store.load(task_id)
    deadline = time.monotonic() + args.timeout_minutes * 60
    result_id = f"RESULT-{task_id}"
    while not store.path_of(result_id).exists():
        if time.monotonic() > deadline:
            print(f"WAITING {task_id}: no result after {args.timeout_minutes} minutes")
            return 3
        time.sleep(args.poll_seconds)
    record = store.load(result_id)
    fields = record.fields
    print(record.ref)
    print(json.dumps({k: fields[k] for k in ("outcome", "transport", "preflight", "exit_code",
                                             "missing_outputs", "message")}, ensure_ascii=False))
    return 0


GIT_VERBS = ("push", "fetch", "pull", "ls-remote")
GIT_FORBIDDEN = ("--force", "-f", "--force-with-lease", "--force-if-includes", "--mirror", "--delete", "-d",
                 "--all", "--prune", "--repo", "--receive-pack", "--upload-pack", "--exec")


def cmd_git(args) -> int:
    """Network Git for a configured workspace, run outside the tower sandbox.
    Only the 'origin' remote is used, and its URL must equal the configured remote."""
    import subprocess
    root = find_data_root(args.data_root)
    repo = Path(args.repo).resolve()
    workspaces = (Store(root).config().get("workspaces") or {})
    match = [w for w in workspaces.values() if (root / w["path"]).resolve() == repo]
    if not match:
        raise RecordError(f"{repo} is not a configured workspace in config.json")
    url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True,
                         text=True).stdout.strip()
    if url != match[0].get("remote"):
        raise RecordError(f"origin is {url!r}, but config.json gives {match[0].get('remote')!r}")
    if args.verb not in GIT_VERBS:
        raise RecordError(f"verb must be one of {', '.join(GIT_VERBS)}")
    rest = list(args.rest)
    for word in rest:
        if word in GIT_FORBIDDEN or word.startswith(("--force", "--mirror", "--delete", "--receive-pack",
                                                     "--upload-pack", "--exec", "--repo")) \
                or (args.verb == "push" and (word.startswith("+") or word.startswith(":"))):
            raise RecordError(f"argument {word!r} is not allowed")
    command = ["git", "-C", str(repo), args.verb]
    if args.verb == "pull":
        command.append("--ff-only")
    command += ["origin", *rest]
    return subprocess.run(command, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).returncode


def cmd_tower(args) -> int:
    if args.action in ("start", "restart"):
        session = tower.start(args.name, find_data_root(args.data_root), args.model, args.prompt,
                              replace=args.action == "restart", resume=args.resume)
        print(json.dumps(session, ensure_ascii=False))
    elif args.action == "stop":
        removed = tower.stop(args.name)
        print(f"stopped and removed: {', '.join(removed) or 'none'}")
    elif args.action == "command":
        print(" ".join(tower.start_command(args.name, find_data_root(args.data_root), args.model)))
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
    p.add_argument("--dry-run", action="store_true", help="validate only. Do not store the record")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("handoff-draft", help="write a draft of the next handoff revision")
    p.add_argument("experiment")
    p.add_argument("--out", help="draft file path (default: print)")
    p.set_defaults(func=cmd_handoff_draft)

    p = sub.add_parser("status", help="overview of experiments, tasks, results, and verdicts")
    p.add_argument("--experiment")
    p.set_defaults(func=cmd_status)

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

    p = sub.add_parser("executor", help="run the executor daemon, show its status, or run one worker")
    p.add_argument("action", choices=("watch", "status", "worker"))
    p.add_argument("task", nargs="?")
    p.add_argument("--once", action="store_true", help="watch: run one scan and exit")
    p.set_defaults(func=cmd_executor)

    p = sub.add_parser("wait", help="wait for the result of a task and print it")
    p.add_argument("task", help="task ID or reference")
    p.add_argument("--timeout-minutes", type=float, default=60)
    p.add_argument("--poll-seconds", type=float, default=5)
    p.set_defaults(func=cmd_wait)

    p = sub.add_parser("git", help="push, fetch, pull, or ls-remote the origin of a workspace (ET)")
    p.add_argument("repo", help="workspace repository path")
    p.add_argument("verb", choices=GIT_VERBS)
    p.add_argument("rest", nargs=argparse.REMAINDER, help="arguments after 'origin', such as a branch")
    p.set_defaults(func=cmd_git)

    p = sub.add_parser("tower", help="start or list tower sessions")
    p.add_argument("action", choices=("start", "restart", "stop", "status", "command"))
    p.add_argument("name", nargs="?", choices=tower.TOWERS)
    p.add_argument("--model", help="model for the session, such as sonnet or opus")
    p.add_argument("--prompt", help="first prompt for the session")
    p.add_argument("--resume", help="continue an earlier tower conversation (session ID or its prefix)")
    p.set_defaults(func=cmd_tower)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tower" and args.action in ("start", "restart", "stop", "command") and not args.name:
        print(f"harness: tower {args.action} needs a tower name", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except (RecordError, OSError) as error:
        messages_ = error.errors if isinstance(error, RecordError) else [str(error)]
        for message in messages_:
            print(f"harness: {message}", file=sys.stderr)
        return 2
