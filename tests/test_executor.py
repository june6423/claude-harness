"""Executor tests with the local transport and a fake docker (tests/fakebin/docker)."""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from harness.cli import main
from harness.executor import Executor, Paths, Worker, read_json
from harness.records import RecordError
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
                "testc": {"workdir": str(self.checkout)}, "stopped-c": {"workdir": str(self.checkout)},
                "directc": {"workdir": str(self.checkout), "runtime": "direct", "image": "vast/cuda-template"}}}}}}}
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

    def test_fetch_outputs(self):
        result = self.run_task(fetch_outputs=["run/metrics.json"])
        self.assertEqual(result["outcome"], "PASS")
        item = result["fetched"][0]
        local = self.root / item["local_path"]
        self.assertEqual(local.read_text(), '{"acc": 1}\n')
        self.assertEqual(local.stat().st_mode & 0o777, 0o444)
        self.assertEqual(item["sha256"], result["outputs"][0]["sha256"])
        self.assertEqual(result["fetch_skipped"], [])
        self.assertEqual(self.store.verify(), [])
        os.chmod(local, 0o644)
        local.write_text("changed")
        self.assertTrue(any("does not match its SHA-256" in p for p in self.store.verify()))

    def test_fetch_respects_the_size_limit(self):
        config = json.loads((self.root / "config.json").read_text())
        config["executor"]["fetch_max_file_bytes"] = 5
        (self.root / "config.json").write_text(json.dumps(config))
        result = self.run_task(fetch_outputs=["run/metrics.json"])
        self.assertEqual(result["outcome"], "PASS")
        self.assertEqual(result["fetched"], [])
        self.assertIn("more than the file limit", result["fetch_skipped"][0])

    def test_fetch_path_must_be_a_required_output(self):
        from harness.records import RecordError
        with self.assertRaises(RecordError) as caught:
            self.task(fetch_outputs=["run/other.json"])
        self.assertIn("must also be in required_outputs", str(caught.exception))

    def test_older_commit_on_the_branch_runs(self):
        source = Path(self.tmp.name) / "src"
        (source / "NEWER.txt").write_text("newer")
        git("add", "-A", cwd=source)
        git("commit", "-q", "-m", "newer", cwd=source)
        git("push", "-q", str(Path(self.tmp.name) / "origin.git"), "main", cwd=source)
        result = self.run_task()  # self.commit is now one commit behind the branch tip
        self.assertEqual(result["outcome"], "PASS", result["message"])
        self.assertEqual(result["git"]["actual"], self.commit)

    def test_direct_runtime_pass_and_fetch(self):
        result = self.run_task(runtime={"container": "directc"}, fetch_outputs=["run/metrics.json"],
                               env={"MARK": "x"})
        self.assertEqual(result["outcome"], "PASS", result["message"])
        self.assertEqual(result["runtime"]["kind"], "direct")
        self.assertEqual(result["runtime"]["image"], "vast/cuda-template")
        self.assertEqual(len(result["fetched"]), 1)
        self.assertEqual((self.root / result["logs"]["stdout"]).read_text(), "line one\n")

    def test_direct_runtime_nonzero_exit(self):
        result = self.run_task("scripts/fail.sh", runtime={"container": "directc"})
        self.assertEqual((result["outcome"], result["exit_code"]), ("FAIL", 3))
        self.assertIn("bad input", result["message"])

    def test_direct_runtime_timeout_kills_the_process_group(self):
        started = time.time()
        result = self.run_task("scripts/slow.sh", runtime={"container": "directc"}, timeout_seconds=1)
        self.assertLess(time.time() - started, 20)  # the launch must not wait for the 30-second script
        self.assertEqual(result["outcome"], "FAIL")
        self.assertIn("Timed out", result["message"])
        pid = int((self.out / "case" / ".harness" / result["task"].split("@")[0] / "pid").read_text())
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

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
        self.assertIn("is not on origin/main", result["message"])

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

    def cancel(self, task, reason="The judge server is no longer needed.", actor="ET", code=0):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.assertEqual(main(["--data-root", str(self.root), "cancel", task.record_id,
                                   "--reason", reason, "--actor", actor]), code, err.getvalue())
        return out.getvalue().strip() if code == 0 else err.getvalue()

    def test_cancel_stops_a_running_task(self):
        task = self.task("scripts/slow.sh", timeout_seconds=600)
        worker = threading.Thread(target=Worker(self.store, task.record_id).run)
        worker.start()
        receipt = Paths(self.store).receipt(task.record_id)
        deadline = time.time() + 20
        while time.time() < deadline and read_json(receipt).get("phase") != "LAUNCHED":
            time.sleep(0.1)
        cancel = self.cancel(task)
        self.assertTrue(cancel.startswith(f"CANCEL-{task.record_id}@sha256:"))
        started = time.time()
        worker.join(30)
        self.assertLess(time.time() - started, 20)
        result = self.store.load(f"RESULT-{task.record_id}").fields
        self.assertEqual((result["outcome"], result["cancel"]), ("CANCELLED", cancel))
        self.assertEqual(result["preflight"], "PASS")
        self.assertIn("The judge server is no longer needed.", result["message"])
        pid = int((self.out / "case" / ".harness" / task.record_id / "pid").read_text())
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.store.verify(), [])

    def test_cancel_before_launch(self):
        task = self.task("scripts/ok.sh")
        cancel = self.cancel(task, actor="RESEARCHER")
        Worker(self.store, task.record_id).run()
        result = self.store.load(f"RESULT-{task.record_id}").fields
        self.assertEqual((result["outcome"], result["preflight"], result["cancel"]), ("CANCELLED", "NOT_RUN", cancel))
        self.assertFalse((self.out / "case").exists())  # nothing ran
        self.assertIn("already has a result", self.cancel(task, code=2))

    def test_cancel_refuses_a_second_cancel_and_a_finished_task(self):
        task = self.task("scripts/ok.sh", name="a")
        self.cancel(task)
        with self.assertRaises(RecordError):
            self.store.publish("cancel", "---\n" + json.dumps(
                {"kind": "cancel", "task": task.ref, "reason": "again"}) + "\n---\n", "ET")
        done = self.task("scripts/ok.sh", name="b")
        Worker(self.store, done.record_id).run()
        self.assertIn("already has a result", self.cancel(done, code=2))

    def test_daemon_does_not_hold_a_cancelled_task_behind_a_busy_slot(self):
        running = self.task("scripts/slow.sh", name="a", timeout_seconds=600)
        queued = self.task("scripts/ok.sh", name="b")
        executor = Executor(self.store)
        self.assertEqual(executor.step(), [running.record_id])
        self.assertEqual(executor.step(), [])  # the slot is busy
        self.cancel(queued)
        self.assertEqual(executor.step(), [queued.record_id])
        self.cancel(running)
        deadline = time.time() + 30
        while time.time() < deadline and not all(
                self.store.path_of(f"RESULT-{t.record_id}").exists() for t in (running, queued)):
            executor.step()
            time.sleep(0.3)
        for task in (running, queued):
            self.assertEqual(self.store.load(f"RESULT-{task.record_id}").fields["outcome"], "CANCELLED")

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

    def test_daemon_survives_a_failed_step(self):
        executor = Executor(self.store)
        calls = []

        def step():
            calls.append(1)
            if len(calls) == 1:
                raise ValueError("config.json is invalid")
            raise KeyboardInterrupt  # end the loop after the second step

        executor.poll_seconds = 0
        with mock.patch.object(executor, "step", side_effect=step), \
                mock.patch("sys.stdout", new=__import__("io").StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                executor.watch()
        self.assertEqual(len(calls), 2)

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
