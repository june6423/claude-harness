"""Executor tests with the local transport and a fake docker (tests/fakebin/docker)."""

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from harness.executor import Executor, Worker, read_json
from harness.store import Store, init_data_root

FAKEBIN = Path(__file__).parent / "fakebin"
SCRIPTS = {
    "scripts/ok.sh": '#!/bin/bash\nmkdir -p "$1/run"\necho "line one"\necho \'{"acc": 1}\' > "$1/run/metrics.json"\n',
    "scripts/fail.sh": '#!/bin/bash\necho "bad input" >&2\nexit 3\n',
    "scripts/silent.sh": "#!/bin/bash\nexit 0\n",
    "scripts/slow.sh": "#!/bin/bash\nsleep 30\n",
    "scripts/envcheck.sh": '#!/bin/bash\nmkdir -p "$1/run"\necho "$MARK" > "$1/run/metrics.json"\n',
}


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"PATH": f"{FAKEBIN}:{os.environ['PATH']}"})
        self.env.start()
        source = base / "src"
        source.mkdir()
        git("init", "-q", "-b", "main", cwd=source)
        for name, text in SCRIPTS.items():
            path = source / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
            path.chmod(0o755)
        (source / "scripts" / "plain.sh").write_text("#!/bin/bash\n")
        git("add", "-A", cwd=source)
        git("commit", "-q", "-m", "scripts", cwd=source)
        self.commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source, capture_output=True,
                                     text=True, check=True).stdout.strip()
        origin = base / "origin.git"
        git("clone", "-q", "--bare", str(source), str(origin), cwd=base)
        self.checkout = base / "server-checkout"
        git("clone", "-q", str(origin), str(self.checkout), cwd=base)
        self.out = base / "out"
        self.root = init_data_root(base / "data", write_pointer=False)
        config = {"executor": {"poll_seconds": 0.2}, "servers": {"local": {"transport": "local", "repos": {
            "repo": {"path": str(self.checkout), "containers": {
                "testc": {"workdir": str(self.checkout)}, "stopped-c": {"workdir": str(self.checkout)}}}}}}}
        (self.root / "config.json").write_text(json.dumps(config))
        self.store = Store(self.root)
        handoff = {"kind": "handoff", "experiment_id": "EXP-1", "revision": 1, "predecessor": "NONE",
                   "status": "APPROVED", "authorization": "VALIDATION_RUN", "approval": "run it",
                   "gates": [{"id": "G1", "phase": "POST_RUN", "check": "The metrics file exists."}],
                   "full_run_condition": "NONE"}
        self.handoff = self.store.publish("handoff", "---\n" + json.dumps(handoff) + "\n---\nBody.\n", "DT")

    def tearDown(self):
        self.env.stop()
        subprocess.run(["chmod", "-R", "u+w", self.tmp.name])
        self.tmp.cleanup()

    def task(self, script="scripts/ok.sh", name="case", **changes):
        output_root = str(self.out / name)
        fields = {
            "kind": "task", "experiment_id": "EXP-1", "handoff": self.handoff.ref, "source": "HANDOFF",
            "user_request": "NONE", "mode": "run", "execution_class": "VALIDATION", "server": "local",
            "repo": "repo", "branch": "main", "commit": self.commit, "runtime": {"container": "testc"},
            "entrypoint": [script, output_root], "instructions": "NONE", "env": {},
            "output_root": output_root, "required_outputs": ["run/metrics.json"], "gpu": "NONE",
            "timeout_seconds": 60, "pre_run_verdict": "NONE",
            "lineage": {"parent": "NONE", "recovery_of": "NONE", "attempt": 0},
        }
        fields.update(changes)
        return self.store.publish("task", json.dumps(fields), "ET")

    def run_task(self, *args, **kwargs):
        task = self.task(*args, **kwargs)
        Worker(self.store, task.record_id).run()
        return self.store.load(f"RESULT-{task.record_id}").fields

    def test_pass(self):
        result = self.run_task()
        self.assertEqual(result["outcome"], "PASS", result["message"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["git"]["actual"], self.commit)
        self.assertEqual(result["outputs"][0]["path"], "run/metrics.json")
        self.assertEqual(result["runtime"]["image"], "sha256:fakeimage")
        stdout = (self.root / result["logs"]["stdout"]).read_text()
        self.assertEqual(stdout, "line one\n")
        self.assertEqual(self.store.verify(), [])

    def test_env_reaches_the_script(self):
        result = self.run_task("scripts/envcheck.sh", env={"MARK": "hello"})
        self.assertEqual(result["outcome"], "PASS")
        self.assertEqual((self.out / "case" / "run" / "metrics.json").read_text(), "hello\n")

    def test_nonzero_exit(self):
        result = self.run_task("scripts/fail.sh")
        self.assertEqual((result["outcome"], result["exit_code"]), ("FAIL", 3))
        self.assertIn("bad input", result["message"])

    def test_missing_output(self):
        result = self.run_task("scripts/silent.sh")
        self.assertEqual(result["outcome"], "FAIL")
        self.assertEqual((self.root / result["logs"]["stdout"]).read_text(), "")
        self.assertEqual(result["missing_outputs"], ["run/metrics.json"])

    def test_entrypoint_not_executable(self):
        result = self.run_task("scripts/plain.sh")
        self.assertEqual((result["outcome"], result["preflight"]), ("FAIL", "FAIL"))
        self.assertIn("not an executable file", result["message"])

    def test_commit_mismatch(self):
        result = self.run_task(commit="0" * 40)
        self.assertEqual(result["preflight"], "FAIL")
        self.assertIn("not the requested commit", result["message"])

    def test_tracked_changes_block_the_run(self):
        (self.checkout / "scripts" / "ok.sh").write_text("#!/bin/bash\nexit 0\n")
        result = self.run_task()
        self.assertEqual(result["preflight"], "FAIL")
        self.assertIn("tracked changes", result["message"])

    def test_stopped_container(self):
        result = self.run_task(runtime={"container": "stopped-c"})
        self.assertEqual(result["preflight"], "FAIL")
        self.assertIn("is not running", result["message"])

    def test_timeout(self):
        result = self.run_task("scripts/slow.sh", timeout_seconds=1)
        self.assertEqual(result["outcome"], "FAIL")
        self.assertIn("Timed out", result["message"])

    def test_unknown_container_is_rejected(self):
        result = self.run_task(runtime={"container": "other"})
        self.assertEqual(result["outcome"], "REJECTED")

    def test_allocate_run_is_rejected(self):
        gpu = {"count": 1, "policy": "BEST_EFFORT_JIT", "devices": "SELECT_AT_RUN_TIME", "constraints": "NONE"}
        result = self.run_task(mode="allocate-run", runtime={"template": "t"}, gpu=gpu)
        self.assertEqual(result["outcome"], "REJECTED")
        self.assertIn("not implemented", result["message"])

    def test_reattach_after_worker_loss(self):
        task = self.task("scripts/ok.sh")
        first = Worker(self.store, task.record_id)
        first.receipt.setdefault("started_at", "x")
        first.authorize()
        container, workdir = first.container_workdir()
        first.container = container
        self.assertIsNone(first.preflight(container, workdir))
        first.launch(container, workdir)
        time.sleep(0.5)
        second = Worker(self.store, task.record_id)  # reads the LAUNCHED receipt
        second.run()
        result = self.store.load(f"RESULT-{task.record_id}").fields
        self.assertEqual(result["outcome"], "PASS", result["message"])

    def test_daemon_runs_tasks_one_at_a_time_per_repository(self):
        first = self.task("scripts/ok.sh", name="a")
        second = self.task("scripts/ok.sh", name="b")
        executor = Executor(self.store)
        self.assertEqual(executor.step(), [first.record_id])
        deadline = time.time() + 30
        started = []
        while time.time() < deadline and not self.store.path_of(f"RESULT-{second.record_id}").exists():
            started += executor.step()
            time.sleep(0.3)
        self.assertEqual(started, [second.record_id])
        for task in (first, second):
            self.assertEqual(self.store.load(f"RESULT-{task.record_id}").fields["outcome"], "PASS")
        self.assertTrue(all(row["result"] for row in executor.status()))

    def test_zombie_counts_as_dead(self):
        from harness.executor import pid_alive, reap_children
        process = subprocess.Popen(["true"])
        time.sleep(0.3)
        self.assertFalse(pid_alive(process.pid))  # exited but not reaped: a zombie
        reap_children()
        self.assertFalse(pid_alive(process.pid))
        process.returncode = 0

    def test_dead_worker_is_restarted(self):
        task = self.task("scripts/ok.sh")
        executor = Executor(self.store)
        claim = executor.paths.claim(task.record_id)
        claim.parent.mkdir(parents=True, exist_ok=True)
        claim.write_text(json.dumps({"task_id": task.record_id, "slot": ["local", "repo"], "worker_pid": 999999}))
        self.assertEqual(executor.step(), [task.record_id])
        self.assertEqual(read_json(claim)["restarts"], 1)
        deadline = time.time() + 30
        while time.time() < deadline and not self.store.path_of(f"RESULT-{task.record_id}").exists():
            time.sleep(0.3)
        self.assertEqual(self.store.load(f"RESULT-{task.record_id}").fields["outcome"], "PASS")


if __name__ == "__main__":
    unittest.main()
