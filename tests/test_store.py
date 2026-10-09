import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from harness import journal
from harness.records import RecordError, compute_outcome, parse, parse_ref, serialize
from harness.store import Store, init_data_root

FIXTURES = Path(__file__).parent / "fixtures"
COMMIT = "ff25847a583f3117ed2ee8e658a97b266e54f5a4"


def fixture(name: str, **refs: str) -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    for key, value in refs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def markdown(front: dict, body: str = "Body text.\n") -> str:
    return "---\n" + json.dumps(front, indent=2) + "\n---\n" + body


def edit_markdown(text: str, **changes) -> str:
    front, body = parse("handoff", text)
    front.update(changes)
    return markdown(front, body)


def edit_json(text: str, **changes) -> str:
    fields = json.loads(text)
    fields.update(changes)
    return json.dumps(fields)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = init_data_root(Path(self.tmp.name) / "data", write_pointer=False)
        self.store = Store(self.root)

    def tearDown(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o644)
        self.tmp.cleanup()

    def assertRefused(self, kind, text, fragment, actor="ET"):
        with self.assertRaises(RecordError) as caught:
            self.store.publish(kind, text, actor)
        self.assertTrue(any(fragment in e for e in caught.exception.errors),
                        f"{fragment!r} not in {caught.exception.errors}")

    def publish_handoff(self):
        return self.store.publish("handoff", fixture("handoff_transplant.md"), "DT")

    def publish_verdict(self, handoff):
        return self.store.publish("verdict", fixture("verdict_pre_run.md", HANDOFF_REF=handoff.ref), "VT")

    # Exit criterion 2: converted records are accepted.

    def test_converted_handoff_verdict_and_task(self):
        handoff = self.publish_handoff()
        self.assertEqual(handoff.record_id, "HANDOFF-VLM-OPSD-TRANSPLANT-001-R1")
        verdict = self.publish_verdict(handoff)
        self.assertEqual(verdict.fields["overall"], "VALID")
        task = self.store.publish("task", fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref,
                                                  VERDICT_REF=verdict.ref), "ET")
        self.assertEqual(task.record_id, "TASK-VLM-OPSD-TRANSPLANT-001-001")
        self.assertEqual(self.store.verify(), [])

    def test_records_are_read_only_and_journaled(self):
        handoff = self.publish_handoff()
        path = self.store.path_of(handoff.record_id)
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        events = list(journal.read(self.store.journal_path))
        self.assertEqual(events[-1]["ref"], handoff.ref)
        self.assertEqual(events[-1]["actor"], "DT")
        self.assertEqual(self.store.resolve(handoff.ref, "handoff").data, path.read_bytes())

    def test_verify_detects_tampering(self):
        handoff = self.publish_handoff()
        path = self.store.path_of(handoff.record_id)
        os.chmod(path, 0o644)
        path.write_bytes(path.read_bytes().replace(b"Body", b"body"))
        path.write_bytes(path.read_bytes() + b"\nedited\n")
        problems = self.store.verify()
        self.assertTrue(any("writable" in p for p in problems))
        self.assertTrue(any("no PUBLISHED journal entry" in p for p in problems))

    # Exit criterion 3: invalid drafts are refused with a specific error.

    def test_draft_with_id_is_refused(self):
        text = edit_markdown(fixture("handoff_transplant.md"), id="HANDOFF-X-R1")
        self.assertRefused("handoff", text, "must not contain 'id'")

    def test_handoff_revision_order(self):
        handoff = self.publish_handoff()
        again = fixture("handoff_transplant.md")
        self.assertRefused("handoff", edit_markdown(again, revision=3, predecessor=handoff.ref),
                           "revision must be 2")
        other = "HANDOFF-VLM-OPSD-TRANSPLANT-001-R1@sha256:" + "0" * 64
        self.assertRefused("handoff", edit_markdown(again, revision=2, predecessor=other),
                           "predecessor must be")
        second = self.store.publish("handoff", edit_markdown(again, revision=2, predecessor=handoff.ref), "DT")
        self.assertEqual(second.record_id, "HANDOFF-VLM-OPSD-TRANSPLANT-001-R2")

    def test_handoff_rules(self):
        text = fixture("handoff_transplant.md")
        self.assertRefused("handoff", edit_markdown(text, approval="NONE"), "needs an 'approval' quotation")
        only_pre = [{"id": "G1", "phase": "PRE_RUN", "check": "One check."}]
        self.assertRefused("handoff", edit_markdown(text, gates=only_pre), "at least one POST_RUN gate")
        only_post = [{"id": "G1", "phase": "POST_RUN", "check": "One check."}]
        self.assertRefused("handoff", edit_markdown(text, gates=only_post), "at least one PRE_RUN gate")
        self.assertRefused("handoff", edit_markdown(text, status="FINAL"), "'status' must be one of")
        self.assertRefused("handoff", edit_markdown(text, extra=1), "unknown field 'extra'")

    def test_verdict_overall_is_computed_and_gates_must_match(self):
        handoff = self.publish_handoff()
        base = fixture("verdict_pre_run.md", HANDOFF_REF=handoff.ref)
        front, body = parse("verdict", base)
        partial = copy.deepcopy(front)
        partial["gates"][1]["verdict"] = "PARTIAL"
        self.assertEqual(self.store.publish("verdict", markdown(partial, body), "VT").fields["overall"],
                         "NOT_VALID")
        missing = copy.deepcopy(front)
        missing["gates"] = missing["gates"][:1]
        self.assertRefused("verdict", markdown(missing, body), "verdict gates must be exactly", "VT")
        with_overall = dict(front, overall="VALID")
        self.assertRefused("verdict", markdown(with_overall, body), "must not contain 'overall'", "VT")

    def test_full_task_needs_matching_valid_verdict(self):
        handoff = self.publish_handoff()
        verdict = self.publish_verdict(handoff)
        task = fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref, VERDICT_REF=verdict.ref)
        self.assertRefused("task", edit_json(task, pre_run_verdict="NONE"), "needs a pre_run_verdict")
        self.assertRefused("task", edit_json(task, commit="a" * 40), "names a different commit")
        front, body = parse("verdict", fixture("verdict_pre_run.md", HANDOFF_REF=handoff.ref))
        front["gates"][0]["verdict"] = "INVALID"
        bad = self.store.publish("verdict", markdown(front, body), "VT")
        self.assertRefused("task", edit_json(task, pre_run_verdict=bad.ref), "is not VALID")
        wrong_hash = verdict.ref[:-1] + ("0" if verdict.ref[-1] != "0" else "1")
        self.assertRefused("task", edit_json(task, pre_run_verdict=wrong_hash), "SHA-256 does not match")

    def test_verdict_on_promoting_predecessor_is_accepted(self):
        text = fixture("handoff_transplant.md")
        validation = self.store.publish("handoff", edit_markdown(
            text, authorization="VALIDATION_RUN", full_run_condition="ALL_PRE_RUN_GATES_VALID"), "DT")
        verdict = self.store.publish("verdict", fixture("verdict_pre_run.md", HANDOFF_REF=validation.ref), "VT")
        full = self.store.publish("handoff", edit_markdown(text, revision=2, predecessor=validation.ref), "DT")
        task = fixture("task_transplant_full.json", HANDOFF_REF=full.ref, VERDICT_REF=verdict.ref)
        self.store.publish("task", task, "ET")
        changed = parse("handoff", text)[0]["gates"]
        changed[0]["check"] = "A different check."
        third = self.store.publish("handoff", edit_markdown(text, revision=3, predecessor=full.ref, gates=changed), "DT")
        task = fixture("task_transplant_full.json", HANDOFF_REF=third.ref, VERDICT_REF=verdict.ref)
        self.assertRefused("task", task, "names a different handoff")

    def test_handoff_task_authorization_matrix(self):
        handoff = self.publish_handoff()  # APPROVED / FULL_RUN
        task = fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref, VERDICT_REF="NONE")
        validation = edit_json(task, source="HANDOFF", user_request="NONE", execution_class="VALIDATION")
        self.store.publish("task", validation, "ET")  # FULL_RUN also covers VALIDATION runs
        text = fixture("handoff_transplant.md")
        implement_only = edit_markdown(text, revision=2, predecessor=handoff.ref, authorization="IMPLEMENT_ONLY",
                                       gates=[{"id": "G1", "phase": "POST_RUN", "check": "One check."}])
        second = self.store.publish("handoff", implement_only, "DT")
        task = fixture("task_transplant_full.json", HANDOFF_REF=second.ref, VERDICT_REF="NONE")
        validation = edit_json(task, source="HANDOFF", user_request="NONE", execution_class="VALIDATION")
        self.assertRefused("task", validation, "needs authorization VALIDATION_RUN or FULL_RUN")

    def test_task_schema_rules(self):
        handoff = self.publish_handoff()
        task = fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref, VERDICT_REF="NONE")
        validation = edit_json(task, execution_class="VALIDATION")
        self.assertRefused("task", edit_json(validation, required_outputs=[]), "non-empty required_outputs")
        self.assertRefused("task", edit_json(validation, entrypoint=[]), "non-empty entrypoint")
        self.assertRefused("task", edit_json(validation, commit="ff25847"), "full 40-character commit")
        self.assertRefused("task", edit_json(validation, output_root="relative/path"), "absolute output_root")
        self.assertRefused("task", edit_json(validation, required_outputs=["../x"]), "relative path inside")
        gpu = {"count": 2, "policy": "FIXED", "devices": [6], "constraints": "NONE"}
        self.assertRefused("task", edit_json(validation, gpu=gpu), "list of 'count' GPU indices")
        self.assertRefused("task", edit_json(validation, user_request="NONE"), "must copy the user request")

    def test_server_and_repo_must_be_configured_when_config_has_servers(self):
        os.chmod(self.root / "config.json", 0o644)
        (self.root / "config.json").write_text(json.dumps({"servers": {"a100": {"repos": {"x": {}}}}}))
        handoff = self.publish_handoff()
        task = fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref, VERDICT_REF="NONE")
        self.assertRefused("task", edit_json(task, execution_class="VALIDATION"), "not in config.json")

    def test_result_outcome_rule(self):
        handoff = self.publish_handoff()
        verdict = self.publish_verdict(handoff)
        task = self.store.publish("task", fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref,
                                                  VERDICT_REF=verdict.ref), "ET")
        result = {"kind": "result", "task": task.ref, "transport": "OK", "preflight": "PASS", "exit_code": 0,
                  "outcome": "PASS", "git": {}, "runtime": {}, "outputs": [], "missing_outputs": ["run/metrics.json"],
                  "started_at": "x", "ended_at": "y", "logs": {}, "message": "NONE"}
        self.assertEqual(compute_outcome(result), "FAIL")
        self.assertRefused("result", json.dumps(result), "outcome must be FAIL", "EXECUTOR")
        result.update(missing_outputs=[], exit_code=0)
        stored = self.store.publish("result", json.dumps(result), "EXECUTOR")
        self.assertEqual(stored.record_id, f"RESULT-{task.record_id}")
        self.assertRefused("result", json.dumps(result), "already exists", "EXECUTOR")

    def test_report_needs_sections(self):
        handoff = self.publish_handoff()
        front = {"kind": "report", "experiment_id": "VLM-OPSD-TRANSPLANT-001", "handoff": handoff.ref,
                 "commits": [COMMIT], "tasks": [], "results": []}
        body = "## Changes\n\nx\n\n## Evidence\n\nx\n\n## Proposals\n\nNONE\n"
        self.assertRefused("report", markdown(front, body), "'## Deviations'")
        report = self.store.publish("report", markdown(front, body + "\n## Deviations\n\nNONE\n"), "ET")
        self.assertEqual(report.record_id, "REPORT-VLM-OPSD-TRANSPLANT-001-001")

    def directive(self, scope="REMOTE_RUN", target="et", experiment="VLM-OPSD-TRANSPLANT-001"):
        front = {"kind": "directive", "target": target, "scope": scope, "instruction": "run the base model",
                 "experiment_id": experiment}
        return self.store.publish("directive", markdown(front, "Run the BASE 4B full run.\n"), "DT")

    def test_directive_ids_and_schema(self):
        self.assertEqual(self.directive(experiment="NONE").record_id, "DIRECTIVE-GENERAL-001")
        self.assertEqual(self.directive().record_id, "DIRECTIVE-VLM-OPSD-TRANSPLANT-001-001")
        front = {"kind": "directive", "target": "dt", "scope": "ALL", "instruction": "x", "experiment_id": "NONE"}
        self.assertRefused("directive", markdown(front), "'target' must be one of", "DT")
        self.assertRefused("directive", markdown(front), "'scope' must be one of", "DT")

    def test_task_directive_scope(self):
        handoff = self.publish_handoff()
        task = fixture("task_transplant_full.json", HANDOFF_REF=handoff.ref, VERDICT_REF="NONE")
        validation = json.loads(edit_json(task, execution_class="VALIDATION"))
        read_only = self.directive(scope="READ_ONLY")
        self.assertRefused("task", json.dumps(dict(validation, directive=read_only.ref)), "needs a directive with scope REMOTE_RUN")
        for_vt = self.directive(target="vt")
        self.assertRefused("task", json.dumps(dict(validation, directive=for_vt.ref)), "does not target et")
        self.store.publish("task", json.dumps(dict(validation, directive=self.directive().ref)), "ET")

    def test_ref_format(self):
        with self.assertRaises(RecordError):
            parse_ref("TASK-X-001")
        self.assertEqual(parse_ref("TASK-X-001@sha256:" + "a" * 64)[0], "TASK-X-001")

    def test_serialize_round_trip(self):
        fields = {"id": "HANDOFF-X-R1", "kind": "handoff"}
        data = serialize("handoff", fields, "## A\n")
        self.assertEqual(parse("handoff", data.decode()), (fields, "## A\n"))


if __name__ == "__main__":
    unittest.main()
