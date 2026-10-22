"""A deliberately small AST interpreter. Never executes Python code."""
import ast
import copy
import math
import operator
import re
from urllib.parse import quote

from .errors import GhostError

TOKEN = re.compile(r"\{\{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)?)\s*\}\}")
OPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not,
           ast.Compare, ast.Name, ast.Load, ast.Constant, *OPS)


def compile_expression(expression):
    if len(expression) > 2048:
        raise GhostError("VALIDATION_ERROR", "Expression too long", 422)
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise GhostError("VALIDATION_ERROR", "Invalid decision expression", 422) from exc
    if sum(1 for _ in ast.walk(tree)) > 128 or any(not isinstance(n, ALLOWED) for n in ast.walk(tree)):
        raise GhostError("VALIDATION_ERROR", "Unsupported decision syntax", 422)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if type(node.value) not in (str, bool, int, float) or (
                type(node.value) is float and not math.isfinite(node.value)
            ):
                raise GhostError("VALIDATION_ERROR", "Unsupported literal", 422)
    return tree


def evaluate(expression, values):
    tree = compile_expression(expression)

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise GhostError("VALIDATION_ERROR", "Decision input unavailable", 422)
            return values[node.id]
        if isinstance(node, ast.UnaryOp):
            return not boolean(visit(node.operand))
        if isinstance(node, ast.BoolOp):
            # Validate all operands even if one would short circuit.
            operands = [boolean(visit(v)) for v in node.values]
            return all(operands) if isinstance(node.op, ast.And) else any(operands)
        if isinstance(node, ast.Compare):
            left = visit(node.left)
            passed = True
            for op, other in zip(node.ops, node.comparators):
                right = visit(other)
                numeric = type(left) in (int, float) and type(right) in (int, float)
                if not numeric and type(left) is not type(right):
                    raise GhostError("VALIDATION_ERROR", "Decision operand types differ", 422)
                if not isinstance(op, (ast.Eq, ast.NotEq)) and not (numeric or type(left) is str):
                    raise GhostError("VALIDATION_ERROR", "Invalid ordered comparison", 422)
                passed = OPS[type(op)](left, right) and passed
                left = right
            return passed
        raise GhostError("VALIDATION_ERROR", "Unsupported decision syntax", 422)

    return boolean(visit(tree))


def boolean(value):
    if type(value) is not bool:
        raise GhostError("VALIDATION_ERROR", "Decision must evaluate to a boolean", 422)
    return value


def resolve_name(name, values):
    if name in values:
        return values[name]
    if name.endswith(".length") and type(values.get(name[:-7])) is list:
        return len(values[name[:-7]])
    raise GhostError("VALIDATION_ERROR", "Template input unavailable", 422)


def render(value, values, url=False):
    if isinstance(value, dict):
        return {k: render(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, values) for v in value]
    if not isinstance(value, str):
        return value
    match = TOKEN.fullmatch(value)
    if match and not url:
        return copy.deepcopy(resolve_name(match[1], values))

    def substitute(match):
        item = resolve_name(match[1], values)
        if type(item) not in (str, int, float, bool):
            raise GhostError("VALIDATION_ERROR", "Only scalar interpolation is allowed", 422)
        return quote(str(item), safe="") if url else str(item)

    output = TOKEN.sub(substitute, value)
    if "{{" in output or "}}" in output:
        raise GhostError("VALIDATION_ERROR", "Malformed template", 422)
    return output
