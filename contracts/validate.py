"""Draft contract checks. Does not implement an execution or crypto verifier."""
import ast
import copy
import json
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
TEMPLATE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
EXPR_NODES = (ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name,
              ast.Load, ast.Constant, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq,
              ast.Lt, ast.LtE, ast.Gt, ast.GtE)
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def utc_timestamp(value):
    # Contract timestamps use UTC Z; enforce without optional format packages.
    if not isinstance(value, str):
        return True
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value):
        return False
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))


def semantic_errors(workflow):
    """Check static references/scopes, not runtime permission or adapter behavior."""
    errors = []
    seen = set()
    params = {p["name"]: p for p in workflow["parameters"]}
    rules = {r["id"]: r for r in workflow["decision_rules"]}
    if len(params) != len(workflow["parameters"]):
        errors.append("duplicate parameter name")
    if len(rules) != len(workflow["decision_rules"]):
        errors.append("duplicate rule ID")
    for p in params.values():
        if p["name"] in ("run_id", "expected_tags", "decision"):
            errors.append("reserved parameter name")
        if not p["confirmed"]:
            errors.append("unconfirmed parameter")
        if p["source"] != "user" and not re.fullmatch(r"step:[a-z][a-z0-9_]*\.output", p["source"]):
            errors.append("unsupported parameter source")
    rule_inputs = {}
    for rid, rule in rules.items():
        if not rule["confirmed"]:
            errors.append("unconfirmed rule")
        try:
            tree = ast.parse(rule["expr"], mode="eval")
            if any(not isinstance(node, EXPR_NODES) for node in ast.walk(tree)):
                errors.append("unsupported expression syntax")
            if any(isinstance(node, ast.Constant) and type(node.value) not in (str, int, float, bool)
                   for node in ast.walk(tree)):
                errors.append("unsupported expression literal")
            rule_inputs[rid] = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        except (SyntaxError, ValueError, RecursionError):
            errors.append("invalid decision expression")
            rule_inputs[rid] = set()

    bindings = {}

    def templates(value, scope, label):
        if isinstance(value, dict):
            for child in value.values():
                templates(child, scope, label)
        elif isinstance(value, list):
            for child in value:
                templates(child, scope, label)
        elif isinstance(value, str):
            for name in TEMPLATE.findall(value):
                if name in scope:
                    continue
                if name.endswith(".length") and scope.get(name[:-7]) == "string[]":
                    continue
                errors.append(f"{label}: unavailable template {name}")
            if "{{" in TEMPLATE.sub("", value) or "}}" in TEMPLATE.sub("", value):
                errors.append(f"{label}: malformed template")

    def walk(steps, scope, depth=0):
        for step in steps:
            sid = step["step_id"]
            if sid in seen:
                errors.append("duplicate step ID")
            seen.add(sid)
            for field in ("input", "pre", "locators"):
                templates(step.get(field), scope, sid)
            for check in step["pre"] + step["post"]:
                if "locator_ref" in check and not 0 <= check["locator_ref"] < len(step["locators"]):
                    errors.append(f"{sid}: locator_ref out of range")
            if step["kind"] == "loop":
                iterator = step["loop_iterator"]
                if scope.get(iterator["source_param"]) != "string[]":
                    errors.append(f"{sid}: loop source must be available string[]")
                if iterator["item_name"] in scope or iterator["item_name"] in ("decision", "expected_tags"):
                    errors.append(f"{sid}: loop item shadows existing name")
                walk(step["body"], {**scope, iterator["item_name"]: "string"}, depth + 1)
            elif step["kind"] == "decision":
                rid = step["rule_id"]
                if rid not in rules:
                    errors.append(f"{sid}: unknown decision rule")
                elif not rule_inputs[rid].issubset(scope):
                    errors.append(f"{sid}: unavailable decision input")
                scope[f"decision.{rid}"] = "string"
            elif step.get("verb") == "read_field":
                name = step["output_name"]
                if name in scope or name in ("decision", "expected_tags"):
                    errors.append(f"{sid}: read output shadows existing name")
                scope[name] = step["output_type"]
                bindings[sid] = (name, step["output_type"], depth)
            templates(step["post"], scope, sid)
        return scope

    scope = walk(workflow["steps"], {"run_id": "string", **{
        p["name"]: p["type"] for p in params.values() if p["source"] == "user"
    }})
    for p in params.values():
        if p["source"].startswith("step:"):
            sid = p["source"][5:-7]
            if bindings.get(sid) != (p["name"], p["type"], 0):
                errors.append("parameter output binding mismatch")
    templates(workflow["outcome_checks"], {**scope, "expected_tags": "map"}, "outcomes")
    return errors


