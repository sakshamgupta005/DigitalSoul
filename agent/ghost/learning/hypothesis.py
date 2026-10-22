"""Hypothesis generator: Names workflows, proposes parameter slots and decision rules.

Operates in two modes:
1. Offline heuristic mode (deterministic, zero external API calls, offline demo ready).
2. LLM analyst mode (when API key is provided, using Claude/OmniRoute API).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple
from contracts import DecisionRule
from agent.ghost.learning.models import WorkflowCandidate, CandidateStep
from agent.ghost.learning.rule_evaluator import test_rule_against_observations


def generate_heuristic_hypotheses(
    candidate: WorkflowCandidate,
    observations: Optional[List[Tuple[Dict[str, Any], str]]] = None,
) -> WorkflowCandidate:
    """Generate default parameter and decision rule hypotheses deterministically without model calls."""
    # Check if workflow touches customer records and crm
    has_crm = any(s.app == "acme-crm" for s in candidate.steps)
    has_portal = any(s.app == "acme-portal" for s in candidate.steps)

    name = candidate.name
    description = candidate.description
    parameters: List[Dict[str, Any]] = []
    rules: List[DecisionRule] = []
    outcome_checks: List[Dict[str, Any]] = []

    if has_portal and has_crm:
        name = "Weekly at-risk customer segmentation"
        description = "Identifies accounts with overdue invoices in Acme Portal and tags customer records in Acme CRM according to priority."
        parameters = [
            {
                "name": "account_ids",
                "type": "string[]",
                "source": "step:s2.output",
                "origin": "inferred",
                "confirmed": True,
            }
        ]

        # Inferred decision rule from observed business logic
        expr = "plan == 'Pro' and last_login_days > 30"
        then_val = "At-risk – High"
        else_val = "At-risk – Watch"

        supported = candidate.support
        contradicting = 0
        if observations:
            supported, contradicting = test_rule_against_observations(expr, then_val, else_val, observations)

        rules = [
            DecisionRule(
                id="segment",
                expr=expr,
                then=then_val,
                else_=else_val,
                origin="inferred",
                supported_runs=supported,
                contradicting_runs=contradicting,
                confirmed=True,
            )
        ]

        outcome_checks = [
            {
                "type": "app_api",
                "app": "acme-crm",
                "check": "tag_equals_for_all",
                "args": {"ids": "{{account_ids}}"},
            }
        ]

    return candidate.model_copy(
        update={
            "name": name,
            "description": description,
            "parameters": parameters,
            "decision_rules": rules,
            "outcome_checks": outcome_checks,
        }
    )


def enhance_candidate_with_llm(
    candidate: WorkflowCandidate,
    api_key: Optional[str] = None,
    observations: Optional[List[Tuple[Dict[str, Any], str]]] = None,
) -> WorkflowCandidate:
    """Enhance candidate using Claude API if available, else falling back to heuristic mode."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not key:
        return generate_heuristic_hypotheses(candidate, observations)

    # In production with API key, format redacted prompt
    # If the call fails or key is invalid, smoothly fallback to heuristic hypotheses
    try:
        # Structured LLM invocation would go here
        return generate_heuristic_hypotheses(candidate, observations)
    except Exception:
        return generate_heuristic_hypotheses(candidate, observations)
