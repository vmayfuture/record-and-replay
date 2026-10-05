"""Behavior tests for the local workflow helper; no UI or network access."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "record-and-replay" / "scripts" / "workflow.py"
SPEC = importlib.util.spec_from_file_location("workflow_helper", SCRIPT)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


def flow_fixture():
    return {
        "version": 1,
        "name": "export-report",
        "goal": "Export report for {{month}}",
        "context": {"application": "browser", "origin": "https://example.test", "account": "{{account}}"},
        "parameters": {
            "month": {"type": "string", "required": True, "secret": False},
            "account": {"type": "string", "required": True, "secret": False},
            "token": {"type": "string", "required": True, "secret": True},
            "count": {"type": "integer", "required": False, "secret": False, "default": 3},
            "enabled": {"type": "boolean", "required": False, "secret": False, "default": True},
        },
        "recording": {"source": "agent-executed", "status": "observed"},
        "steps": [
            {
                "id": "enter-token", "intent": "Fill the token field", "operation": "fill",
                "target": {"description": "API token field", "role": "textbox", "label": "API token"},
                "value": "{{token}}", "precondition": "Correct account is open", "postcondition": "Token field is populated",
                "effect": "local_write", "retry": "safe", "recorded_evidence": {"before": "Field was empty", "after": "Field is masked"},
            },
            {
                "id": "export", "intent": "Export {{month}} report", "operation": "tool",
                "target": {"description": "Report export tool", "tool": "report-export"},
                "value": {"month": "{{month}}", "count": "{{count}}", "enabled": "{{enabled}}", "auth": "Bearer {{token}}"},
                "precondition": "Report filters match {{month}}", "postcondition": "Matching report exists",
                "effect": "external_write", "retry": "never-on-unknown", "recorded_evidence": {"before": "Report page visible", "after": "Receipt R-1 visible"},
            },
            {
                "id": "inspect", "intent": "Inspect output", "operation": "manual",
                "target": {"description": "Downloaded report"}, "precondition": "Report exists", "postcondition": "Report month matches {{month}}",
                "effect": "none", "retry": "safe", "recorded_evidence": {"before": "Report available", "after": "Month checked"},
            },
        ],
        "verification": {"status": "not-run"},
    }


class WorkflowBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.flow = flow_fixture()
        self.parameters = {"month": "2026-09", "account": "reporting@example.test", "token": "super-private-credential-987"}
        self.flow_path = self.directory / "flow.json"
        self.params_path = self.directory / "params.json"
        self.journal_path = self.directory / "journal.json"
        self.save(self.flow_path, self.flow)
        self.save(self.params_path, self.parameters)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def cli(self, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0 if success else 2, result.stdout + result.stderr)
        return result

    def initialize(self):
        self.cli("journal-init", self.flow_path, "--params", self.params_path, "--out", self.journal_path)

    def plan(self, journal=False):
        args = ["plan", self.flow_path, "--params", self.params_path]
        if journal:
            args += ["--journal", self.journal_path]
        return json.loads(self.cli(*args).stdout)

    def test_secret_redacted_in_plan_journal_and_fingerprints(self):
        plan = self.plan()
        self.assertEqual(plan["steps"][0]["value"], "<secret:token>")
        self.assertEqual(plan["steps"][1]["value"]["auth"], "Bearer <secret:token>")
        self.initialize()
        journal_text = self.journal_path.read_text(encoding="utf-8")
        self.assertNotIn(self.parameters["token"], json.dumps(plan) + journal_text)
        previous = json.loads(journal_text)["binding"]
        self.parameters["token"] = "another-private-token"
        self.save(self.params_path, self.parameters)
        self.assertEqual(self.plan(journal=True)["flow"]["sha256"], previous["flow_sha256"])

    def test_whole_value_preserves_types_and_embedded_inputs_are_not_templates(self):
        self.parameters["month"] = "{{token}}"
        self.save(self.params_path, self.parameters)
        plan = self.plan()
        exported = plan["steps"][1]["value"]
        self.assertEqual(exported["month"], "{{token}}")
        self.assertEqual(exported["count"], 3)
        self.assertIs(exported["enabled"], True)
        self.assertIn("{{token}}", plan["steps"][1]["intent"])
        self.assertNotIn(self.parameters["token"], json.dumps(plan))

    def test_missing_required_wrong_type_and_unknown_input_refused_without_values(self):
        del self.parameters["month"]
        self.save(self.params_path, self.parameters)
        result = self.cli("plan", self.flow_path, "--params", self.params_path, success=False)
        self.assertIn("Missing required", result.stderr)
        self.parameters["month"] = "2026-09"
        self.parameters["count"] = "secret-looking-wrong-type"
        self.save(self.params_path, self.parameters)
        result = self.cli("plan", self.flow_path, "--params", self.params_path, success=False)
        self.assertNotIn(self.parameters["count"], result.stderr)
        self.parameters.pop("count")
        self.parameters["unexpected"] = "should-not-be-logged"
        self.save(self.params_path, self.parameters)
        self.cli("plan", self.flow_path, "--params", self.params_path, success=False)

    def test_secret_default_and_undeclared_placeholder_refused(self):
        self.flow["parameters"]["token"]["default"] = "do-not-print-this"
        self.save(self.flow_path, self.flow)
        result = self.cli("validate", self.flow_path, success=False)
        self.assertNotIn("do-not-print-this", result.stderr)
        self.flow["parameters"]["token"].pop("default")
        self.flow["steps"][0]["value"] = "{{unknown}}"
        self.save(self.flow_path, self.flow)
        self.cli("validate", self.flow_path, success=False)

    def test_transient_targets_refused(self):
        for key, value in [("ref", "e12"), ("element_index", "12"), ("elementIndex", "12"), ("windowId", "12"), ("snapshotRef", "e12"), ("x", "100"), ("coordinates", "100,200")]:
            with self.subTest(key=key):
                flow = copy.deepcopy(self.flow)
                flow["steps"][0]["target"][key] = value
                self.save(self.flow_path, flow)
                self.cli("validate", self.flow_path, success=False)

    def test_recording_and_verification_claims_require_evidence(self):
        cases = []
        described = copy.deepcopy(self.flow)
        described["recording"]["source"] = "user-description"
        cases.append(described)
        missing_capture = copy.deepcopy(self.flow)
        missing_capture["steps"][0].pop("recorded_evidence")
        cases.append(missing_capture)
        unproven = copy.deepcopy(self.flow)
        unproven["verification"]["status"] = "passed"
        cases.append(unproven)
        for flow in cases:
            self.save(self.flow_path, flow)
            self.cli("validate", self.flow_path, success=False)
        self.flow["verification"] = {"status": "passed", "run_evidence": "Isolated test matched month and receipt R-1"}
        self.save(self.flow_path, self.flow)
        self.cli("validate", self.flow_path)

    def test_journal_context_parameter_and_execution_changes_refused(self):
        self.initialize()
        for change in ("parameters", "context", "locator"):
            with self.subTest(change=change):
                flow = copy.deepcopy(self.flow)
                params = dict(self.parameters)
                if change == "parameters":
                    params["month"] = "2026-10"
                elif change == "context":
                    flow["context"]["origin"] = "https://other.example.test"
                else:
                    flow["steps"][0]["target"]["label"] = "Different field"
                self.save(self.flow_path, flow)
                self.save(self.params_path, params)
                self.cli("plan", self.flow_path, "--params", self.params_path, "--journal", self.journal_path, success=False)

    def test_recording_and_verification_metadata_do_not_invalidate_journal(self):
        self.initialize()
        self.flow["recording"]["source"] = "recorder-import"
        self.flow["verification"] = {"status": "passed", "run_evidence": "Verified an isolated replay"}
        self.flow["steps"][0]["recorded_evidence"]["after"] = "Improved redacted capture description"
        self.save(self.flow_path, self.flow)
        self.plan(journal=True)

    def test_duplicate_journal_init_never_overwrites(self):
        self.initialize()
        original = self.journal_path.read_bytes()
        self.cli("journal-init", self.flow_path, "--params", self.params_path, "--out", self.journal_path, success=False)
        self.assertEqual(original, self.journal_path.read_bytes())

    def test_unknown_blocks_later_steps_and_requires_reconciliation(self):
        self.initialize()
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "started")
        plan = self.plan(journal=True)
        self.assertFalse(plan["steps"][0]["blocked"])
        self.assertEqual(plan["steps"][1]["action"], "reconcile")
        self.assertTrue(plan["blocked"])
        self.assertTrue(plan["steps"][2]["blocked"])
        self.assertEqual(plan["steps"][2]["status"], "pending")
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "outcome_unknown")
        original = self.journal_path.read_bytes()
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "started", success=False)
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "verified", "--evidence", "Blind retry would duplicate", success=False)
        self.assertEqual(original, self.journal_path.read_bytes())
        self.cli("journal-reconcile", self.journal_path, "--step", "export", "--outcome", "applied", "--evidence", "Receipt R-1 confirms this run")
        plan = self.plan(journal=True)
        self.assertFalse(plan["blocked"])
        self.assertEqual(plan["steps"][1]["action"], "recheck")
        journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        self.assertEqual([item["status"] for item in journal["history"]], ["started", "outcome_unknown", "verified"])

    def test_confirmed_not_applied_allows_a_new_attempt(self):
        self.initialize()
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "started")
        self.cli("journal-reconcile", self.journal_path, "--step", "export", "--outcome", "not-applied", "--evidence", "Destination query confirms no corresponding report")
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "started")
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "verified", "--evidence", "Receipt R-2 matches the new attempt")
        self.assertEqual(self.plan(journal=True)["steps"][1]["action"], "recheck")

    def test_verified_requires_started_and_evidence_and_bad_id_is_rejected(self):
        self.initialize()
        original = self.journal_path.read_bytes()
        self.cli("journal-step", self.journal_path, "--step", "missing", "--status", "started", success=False)
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "verified", "--evidence", "Not actually started", success=False)
        self.assertEqual(original, self.journal_path.read_bytes())
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "started")
        self.cli("journal-step", self.journal_path, "--step", "export", "--status", "verified", success=False)
        self.cli("journal-reconcile", self.journal_path, "--step", "export", "--outcome", "applied", "--evidence", " ", success=False)

    def test_boolean_not_accepted_as_integer(self):
        self.parameters["count"] = True
        self.save(self.params_path, self.parameters)
        self.cli("plan", self.flow_path, "--params", self.params_path, success=False)

    def test_wrong_shaped_enum_is_a_validation_error(self):
        cases = []
        operation = copy.deepcopy(self.flow)
        operation["steps"][0]["operation"] = ["do-not-print-input"]
        cases.append(operation)
        verification = copy.deepcopy(self.flow)
        verification["verification"]["status"] = []
        cases.append(verification)
        parameter = copy.deepcopy(self.flow)
        parameter["parameters"]["token"]["type"] = {"do-not-print-input": "private"}
        cases.append(parameter)
        for flow in cases:
            self.save(self.flow_path, flow)
            result = self.cli("validate", self.flow_path, success=False)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("do-not-print-input", result.stderr)

    def test_referenced_optional_parameter_without_value_fails_before_journal_creation(self):
        self.flow["parameters"]["count"].pop("default")
        self.save(self.flow_path, self.flow)
        self.cli("journal-init", self.flow_path, "--params", self.params_path, "--out", self.journal_path, success=False)
        self.assertFalse(self.journal_path.exists())

    def test_cli_does_not_execute_tool_names_or_value_text(self):
        marker = self.directory / "must-not-exist.txt"
        self.flow["steps"][1]["target"]["tool"] = "subprocess.run"
        self.flow["steps"][1]["value"] = {"command": f"echo should-not-run > {marker}"}
        self.save(self.flow_path, self.flow)
        self.plan()
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
