import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import context_guard  # noqa: E402
from harness.cli import main  # noqa: E402
from harness.executor import Executor  # noqa: E402
from harness.records import RecordError  # noqa: E402
from harness.store import Store, init_data_root  # noqa: E402


def markdown(front, body="Body.\n"):
    return "---\n" + json.dumps(front, ensure_ascii=False) + "\n---\n" + body


HANDOFF = {"kind": "handoff", "experiment_id": "EXP-1", "revision": 1, "predecessor": "NONE", "status": "APPROVED",
           "authorization": "VALIDATION_RUN", "approval": "ok",
           "gates": [{"id": "G1", "phase": "POST_RUN", "check": "A."}], "full_run_condition": "NONE"}


class GrantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)
        self.store = Store(self.root)

    def tearDown(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        self.tmp.cleanup()

    def grant(self, **changes):
        front = {"kind": "grant", "holder": "et", "category": "PUSH_BRANCH", "constraints": "exp/*",
                 "instruction": "push는 앞으로 묻지 마", "experiment_id": "NONE", "expires": "NEVER", **changes}
        return self.store.publish("grant", markdown(front), "DT")

    def test_ids_and_schema(self):
        self.assertEqual(self.grant().record_id, "GRANT-GENERAL-001")
        with self.assertRaises(RecordError):
            self.grant(category="EVERYTHING")
        with self.assertRaises(RecordError):
            self.grant(expires="SOON")

    def test_active_grants(self):
        forever = self.grant()
        dated = self.grant(expires="DATE:2026-10-01")
        self.assertEqual([g.ref for g in self.store.active_grants(today="2026-10-10")], [forever.ref])
        self.assertEqual(len(self.store.active_grants(today="2026-09-30")), 2)
        self.assertEqual(self.store.active_grants(holder="vt"), [])
        revoke = {"kind": "revoke", "revokes": [forever.ref], "instruction": "push 권한 취소"}
        self.assertEqual(self.store.publish("revoke", markdown(revoke), "DT").record_id, "REVOKE-GENERAL-001")
        self.assertEqual([g.ref for g in self.store.active_grants(today="2026-09-30")], [dated.ref])
        self.assertNotIn(forever.ref, [g.ref for g in self.store.active_grants(today="2026-09-30")])

    def test_handoff_expiry(self):
        first = self.store.publish("handoff", markdown(HANDOFF), "DT")
        grant = self.grant(experiment_id="EXP-1", expires=f"HANDOFF:{first.ref}")
        self.assertEqual([g.ref for g in self.store.active_grants(experiment="EXP-1")], [grant.ref])
        self.store.publish("handoff", markdown(dict(HANDOFF, revision=2, predecessor=first.ref)), "DT")
        self.assertEqual(self.store.active_grants(experiment="EXP-1"), [])

    def test_grants_command(self):
        self.grant()
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            main(["--data-root", str(self.root), "grants", "--holder", "et"])
        self.assertIn("PUSH_BRANCH", out.getvalue())


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)
        (self.root / "config.json").write_text(json.dumps({"limits": {"context_rotate_tokens": 1000}}))
        self.env = mock.patch.dict(os.environ, {"HARNESS_DATA_ROOT": str(self.root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def transcript(self, tokens):
        path = Path(self.tmp.name) / "t.jsonl"
        usage = {"input_tokens": 2, "cache_creation_input_tokens": 10, "cache_read_input_tokens": tokens - 12}
        path.write_text(json.dumps({"type": "assistant", "message": {"model": "m", "usage": usage}}) + "\n")
        return str(path)

    def guard(self, tokens, active=False):
        event = {"hook_event_name": "Stop", "transcript_path": self.transcript(tokens), "stop_hook_active": active}
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), mock.patch.object(sys, "stderr", stderr), \
                mock.patch.object(sys, "argv", ["context_guard.py", "--tower", "et"]):
            return context_guard.main(), stderr.getvalue()

    def test_guard_asks_for_a_handover_once(self):
        self.assertEqual(self.guard(999)[0], 0)
        code, message = self.guard(1000)
        self.assertEqual(code, 2)
        self.assertIn("bin/harness handover et", message)
        self.assertEqual(self.guard(5000, active=True)[0], 0)

    def test_handover_writes_worklog_and_request(self):
        draft = Path(self.tmp.name) / "h.md"
        draft.write_text("Current work: task 32 runs.")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "et", str(draft)]), 0)
        self.assertIn("## Handover", (self.root / "worklog" / "et.md").read_text())
        self.assertTrue((self.root / "runtime" / "rotate" / "et.json").exists())
        self.assertEqual(self.guard(5000)[0], 0)  # handover written: the stop is allowed

    def test_executor_rotates_an_idle_tower(self):
        request = self.root / "runtime" / "rotate" / "et.json"
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text("{}")
        executor = Executor(Store(self.root))
        busy = [{"name": "et", "state": "working", "status": "busy", "sessionId": "s1"}]
        idle = [{"name": "et", "state": "blocked", "status": "idle", "sessionId": "s1"}]
        waiting = [{"name": "et", "state": "done", "status": "busy", "sessionId": "s1"}]  # a background wait
        with mock.patch("harness.tower.named", return_value=busy), mock.patch("harness.tower.start") as start:
            self.assertEqual(executor.rotate_towers(), [])
            start.assert_not_called()
        config = json.loads((self.root / "config.json").read_text())
        config["towers"] = {"models": {"et": "opus"}}
        (self.root / "config.json").write_text(json.dumps(config))
        with mock.patch("harness.tower.named", return_value=waiting), \
                mock.patch("harness.tower.start", return_value={"sessionId": "s2"}) as start:
            self.assertEqual(executor.rotate_towers(), ["et"])
            args, kwargs = start.call_args
            self.assertEqual(args[:3], ("et", self.root, "opus"))
            self.assertIn("new ET session after a context rotation", args[3])
            self.assertTrue(kwargs["replace"])
        self.assertFalse(request.exists())


class KeepAliveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reaped_detection(self):
        from harness import tower
        from harness.store import REPO_ROOT
        rows = {"et": [{"id": "a", "cwd": str(REPO_ROOT), "state": "done", "startedAt": 2}],
                "dt": [{"id": "b", "cwd": str(REPO_ROOT), "state": "blocked", "startedAt": 2}],
                "vt": [{"id": "c", "cwd": str(REPO_ROOT), "state": "done", "pid": 5, "startedAt": 2}]}
        with mock.patch.object(tower, "named", side_effect=lambda name: rows[name]):
            self.assertEqual(tower.reaped("et")["id"], "a")   # done and no process
            self.assertEqual(tower.reaped("dt")["id"], "b")   # blocked (waits for a reply) and no process
            self.assertIsNone(tower.reaped("vt"))             # process alive
        with mock.patch.object(tower, "named", return_value=[]):
            self.assertIsNone(tower.reaped("et"))             # removed by 'harness tower stop'

    def test_executor_respawns_once_per_interval(self):
        executor = Executor(Store(self.root))
        session = {"id": "a", "sessionId": "a-full"}
        with mock.patch("harness.tower.reaped", side_effect=lambda name: session if name == "et" else None), \
                mock.patch("harness.tower.respawn") as respawn:
            self.assertEqual(executor.revive_towers(), ["et"])
            self.assertEqual(executor.revive_towers(), [])  # waits KEEPALIVE_RETRY_SECONDS before a retry
            respawn.assert_called_once_with("a")


