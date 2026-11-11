import io
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
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

    def test_mid_turn_warning_once_per_session(self):
        transcript = self.root / "t.jsonl"
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        def write(tokens):
            transcript.write_text(json.dumps({"type": "assistant", "message": {"usage": dict(usage, input_tokens=tokens)}}) + "\n")
        def post(session="s1"):
            event = {"hook_event_name": "PostToolUse", "session_id": session, "transcript_path": str(transcript)}
            err = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(event))), mock.patch.object(sys, "stderr", err), \
                    mock.patch.object(sys, "argv", ["context_guard.py", "--tower", "vt"]):
                return context_guard.main(), err.getvalue()
        write(500)  # setUp sets context_rotate_tokens to 1000
        self.assertEqual(post()[0], 0)
        write(400000)
        code, message = post()
        self.assertEqual(code, 2)
        self.assertIn("end your turn", message)
        self.assertEqual(post()[0], 0)            # once per session
        self.assertEqual(post("s2")[0], 2)        # a new session is warned again
        self.assertEqual(context_guard.context_tokens(str(transcript), tail_bytes=10), 0)  # a cut line is skipped
        self.assertEqual(context_guard.context_tokens(str(transcript), tail_bytes=10**6), 400000)

    def test_handover_moves_the_session_entries_to_the_archive(self):
        worklog = self.root / "worklog" / "dt.md"
        worklog.write_text("# DT worklog\n\nNotes.\n\n## 2026-11-01 session\n\n- did A\n\n## 2026-11-02 analysis\n\n- did B\n")
        draft = self.root / "h.md"
        draft.write_text("Handover one.")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "dt", str(draft)]), 0)
        text = worklog.read_text()
        self.assertTrue(text.startswith("# DT worklog\n\nNotes.\n"))
        self.assertNotIn("did A", text)
        self.assertEqual(text.count("## Handover "), 1)
        logs = list((self.root / "archive").glob("dt-log-*.md"))
        self.assertEqual(len(logs), 1)
        archived = logs[0].read_text()
        self.assertIn("## 2026-11-01 session", archived)
        self.assertIn("did B", archived)
        self.assertIn(logs[0].name, (self.root / "archive" / "INDEX.md").read_text())
        # the next rotation appends to the same month file and keeps one INDEX line
        worklog.write_text(text + "\n## 2026-11-03 work\n\n- did C\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "dt", str(draft)]), 0)
        self.assertEqual(len(list((self.root / "archive").glob("dt-log-*.md"))), 1)
        self.assertIn("did C", logs[0].read_text())
        self.assertIn("Handover one.", logs[0].read_text())  # the earlier handover moved too
        self.assertEqual((self.root / "archive" / "INDEX.md").read_text().count(logs[0].name), 1)

    def _old(self, path: Path, text: str = "x", days: float = 10) -> Path:
        """Write an old file. Its new directories under the drafts directory get the old time too."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        stamp = path.stat().st_mtime - days * 86400
        os.utime(path, (stamp, stamp))
        drafts_root = self.root / "runtime" / "drafts"
        for parent in path.parents:
            if parent.parent == drafts_root or drafts_root not in parent.parents:
                break  # do not age runtime/drafts/<tower> itself
            if not any(item.stat().st_mtime > stamp for item in parent.iterdir()):
                os.utime(parent, (stamp, stamp))
        return path

    def test_handover_prunes_old_dt_drafts(self):
        base = self.root / "runtime" / "drafts" / "dt"
        old_msg = self._old(base / "msg" / "msg_et_001.txt")
        old_flat = self._old(base / "notes.md")
        draft_front = '---\n{"kind": "directive", "experiment_id": "VLM-X-001"}\n---\n'
        unpublished = self._old(base / "records" / "DIRECTIVE-plan.md", draft_front + "## Plan\n\nOnly copy of a plan.\n")
        (self.root / "records" / "directive" / "DIRECTIVE-VLM-X-001-001.md").write_text(
            '---\n{"kind": "directive", "id": "DIRECTIVE-VLM-X-001-001"}\n---\n## Plan\n\nPublished  text.\n')
        published = self._old(base / "records" / "DIRECTIVE-pub.md", draft_front + "## Plan\n\nPublished text.\n")
        old_script = self._old(base / "scripts" / "one_time.py")
        cited = self._old(base / "scripts" / "cited_check.py")
        pending = self._old(base / "records" / "HANDOFF-VLM-X-002-R1.md")
        cache = self._old(base / "__pycache__" / "a.cpython-312.pyc", days=0)
        hidden = self._old(base / ".claude" / "x.json")
        handover = self._old(base / "handover.md", "Handover text. Pending: records/HANDOFF-VLM-X-002-R1.md")
        new_script = base / "scripts" / "today.py"
        new_script.write_text("x")
        (self.root / "archive" / "reports").mkdir(parents=True, exist_ok=True)
        (self.root / "archive" / "reports" / "2026-11-01_X_결과보고서.md").write_text("Made by cited_check.py.\n")
        with mock.patch.object(sys, "stdout", io.StringIO()) as out:
            self.assertEqual(main(["--data-root", str(self.root), "handover", "dt", str(handover)]), 0)
        self.assertIn("deleted 5 old drafts", out.getvalue())
        self.assertIn("moved 1 unpublished record drafts", out.getvalue())
        for gone in (old_msg, old_flat, old_script, cache.parent, published, unpublished):
            self.assertFalse(gone.exists(), gone)
        moved = self.root / "archive" / "unpublished" / "dt" / "DIRECTIVE-plan.md"
        self.assertIn("Only copy of a plan.", moved.read_text())
        for kept in (cited, pending, hidden, handover, new_script):
            self.assertTrue(kept.exists(), kept)  # cited, named in the new handover, hidden, the draft, new
        for name in ("records", "msg", "scripts"):
            self.assertTrue((base / name).is_dir())
        events = [json.loads(l) for l in (self.root / "journal.jsonl").read_text().splitlines()]
        pruned = [e for e in events if e.get("event") == "DRAFTS_PRUNED"]
        self.assertEqual(pruned[-1]["count"], 5)
        self.assertIn("msg/msg_et_001.txt", pruned[-1]["files"])
        self.assertEqual(pruned[-1]["archived"], ["records/DIRECTIVE-plan.md"])

    def test_handover_prunes_et_directories_and_keeps_venvs(self):
        base = self.root / "runtime" / "drafts" / "et"
        old_dir = base / "scratch_rescore"
        self._old(old_dir / "out" / "a.json")
        self._old(old_dir / "README.md")  # a common file name in the archive does not keep the directory
        self._old(old_dir / "TASK-plan.md", '---\n{"kind": "task"}\n---\n## Plan\n\nNever published.\n')
        mixed = base / "packfix"
        self._old(mixed / "old.log")
        (mixed / "new.log").write_text("x")  # one new file keeps the directory
        named = base / "r2"
        self._old(named / "x.py")
        short = base / "pr2"
        self._old(short / "x.py")  # "r2" in the archive does not name "pr2"
        venv = base / "rollout-venv"
        self._old(venv / "pyvenv.cfg")
        self._old(venv / "lib" / "torch.py")
        loose = self._old(base / "old.py")
        (self.root / "archive" / "VLM-X-001-et.md").write_text("# X\n\n## Log\n\nSee runtime/drafts/et/r2/ and README.md.\n")
        draft = base / "handover.md"
        draft.write_text("Handover.")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "et", str(draft)]), 0)
        for gone in (old_dir, short, loose):
            self.assertFalse(gone.exists(), gone)
        for kept in (mixed, named, venv / "lib" / "torch.py", draft):
            self.assertTrue(kept.exists(), kept)
        self.assertIn("Never published.",
                      (self.root / "archive" / "unpublished" / "et" / "scratch_rescore" / "TASK-plan.md").read_text())
        self.assertFalse((base / "records").exists())  # the subdirectories are a DT rule only

    def test_vt_keeps_only_drafts_that_the_live_layers_name(self):
        base = self.root / "runtime" / "drafts" / "vt"
        logged = self._old(base / "rollout-71514bc6" / "a.py")
        live = self._old(base / "grad-aaaf1453" / "a.py")
        (self.root / "archive" / "vt-log-2026-11.md").write_text("# VT\n\n## Log\n\nExported rollout-71514bc6.\n")
        (self.root / "worklog" / "vt.md").write_text("# VT worklog\n\nOpen: grad-aaaf1453 export.\n")
        draft = self.root / "h.md"
        draft.write_text("Handover.")
        with mock.patch.object(sys, "stdout", io.StringIO()) as out:
            self.assertEqual(main(["--data-root", str(self.root), "handover", "vt", str(draft)]), 0)
        self.assertIn("not named in worklog/ or state/", out.getvalue())
        self.assertFalse(logged.parent.exists())  # the archive log alone does not keep a VT draft
        self.assertTrue(live.exists())  # the worklog header (not a session entry) names it
        # A name only in a session entry of the worklog moves to the archive at the handover and no longer
        # keeps a VT draft: the handover text and the summary carry what the next session needs.
        moved = self._old(base / "stats-011" / "a.py")
        (self.root / "worklog" / "vt.md").write_text("# VT worklog\n\n## 2026-11-01 session\n\n- used stats-011\n")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "vt", str(draft)]), 0)
        self.assertFalse(moved.parent.exists())

    def test_dt2_is_not_pruned_and_dry_run_deletes_nothing(self):
        dt2_old = self._old(self.root / "runtime" / "drafts" / "dt2" / "old.py")
        draft = self.root / "h.md"
        draft.write_text("Handover.")
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "dt2", str(draft)]), 0)
        self.assertTrue(dt2_old.exists())  # drafts.prune_towers is dt, et, vt
        dt_old = self._old(self.root / "runtime" / "drafts" / "dt" / "old.py")
        with mock.patch.object(sys, "stdout", io.StringIO()) as out:
            self.assertEqual(main(["--data-root", str(self.root), "drafts", "prune", "dt", "--dry-run"]), 0)
        self.assertIn("would delete 1", out.getvalue())
        self.assertIn("old.py", out.getvalue())
        self.assertTrue(dt_old.exists())
        with mock.patch.object(sys, "stdout", io.StringIO()):
            self.assertEqual(main(["--data-root", str(self.root), "drafts", "prune", "dt", "--keep-days", "30"]), 0)
        self.assertTrue(dt_old.exists())  # 10 days old, younger than 30

    def test_handover_warns_when_the_summary_is_over_the_limit(self):
        (self.root / "worklog").mkdir(exist_ok=True)
        (self.root / "worklog" / "et-summary.md").write_text("x" * 9000)
        draft = self.root / "h.md"
        draft.write_text("Handover text.")
        out = io.StringIO()
        with mock.patch.object(sys, "stdout", out):
            self.assertEqual(main(["--data-root", str(self.root), "handover", "et", str(draft)]), 0)
        self.assertIn("handover_summary_chars", out.getvalue())
        events = [json.loads(l)["event"] for l in (self.root / "journal.jsonl").read_text().splitlines() if l.strip()]
        self.assertIn("SUMMARY_OVER_LIMIT", events)

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

    def test_turn_ended_reads_the_transcript(self):
        from harness import tower as towers
        repo = Path(self.tmp.name) / "repo"
        home = Path(self.tmp.name) / "home"
        with mock.patch.object(Path, "home", return_value=home):
            folder = towers.transcript_dir(repo)
            folder.mkdir(parents=True)

            def write(*entries):
                lines = [json.dumps(e) for e in entries] + ["not json"]
                (folder / "s1.jsonl").write_text("\n".join(lines) + "\n")

            text = {"type": "assistant", "timestamp": "2026-11-11T05:57:34.000Z",
                    "message": {"content": [{"type": "text", "text": "done"}]}}
            tool = {"type": "assistant", "timestamp": "2026-11-11T05:57:34.000Z",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}
            result = {"type": "user", "timestamp": "2026-11-11T05:57:40.000Z",
                      "message": {"content": [{"type": "tool_result", "content": "x"}]}}
            later = datetime(2026, 11, 11, 6, 5, tzinfo=timezone.utc).timestamp()
            write({"type": "user", "message": {"content": "hi"}}, text, {"type": "system", "subtype": "x"})
            self.assertTrue(towers.turn_ended("s1", 180, now=later, repo=repo))
            self.assertFalse(towers.turn_ended("s1", 180, now=later - 400, repo=repo))  # too recent
            write(text, tool)
            self.assertFalse(towers.turn_ended("s1", 180, now=later, repo=repo))  # a tool runs
            write(tool, result)
            self.assertFalse(towers.turn_ended("s1", 180, now=later, repo=repo))  # the model works on a result
            write(text, {"type": "user", "timestamp": "2026-11-11T05:58:00.000Z", "message": {"content": "next"}})
            self.assertFalse(towers.turn_ended("s1", 180, now=later, repo=repo))  # a new prompt
            self.assertFalse(towers.turn_ended("missing", 180, now=later, repo=repo))

    def test_executor_rotates_a_tower_stuck_in_working_after_its_turn(self):
        request = self.root / "runtime" / "rotate" / "dt.json"
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text("{}")
        executor = Executor(Store(self.root))
        stuck = [{"name": "dt", "state": "working", "status": "idle", "sessionId": "s1"}]
        with mock.patch("harness.tower.named", return_value=stuck), \
                mock.patch("harness.tower.turn_ended", return_value=True), \
                mock.patch("harness.tower.start", return_value={"sessionId": "s2"}) as start:
            self.assertEqual(executor.rotate_towers(), [])  # the request is new: wait
            start.assert_not_called()
            old = time.time() - 400
            os.utime(request, (old, old))
            with mock.patch("harness.tower.turn_ended", return_value=False):
                self.assertEqual(executor.rotate_towers(), [])  # a tool runs: wait
            with mock.patch.object(sys, "stdout", io.StringIO()):
                self.assertEqual(executor.rotate_towers(), ["dt"])
        self.assertFalse(request.exists())
        events = [json.loads(l) for l in (self.root / "journal.jsonl").read_text().splitlines()]
        self.assertTrue([e for e in events if e.get("event") == "TOWER_ROTATED"][-1]["stuck_working"])


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

    def test_restart_holds_the_keep_alive(self):
        import time
        from harness import tower
        executor = Executor(Store(self.root))
        session = {"id": "a", "sessionId": "a-full"}
        hold = tower.keepalive_hold("et", self.root)
        hold.parent.mkdir(parents=True)
        hold.write_text(str(time.time()))
        with mock.patch("harness.tower.reaped", side_effect=lambda name: session if name == "et" else None), \
                mock.patch("harness.tower.respawn") as respawn:
            self.assertEqual(executor.revive_towers(), [])  # et is held
            respawn.assert_not_called()
        hold.write_text(str(time.time() - 1000))  # a stale hold from a crashed restart does not block
        with mock.patch("harness.tower.reaped", side_effect=lambda name: session if name == "et" else None), \
                mock.patch("harness.tower.respawn") as respawn:
            self.assertEqual(executor.revive_towers(), ["et"])
        # start(replace=True) writes the hold before the stop and removes it at the end
        with mock.patch.object(tower, "stop") as stop, mock.patch.object(tower, "named", return_value=[]), \
                mock.patch.object(tower, "running", return_value={"id": "n"}), \
                mock.patch("subprocess.run") as run:
            def check_hold(*args, **kwargs):
                self.assertTrue(tower.keepalive_held("et", self.root))
            stop.side_effect = check_hold
            tower.start("et", self.root, replace=True)
            stop.assert_called_once()
        self.assertFalse(tower.keepalive_held("et", self.root))

    def test_executor_does_not_respawn_a_session_that_just_started(self):
        import time
        executor = Executor(Store(self.root))
        session = {"id": "n", "sessionId": "n-full", "startedAt": time.time() * 1000 - 5000}
        with mock.patch("harness.tower.reaped", side_effect=lambda name: session if name == "dt" else None), \
                mock.patch("harness.tower.respawn") as respawn:
            self.assertEqual(executor.revive_towers(), [])
            respawn.assert_not_called()

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
