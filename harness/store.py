"""The data root: immutable record store, journal, and cross-record checks."""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import journal
from .records import (
    KINDS, NONE, RecordError, extension, kind_of_id, make_ref, parse, parse_ref,
    serialize, sha256_bytes, validate,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
POINTER_FILE = REPO_ROOT / ".harness-data-root"
ACTORS = ("DT", "ET", "VT", "EXECUTOR", "RESEARCHER")
# Character limits. config.json "limits" can change each value.
DEFAULT_LIMITS = {"handoff_body_chars": 12000, "report_body_chars": 9000, "verdict_body_chars": 9000,
                  "directive_body_chars": 9000, "project_state_chars": 18000,
                  # Budgets for each handoff revision. A new revision needs researcher approval,
                  # so the counts start again only after the researcher acts.
                  "run_tasks_per_handoff": 20, "full_tasks_per_handoff": 3, "recovery_attempts": 5,
                  # Cross-session messages from one tower to one peer in 10 minutes.
                  "messages_per_peer_per_10_minutes": 10,
                  # A tower writes a handover and gets a new session at this context size.
                  "context_rotate_tokens": 300000}
PROJECT_STATE_TEMPLATE = """# Project State

## Current decisions

NONE

## Live hypotheses

NONE

## Validated evidence

NONE

## Open questions

NONE
"""


@dataclass
class Record:
    record_id: str
    kind: str
    fields: dict[str, Any]
    body: str
    data: bytes

    @property
    def ref(self) -> str:
        return make_ref(self.record_id, self.data)


def find_data_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("HARNESS_DATA_ROOT"):
        return Path(os.environ["HARNESS_DATA_ROOT"]).expanduser().resolve()
    if POINTER_FILE.exists():
        return Path(POINTER_FILE.read_text(encoding="utf-8").strip()).expanduser().resolve()
    raise RecordError("no data root: use --data-root, HARNESS_DATA_ROOT, or 'harness init'")


def init_data_root(path: Path, write_pointer: bool = True) -> Path:
    path = path.expanduser().resolve()
    for kind in KINDS:
        (path / "records" / kind).mkdir(parents=True, exist_ok=True)
    (path / "artifacts").mkdir(exist_ok=True)
    (path / "worklog").mkdir(exist_ok=True)
    (path / "state").mkdir(exist_ok=True)
    (path / "runtime").mkdir(exist_ok=True)
    (path / "workspace").mkdir(exist_ok=True)
    config = path / "config.json"
    if not config.exists():
        config.write_text(json.dumps({"servers": {}}, indent=2) + "\n", encoding="utf-8")
    state = path / "state" / "PROJECT_STATE.md"
    if not state.exists():
        state.write_text(PROJECT_STATE_TEMPLATE, encoding="utf-8")
    ignore = path / ".gitignore"
    if not ignore.exists():
        ignore.write_text("runtime/\n.lock\nworkspace/\n", encoding="utf-8")
    for name in ("journal.jsonl", "messages.jsonl"):
        (path / name).touch()
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    if write_pointer:
        POINTER_FILE.write_text(str(path) + "\n", encoding="utf-8")
    return path


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not (self.root / "records").is_dir():
            raise RecordError(f"not a data root: {self.root}")
        self.journal_path = self.root / "journal.jsonl"
        self.messages_path = self.root / "messages.jsonl"

    # Reading

    def path_of(self, record_id: str) -> Path:
        kind = kind_of_id(record_id)
        return self.root / "records" / kind / f"{record_id}{extension(kind)}"

    def load(self, record_id: str) -> Record:
        path = self.path_of(record_id)
        if not path.exists():
            raise RecordError(f"record {record_id} does not exist")
        data = path.read_bytes()
        kind = kind_of_id(record_id)
        fields, body = parse(kind, data.decode("utf-8"))
        return Record(record_id, kind, fields, body, data)

    def resolve(self, ref: str, kind: str) -> Record:
        record_id, sha = parse_ref(ref)
        if kind_of_id(record_id) != kind:
            raise RecordError(f"{ref} is not a {kind} reference")
        record = self.load(record_id)
        if sha256_bytes(record.data) != sha:
            raise RecordError(f"{ref}: SHA-256 does not match the stored record")
        return record

    def ids(self, kind: str) -> list[str]:
        directory = self.root / "records" / kind
        return sorted(p.stem for p in directory.glob(f"*{extension(kind)}"))

    def records(self, kind: str | None = None) -> Iterator[Record]:
        for each in ([kind] if kind else KINDS):
            for record_id in self.ids(each):
                yield self.load(record_id)

    def latest_handoff(self, experiment_id: str) -> Record | None:
        best = None
        for record in self.records("handoff"):
            if record.fields.get("experiment_id") == experiment_id and (
                    best is None or record.fields["revision"] > best.fields["revision"]):
                best = record
        return best

    def handoff_draft(self, experiment_id: str) -> str:
        """A draft of the next handoff revision. It copies the gates and body of the latest revision."""
        latest = self.latest_handoff(experiment_id)
        if latest is None:
            fields = {"kind": "handoff", "experiment_id": experiment_id, "revision": 1, "predecessor": NONE,
                      "status": "DRAFT", "authorization": "IMPLEMENT_ONLY", "approval": NONE,
                      "gates": [{"id": "G1", "phase": "POST_RUN", "check": "<one sentence that VT can check>"}],
                      "full_run_condition": NONE}
            body = "## Scope\n\n<the experiment, its question, and the work for ET>\n"
        else:
            fields = {k: v for k, v in latest.fields.items() if k != "id"}
            fields.update(revision=latest.fields["revision"] + 1, predecessor=latest.ref,
                          status="DRAFT", approval=NONE)
            body = latest.body
        return serialize("handoff", fields, body).decode("utf-8")

    def config(self) -> dict[str, Any]:
        path = self.root / "config.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    # Publishing

    @contextmanager
    def locked(self) -> Iterator[None]:
        with (self.root / ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def limits(self) -> dict[str, int]:
        return {**DEFAULT_LIMITS, **(self.config().get("limits") or {})}

    def check_limits(self, kind: str, body: str) -> None:
        key = f"{kind}_body_chars"
        limit = self.limits().get(key)
        if limit is not None and len(body) > limit:
            raise RecordError(f"the {kind} body has {len(body)} characters. The limit is {limit} ({key}). "
                              "Remove repeated or settled material, or refer to it by record reference")

    def budget_usage(self, handoff_ref: str) -> dict[str, int]:
        usage = {"run_tasks": 0, "full_tasks": 0, "max_recovery_attempt": 0}
        for record in self.records("task"):
            if record.fields.get("handoff") != handoff_ref:
                continue
            if record.fields.get("mode") in ("run", "allocate-run"):
                usage["run_tasks"] += 1
            if record.fields.get("execution_class") == "FULL":
                usage["full_tasks"] += 1
            usage["max_recovery_attempt"] = max(usage["max_recovery_attempt"],
                                                record.fields.get("lineage", {}).get("attempt", 0))
        return usage

    def active_grants(self, holder: str | None = None, experiment: str | None = None,
                      today: str | None = None) -> list[Record]:
        """Grants that no revoke names and that have not expired."""
        from datetime import date
        today = today or date.today().isoformat()
        revoked = {ref for r in self.records("revoke") for ref in r.fields["revokes"]}
        active = []
        for grant in self.records("grant"):
            f = grant.fields
            if grant.ref in revoked or (holder and f["holder"] != holder):
                continue
            if experiment and f["experiment_id"] not in (NONE, experiment):
                continue
            expires = f["expires"]
            if expires.startswith("DATE:") and today > expires[5:]:
                continue
            if expires.startswith("HANDOFF:"):
                handoff = self.load(parse_ref(expires[8:])[0])
                latest = self.latest_handoff(handoff.fields["experiment_id"])
                if latest is None or latest.ref != expires[8:]:
                    continue
            active.append(grant)
        return active

    def check_budgets(self, fields: dict[str, Any]) -> None:
        """Refuse a new task over a budget. Only publish checks budgets; the executor does not."""
        if fields.get("handoff", NONE) == NONE:
            return
        limits = self.limits()
        usage = self.budget_usage(fields["handoff"])
        errors = []
        if fields.get("mode") in ("run", "allocate-run") and usage["run_tasks"] >= limits["run_tasks_per_handoff"]:
            errors.append(f"the handoff already has {usage['run_tasks']} run tasks. "
                          f"The limit is {limits['run_tasks_per_handoff']} (run_tasks_per_handoff)")
        if fields.get("execution_class") == "FULL" and usage["full_tasks"] >= limits["full_tasks_per_handoff"]:
            errors.append(f"the handoff already has {usage['full_tasks']} FULL tasks. "
                          f"The limit is {limits['full_tasks_per_handoff']} (full_tasks_per_handoff)")
        attempt = fields.get("lineage", {}).get("attempt", 0)
        if attempt > limits["recovery_attempts"]:
            errors.append(f"recovery attempt {attempt} is more than the limit {limits['recovery_attempts']} "
                          "(recovery_attempts)")
        if errors:
            errors.append("Stop and report to DT. Only the researcher can raise a limit in config.json, "
                          "or approve a new handoff revision")
            raise RecordError(errors)

    def publish(self, kind: str, text: str, actor: str, dry_run: bool = False) -> Record:
        if actor not in ACTORS:
            raise RecordError(f"actor must be one of {', '.join(ACTORS)}")
        fields, body = parse(kind, text)
        if "id" in fields:
            raise RecordError("a draft must not contain 'id'. The CLI assigns the ID")
        if kind == "verdict" and "overall" in fields:
            raise RecordError("a draft must not contain 'overall'. The CLI computes it")
        validate(kind, fields, body)
        self.check_limits(kind, body)
        if kind == "task":
            self.check_budgets(fields)
        with self.locked():
            extra = self.check_references(kind, fields)
            record_id = self.next_id(kind, fields)
            stored = {"id": record_id, **fields, **extra}
            data = serialize(kind, stored, body)
            if dry_run:
                return Record(record_id, kind, stored, body, data)
            path = self.path_of(record_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            except FileExistsError:
                raise RecordError(f"record {record_id} already exists") from None
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            record = Record(record_id, kind, stored, body, data)
            journal.append(self.journal_path, {"actor": actor, "event": "PUBLISHED", "ref": record.ref})
        return record

    def next_id(self, kind: str, fields: dict[str, Any]) -> str:
        if kind == "handoff":
            return f"HANDOFF-{fields['experiment_id']}-R{fields['revision']}"
        if kind == "result":
            task_id, _ = parse_ref(fields["task"])
            return f"RESULT-{task_id}"
        experiment = fields.get("experiment_id")
        if kind in ("directive", "grant") and experiment == NONE:
            experiment = "GENERAL"
        if kind == "revoke":
            experiment = "GENERAL"
        if kind == "verdict":
            experiment = self.resolve(fields["handoff"], "handoff").fields["experiment_id"]
        prefix = f"{kind.upper()}-{experiment}-"
        numbers = [int(m.group(1)) for i in self.ids(kind)
                   if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", i))]
        return f"{prefix}{max(numbers, default=0) + 1:03d}"

    def check_references(self, kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Check rules that need other records. Return fields that the CLI adds."""
        errors: list[str] = []

        def resolve(ref: str, ref_kind: str) -> Record | None:
            try:
                return self.resolve(ref, ref_kind)
            except RecordError as error:
                errors.extend(error.errors)
                return None

        extra: dict[str, Any] = {}
        if kind == "handoff":
            latest = self.latest_handoff(fields["experiment_id"])
            expected = latest.fields["revision"] + 1 if latest else 1
            if fields["revision"] != expected:
                errors.append(f"revision must be {expected}")
            elif latest and fields["predecessor"] != latest.ref:
                errors.append(f"predecessor must be {latest.ref}")
        elif kind == "task":
            self._check_task(fields, resolve, errors)
        elif kind == "result":
            resolve(fields["task"], "task")
        elif kind == "report":
            handoff = resolve(fields["handoff"], "handoff")
            if handoff and handoff.fields["experiment_id"] != fields["experiment_id"]:
                errors.append("report and handoff name different experiments")
            for ref in fields["tasks"]:
                resolve(ref, "task")
            for ref in fields["results"]:
                resolve(ref, "result")
        elif kind == "verdict":
            extra["overall"] = self._check_verdict(fields, resolve, errors)
        elif kind == "grant" and fields["expires"].startswith("HANDOFF:"):
            handoff = resolve(fields["expires"][len("HANDOFF:"):], "handoff")
            if handoff and fields["experiment_id"] not in (NONE, handoff.fields["experiment_id"]):
                errors.append("the grant and its expiry handoff name different experiments")
        elif kind == "revoke":
            for ref in fields["revokes"]:
                resolve(ref, "grant")
        if errors:
            raise RecordError(errors)
        return extra

    def _check_task(self, fields: dict[str, Any], resolve, errors: list[str]) -> None:
        handoff = None
        if fields["handoff"] != NONE:
            handoff = resolve(fields["handoff"], "handoff")
            if handoff and handoff.fields["experiment_id"] != fields["experiment_id"]:
                errors.append("task and handoff name different experiments")
        if fields["source"] == "HANDOFF":
            if fields["handoff"] == NONE:
                errors.append("a HANDOFF task needs a handoff reference")
            elif handoff:
                if handoff.fields["status"] != "APPROVED":
                    errors.append("the handoff is not APPROVED")
                # FULL_RUN also covers the VALIDATION runs that come before the VT PRE_RUN verdict.
                allowed = {"VALIDATION": ("VALIDATION_RUN", "FULL_RUN"), "FULL": ("FULL_RUN",)}.get(
                    fields["execution_class"])
                if allowed and handoff.fields["authorization"] not in allowed:
                    errors.append(f"execution_class {fields['execution_class']} needs authorization "
                                  f"{' or '.join(allowed)}, the handoff has {handoff.fields['authorization']}")
        if fields.get("directive", NONE) != NONE:
            directive = resolve(fields["directive"], "directive")
            if directive:
                needed = "REMOTE_RUN" if fields["mode"] in ("run", "allocate-run", "inspect") else None
                if directive.fields["target"] != "et":
                    errors.append("the directive does not target et")
                if needed and directive.fields["scope"] != needed:
                    errors.append(f"mode {fields['mode']} needs a directive with scope {needed}, "
                                  f"the directive has {directive.fields['scope']}")
                if directive.fields["experiment_id"] not in (NONE, fields["experiment_id"]):
                    errors.append("the directive names a different experiment")
        if fields["execution_class"] == "FULL" and fields["pre_run_verdict"] != NONE:
            verdict = resolve(fields["pre_run_verdict"], "verdict")
            if verdict:
                checks = (
                    (verdict.fields.get("phase") == "PRE_RUN", "the pre_run_verdict is not a PRE_RUN verdict"),
                    (verdict.fields.get("overall") == "VALID", "the pre_run_verdict is not VALID"),
                    (verdict.fields.get("subject", {}).get("commit") == fields["commit"],
                     "the pre_run_verdict names a different commit"),
                    (self._verdict_handoff_matches(verdict.fields.get("handoff"), fields["handoff"]),
                     "the pre_run_verdict names a different handoff"),
                )
                errors.extend(message for passed, message in checks if not passed)
        for key in ("parent", "recovery_of"):
            value = fields["lineage"][key]
            if value != NONE and not self.path_of(value).exists():
                errors.append(f"lineage {key} {value} does not exist")
        servers = self.config().get("servers") or {}
        if servers:
            server = servers.get(fields["server"])
            if server is None:
                errors.append(f"server {fields['server']!r} is not in config.json")
            elif fields["repo"] not in (server.get("repos") or {}):
                errors.append(f"repo {fields['repo']!r} is not configured for server {fields['server']!r}")

    def _verdict_handoff_matches(self, verdict_handoff: str | None, task_handoff: str) -> bool:
        """A PRE_RUN verdict binds the task handoff, or the predecessor that set
        ALL_PRE_RUN_GATES_VALID with the same PRE_RUN gates (the promotion to FULL_RUN)."""
        if verdict_handoff == task_handoff:
            return True
        try:
            successor = self.resolve(task_handoff, "handoff")
            if successor.fields["predecessor"] != verdict_handoff:
                return False
            predecessor = self.resolve(verdict_handoff, "handoff")
        except RecordError:
            return False

        def pre_run(record: Record) -> list:
            return sorted((g["id"], g["check"]) for g in record.fields["gates"] if g["phase"] == "PRE_RUN")

        return (predecessor.fields["full_run_condition"] == "ALL_PRE_RUN_GATES_VALID"
                and successor.fields["authorization"] == "FULL_RUN"
                and pre_run(predecessor) == pre_run(successor))

    def _check_verdict(self, fields: dict[str, Any], resolve, errors: list[str]) -> str:
        handoff = resolve(fields["handoff"], "handoff")
        subject = fields["subject"]
        if subject["report"] != NONE:
            resolve(subject["report"], "report")
        for ref in subject["results"]:
            resolve(ref, "result")
        if not handoff:
            return "NOT_VALID"
        expected = {g["id"] for g in handoff.fields["gates"] if g["phase"] == fields["phase"]}
        given = {g["id"] for g in fields["gates"]}
        if not expected:
            errors.append(f"the handoff has no {fields['phase']} gates")
        elif given != expected:
            errors.append(f"verdict gates must be exactly the {fields['phase']} gates "
                          f"{sorted(expected)}, got {sorted(given)}")
        valid = bool(expected) and given == expected and all(
            g["verdict"] == "VALID" for g in fields["gates"])
        return "VALID" if valid else "NOT_VALID"

    # Integrity check

    def verify(self) -> list[str]:
        problems: list[str] = []
        published = {e.get("ref") for e in journal.read(self.journal_path) if e.get("event") == "PUBLISHED"}
        for kind in KINDS:
            for record_id in self.ids(kind):
                path = self.path_of(record_id)
                try:
                    record = self.load(record_id)
                except RecordError as error:
                    problems.append(f"{record_id}: {error}")
                    continue
                if path.stat().st_mode & 0o222:
                    problems.append(f"{record_id}: file is writable")
                if record.fields.get("id") != record_id:
                    problems.append(f"{record_id}: 'id' field does not match the file name")
                if record.ref not in published:
                    problems.append(f"{record_id}: no PUBLISHED journal entry with this SHA-256")
                fields = {k: v for k, v in record.fields.items() if k not in ("id", "overall")}
                try:
                    validate(kind, fields, record.body)
                except RecordError as error:
                    problems.append(f"{record_id}: {error}")
                for item in record.fields.get("fetched", []) if kind == "result" else []:
                    local = self.root / item.get("local_path", "")
                    if not local.is_file():
                        problems.append(f"{record_id}: fetched file {item.get('local_path')} is missing")
                    elif sha256_bytes(local.read_bytes()) != item.get("sha256"):
                        problems.append(f"{record_id}: fetched file {item.get('local_path')} does not match its SHA-256")
                for ref in _refs_in(record.fields):
                    try:
                        self.resolve(ref, kind_of_id(parse_ref(ref)[0]))
                    except RecordError as error:
                        problems.append(f"{record_id}: {error}")
        return problems


def _refs_in(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        try:
            parse_ref(value)
        except RecordError:
            return
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _refs_in(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _refs_in(item)