class ForkTest(unittest.TestCase):
    """DT2 is a discussion-only copy of DT (researcher decision 2026-10-20)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fork_copies_the_worklog_and_starts_with_the_fork_prompt(self):
        from harness import tower
        (self.root / "worklog" / "dt.md").write_text("## Handover\nNext: R4.\n")
        (self.root / "worklog" / "dt-summary.md").write_text("Summary.\n")
        with mock.patch.object(tower, "named", return_value=[]), \
                mock.patch.object(tower, "start", return_value={"sessionId": "f"}) as start:
            self.assertEqual(tower.fork("dt2", self.root), {"sessionId": "f"})
        self.assertIn("Next: R4.", (self.root / "worklog" / "dt2.md").read_text())
        self.assertIn("Summary.", (self.root / "worklog" / "dt2-summary.md").read_text())
        prompt = start.call_args.args[3]
        self.assertIn("discussion-only copy of DT", prompt)
        self.assertIn("dt2.md", prompt)
        with mock.patch.object(tower, "named", return_value=[{"id": "x"}]):
            with self.assertRaises(RecordError):
                tower.fork("dt2", self.root)  # a DT2 session exists and --replace is not given
        with self.assertRaises(RecordError):
            tower.fork("et", self.root)

    def test_fork_rules_and_model(self):
        from harness import tower
        rules = tower.render_rules("dt2", self.root).read_text()
        self.assertTrue(rules.startswith("# Discussion Tower (DT)"))
        self.assertIn("# DT2 rules", rules)
        self.assertEqual(tower.render_rules("dt", self.root).name, "dt.md")
        (self.root / "config.json").write_text(json.dumps({"towers": {"models": {"dt": "fable"}}}))
        self.assertEqual(tower.tower_model("dt2", self.root), "fable")  # follows DT unless set
        self.assertNotIn("background wait", tower.rotation_prompt("dt2", self.root))

    def test_remote_control_flag_and_link(self):
        from harness import tower
        self.assertNotIn("--remote-control", tower.start_command("dt", self.root))
        (self.root / "config.json").write_text(json.dumps({"towers": {"remote_control": ["dt", "dt2"]}}))
        command = tower.start_command("dt2", self.root)
        self.assertEqual(command[command.index("--remote-control") + 1], "dt2")
        self.assertNotIn("--remote-control", tower.start_command("et", self.root))
        (self.root / "config.json").write_text(json.dumps({"towers": {"remote_control": True}}))
        self.assertIn("--remote-control", tower.start_command("et", self.root))
        jobs = self.root / "jobs"
        (jobs / "ab12").mkdir(parents=True)
        (jobs / "ab12" / "state.json").write_text(json.dumps({"bridgeSessionId": "cse_01ABC"}))
        (jobs / "cd34").mkdir()
        (jobs / "cd34" / "state.json").write_text(json.dumps({"name": "et"}))
        self.assertEqual(tower.remote_url({"id": "ab12"}, jobs), "https://claude.ai/code/session_01ABC")
        self.assertIsNone(tower.remote_url({"id": "cd34"}, jobs))
        self.assertIsNone(tower.remote_url({"id": "none"}, jobs))

    def test_name_suffix_makes_a_separate_cycle(self):
        from harness import tower
        self.assertEqual(tower.session_name("dt", self.root), "dt")
        self.assertEqual(tower.render_rules("et", self.root).name, "et.md")
        (self.root / "config.json").write_text(json.dumps({"towers": {"name_suffix": "_tmp", "remote_control": True}}))
        self.assertEqual(tower.peers(self.root), {"dt": "dt_tmp", "et": "et_tmp", "vt": "vt_tmp", "dt2": "dt2_tmp"})
        command = tower.start_command("et", self.root)
        self.assertEqual(command[command.index("--name") + 1], "et_tmp")
        self.assertEqual(command[command.index("--agent") + 1], "et")
        self.assertEqual(command[command.index("--remote-control") + 1], "et_tmp")
        rules = tower.render_rules("et", self.root).read_text()
        self.assertTrue(rules.startswith("# Engineering Tower"), rules[:40])
        self.assertIn("You are `et_tmp`", rules)
        self.assertIn("`vt` is `vt_tmp`", rules)
        self.assertIn(str(self.root / "runtime" / "rules" / "et.md"), command)
        dt2 = tower.render_rules("dt2", self.root).read_text()
        self.assertIn("# DT2 rules", dt2)
        self.assertIn("You are `dt2_tmp`", dt2)
        with mock.patch.object(tower, "named", return_value=[{"id": "x", "state": "done"}]) as named:
            with self.assertRaises(RecordError):
                tower.start("et", self.root)
            named.assert_called_with("et_tmp")

    def test_fork_cannot_publish_or_message(self):
        from harness import tower
        settings = json.loads(tower.render_settings("dt2", self.root).read_text())
        allow, deny = settings["permissions"]["allow"], settings["permissions"]["deny"]
        self.assertIn("SendMessage", deny)
        self.assertIn("Bash(bin/harness publish *)", deny)
        self.assertIn(f"Edit(/{self.root}/state/**)", deny)
        self.assertIn(f"Edit(/{self.root}/worklog/dt.md)", deny)
        self.assertFalse([rule for rule in allow if "publish" in rule or "PROJECT_STATE" in rule])
        self.assertIn(f"Edit(/{self.root}/worklog/dt2.md)", allow)
        self.assertIn("Bash(bin/harness handover dt2 *)", allow)


if __name__ == "__main__":
    unittest.main()
