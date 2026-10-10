"""Phase 1 executor: authorize, preflight, run, and record each published task.

The daemon (`watch`) claims tasks and starts one detached worker process for
each claim. A worker survives a daemon restart. A run in the container
survives a worker restart: the wrapper writes its PID, logs, and exit code in
a run directory, and a new worker reattaches to it.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import journal
from .records import NONE, RecordError, validate
from .store import REPO_ROOT, Store
from .transport import Completed, Transport

LOG_CHUNK_BYTES = 1 << 20
TAIL_BYTES = 4000
START_GRACE_SECONDS = 30
DEFAULT_FETCH_MAX_FILE_BYTES = 1 << 20
DEFAULT_FETCH_MAX_TOTAL_BYTES = 20 << 20


def utc(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(epoch or time.time(), timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def pid_alive(pid: Any) -> bool:
    """True when the process exists and is not a zombie."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return True
    return state != "Z"


def reap_children() -> None:
    """Collect exited worker processes so that they do not stay as zombies."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


class Paths:
    def __init__(self, store: Store):
        base = store.root / "runtime" / "executor"
        self.base = base
        self.claims = base / "claims"
        self.live = base / "live"
        self.logs = store.root / "runtime" / "logs"
        # A Unix socket path has a limit of about 108 bytes, so SSH sockets use a short path.
        self.ssh = Path(f"/tmp/harness-{os.getuid()}") / "ssh"
        self.lock = base / "executor.lock"

    def claim(self, task_id: str) -> Path:
        return self.claims / f"{task_id}.json"

    def receipt(self, task_id: str) -> Path:
        return self.live / f"{task_id}.json"


class Rejected(Exception):
    """The task cannot run. The worker writes a REJECTED result."""


class Executor:
    def __init__(self, store: Store):
        self.store = store
        self.paths = Paths(store)
        config = store.config().get("executor", {})
        self.poll_seconds = float(config.get("poll_seconds", 10))

    # Daemon

    @contextmanager
    def singleton(self) -> Iterator[None]:
        self.paths.base.mkdir(parents=True, exist_ok=True)
        with self.paths.lock.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecordError("another executor already runs for this data root") from None
            yield

    def pending(self) -> list[str]:
        done = set(self.store.ids("result"))
        return [task_id for task_id in self.store.ids("task")
                if f"RESULT-{task_id}" not in done and not self.paths.claim(task_id).exists()]

    def active_slots(self) -> set[tuple[str, str]]:
        slots = set()
        for claim in self.paths.claims.glob("*.json"):
            data = read_json(claim)
            if data.get("slot") and not self.store.path_of(f"RESULT-{data['task_id']}").exists():
                slots.add(tuple(data["slot"]))
        return slots

    def rotate_towers(self) -> list[str]:
        """Start a new session for each tower that wrote a handover, once the tower is idle."""
        if not (self.store.config().get("towers") or {}).get("auto_rotate", True):
            return []
        from . import tower as towers
        rotated = []
        for request in sorted((self.store.root / "runtime" / "rotate").glob("*.json")):
            name = request.stem
            try:
                sessions = towers.named(name)
                live = [s for s in sessions if s.get("state") not in ("stopped", "failed")]
                if any(s.get("status") == "busy" for s in live):
                    continue
                model = towers.tower_model(name, self.store.root)
                new = towers.start(name, self.store.root, model, towers.rotation_prompt(name, self.store.root),
                                   replace=True)
                request.unlink()
                journal.append(self.store.journal_path, {
                    "actor": "EXECUTOR", "event": "TOWER_ROTATED", "tower": name,
                    "old_sessions": [s.get("sessionId") for s in sessions], "new_session": new.get("sessionId")})
                rotated.append(name)
            except Exception as error:  # keep the request; the next step tries again
                print(f"{utc()} tower rotation for {name} failed: {error}", flush=True)
        return rotated

    def step(self) -> list[str]:
        """Recover dead workers, then claim and start new tasks. Return started task IDs."""
        started = []
        reap_children()
        for name in self.rotate_towers():
            print(f"{utc()} started a new {name} session after a handover", flush=True)
        self.paths.claims.mkdir(parents=True, exist_ok=True)
        for claim in sorted(self.paths.claims.glob("*.json")):
            data = read_json(claim)
            task_id = data["task_id"]
            if self.store.path_of(f"RESULT-{task_id}").exists():
                continue
            if not pid_alive(data.get("worker_pid")):
                data["worker_pid"] = self.spawn(task_id)
                data["restarts"] = data.get("restarts", 0) + 1
                write_json(claim, data)
                journal.append(self.store.journal_path, {"actor": "EXECUTOR", "event": "WORKER_RESTARTED",
                                                         "task": task_id})
                started.append(task_id)
        busy = self.active_slots()
        for task_id in self.pending():
            task = self.store.load(task_id).fields
            slot = (task.get("server"), task.get("repo")) if task.get("mode") in ("run", "allocate-run") else None
            if slot and slot in busy:
                continue
            claim = self.paths.claim(task_id)
            try:
                descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            os.close(descriptor)
            data = {"task_id": task_id, "slot": list(slot) if slot else None, "claimed_at": utc()}
            write_json(claim, data)
            data["worker_pid"] = self.spawn(task_id)
            write_json(claim, data)
            journal.append(self.store.journal_path, {"actor": "EXECUTOR", "event": "CLAIMED", "task": task_id})
            if slot:
                busy.add(slot)
            started.append(task_id)
        return started

    def spawn(self, task_id: str) -> int:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        log = (self.paths.logs / f"{task_id}.worker.log").open("a", encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HARNESS_DATA_ROOT": str(self.store.root)}
        process = subprocess.Popen([sys.executable, "-m", "harness", "executor", "worker", task_id],
                                   cwd=REPO_ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        log.close()
        return process.pid

    def watch(self, once: bool = False) -> None:
        with self.singleton():
            while True:
                for task_id in self.step():
                    print(f"{utc()} started worker for {task_id}", flush=True)
                if once:
                    return
                time.sleep(self.poll_seconds)

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for claim in sorted(self.paths.claims.glob("*.json")):
            data = read_json(claim)
            task_id = data["task_id"]
            receipt = read_json(self.paths.receipt(task_id))
            rows.append({"task": task_id, "phase": receipt.get("phase", "CLAIMED"),
                         "worker_alive": pid_alive(data.get("worker_pid")),
                         "result": self.store.path_of(f"RESULT-{task_id}").exists(),
                         "updated_at": receipt.get("updated_at")})
        return rows


class Worker:
    def __init__(self, store: Store, task_id: str):
        self.store = store
        self.task_id = task_id
        self.paths = Paths(store)
        self.record = store.load(task_id)
        self.task = self.record.fields
        self.receipt_path = self.paths.receipt(task_id)
        self.receipt = read_json(self.receipt_path)
        config = store.config()
        executor_config = config.get("executor", {})
        self.poll_seconds = float(executor_config.get("poll_seconds", 10))
        self.server_config = (config.get("servers") or {}).get(self.task["server"])
        self.ssh_config = config.get("ssh", {})
        self.stdout_log = self.paths.logs / f"{task_id}.stdout"
        self.stderr_log = self.paths.logs / f"{task_id}.stderr"

    # Receipt and result

    def update(self, **changes: Any) -> None:
        self.receipt.update(changes, task_id=self.task_id, worker_pid=os.getpid(), updated_at=utc())
        write_json(self.receipt_path, self.receipt)

    def publish_result(self, **fields: Any) -> None:
        if self.store.path_of(f"RESULT-{self.task_id}").exists():
            return
        for log in (self.stdout_log, self.stderr_log):
            log.touch(exist_ok=True)  # a silent run still gets the log files that the result names
        result = {
            "kind": "result", "task": self.record.ref, "transport": "OK", "preflight": "NOT_RUN",
            "exit_code": None, "outcome": "FAIL", "git": {}, "runtime": {}, "outputs": [],
            "missing_outputs": [], "started_at": self.receipt.get("started_at", utc()), "ended_at": utc(),
            "logs": {"stdout": str(self.stdout_log.relative_to(self.store.root)),
                     "stderr": str(self.stderr_log.relative_to(self.store.root))},
            "message": NONE,
        }
        result.update(fields)
        if result["outcome"] not in ("REJECTED", "INTERRUPTED"):
            from .records import compute_outcome
            result["outcome"] = compute_outcome(result)
        record = self.store.publish("result", json.dumps(result), "EXECUTOR")
        self.update(phase="DONE", result=record.ref)

    # Main

    def run(self) -> None:
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        self.receipt.setdefault("started_at", utc())
        try:
            self.authorize()
            mode = self.task["mode"]
            if mode == "gpu-status":
                self.run_gpu_status()
            elif mode == "inspect":
                self.run_inspect()
            elif mode == "run":
                self.run_container()
            else:
                raise Rejected(f"mode {mode} is not implemented in Phase 1")
        except Rejected as rejection:
            self.publish_result(outcome="REJECTED", message=str(rejection))
        except Exception as error:  # record any other failure; never leave a claim without a result
            self.publish_result(outcome="INTERRUPTED", message=f"worker error: {error!r}"[:TAIL_BYTES])
            raise

    def authorize(self) -> None:
        fields = {k: v for k, v in self.task.items() if k != "id"}
        try:
            validate("task", fields, "")
            self.store.check_references("task", fields)
        except RecordError as error:
            raise Rejected(f"authorization failed: {error}") from None
        if self.server_config is None:
            raise Rejected(f"server {self.task['server']!r} is not in config.json")
        try:
            self.transport = Transport(self.task["server"], self.server_config, self.ssh_config, self.paths.ssh)
        except RecordError as error:
            raise Rejected(str(error)) from None
        self.repo_config = (self.server_config.get("repos") or {}).get(self.task["repo"])
        if self.repo_config is None:
            raise Rejected(f"repo {self.task['repo']!r} is not configured for server {self.task['server']!r}")

    def shell(self, command: str, timeout: float = 600, input_text: str | None = None) -> Completed:
        return self.transport.run(command, timeout=timeout, input_text=input_text)

    # gpu-status and inspect

    def run_gpu_status(self) -> None:
        done = self.shell("nvidia-smi --query-gpu=index,uuid,name,memory.used,memory.total,utilization.gpu "
                          "--format=csv,noheader && echo --- && nvidia-smi --query-compute-apps="
                          "gpu_uuid,pid,used_memory --format=csv,noheader", timeout=120)
        self.stdout_log.write_text(done.stdout, encoding="utf-8")
        self.stderr_log.write_text(done.stderr, encoding="utf-8")
        self.publish_result(transport="ERROR" if done.transport_error else "OK", preflight="PASS",
                            exit_code=done.code, message=done.stdout[-TAIL_BYTES:] or NONE)

    def run_inspect(self) -> None:
        agent = self.server_config.get("agent_command", "claude")
        command = (f"cd {shlex.quote(self.repo_config['path'])} && {agent} -p --output-format json "
                   "--permission-mode plan --disallowedTools Edit Write NotebookEdit")
        prompt = ("Inspect this server in read-only mode. Do not change any file, container, or process.\n\n"
                  + self.task["instructions"])
        self.update(phase="INSPECTING")
        done = self.shell(command, timeout=self.task["timeout_seconds"], input_text=prompt)
        self.stdout_log.write_text(done.stdout, encoding="utf-8")
        self.stderr_log.write_text(done.stderr, encoding="utf-8")
        message = done.stdout[-TAIL_BYTES:]
        try:
            message = json.loads(done.stdout).get("result", message)[-TAIL_BYTES:]
        except (json.JSONDecodeError, AttributeError):
            pass
        self.publish_result(transport="ERROR" if done.transport_error else "OK", preflight="PASS",
                            exit_code=done.code, message=message or NONE)

    # run

    def container_workdir(self) -> tuple[str, str]:
        name = self.task["runtime"]["container"]
        container = (self.repo_config.get("containers") or {}).get(name)
        if not container or not container.get("workdir"):
            raise Rejected(f"container {name!r} has no workdir in config.json for repo {self.task['repo']!r}")
        return name, container["workdir"]

    def run_dir(self) -> str:
        return f"{self.task['output_root'].rstrip('/')}/.harness/{self.task_id}"

    def docker(self, container: str, inner: str) -> str:
        return f"docker exec {shlex.quote(container)} bash -c {shlex.quote(inner)}"

    def run_container(self) -> None:
        container, workdir = self.container_workdir()
        self.container = container
        if self.receipt.get("phase") in ("LAUNCHED", "FINALIZING"):
            self.poll_until_exit()
            return
        self.update(phase="PREFLIGHT")
        preflight = self.preflight(container, workdir)
        if preflight is not None:
            self.publish_result(**preflight)
            return
        self.launch(container, workdir)
        self.poll_until_exit()

    def preflight(self, container: str, workdir: str) -> dict[str, Any] | None:
        """Return result fields for a failed preflight, or None when it passes."""
        task = self.task
        branch, commit, entry = task["branch"], task["commit"], task["entrypoint"][0]
        q = shlex.quote
        git = (
            "set -eu; "
            f"cd {q(self.repo_config['path'])}; "
            'if [ -n "$(git status --porcelain --untracked-files=no)" ]; then '
            "echo 'PREFLIGHT: tracked changes in the server checkout' >&2; exit 66; fi; "
            f"git fetch -q origin {q(f'+refs/heads/{branch}:refs/remotes/origin/{branch}')}; "
            f"if git show-ref --verify --quiet {q(f'refs/heads/{branch}')}; then git switch -q {q(branch)}; "
            f"else git switch -q --no-track -c {q(branch)} {q(f'refs/remotes/origin/{branch}')}; fi; "
            f"git merge -q --ff-only {q(f'refs/remotes/origin/{branch}')}; "
            'actual=$(git rev-parse HEAD); echo "ACTUAL=$actual"; '
            f'if [ "$actual" != {q(commit)} ]; then echo "PREFLIGHT: HEAD $actual is not the requested commit" >&2; exit 67; fi; '
            f"mode=$(git ls-tree {q(commit)} -- {q(entry)} | cut -d' ' -f1); "
            f'if [ "$mode" != 100755 ]; then echo "PREFLIGHT: {entry} is not an executable file at the commit" >&2; exit 68; fi'
        )
        done = self.shell(git, timeout=300)
        actual = next((line[7:] for line in done.stdout.splitlines() if line.startswith("ACTUAL=")), None)
        git_info = {"requested": commit, "actual": actual, "branch": branch}
        self.update(git=git_info)
        if done.transport_error:
            return {"transport": "ERROR", "git": git_info, "message": f"git preflight: {done.stderr[-TAIL_BYTES:]}"}
        if done.code != 0:
            return {"preflight": "FAIL", "git": git_info, "message": done.stderr[-TAIL_BYTES:] or f"exit {done.code}"}
        inspect = self.shell(f"docker inspect -f '{{{{.State.Running}}}} {{{{.Image}}}}' {q(container)}", timeout=60)
        parts = inspect.stdout.split()
        if inspect.code != 0 or not parts or parts[0] != "true":
            return {"preflight": "FAIL", "git": git_info,
                    "message": f"PREFLIGHT: container {container} is not running. {inspect.stderr[-500:]}"}
        runtime = {"container": container, "image": parts[1] if len(parts) > 1 else None}
        check = self.shell(self.docker(container, f"test -x {q(workdir)}/{q(entry)}"), timeout=60)
        if check.code != 0:
            return {"preflight": "FAIL", "git": git_info, "runtime": runtime,
                    "message": f"PREFLIGHT: {workdir}/{entry} is not executable in the container"}
        writable = self.shell(self.docker(container, f"mkdir -p {q(self.task['output_root'])} && "
                                                     f"test -w {q(self.task['output_root'])}"), timeout=60)
        if writable.code != 0:
            return {"preflight": "FAIL", "git": git_info, "runtime": runtime,
                    "message": f"PREFLIGHT: output_root {self.task['output_root']} is not writable in the container. "
                               f"{writable.stderr[-500:]}"}
        gpus = self.shell(self.docker(container, "nvidia-smi --query-gpu=index,uuid,memory.used "
                                                 "--format=csv,noheader 2>/dev/null || true"), timeout=60)
        runtime["gpus"] = gpus.stdout.strip().splitlines()
        self.update(runtime=runtime)
        return None

    def launch(self, container: str, workdir: str) -> None:
        q = shlex.quote
        directory = self.run_dir()
        command = " ".join(q(a) for a in ["./" + self.task["entrypoint"][0].lstrip("./"),
                                          *self.task["entrypoint"][1:]])
        wrapper = (
            f'D={q(directory)}; mkdir -p "$D"; echo $$ > "$D/pid"; date -u +%s > "$D/started"; '
            f'{command} > "$D/stdout" 2> "$D/stderr"; code=$?; '
            'echo $code > "$D/exit_code.tmp"; mv "$D/exit_code.tmp" "$D/exit_code"'
        )
        env = " ".join(f"-e {q(f'{k}={v}')}" for k, v in self.task["env"].items())
        prepare = self.shell(self.docker(container, f"test ! -e {q(directory)}"), timeout=60)
        if prepare.code != 0:
            raise Rejected(f"run directory {directory} already exists in the container")
        done = self.shell(f"docker exec -d -w {q(workdir)} {env} {q(container)} setsid bash -c {q(wrapper)}",
                          timeout=60)
        if done.code != 0:
            raise Rejected(f"launch failed: {done.stderr[-TAIL_BYTES:]}")
        self.update(phase="LAUNCHED", launched_at=utc(), launched_epoch=time.time(), run_dir=directory,
                    offsets={"stdout": 0, "stderr": 0})
        journal.append(self.store.journal_path, {"actor": "EXECUTOR", "event": "LAUNCHED", "task": self.task_id})

    def sync_logs(self) -> None:
        q = shlex.quote
        offsets = self.receipt.setdefault("offsets", {"stdout": 0, "stderr": 0})
        for stream, local in (("stdout", self.stdout_log), ("stderr", self.stderr_log)):
            while True:
                path = f"{self.run_dir()}/{stream}"
                inner = f"tail -c +{offsets[stream] + 1} {q(path)} 2>/dev/null | head -c {LOG_CHUNK_BYTES} | base64 -w0"
                done = self.shell(self.docker(self.container, inner), timeout=120)
                if done.code != 0 or not done.stdout.strip():
                    break
                chunk = base64.b64decode(done.stdout.strip())
                with local.open("ab") as handle:
                    handle.write(chunk)
                offsets[stream] += len(chunk)
                if len(chunk) < LOG_CHUNK_BYTES:
                    break
        self.update(offsets=offsets)

    def poll_until_exit(self) -> None:
        q = shlex.quote
        directory = self.run_dir()
        deadline = self.receipt["launched_epoch"] + self.task["timeout_seconds"]
        message = NONE
        exit_code = None
        started = False
        while True:
            state = self.shell(self.docker(self.container,
                                           f'D={q(directory)}; if [ -f "$D/exit_code" ]; then echo EXIT $(cat "$D/exit_code"); '
                                           'elif kill -0 "$(cat "$D/pid" 2>/dev/null)" 2>/dev/null; then echo RUNNING; '
                                           'else echo GONE; fi'), timeout=120)
            self.sync_logs()
            words = state.stdout.split()
            if state.code == 0 and words[:1] == ["EXIT"]:
                exit_code = int(words[1])
                break
            if state.code == 0 and words[:1] == ["GONE"]:
                if time.time() - self.receipt["launched_epoch"] < START_GRACE_SECONDS and not started:
                    time.sleep(self.poll_seconds)
                    continue
                message = ("The run process ended without an exit code." if started else
                           f"The run did not start: no PID file in {directory} after {START_GRACE_SECONDS} seconds.")
                break
            started = started or (state.code == 0 and words[:1] == ["RUNNING"])
            if time.time() > deadline:
                self.shell(self.docker(self.container,
                                       f'P=$(cat {q(directory)}/pid); kill -TERM -- -$P 2>/dev/null; '
                                       'for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 $P 2>/dev/null || break; sleep 1; done; '
                                       'kill -KILL -- -$P 2>/dev/null; true'), timeout=60)
                self.sync_logs()
                message = f"Timed out after {self.task['timeout_seconds']} seconds."
                break
            self.update(phase="LAUNCHED", last_poll=utc())
            time.sleep(self.poll_seconds)
        self.update(phase="FINALIZING")
        outputs, missing = self.collect_outputs()
        fetched, skipped = self.fetch_outputs(outputs)
        stderr_tail = self.stderr_log.read_text(encoding="utf-8", errors="replace")[-1000:] \
            if self.stderr_log.exists() else ""
        if message == NONE and exit_code not in (0, None):
            message = f"exit code {exit_code}. stderr tail: {stderr_tail}" if stderr_tail else f"exit code {exit_code}"
        self.publish_result(preflight="PASS", exit_code=exit_code, git=self.receipt.get("git", {}),
                            runtime=self.receipt.get("runtime", {}), outputs=outputs,
                            missing_outputs=missing, message=message, fetched=fetched, fetch_skipped=skipped)

    def fetch_outputs(self, outputs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Copy small required outputs to <data-root>/artifacts/<task-id>/ and check each SHA-256."""
        wanted = self.task.get("fetch_outputs", [])
        if not wanted:
            return [], []
        limits = self.store.config().get("executor", {})
        max_file = int(limits.get("fetch_max_file_bytes", DEFAULT_FETCH_MAX_FILE_BYTES))
        budget = int(limits.get("fetch_max_total_bytes", DEFAULT_FETCH_MAX_TOTAL_BYTES))
        recorded = {o["path"]: o for o in outputs}
        target_root = self.store.root / "artifacts" / self.task_id
        fetched, skipped = [], []
        for path in wanted:
            output = recorded.get(path)
            if output is None:
                skipped.append(f"{path}: the output is missing")
                continue
            if output["bytes"] > max_file:
                skipped.append(f"{path}: {output['bytes']} bytes is more than the file limit {max_file}")
                continue
            if output["bytes"] > budget:
                skipped.append(f"{path}: {output['bytes']} bytes is more than the remaining task limit {budget}")
                continue
            local = target_root / path
            if local.exists():
                data = local.read_bytes()
            else:
                inner = f"base64 -w0 < {shlex.quote(self.task['output_root'].rstrip('/') + '/' + path)}"
                done = self.shell(self.docker(self.container, inner), timeout=600)
                if done.code != 0:
                    skipped.append(f"{path}: the copy failed. {done.stderr[-300:]}")
                    continue
                data = base64.b64decode(done.stdout.strip() or b"")
            digest = hashlib.sha256(data).hexdigest()
            if digest != output["sha256"]:
                skipped.append(f"{path}: the copied SHA-256 {digest} is not the recorded SHA-256")
                continue
            if not local.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
            budget -= len(data)
            fetched.append({"path": path, "bytes": len(data), "sha256": digest,
                            "local_path": str(local.relative_to(self.store.root))})
        return fetched, skipped

    def collect_outputs(self) -> tuple[list[dict[str, Any]], list[str]]:
        q = shlex.quote
        names = " ".join(q(p) for p in self.task["required_outputs"])
        inner = (f"cd {q(self.task['output_root'])} 2>/dev/null || exit 3; "
                 f'for f in {names}; do if [ -f "$f" ]; then '
                 'printf "OK\\t%s\\t%s\\t%s\\n" "$f" "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d" " -f1)"; '
                 'else printf "MISSING\\t%s\\n" "$f"; fi; done')
        done = self.shell(self.docker(self.container, inner), timeout=3600)
        if done.code == 3:
            return [], list(self.task["required_outputs"])
        outputs, missing = [], []
        for line in done.stdout.splitlines():
            parts = line.split("\t")
            if parts[0] == "OK" and len(parts) == 4:
                outputs.append({"path": parts[1], "bytes": int(parts[2]), "sha256": parts[3]})
            elif parts[0] == "MISSING" and len(parts) == 2:
                missing.append(parts[1])
        reported = {o["path"] for o in outputs} | set(missing)
        missing.extend(p for p in self.task["required_outputs"] if p not in reported)
        return outputs, missing
