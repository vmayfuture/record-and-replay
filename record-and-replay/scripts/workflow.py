#!/usr/bin/env python3
"""Validate recorded workflows, print replay plans, and maintain local journals.

This helper never controls a UI, evaluates commands, or executes a workflow.
Only the Python standard library is required.
"""

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone


class WorkflowError(Exception):
    """An input or state error that can be reported without exposing values."""


SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
PARAMETER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")
PLACEHOLDER = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_-]*)\}\}")
OPERATIONS = {"open", "click", "fill", "select", "key", "scroll", "drag", "wait", "tool", "manual"}
EFFECTS = {"none", "local_write", "external_write", "irreversible"}
RETRIES = {"safe", "check-first", "never-on-unknown"}
STATUSES = {"pending", "started", "verified", "failed", "outcome_unknown"}
TRANSIENT_TARGET_KEYS = {
    "x", "y", "left", "top", "right", "bottom", "width", "height", "bounds",
    "frame", "coordinates", "coordinate", "position", "point", "bbox", "bounding_box",
    "from_x", "from_y", "to_x", "to_y", "element_index", "elementindex",
    "element_ref", "elementref", "ref", "index", "window_id", "window_index",
    "snapshot_ref", "snapshot_id", "windowid", "windowindex", "snapshotref", "snapshotid",
}


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def choice(value, allowed):
    return isinstance(value, str) and value in allowed


def slug(value):
    return isinstance(value, str) and len(value) <= 64 and bool(SLUG.fullmatch(value))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def duplicate_checked_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON contains a duplicate object key")
        result[key] = value
    return result


def reject_constant(_value):
    raise WorkflowError("JSON contains a non-finite number")


def read_json(path):
    try:
        with Path(path).open("r", encoding="utf-8-sig") as source:
            return json.load(source, object_pairs_hook=duplicate_checked_object, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}") from None


def type_matches(value, kind):
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return type(value) is int or isinstance(value, float) and math.isfinite(value)
    if kind == "boolean":
        return isinstance(value, bool)
    return False


def check_templates(value, parameters, location):
    if isinstance(value, str):
        for match in PLACEHOLDER.finditer(value):
            require(match.group(1) in parameters, f"{location}: undeclared parameter placeholder")
        remainder = PLACEHOLDER.sub("", value)
        require("{{" not in remainder and "}}" not in remainder, f"{location}: malformed parameter placeholder")
    elif isinstance(value, dict):
        for child in value.values():
            check_templates(child, parameters, location)
    elif isinstance(value, list):
        for child in value:
            check_templates(child, parameters, location)


