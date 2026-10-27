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

    def test_message_loop_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"limits": {"messages_per_peer_per_10_minutes": 3}}))
            text = format_message("STATUS", [], "ping")
            event = {"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
                     "tool_input": {"to": "dt", "message": text}}
            codes = []
            for _ in range(4):
                stdin = io.StringIO(json.dumps(event))
                with mock.patch.dict(os.environ, {"HARNESS_DATA_ROOT": str(root)}), \
                        mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stderr", io.StringIO()), \
                        mock.patch.object(sys, "argv", ["message_ledger.py", "--tower", "et"]):
                    codes.append(message_ledger.main())
            self.assertEqual(codes, [0, 0, 0, 2])
            entries = list(journal.read(root / "messages.jsonl"))
            self.assertEqual(entries[-1]["blocked"], "rate limit")

    def test_message_to_another_cycle_is_blocked(self):
        def send(root, tower, to):
            event = {"hook_event_name": "PreToolUse", "tool_name": "SendMessage",
                     "tool_input": {"to": to, "message": format_message("STATUS", [], "ping")}}
            with mock.patch.dict(os.environ, {"HARNESS_DATA_ROOT": str(root)}), \
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), \
                    mock.patch.object(sys, "stderr", io.StringIO()), \
                    mock.patch.object(sys, "argv", ["message_ledger.py", "--tower", tower]):
                return message_ledger.main()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"towers": {"name_suffix": "_tmp"}}))
            self.assertEqual(send(root, "dt", "et"), 2)          # the main cycle
            self.assertEqual(send(root, "dt", "et_tmp"), 0)
            self.assertEqual(send(root, "dt", "et_tmp [3fa9c1]"), 0)
            self.assertEqual(send(root, "dt", "codex-to-claude-harness-migration"), 0)  # not a tower
            (root / "config.json").write_text(json.dumps({"towers": {}}))
            self.assertEqual(send(root, "et", "dt_tmp"), 2)
            self.assertEqual(send(root, "et", "dt2"), 0)
            self.assertEqual(send(root, "et", "dt"), 0)
            blocked = [e["peer"] for e in journal.read(root / "messages.jsonl") if e.get("blocked") == "other cycle"]
            self.assertEqual(blocked, ["et", "dt_tmp"])

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

    def test_models_from_config(self):
        from harness.tower import render_settings, tower_model
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = json.loads(render_settings("vt", root).read_text())["env"]
            self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-opus-5-5")  # default: never below Opus
            self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"], "1")
            self.assertEqual(env["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "claude-opus-5-5")
            (root / "config.json").write_text(json.dumps({"towers": {"models": {"vt": "fable"}}}))
            self.assertEqual(tower_model("vt", root), "fable")
            self.assertEqual(tower_model("et", root), "opus")
            env = json.loads(render_settings("vt", root).read_text())["env"]
            self.assertEqual(env["CLAUDE_CODE_SUBAGENT_MODEL"], "claude-fable-5-1")

    def test_resume_adds_session_and_snapshot_off(self):
        from harness import tower
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(tower, "resolve_session", return_value="8cdef581-full-id"):
                command = tower.start_command("dt", root, resume="8cdef581")
            self.assertEqual(command[-4:], ["--resume", "8cdef581-full-id", "--system-prompt-snapshot", "off"])

    def test_every_tower_template_renders(self):
        from harness.store import REPO_ROOT
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
                # A tower can call the CLI by a relative or an absolute path (seen 2026-10-09).
                self.assertIn(f"{REPO_ROOT}/bin/harness *", settings["sandbox"]["excludedCommands"])
                self.assertIn(f"Bash({REPO_ROOT}/bin/harness executor *)", settings["permissions"]["deny"])
                self.assertIn(f"Bash(./bin/harness root)", settings["permissions"]["allow"])
            dt = json.loads(render_settings("dt", root).read_text())
            self.assertNotIn(f"Read(/{root}/workspace/**)", dt["permissions"]["deny"])  # DT reads code (option B)
            self.assertIn(f"Edit(/{root}/workspace/**)", dt["permissions"]["deny"])
            self.assertNotIn(f"{root}/workspace", dt["sandbox"]["filesystem"]["denyRead"])


if __name__ == "__main__":
    unittest.main()
