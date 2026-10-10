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
        with mock.patch("harness.tower.named", return_value=busy), mock.patch("harness.tower.start") as start:
            self.assertEqual(executor.rotate_towers(), [])
            start.assert_not_called()
        with mock.patch("harness.tower.named", return_value=idle), \
                mock.patch("harness.tower.last_model", return_value="claude-opus-5-5"), \
                mock.patch("harness.tower.start", return_value={"sessionId": "s2"}) as start:
            self.assertEqual(executor.rotate_towers(), ["et"])
            args, kwargs = start.call_args
            self.assertEqual(args[:3], ("et", self.root, "claude-opus-5-5"))
            self.assertIn("new ET session after a context rotation", args[3])
            self.assertTrue(kwargs["replace"])
        self.assertFalse(request.exists())


if __name__ == "__main__":
    unittest.main()