def main():
    validators = {}
    count = 0
    for path in sorted(ROOT.glob("*.schema.json")):
        schema = load(path.name)
        Draft202012Validator.check_schema(schema)
        validators[path.name.removesuffix(".schema.json")] = Draft202012Validator(schema, format_checker=FORMATS)
        count += 1
    for path in sorted((ROOT / "fixtures").glob("*.json")):
        kind = path.name.split(".")[0]
        valid = not list(validators[kind].iter_errors(load("fixtures/" + path.name)))
        assert valid == (".valid." in path.name), f"Unexpected fixture result: {path.name}"
        count += 1
    wf = load("fixtures/workflow.valid.json")
    assert not semantic_errors(wf), semantic_errors(wf)
    count += 1

    def reject(kind, mutate, semantic=False):
        nonlocal count
        name = "raw_event.valid.1.json" if kind == "raw_event" else kind + ".valid.json"
        value = copy.deepcopy(load("fixtures/" + name))
        mutate(value)
        errors = list(validators[kind].iter_errors(value))
        if semantic and not errors:
            errors = semantic_errors(value)
        assert errors, f"Mutation unexpectedly accepted: {kind} {mutate}"
        count += 1

    reject("raw_event", lambda v: v["value"].update(redacted=True, text="secret"))
    reject("raw_event", lambda v: v["value"].update(shape="secret_omitted", redacted=False, text=None))
    reject("raw_event", lambda v: v["app"].update(unexpected="data"))
    reject("raw_event", lambda v: v.update(ts="not-a-date"))
    reject("workflow", lambda v: v["steps"][2].pop("body"))
    reject("workflow", lambda v: v["steps"][2]["loop_iterator"].update(max_iterations=0))
    reject("workflow", lambda v: v["steps"][1].pop("output_name"))
    reject("workflow", lambda v: v["steps"][-1]["recovery"].update(retries=1))
    reject("workflow", lambda v: v["steps"][-1].update(post=[]))
    reject("workflow", lambda v: v["steps"][0]["locators"][0].pop("value"))
    reject("workflow", lambda v: v["steps"][2]["body"][3].update(body=[v["steps"][0]]))
    reject("workflow", lambda v: v["parameters"][0].update(default_value=42))
    reject("workflow", lambda v: v["steps"][1]["pre"][0].update(locator_ref=50), True)
    reject("workflow", lambda v: v["steps"][1].update(step_id=v["steps"][0]["step_id"]), True)
    reject("workflow", lambda v: v["steps"][2]["body"][3].update(rule_id="missing"), True)
    reject("workflow", lambda v: v["decision_rules"][0].update(expr="missing > 30"), True)
    reject("workflow", lambda v: v["decision_rules"][0].update(expr="__import__('os').system('whoami')"), True)
    reject("workflow", lambda v: v["steps"][2]["body"].reverse(), True)
    reject("workflow", lambda v: v["steps"][-2].update(input="{{plan}}"), True)
    reject("workflow", lambda v: v["steps"][2]["loop_iterator"].update(source_param="missing"), True)
    reject("command", lambda v: v["payload"].pop("approval_id"))
    reject("command", lambda v: v["payload"].update(params={"secret": "value"}))
    reject("command", lambda v: v.update(protocol_version=2))
    reject("command", lambda v: v["payload"].update(nonce="short"))
    reject("command", lambda v: v.update(sig="not-a-signature"))
    reject("omniroute_hypothesis", lambda v: v["inferred_parameters"][0].update(executable="bad"))

    # Ensure the legacy document does not silently drift from authoritative JSON.
    doc = (ROOT.parent / "CONTRACTS.md").read_text(encoding="utf-8")
    schemas = {validator.schema["$id"]: validator.schema for validator in validators.values()}
    for block in re.findall(r"```json\s*\n(.*?)\n```", doc, re.S):
        try:
            value = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("$id") in schemas:
            assert value == schemas[value["$id"]], "Embedded schema drift"
            count += 1
        if isinstance(value, dict) and value.get("workflow_id") == wf["workflow_id"] and "steps" in value:
            assert value == wf, "Embedded workflow drift"
            count += 1
        if isinstance(value, dict) and value.get("kind") == "command":
            fixture = "command.invalid.json" if value.get("msg_id") == "msg_stale_123" else "command.valid.json"
            assert value == load("fixtures/" + fixture), "Embedded command drift"
            count += 1
    print(f"PASS: {count} schema, fixture, static semantic, synchronization and regression checks.")
    print("Runtime execution, crypto, pairing, approval and recovery gates are not implemented here.")


if __name__ == "__main__":
    main()
