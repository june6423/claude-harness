import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))

import message_ledger  # noqa: E402
from harness import journal  # noqa: E402
from harness.messages import format_message, parse_header, parse_inbound  # noqa: E402
from harness.records import RecordError  # noqa: E402

REF = "REPORT-X-001@sha256:" + "a" * 64


class HeaderTest(unittest.TestCase):
    def test_round_trip(self):
        text = format_message("REVIEW_REQUEST", [REF], "Review the report.")
        self.assertEqual(parse_header(text), {"type": "REVIEW_REQUEST", "refs": [REF], "body": "Review the report."})

    def test_no_refs(self):
        self.assertEqual(parse_header(format_message("STATUS", [], "ET starts."))["refs"], [])

    def test_invalid_headers(self):
        for text, fragment in (
            ("hello", "must start with"),
            ("[HMSG v1]\ntype: STATUS\nrefs: NONE\n", "no '---' line"),
            ("[HMSG v1]\ntype: PING\nrefs: NONE\n---\nx", "type must be one of"),
            ("[HMSG v1]\ntype: STATUS\nrefs: TASK-1\n---\nx", "not a record reference"),
            ("[HMSG v1]\ntype: STATUS\nrefs: NONE\n---\n", "body must not be empty"),
        ):
            with self.assertRaises(RecordError) as caught:
                parse_header(text)
            self.assertTrue(any(fragment in e for e in caught.exception.errors), caught.exception.errors)


class InboundTest(unittest.TestCase):
    # The wrapper format comes from a live test on 2026-10-08 (Claude Code 2.1.293).
    PROMPT = ('<cross-session-message from="uds:/run/user/1000/cc-socks/1.sock" from-name="et" '
              'from-mode="prompting">\n[HMSG v1]\ntype: STATUS\nrefs: NONE\n---\nET starts.\n'
              '</cross-session-message>')

    def test_parse_inbound(self):
        found = parse_inbound(self.PROMPT)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["from_name"], "et")
        self.assertEqual(parse_header(found[0]["text"])["body"], "ET starts.")

    def test_plain_prompt_has_no_message(self):
        self.assertEqual(parse_inbound("Run the tests."), [])


class LedgerHookTest(unittest.TestCase):
    def run_hook(self, event, root):
        stdin = io.StringIO(json.dumps(event))
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"HARNESS_DATA_ROOT": str(root)}), \
                mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "argv", ["message_ledger.py", "--tower", "vt"]):
            code = message_ledger.main()
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "")  # UserPromptSubmit stdout would enter the model context
        return list(journal.read(root / "messages.jsonl"))

    def test_sent_and_received(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = format_message("VERDICT_READY", [REF], "The verdict is ready.")
            sent = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "SendMessage",
                    "tool_input": {"to": "dt", "summary": "verdict", "message": text}}
            entries = self.run_hook(sent, root)
            self.assertEqual(entries[-1]["direction"], "sent")
            self.assertEqual(entries[-1]["peer"], "dt")
            self.assertEqual(entries[-1]["header"]["type"], "VERDICT_READY")
            received = {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                        "prompt": InboundTest.PROMPT}
            entries = self.run_hook(received, root)
            self.assertEqual(entries[-1]["direction"], "received")
            self.assertEqual(entries[-1]["peer"], "et")

    def test_malformed_message_is_recorded_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
                     "tool_input": {"to": "dt", "message": "free text"}}
            entry = self.run_hook(event, Path(tmp))[-1]
            self.assertIsNone(entry["header"])
            self.assertIn("must start with", entry["header_error"])

    def test_other_events_write_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
            self.assertEqual(self.run_hook(event, Path(tmp)), [])
            self.assertEqual(self.run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "hi"}, Path(tmp)), [])


class TowerCommandTest(unittest.TestCase):
    def test_start_command_and_rendered_settings(self):
        from harness.store import REPO_ROOT
        from harness.tower import start_command
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = start_command("vt", root, model="sonnet")
            self.assertEqual(command[:7], ["claude", "--bg", "--agent", "vt", "--name", "vt",
                                           "--append-system-prompt-file"])
            self.assertEqual(command[-2:], ["--model", "sonnet"])
            settings_path = Path(command[command.index("--settings") + 1])
            self.assertEqual(settings_path, root / "runtime" / "settings" / "vt.json")
            text = settings_path.read_text()
            self.assertNotIn("{{", text)
            settings = json.loads(text)
            self.assertEqual(settings["permissions"]["defaultMode"], "dontAsk")
            self.assertIn(f"Edit(/{root}/runtime/drafts/vt/**)", settings["permissions"]["allow"])
            self.assertIn(f"Edit(/{REPO_ROOT}/**)", settings["permissions"]["deny"])
            self.assertTrue((root / "runtime" / "drafts" / "vt").is_dir())
            with self.assertRaises(RecordError):
                start_command("xt", root)

    def test_resume_adds_session_and_snapshot_off(self):
        from harness import tower
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(tower, "resolve_session", return_value="8cdef581-full-id"):
                command = tower.start_command("dt", root, resume="8cdef581")
            self.assertEqual(command[-4:], ["--resume", "8cdef581-full-id", "--system-prompt-snapshot", "off"])

    def test_every_tower_template_renders(self):
        from harness.tower import TOWERS, render_settings
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in TOWERS:
                settings = json.loads(render_settings(name, root).read_text())
                self.assertNotIn("sandbox", settings)  # off unless config.json enables it
                self.assertIn(f"--tower {name}", json.dumps(settings["hooks"]))
            (root / "config.json").write_text(json.dumps({"towers": {"sandbox": True}}))
            for name in TOWERS:
                settings = json.loads(render_settings(name, root).read_text())
                self.assertTrue(settings["sandbox"]["enabled"])
                self.assertTrue(settings["sandbox"]["failIfUnavailable"])
            dt = json.loads(render_settings("dt", root).read_text())
            self.assertNotIn(f"Read(/{root}/workspace/**)", dt["permissions"]["deny"])  # DT reads code (option B)
            self.assertIn(f"Edit(/{root}/workspace/**)", dt["permissions"]["deny"])
            self.assertNotIn(f"{root}/workspace", dt["sandbox"]["filesystem"]["denyRead"])


if __name__ == "__main__":
    unittest.main()