def validate_flow(flow):
    require(isinstance(flow, dict), "Workflow must be a JSON object")
    require(type(flow.get("version")) is int and flow["version"] == 1, "Workflow version must be 1")
    require(slug(flow.get("name")), "Workflow name must be a lowercase slug of at most 64 characters")
    require(nonempty(flow.get("goal")), "Workflow goal must be a nonempty string")
    context = flow.get("context")
    require(isinstance(context, dict) and nonempty(context.get("application")), "Context needs an application string")
    require(all(nonempty(value) for value in context.values()), "Context values must be nonempty semantic strings")
    parameters = flow.get("parameters")
    require(isinstance(parameters, dict), "Parameters must be an object")
    for name, spec in parameters.items():
        require(bool(PARAMETER_NAME.fullmatch(name)) and len(name) <= 64, "Invalid parameter name")
        require(isinstance(spec, dict), f"Parameter {name}: definition must be an object")
        require(choice(spec.get("type"), {"string", "integer", "number", "boolean"}), f"Parameter {name}: invalid type")
        require(type(spec.get("required")) is bool, f"Parameter {name}: required must be a boolean")
        require(type(spec.get("secret")) is bool, f"Parameter {name}: secret must be a boolean")
        require(not (spec["secret"] and "default" in spec), f"Parameter {name}: secret defaults are forbidden")
        if "default" in spec:
            require(type_matches(spec["default"], spec["type"]), f"Parameter {name}: default has the wrong type")
    recording = flow.get("recording")
    require(isinstance(recording, dict), "Recording metadata must be an object")
    require(choice(recording.get("source"), {"agent-executed", "recorder-import", "user-description"}), "Invalid recording source")
    require(choice(recording.get("status"), {"draft", "observed"}), "Invalid recording status")
    require(not (recording["source"] == "user-description" and recording["status"] != "draft"), "User-described workflows must remain draft")
    steps = flow.get("steps")
    require(isinstance(steps, list) and bool(steps), "Workflow needs at least one step")
    step_ids = set()
    for step in steps:
        require(isinstance(step, dict), "Each step must be an object")
        step_id = step.get("id")
        require(slug(step_id), "Step IDs must be lowercase slugs of at most 64 characters")
        require(step_id not in step_ids, "Step IDs must be unique")
        step_ids.add(step_id)
        for field in ("intent", "precondition", "postcondition"):
            require(nonempty(step.get(field)), f"Step {step_id}: {field} must be a nonempty string")
        require(choice(step.get("operation"), OPERATIONS), f"Step {step_id}: invalid operation")
        target = step.get("target")
        require(isinstance(target, dict) and nonempty(target.get("description")), f"Step {step_id}: target needs a description")
        for key, value in target.items():
            normalized_key = key.replace("-", "_").lower()
            require(normalized_key not in TRANSIENT_TARGET_KEYS, f"Step {step_id}: transient indexes and coordinates are forbidden")
            require(nonempty(value), f"Step {step_id}: target fields must be nonempty semantic strings")
        require(choice(step.get("effect"), EFFECTS), f"Step {step_id}: invalid effect")
        require(choice(step.get("retry"), RETRIES), f"Step {step_id}: invalid retry policy")
        if "value" in step:
            require(isinstance(step["value"], (str, dict)), f"Step {step_id}: value must be a string or object")
        evidence = step.get("recorded_evidence")
        if evidence is not None or recording["status"] == "observed":
            require(isinstance(evidence, dict) and nonempty(evidence.get("before")) and nonempty(evidence.get("after")), f"Step {step_id}: observed evidence needs before and after strings")
        executable_step = {key: value for key, value in step.items() if key != "recorded_evidence"}
        check_templates(executable_step, parameters, f"Step {step_id}")
    check_templates(flow["goal"], parameters, "Goal")
    check_templates(context, parameters, "Context")
    verification = flow.get("verification")
    require(isinstance(verification, dict) and choice(verification.get("status"), {"not-run", "passed", "failed"}), "Invalid verification metadata")
    if verification["status"] == "passed":
        require(nonempty(verification.get("run_evidence")), "Passed verification requires run_evidence")
    return flow


def flow_fingerprint(flow):
    execution = {key: copy.deepcopy(flow[key]) for key in ("version", "name", "goal", "context", "parameters", "steps")}
    for step in execution["steps"]:
        step.pop("recorded_evidence", None)
    return hashlib.sha256(canonical(execution).encode("utf-8")).hexdigest()


def resolve_parameters(flow, supplied):
    require(isinstance(supplied, dict), "Parameter input must be a JSON object")
    definitions = flow["parameters"]
    require(not (supplied.keys() - definitions.keys()), "Parameter input contains undeclared names")
    values = {}
    for name, spec in definitions.items():
        if name in supplied:
            value = supplied[name]
        elif "default" in spec:
            value = spec["default"]
        else:
            require(not spec["required"], f"Missing required parameter: {name}")
            continue
        require(type_matches(value, spec["type"]), f"Parameter {name}: input has the wrong type")
        values[name] = value
    return values


