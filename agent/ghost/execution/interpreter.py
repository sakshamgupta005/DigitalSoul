import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from contracts.validate import FORMATS, semantic_errors
from .context import typed
from .errors import GhostError
from .expressions import compile_expression, render

SCHEMA = Path(__file__).resolve().parents[3] / "contracts" / "workflow.schema.json"


def compile_workflow(workflow, policy):
    errors = list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FORMATS).iter_errors(workflow))
    if errors:
        raise GhostError("VALIDATION_ERROR", "Workflow does not match the shared schema", 422)
    if semantic_errors(workflow):
        raise GhostError("VALIDATION_ERROR", "Workflow references or scopes are invalid", 422)
    for rule in workflow["decision_rules"]:
        compile_expression(rule["expr"])

    def walk(steps):
        for step in steps:
            policy.classify(step)
            if step["recovery"]["on_fail"] == "skip":
                raise GhostError("POLICY_BLOCKED", "Optional-step skipping is not supported by this runtime", 403)
            if step["kind"] in ("loop", "decision") and (step["pre"] or step["post"]):
                raise GhostError("POLICY_BLOCKED", "Container conditions require an explicit assert step", 403)
            if step["kind"] == "loop":
                walk(step["body"])
    walk(workflow["steps"])
    allowed_checks = {
        ("acme-crm", "tag_equals_for_all"): {"ids", "expected_by_id"},
        ("acme-portal", "team_note_exists"): {"containing", "run_id", "expected_count"},
    }
    for check in workflow["outcome_checks"]:
        fields = allowed_checks.get((check["app"], check["check"]))
        if check["type"] != "app_api" or fields is None or set(check["args"]) != fields:
            raise GhostError("POLICY_BLOCKED", "Outcome adapter or arguments are not registered", 403)
    # Copy out of the owner's mutable object; callers cannot edit an admitted plan.
    return copy.deepcopy(workflow)


def bind_parameters(workflow, supplied):
    user = {p["name"]: p for p in workflow["parameters"] if p["source"] == "user"}
    if set(supplied) - user.keys():
        raise GhostError("VALIDATION_ERROR", "Unknown or derived parameter override", 422)
    result = {}
    for name, parameter in user.items():
        if name not in supplied and "default_value" not in parameter:
            raise GhostError("VALIDATION_ERROR", "Required run parameter missing", 422)
        result[name] = typed(supplied.get(name, parameter.get("default_value")), parameter["type"])
    return result


def resolved_step(step, values):
    result = copy.deepcopy(step)
    for field in ("locators", "pre", "post", "input"):
        if field in result:
            result[field] = render(result[field], values, url=field == "input" and step.get("verb") == "navigate")
    return result
