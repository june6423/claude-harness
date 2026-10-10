"""Record schemas, parsing, serialization, and per-record validation.

Store-level checks that need other records (revision order, reference
resolution, authorization) live in store.py.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

NONE = "NONE"
MARKDOWN_KINDS = ("handoff", "report", "verdict", "directive", "grant", "revoke")
JSON_KINDS = ("task", "result")
KINDS = MARKDOWN_KINDS + JSON_KINDS
ID_PREFIX = {kind: kind.upper() for kind in KINDS}

EXPERIMENT_RE = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?$")
RECORD_ID_RE = re.compile(r"^(HANDOFF|REPORT|VERDICT|DIRECTIVE|GRANT|REVOKE|TASK|RESULT)-[A-Z0-9][A-Z0-9-]*$")
REF_RE = re.compile(r"^(?P<id>[A-Z]+-[A-Z0-9-]+)@sha256:(?P<sha>[0-9a-f]{64})$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GATE_ID_RE = re.compile(r"^G[0-9]+$")

STATUSES = ("DRAFT", "APPROVED")
AUTHORIZATIONS = ("IMPLEMENT_ONLY", "VALIDATION_RUN", "FULL_RUN")
PHASES = ("PRE_RUN", "POST_RUN")
GATE_VERDICTS = ("VALID", "PARTIAL", "INVALID", "NOT_VERIFIABLE")
FULL_RUN_CONDITIONS = (NONE, "ALL_PRE_RUN_GATES_VALID")
SOURCES = ("HANDOFF", "USER")
MODES = ("gpu-status", "inspect", "run", "allocate-run")
EXECUTION_MODES = ("run", "allocate-run")
EXECUTION_CLASSES = (NONE, "VALIDATION", "FULL")
GPU_POLICIES = ("FIXED", "BEST_EFFORT_JIT")
OUTCOMES = ("PASS", "FAIL", "REJECTED", "INTERRUPTED")
REPORT_SECTIONS = ("Changes", "Evidence", "Proposals", "Deviations")
DIRECTIVE_TARGETS = ("et", "vt")
DIRECTIVE_SCOPES = ("READ_ONLY", "LOCAL_CHANGE", "REMOTE_RUN")
GRANT_HOLDERS = ("et", "vt")
# Standing authority that a tower can use without asking the researcher again.
# A grant never covers a research decision, a handoff approval, or a budget increase.
GRANT_CATEGORIES = {
    "PUSH_BRANCH": "Push branches that match the constraint to origin with bin/harness git.",
    "LOCAL_IMPLEMENTATION": "Change, test, and commit local code inside the scope of an APPROVED handoff.",
    "RUN_VALIDATION": "Publish VALIDATION run tasks under an APPROVED handoff.",
    "RERUN_AFTER_MECHANICAL_FAILURE": "Publish a recovery task after a mechanical failure, such as out of memory, "
                                      "a wrong path, or a missing file, inside the budgets.",
    "START_FULL_AFTER_VALID": "Publish the FULL task after VT gives a VALID PRE_RUN verdict under a FULL_RUN handoff.",
    "USE_SERVER_RESOURCES": "Use the servers, GPUs, and containers that the constraint names.",
    "CONTAINER_SETUP": "Make temporary server-only changes in a registered container, such as a package install.",
    "ANSWER_TOWER_QUESTIONS": "Answer read-only questions from other towers without a directive.",
}
GRANT_EXPIRES_RE = re.compile(r"^(NEVER|DATE:\d{4}-\d{2}-\d{2}|HANDOFF:[A-Z]+-[A-Z0-9-]+@sha256:[0-9a-f]{64})$")
MAX_TIMEOUT_SECONDS = 604800


class RecordError(ValueError):
    """A record failed validation. `errors` lists each problem."""

    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_ref(record_id: str, data: bytes) -> str:
    return f"{record_id}@sha256:{sha256_bytes(data)}"


def parse_ref(value: Any) -> tuple[str, str]:
    match = REF_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise RecordError(f"not a record reference: {value!r}")
    return match["id"], match["sha"]


def kind_of_id(record_id: str) -> str:
    prefix = record_id.split("-", 1)[0]
    for kind, id_prefix in ID_PREFIX.items():
        if prefix == id_prefix:
            return kind
    raise RecordError(f"unknown record ID prefix: {record_id!r}")


def extension(kind: str) -> str:
    return ".md" if kind in MARKDOWN_KINDS else ".json"


# Parsing and serialization


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise RecordError("Markdown record must start with a '---' line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise RecordError("front matter has no closing '---' line") from None
    try:
        meta = json.loads("\n".join(lines[1:end]))
    except json.JSONDecodeError as error:
        raise RecordError(f"front matter is not valid JSON: {error}") from None
    if not isinstance(meta, dict):
        raise RecordError("front matter must be one JSON object")
    return meta, "\n".join(lines[end + 1:])


def parse(kind: str, text: str) -> tuple[dict[str, Any], str]:
    """Return (fields, body). The body is empty for JSON records."""
    if kind in MARKDOWN_KINDS:
        return split_front_matter(text)
    try:
        fields = json.loads(text)
    except json.JSONDecodeError as error:
        raise RecordError(f"record is not valid JSON: {error}") from None
    if not isinstance(fields, dict):
        raise RecordError("JSON record must be one object")
    return fields, ""


def serialize(kind: str, fields: dict[str, Any], body: str = "") -> bytes:
    if kind in MARKDOWN_KINDS:
        front = json.dumps(fields, indent=2, ensure_ascii=False)
        return f"---\n{front}\n---\n{body.lstrip(chr(10))}".encode("utf-8")
    return (json.dumps(fields, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# Field checks. Each check appends messages to `errors` and never raises.


class Checker:
    def __init__(self, fields: dict[str, Any], context: str = ""):
        self.fields = fields
        self.context = context
        self.errors: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(f"{self.context}{message}")

    def keys(self, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> None:
        for key in required:
            if key not in self.fields:
                self.error(f"missing field '{key}'")
        for key in self.fields:
            if key not in required and key not in optional:
                self.error(f"unknown field '{key}'")

    def get(self, key: str) -> Any:
        return self.fields.get(key)

    def choice(self, key: str, options: tuple[str, ...]) -> None:
        if key in self.fields and self.fields[key] not in options:
            self.error(f"'{key}' must be one of {', '.join(options)}")

    def text(self, key: str, allow_none: bool = False) -> None:
        value = self.fields.get(key)
        if key not in self.fields:
            return
        if not isinstance(value, str) or not value.strip():
            self.error(f"'{key}' must be a non-empty string")
        elif value == NONE and not allow_none:
            self.error(f"'{key}' must not be NONE")

    def pattern(self, key: str, regex: re.Pattern[str], what: str, allow_none: bool = False) -> None:
        value = self.fields.get(key)
        if key not in self.fields or (allow_none and value == NONE):
            return
        if not isinstance(value, str) or not regex.match(value):
            self.error(f"'{key}' must be {what}")

    def ref(self, key: str, allow_none: bool = False) -> None:
        self.pattern(key, REF_RE, "a record reference ID@sha256:<hex>", allow_none)

    def integer(self, key: str, minimum: int, maximum: int | None = None) -> None:
        value = self.fields.get(key)
        if key not in self.fields:
            return
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum or (
            maximum is not None and value > maximum
        ):
            bound = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
            self.error(f"'{key}' must be an integer {bound}")

    def string_list(self, key: str, regex: re.Pattern[str] | None = None,
                    what: str = "a non-empty string", non_empty: bool = False) -> None:
        value = self.fields.get(key)
        if key not in self.fields:
            return
        if not isinstance(value, list):
            self.error(f"'{key}' must be a list")
            return
        if non_empty and not value:
            self.error(f"'{key}' must not be empty")
        for item in value:
            if not isinstance(item, str) or not item.strip() or (regex and not regex.match(item)):
                self.error(f"each item of '{key}' must be {what}")
                return


def _common(checker: Checker, kind: str) -> None:
    if checker.get("kind") != kind:
        checker.error(f"'kind' must be '{kind}'")


def validate_handoff(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "experiment_id", "revision", "predecessor", "status", "authorization",
            "approval", "gates", "full_run_condition"), ("id",))
    _common(c, "handoff")
    c.pattern("experiment_id", EXPERIMENT_RE, "an upper-case experiment ID")
    c.integer("revision", 1)
    c.ref("predecessor", allow_none=True)
    c.choice("status", STATUSES)
    c.choice("authorization", AUTHORIZATIONS)
    c.text("approval", allow_none=True)
    c.choice("full_run_condition", FULL_RUN_CONDITIONS)
    gates = fields.get("gates")
    phases: set[str] = set()
    if "gates" in fields:
        if not isinstance(gates, list):
            c.error("'gates' must be a list")
        else:
            seen: set[str] = set()
            for index, gate in enumerate(gates):
                g = Checker(gate if isinstance(gate, dict) else {}, f"gates[{index}]: ")
                if not isinstance(gate, dict):
                    g.error("must be an object")
                else:
                    g.keys(("id", "phase", "check"))
                    g.pattern("id", GATE_ID_RE, "G<number>")
                    g.choice("phase", PHASES)
                    g.text("check")
                    if gate.get("id") in seen:
                        g.error(f"duplicate gate ID {gate.get('id')!r}")
                    seen.add(gate.get("id"))
                    phases.add(gate.get("phase"))
                c.errors.extend(g.errors)
    if fields.get("revision") == 1 and fields.get("predecessor") not in (NONE, None):
        c.error("revision 1 must have predecessor NONE")
    if isinstance(fields.get("revision"), int) and fields["revision"] > 1 and fields.get("predecessor") == NONE:
        c.error("a revision after 1 must name its predecessor")
    if fields.get("status") == "APPROVED":
        if fields.get("approval") == NONE:
            c.error("an APPROVED handoff needs an 'approval' quotation")
        if fields.get("authorization") in ("VALIDATION_RUN", "FULL_RUN") and "POST_RUN" not in phases:
            c.error("an APPROVED VALIDATION_RUN or FULL_RUN handoff needs at least one POST_RUN gate")
    if (fields.get("authorization") == "FULL_RUN"
            or fields.get("full_run_condition") == "ALL_PRE_RUN_GATES_VALID") and "PRE_RUN" not in phases:
        c.error("FULL_RUN or ALL_PRE_RUN_GATES_VALID needs at least one PRE_RUN gate")
    if fields.get("full_run_condition") == "ALL_PRE_RUN_GATES_VALID" and fields.get("authorization") != "VALIDATION_RUN":
        c.error("full_run_condition applies only to a VALIDATION_RUN handoff")
    if not body.strip():
        c.error("handoff body must not be empty")
    return c.errors


def validate_task(fields: dict[str, Any], body: str = "") -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "experiment_id", "handoff", "source", "user_request", "mode", "execution_class",
            "server", "repo", "branch", "commit", "runtime", "entrypoint", "instructions", "env",
            "output_root", "required_outputs", "gpu", "timeout_seconds", "pre_run_verdict", "lineage"),
           ("id", "fetch_outputs", "directive"))
    _common(c, "task")
    c.pattern("experiment_id", EXPERIMENT_RE, "an upper-case experiment ID")
    c.ref("handoff", allow_none=True)
    c.choice("source", SOURCES)
    c.text("user_request", allow_none=True)
    c.choice("mode", MODES)
    c.choice("execution_class", EXECUTION_CLASSES)
    c.text("server")
    c.text("repo")
    c.text("branch", allow_none=True)
    c.pattern("commit", COMMIT_RE, "a full 40-character commit SHA", allow_none=True)
    c.text("instructions", allow_none=True)
    c.integer("timeout_seconds", 1, MAX_TIMEOUT_SECONDS)
    c.ref("pre_run_verdict", allow_none=True)
    c.ref("directive", allow_none=True)

    mode = fields.get("mode")
    execution = mode in EXECUTION_MODES
    source = fields.get("source")
    if source == "USER" and fields.get("user_request") == NONE:
        c.error("a USER task must copy the user request")
    if source == "HANDOFF" and fields.get("user_request") not in (NONE, None):
        c.error("a HANDOFF task must have user_request NONE")
    if execution:
        if fields.get("execution_class") not in ("VALIDATION", "FULL"):
            c.error(f"mode {mode} needs execution_class VALIDATION or FULL")
        if fields.get("handoff") == NONE:
            c.error(f"mode {mode} needs a handoff reference")
        for key in ("branch", "commit"):
            if fields.get(key) == NONE:
                c.error(f"mode {mode} needs '{key}'")
        if fields.get("instructions") not in (NONE, None):
            c.error(f"mode {mode} must have instructions NONE")
    elif mode in MODES:
        if fields.get("execution_class") != NONE:
            c.error(f"mode {mode} needs execution_class NONE")
        if mode == "inspect" and fields.get("instructions") == NONE:
            c.error("mode inspect needs instructions")

    runtime = fields.get("runtime")
    if "runtime" in fields:
        expected = {"run": "container", "allocate-run": "template"}.get(mode)
        if expected:
            if not (isinstance(runtime, dict) and set(runtime) == {expected}
                    and isinstance(runtime[expected], str) and runtime[expected].strip()):
                c.error(f"mode {mode} needs runtime {{\"{expected}\": <name>}}")
        elif runtime != NONE:
            c.error(f"mode {mode} needs runtime NONE")

    entrypoint = fields.get("entrypoint")
    if "entrypoint" in fields:
        if not isinstance(entrypoint, list) or any(not isinstance(a, str) or not a for a in entrypoint):
            c.error("'entrypoint' must be a list of non-empty strings")
        elif execution and not entrypoint:
            c.error(f"mode {mode} needs a non-empty entrypoint")
        elif not execution and entrypoint:
            c.error(f"mode {mode} needs an empty entrypoint")
        elif entrypoint and (entrypoint[0].startswith(("/", "-")) or ".." in entrypoint[0].split("/")
                             or "/" not in entrypoint[0].lstrip("./")):
            c.error("entrypoint[0] must be a script path inside the repository, such as scripts/run.sh")

    env = fields.get("env")
    if "env" in fields and not (isinstance(env, dict) and all(
            isinstance(k, str) and k and isinstance(v, str) for k, v in env.items())):
        c.error("'env' must map strings to strings")

    output_root = fields.get("output_root")
    if "output_root" in fields:
        if execution and not (isinstance(output_root, str) and output_root.startswith("/")):
            c.error(f"mode {mode} needs an absolute output_root")
        if not execution and output_root != NONE:
            c.error(f"mode {mode} needs output_root NONE")

    outputs = fields.get("required_outputs")
    if "required_outputs" in fields:
        if not isinstance(outputs, list):
            c.error("'required_outputs' must be a list")
        elif execution and not outputs:
            c.error(f"mode {mode} needs a non-empty required_outputs list")
        elif not execution and outputs:
            c.error(f"mode {mode} needs an empty required_outputs list")
        else:
            for path in outputs:
                if (not isinstance(path, str) or not path or path.startswith("/")
                        or ".." in path.split("/")):
                    c.error("each required output must be a relative path inside output_root")
                    break

    fetch = fields.get("fetch_outputs", [])
    if not isinstance(fetch, list) or any(not isinstance(p, str) for p in fetch):
        c.error("'fetch_outputs' must be a list of paths")
    elif fetch:
        if not execution:
            c.error(f"mode {mode} needs an empty fetch_outputs list")
        elif isinstance(outputs, list) and any(p not in outputs for p in fetch):
            c.error("each path in fetch_outputs must also be in required_outputs")
        if len(set(fetch)) != len(fetch):
            c.error("fetch_outputs must not repeat a path")

    gpu = fields.get("gpu")
    if "gpu" in fields and gpu != NONE:
        g = Checker(gpu if isinstance(gpu, dict) else {}, "gpu: ")
        if not isinstance(gpu, dict):
            g.error("must be NONE or an object")
        else:
            g.keys(("count", "policy", "devices", "constraints"))
            g.integer("count", 1)
            g.choice("policy", GPU_POLICIES)
            g.text("constraints", allow_none=True)
            devices = gpu.get("devices")
            if gpu.get("policy") == "FIXED":
                if not (isinstance(devices, list) and all(isinstance(d, int) and d >= 0 for d in devices)
                        and len(devices) == gpu.get("count")):
                    g.error("FIXED needs 'devices' as a list of 'count' GPU indices")
            elif gpu.get("policy") == "BEST_EFFORT_JIT" and devices != "SELECT_AT_RUN_TIME":
                g.error("BEST_EFFORT_JIT needs devices SELECT_AT_RUN_TIME")
        c.errors.extend(g.errors)
    if mode == "allocate-run" and gpu == NONE:
        c.error("mode allocate-run needs a gpu request")

    if fields.get("execution_class") == "FULL" and fields.get("pre_run_verdict") == NONE:
        c.error("a FULL task needs a pre_run_verdict reference")
    if fields.get("execution_class") != "FULL" and fields.get("pre_run_verdict") not in (NONE, None):
        c.error("only a FULL task can have a pre_run_verdict")

    lineage = fields.get("lineage")
    if "lineage" in fields:
        g = Checker(lineage if isinstance(lineage, dict) else {}, "lineage: ")
        if not isinstance(lineage, dict):
            g.error("must be an object")
        else:
            g.keys(("parent", "recovery_of", "attempt"))
            g.pattern("parent", RECORD_ID_RE, "a task ID", allow_none=True)
            g.pattern("recovery_of", RECORD_ID_RE, "a task ID", allow_none=True)
            g.integer("attempt", 0)
            if (lineage.get("recovery_of") == NONE) != (lineage.get("attempt") == 0):
                g.error("attempt must be 0 exactly when recovery_of is NONE")
        c.errors.extend(g.errors)
    return c.errors


def compute_outcome(fields: dict[str, Any]) -> str:
    if (fields.get("transport") == "OK" and fields.get("preflight") == "PASS"
            and fields.get("exit_code") == 0 and fields.get("missing_outputs") == []):
        return "PASS"
    return "FAIL"


def validate_result(fields: dict[str, Any], body: str = "") -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "task", "transport", "preflight", "exit_code", "outcome", "git", "runtime",
            "outputs", "missing_outputs", "started_at", "ended_at", "logs", "message"),
           ("id", "fetched", "fetch_skipped"))
    _common(c, "result")
    c.ref("task")
    c.choice("transport", ("OK", "ERROR"))
    c.choice("preflight", ("PASS", "FAIL", "NOT_RUN"))
    c.choice("outcome", OUTCOMES)
    c.text("message", allow_none=True)
    exit_code = fields.get("exit_code")
    if "exit_code" in fields and exit_code is not None and (
            not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        c.error("'exit_code' must be an integer or null")
    c.string_list("missing_outputs")
    outputs = fields.get("outputs")
    if "outputs" in fields:
        if not isinstance(outputs, list):
            c.error("'outputs' must be a list")
        else:
            for item in outputs:
                o = Checker(item if isinstance(item, dict) else {}, "outputs: ")
                o.keys(("path", "bytes", "sha256"))
                o.text("path")
                o.integer("bytes", 0)
                o.pattern("sha256", re.compile(r"^[0-9a-f]{64}$"), "a SHA-256 hex digest")
                c.errors.extend(o.errors)
    fetched = fields.get("fetched", [])
    if not isinstance(fetched, list):
        c.error("'fetched' must be a list")
    else:
        for item in fetched:
            f = Checker(item if isinstance(item, dict) else {}, "fetched: ")
            f.keys(("path", "bytes", "sha256", "local_path"))
            f.text("path")
            f.text("local_path")
            f.integer("bytes", 0)
            f.pattern("sha256", re.compile(r"^[0-9a-f]{64}$"), "a SHA-256 hex digest")
            c.errors.extend(f.errors)
    if "fetch_skipped" in fields:
        c.string_list("fetch_skipped")
    for key in ("git", "runtime", "logs"):
        if key in fields and not isinstance(fields[key], dict):
            c.error(f"'{key}' must be an object")
    if fields.get("outcome") in ("PASS", "FAIL") and fields.get("outcome") != compute_outcome(fields):
        c.error(f"outcome must be {compute_outcome(fields)} for these transport, preflight, "
                "exit_code, and missing_outputs values")
    return c.errors


def report_sections(body: str) -> list[str]:
    return [line[3:].strip() for line in body.split("\n") if line.startswith("## ")]


def validate_report(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "experiment_id", "handoff", "commits", "tasks", "results"), ("id",))
    _common(c, "report")
    c.pattern("experiment_id", EXPERIMENT_RE, "an upper-case experiment ID")
    c.ref("handoff")
    c.string_list("commits", COMMIT_RE, "a full commit SHA")
    c.string_list("tasks", REF_RE, "a record reference")
    c.string_list("results", REF_RE, "a record reference")
    sections = report_sections(body)
    for section in REPORT_SECTIONS:
        if sections.count(section) != 1:
            c.error(f"report body needs exactly one '## {section}' section")
    return c.errors


def validate_verdict(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "phase", "handoff", "subject", "gates"), ("id", "overall"))
    _common(c, "verdict")
    c.choice("phase", PHASES)
    c.ref("handoff")
    subject = fields.get("subject")
    if "subject" in fields:
        s = Checker(subject if isinstance(subject, dict) else {}, "subject: ")
        if not isinstance(subject, dict):
            s.error("must be an object")
        else:
            s.keys(("commit", "report", "results"))
            s.pattern("commit", COMMIT_RE, "a full commit SHA", allow_none=True)
            s.ref("report", allow_none=True)
            s.string_list("results", REF_RE, "a record reference")
            if fields.get("phase") == "PRE_RUN" and subject.get("commit") == NONE:
                s.error("a PRE_RUN verdict needs a commit")
            if fields.get("phase") == "POST_RUN" and not subject.get("results"):
                s.error("a POST_RUN verdict needs at least one result")
        c.errors.extend(s.errors)
    gates = fields.get("gates")
    if "gates" in fields:
        if not isinstance(gates, list) or not gates:
            c.error("'gates' must be a non-empty list")
        else:
            seen: set[str] = set()
            for index, gate in enumerate(gates):
                g = Checker(gate if isinstance(gate, dict) else {}, f"gates[{index}]: ")
                if not isinstance(gate, dict):
                    g.error("must be an object")
                else:
                    g.keys(("id", "verdict", "evidence"))
                    g.pattern("id", GATE_ID_RE, "G<number>")
                    g.choice("verdict", GATE_VERDICTS)
                    g.string_list("evidence", non_empty=True)
                    if gate.get("id") in seen:
                        g.error(f"duplicate gate ID {gate.get('id')!r}")
                    seen.add(gate.get("id"))
                c.errors.extend(g.errors)
    return c.errors


def validate_directive(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "target", "scope", "instruction", "experiment_id"), ("id",))
    _common(c, "directive")
    c.choice("target", DIRECTIVE_TARGETS)
    c.choice("scope", DIRECTIVE_SCOPES)
    c.text("instruction")
    c.pattern("experiment_id", EXPERIMENT_RE, "an upper-case experiment ID", allow_none=True)
    if not body.strip():
        c.error("directive body must not be empty. Write the concrete request")
    return c.errors


def validate_grant(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "holder", "category", "constraints", "instruction", "experiment_id", "expires"), ("id",))
    _common(c, "grant")
    c.choice("holder", GRANT_HOLDERS)
    c.choice("category", tuple(GRANT_CATEGORIES))
    c.text("constraints", allow_none=True)
    c.text("instruction")
    c.pattern("experiment_id", EXPERIMENT_RE, "an upper-case experiment ID", allow_none=True)
    c.pattern("expires", GRANT_EXPIRES_RE, "NEVER, DATE:YYYY-MM-DD, or HANDOFF:<handoff reference>")
    return c.errors


def validate_revoke(fields: dict[str, Any], body: str) -> list[str]:
    c = Checker(fields)
    c.keys(("kind", "revokes", "instruction"), ("id",))
    _common(c, "revoke")
    c.string_list("revokes", REF_RE, "a grant reference", non_empty=True)
    c.text("instruction")
    return c.errors


VALIDATORS = {
    "grant": validate_grant,
    "revoke": validate_revoke,
    "directive": validate_directive,
    "handoff": validate_handoff,
    "task": validate_task,
    "result": validate_result,
    "report": validate_report,
    "verdict": validate_verdict,
}


def validate(kind: str, fields: dict[str, Any], body: str) -> None:
    if kind not in VALIDATORS:
        raise RecordError(f"unknown record kind {kind!r}")
    errors = VALIDATORS[kind](fields, body)
    if errors:
        raise RecordError(errors)