def scalar_text(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def render(value, values, definitions, typed=True):
    """Substitute only the recipe text, once. Input strings are never templates."""
    if isinstance(value, str):
        def parameter(match):
            name = match.group(1)
            require(name in values, f"No value supplied for referenced parameter: {name}")
            return f"<secret:{name}>" if definitions[name]["secret"] else values[name]
        complete = PLACEHOLDER.fullmatch(value)
        if complete:
            result = parameter(complete)
            return result if typed else scalar_text(result)
        return PLACEHOLDER.sub(lambda match: scalar_text(parameter(match)), value)
    if isinstance(value, dict):
        return {key: render(child, values, definitions, typed) for key, child in value.items()}
    if isinstance(value, list):
        return [render(child, values, definitions, typed) for child in value]
    return value


def displayed_parameters(flow, values):
    return {name: f"<secret:{name}>" if flow["parameters"][name]["secret"] else value for name, value in values.items()}


def make_binding(flow, values):
    return {
        "flow_name": flow["name"],
        "flow_sha256": flow_fingerprint(flow),
        "context": render(flow["context"], values, flow["parameters"], typed=False),
        "parameters": displayed_parameters(flow, values),
        "step_ids": [step["id"] for step in flow["steps"]],
    }


def validate_journal(journal):
    require(isinstance(journal, dict) and type(journal.get("version")) is int and journal["version"] == 1, "Invalid journal version")
    binding = journal.get("binding")
    require(isinstance(binding, dict), "Journal binding is missing")
    require(slug(binding.get("flow_name")), "Invalid journal workflow name")
    require(isinstance(binding.get("flow_sha256"), str) and bool(re.fullmatch(r"[0-9a-f]{64}", binding["flow_sha256"])), "Invalid journal workflow fingerprint")
    require(isinstance(binding.get("context"), dict) and isinstance(binding.get("parameters"), dict), "Invalid journal context or parameters")
    step_ids = binding.get("step_ids")
    require(isinstance(step_ids, list) and bool(step_ids) and all(slug(step_id) for step_id in step_ids), "Invalid journal step IDs")
    require(len(step_ids) == len(set(step_ids)), "Journal step IDs must be unique")
    states = journal.get("steps")
    require(isinstance(states, dict) and set(states) == set(step_ids), "Journal steps do not match its binding")
    for step_id, state in states.items():
        require(isinstance(state, dict) and choice(state.get("status"), STATUSES), f"Step {step_id}: invalid journal status")
        if state["status"] == "verified":
            require(nonempty(state.get("evidence")), f"Step {step_id}: verified state needs evidence")
    require(isinstance(journal.get("history"), list), "Journal history must be a list")
    last_status = {}
    for event in journal["history"]:
        require(isinstance(event, dict) and isinstance(event.get("step"), str) and event["step"] in states and choice(event.get("status"), STATUSES), "Invalid journal history event")
        require(nonempty(event.get("at")) and choice(event.get("action"), {"step", "reconcile"}), "Invalid journal history metadata")
        if event["status"] == "verified" or event["action"] == "reconcile":
            require(nonempty(event.get("evidence")), "Journal verification and reconciliation need evidence")
        last_status[event["step"]] = event["status"]
    for step_id, state in states.items():
        require(last_status.get(step_id, "pending") == state["status"], "Journal state differs from its latest history event")
    return journal


def make_plan(flow, values, journal=None):
    binding = make_binding(flow, values)
    if journal is not None:
        validate_journal(journal)
        require(canonical(journal["binding"]) == canonical(binding), "Journal does not match this workflow, context, or non-secret parameters; create a new journal")
    plan_steps = []
    blocked_by = None
    for step in flow["steps"]:
        state = journal["steps"][step["id"]]["status"] if journal else "pending"
        action = "recheck" if state == "verified" else "reconcile" if state in {"started", "outcome_unknown"} else "execute"
        if action == "reconcile" and blocked_by is None:
            blocked_by = step["id"]
        item = {key: render(step[key], values, flow["parameters"], typed=(key == "value")) for key in ("id", "intent", "operation", "target", "precondition", "postcondition", "effect", "retry")}
        if "value" in step:
            item["value"] = render(step["value"], values, flow["parameters"])
        item.update({"status": state, "action": action, "blocked": blocked_by is not None})
        if blocked_by is not None:
            item["blocked_by"] = blocked_by
        plan_steps.append(item)
    return {
        "version": 1,
        "flow": {"name": flow["name"], "sha256": binding["flow_sha256"], "goal": render(flow["goal"], values, flow["parameters"], typed=False)},
        "context": binding["context"],
        "parameters": binding["parameters"],
        "steps": plan_steps,
        "blocked": blocked_by is not None,
    }


def write_json(path, document, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8", newline="\n") as destination:
            json.dump(document, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as destination:
            temporary = Path(destination.name)
            json.dump(document, destination, ensure_ascii=False, indent=2, allow_nan=False)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def journal_init(flow, values, path):
    binding = make_binding(flow, values)
    # Render every executable field before creating a file; missing optional input
    # that is actually referenced must fail without leaving a partial journal.
    make_plan(flow, values)
    journal = {"version": 1, "binding": binding, "steps": {step_id: {"status": "pending"} for step_id in binding["step_ids"]}, "history": []}
    try:
        write_json(path, journal, exclusive=True)
    except FileExistsError:
        raise WorkflowError("Journal already exists; refusing to overwrite it") from None
    return {"journal": str(Path(path)), "flow_name": flow["name"], "step_count": len(flow["steps"])}


def change_journal(path, step_id, status=None, evidence=None, outcome=None):
    journal = validate_journal(read_json(path))
    require(step_id in journal["steps"], "Step ID does not exist in this journal")
    current = journal["steps"][step_id]["status"]
    if outcome is not None:
        require(current in {"started", "outcome_unknown"}, "Only started or outcome_unknown steps can be reconciled")
        require(nonempty(evidence), "Reconciliation requires nonempty, redacted evidence")
        require(choice(outcome, {"applied", "not-applied"}), "Invalid reconciliation outcome")
        new_status = "verified" if outcome == "applied" else "failed"
        action = "reconcile"
    else:
        require(choice(status, STATUSES - {"pending"}), "Invalid requested journal status")
        require(current != "outcome_unknown", "Unknown outcomes require journal-reconcile before any further action")
        if status == "started":
            require(current in {"pending", "failed"}, "Only pending or failed steps can be started; reconcile an unfinished attempt first")
        else:
            require(current == "started", "Verified, failed, or outcome_unknown can only follow started")
        if status == "verified":
            require(nonempty(evidence), "Verification requires nonempty, redacted evidence")
        new_status, action = status, "step"
    state = {"status": new_status}
    event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": step_id, "action": action, "status": new_status}
    if nonempty(evidence):
        state["evidence"] = evidence.strip()
        event["evidence"] = evidence.strip()
    if outcome is not None:
        event["outcome"] = outcome
    journal["steps"][step_id] = state
    journal["history"].append(event)
    validate_journal(journal)
    write_json(path, journal)
    return {"journal": str(Path(path)), "step": step_id, "status": new_status, "history_count": len(journal["history"])}


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate a workflow without executing anything")
    validate.add_argument("flow")
    plan = commands.add_parser("plan", help="Print a parameterized, secret-redacted replay plan")
    plan.add_argument("flow")
    plan.add_argument("--params", required=True, help="Path to a JSON object of input values")
    plan.add_argument("--journal")
    initialize = commands.add_parser("journal-init", help="Create a new bound journal without overwriting an existing file")
    initialize.add_argument("flow")
    initialize.add_argument("--params", required=True)
    initialize.add_argument("--out", required=True)
    step = commands.add_parser("journal-step", help="Record an attempt state; this does not perform the action")
    step.add_argument("journal")
    step.add_argument("--step", required=True)
    step.add_argument("--status", required=True, choices=sorted(STATUSES - {"pending"}))
    step.add_argument("--evidence", help="Already-redacted proof summary or receipt reference; never paste secrets")
    reconcile = commands.add_parser("journal-reconcile", help="Resolve an uncertain attempt using external evidence")
    reconcile.add_argument("journal")
    reconcile.add_argument("--step", required=True)
    reconcile.add_argument("--outcome", required=True, choices=("applied", "not-applied"))
    reconcile.add_argument("--evidence", required=True, help="Already-redacted proof summary or receipt reference")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in {"validate", "plan", "journal-init"}:
            flow = validate_flow(read_json(args.flow))
            if args.command == "validate":
                output = {"valid": True, "name": flow["name"], "step_count": len(flow["steps"]), "sha256": flow_fingerprint(flow)}
            else:
                values = resolve_parameters(flow, read_json(args.params))
                output = make_plan(flow, values, read_json(args.journal) if args.journal else None) if args.command == "plan" else journal_init(flow, values, args.out)
        else:
            output = change_journal(args.journal, args.step, evidence=args.evidence, status=getattr(args, "status", None), outcome=getattr(args, "outcome", None))
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (WorkflowError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
