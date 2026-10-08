import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import dt_guard  # noqa: E402
from harness.store import Store, init_data_root  # noqa: E402

APPROVAL = "DEMO-3 FULL_RUN을 승인한다."


def handoff(status="APPROVED", approval=APPROVAL):
    front = {"kind": "handoff", "experiment_id": "DEMO-3", "revision": 1, "predecessor": "NONE",
             "status": status, "authorization": "FULL_RUN", "approval": approval,
             "gates": [{"id": "G1", "phase": "PRE_RUN", "check": "A."}, {"id": "G2", "phase": "POST_RUN", "check": "B."}],
             "full_run_condition": "NONE"}
    return "---\n" + json.dumps(front) + "\n---\nBody.\n"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = init_data_root(self.base / "data", write_pointer=False)
        self.env = mock.patch.dict(os.environ, {"HARNESS_DATA_ROOT": str(self.root)})
        self.env.start()
        self.draft = self.base / "draft.md"

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def transcript(self, *entries):
        path = self.base / "t.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return str(path)

    def run_guard(self, event):
        stderr = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), mock.patch.object(sys, "stderr", stderr):
            return dt_guard.main(), stderr.getvalue()

    def publish_event(self, transcript, command=None):
        return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "transcript_path": transcript,
                "tool_input": {"command": command or f"bin/harness publish handoff {self.draft} --actor DT"}}

    def test_researcher_approval_passes(self):
        self.draft.write_text(handoff())
        t = self.transcript({"type": "user", "origin": {"kind": "human"},
                             "message": {"content": f"좋아요.  {APPROVAL}  진행하세요"}})
        self.assertEqual(self.run_guard(self.publish_event(t))[0], 0)

    def test_print_mode_prompt_counts_as_researcher_input(self):
        self.draft.write_text(handoff())
        t = self.transcript({"type": "user", "promptId": "p1", "message": {"content": APPROVAL}})
        self.assertEqual(self.run_guard(self.publish_event(t))[0], 0)
        t = self.transcript({"type": "user", "isMeta": True, "promptId": "p1", "message": {"content": APPROVAL}},
                            {"type": "user", "message": {"content": APPROVAL}})
        self.assertEqual(self.run_guard(self.publish_event(t))[0], 2)

    def test_approval_from_a_peer_or_tool_is_blocked(self):
        self.draft.write_text(handoff())
        t = self.transcript(
            {"type": "user", "origin": {"kind": "peer", "name": "et"}, "message": {"content": APPROVAL}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": APPROVAL}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": APPROVAL}]}},
            {"type": "user", "origin": {"kind": "human"}, "message": {"content": "draft the handoff"}})
        code, message = self.run_guard(self.publish_event(t))
        self.assertEqual(code, 2)
        self.assertIn("does not occur verbatim", message)

    def test_missing_transcript_blocks(self):
        self.draft.write_text(handoff())
        code, message = self.run_guard(self.publish_event(str(self.base / "missing.jsonl")))
        self.assertEqual(code, 2)
        self.assertIn("cannot read the session transcript", message)

    def test_draft_and_dry_run_pass(self):
        self.draft.write_text(handoff(status="DRAFT", approval="NONE"))
        self.assertEqual(self.run_guard(self.publish_event(self.transcript()))[0], 0)
        self.draft.write_text(handoff())
        dry = f"bin/harness publish handoff {self.draft} --actor DT --dry-run"
        self.assertEqual(self.run_guard(self.publish_event(self.transcript(), dry))[0], 0)

    def test_other_commands_pass(self):
        self.assertEqual(self.run_guard(self.publish_event(self.transcript(), "bin/harness list"))[0], 0)

    def test_project_state_limit(self):
        config = json.loads((self.root / "config.json").read_text())
        config["limits"] = {"project_state_chars": 200}
        (self.root / "config.json").write_text(json.dumps(config))
        state = str(self.root / "state" / "PROJECT_STATE.md")
        small = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                 "tool_input": {"file_path": state, "content": "# Project State\n"}}
        self.assertEqual(self.run_guard(small)[0], 0)
        big = dict(small, tool_input={"file_path": state, "content": "x" * 201})
        code, message = self.run_guard(big)
        self.assertEqual(code, 2)
        self.assertIn("The limit is 200", message)
        edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                "tool_input": {"file_path": state, "old_string": "NONE", "new_string": "y" * 300}}
        self.assertEqual(self.run_guard(edit)[0], 2)


class LimitAndDraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)
        self.store = Store(self.root)

    def tearDown(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        self.tmp.cleanup()

    def test_body_limit_and_dry_run(self):
        from harness.records import RecordError
        config = json.loads((self.root / "config.json").read_text())
        config["limits"] = {"handoff_body_chars": 3}
        (self.root / "config.json").write_text(json.dumps(config))
        with self.assertRaises(RecordError) as caught:
            self.store.publish("handoff", handoff(), "DT")
        self.assertIn("The limit is 3", str(caught.exception))
        config["limits"] = {}
        (self.root / "config.json").write_text(json.dumps(config))
        record = self.store.publish("handoff", handoff(), "DT", dry_run=True)
        self.assertEqual(record.record_id, "HANDOFF-DEMO-3-R1")
        self.assertEqual(self.store.ids("handoff"), [])

    def test_handoff_draft_fills_revision_and_predecessor(self):
        first = self.store.publish("handoff", handoff(), "DT")
        draft = self.store.handoff_draft("DEMO-3")
        self.assertIn('"revision": 2', draft)
        self.assertIn(first.ref, draft)
        self.assertIn('"status": "DRAFT"', draft)
        self.store.publish("handoff", draft, "DT", dry_run=True)
        self.assertIn('"revision": 1', self.store.handoff_draft("NEW-1"))


if __name__ == "__main__":
    unittest.main()
