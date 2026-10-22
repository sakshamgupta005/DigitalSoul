"""Safe AST rule evaluator for DigitalSoul decision rules according to contracts/REVIEW.md.

Grammar allowed:
- Identifiers (e.g. plan, last_login_days)
- String, number, boolean literals
- Comparisons: ==, !=, <, <=, >, >=
- Boolean operators: and, or, not
- Parentheses
STRICTLY FORBIDDEN: eval, calls, attributes, indexing, arithmetic, imports, assignment.
"""

from __future__ import annotations

import ast
from typing import Any, Dict, List, Tuple


class SafeRuleEvaluator(ast.NodeVisitor):
    """Evaluates a restricted decision rule AST safely against a context dictionary."""

    ALLOWED_NODES = (
        ast.Expression,
        ast.BoolOp,
        ast.And,
        ast.Or,
        ast.UnaryOp,
        ast.Not,
        ast.Compare,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Name,
        ast.Constant,
        ast.Load,
    )

    def __init__(self, context: Dict[str, Any]):
        self.context = context

    def generic_visit(self, node: ast.AST) -> Any:
        if not isinstance(node, self.ALLOWED_NODES):
            raise ValueError(f"Forbidden AST node in decision rule: {type(node).__name__}")
        return super().generic_visit(node)

    def evaluate(self, expr_str: str) -> bool:
        """Parse and evaluate expression against context."""
        try:
            tree = ast.parse(expr_str.strip(), mode="eval")
        except SyntaxError as e:
            raise ValueError(f"Invalid decision rule syntax: {e}")

        # Check all nodes for safety before evaluation
        for node in ast.walk(tree):
            if not isinstance(node, self.ALLOWED_NODES):
                raise ValueError(f"Forbidden AST node in rule: {type(node).__name__}")

        return bool(self._eval_node(tree.body))

    def _eval_node(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (str, int, float, bool, type(None))):
                raise ValueError(f"Unsupported constant type: {type(node.value)}")
            return node.value

        if isinstance(node, ast.Name):
            if node.id in ("True", "true"):
                return True
            if node.id in ("False", "false"):
                return False
            if node.id not in self.context:
                raise KeyError(f"Identifier '{node.id}' not found in execution context")
            return self.context[node.id]

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.Not):
                return not self._eval_node(node.operand)
            raise ValueError(f"Forbidden unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self._eval_node(v) for v in node.values)
            if isinstance(node.op, ast.Or):
                return any(self._eval_node(v) for v in node.values)
            raise ValueError(f"Forbidden boolean operator: {type(node.op).__name__}")

        if isinstance(node, ast.Compare):
            left_val = self._eval_node(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right_val = self._eval_node(comparator)
                if isinstance(op, ast.Eq):
                    matched = left_val == right_val
                elif isinstance(op, ast.NotEq):
                    matched = left_val != right_val
                elif isinstance(op, ast.Lt):
                    matched = left_val < right_val
                elif isinstance(op, ast.LtE):
                    matched = left_val <= right_val
                elif isinstance(op, ast.Gt):
                    matched = left_val > right_val
                elif isinstance(op, ast.GtE):
                    matched = left_val >= right_val
                else:
                    raise ValueError(f"Forbidden comparison operator: {type(op).__name__}")

                if not matched:
                    return False
                left_val = right_val
            return True

        raise ValueError(f"Forbidden AST evaluation node: {type(node).__name__}")


def evaluate_decision_rule(expr: str, context: Dict[str, Any]) -> bool:
    """Safely evaluate an expression string against context variables."""
    evaluator = SafeRuleEvaluator(context)
    return evaluator.evaluate(expr)


def test_rule_against_observations(
    expr: str,
    then_val: str,
    else_val: str,
    observations: List[Tuple[Dict[str, Any], str]],
) -> Tuple[int, int]:
    """Test a proposed rule against a list of observed (field_values, actual_outcome_tag) pairs.

    Returns:
        (supported_runs, contradicting_runs)
    """
    supported = 0
    contradicting = 0

    for context, actual_outcome in observations:
        try:
            cond_result = evaluate_decision_rule(expr, context)
            expected_outcome = then_val if cond_result else else_val
            if expected_outcome == actual_outcome:
                supported += 1
            else:
                contradicting += 1
        except Exception:
            contradicting += 1

    return supported, contradicting
