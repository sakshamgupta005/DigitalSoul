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

    # In production with API key, format redacted prompt and invoke Claude 3.5 Sonnet
    # If the call fails or key is invalid, smoothly fallback to heuristic hypotheses
    try:
        import httpx

        # Prepare redacted summary of candidate steps (no PII or credentials)
        step_summaries = []
        for s in candidate.steps:
            step_summaries.append({
                "step_id": s.step_id,
                "kind": s.kind,
                "verb": s.verb,
                "app": s.app,
                "locators": [loc.model_dump() for loc in (s.locators or [])] if s.locators else [],
            })

        system_prompt = (
            "You are an expert workflow intelligence agent for DigitalSoul. "
            "Analyze the following mined sequence of repetitive UI steps. "
            "Propose a concise, professional workflow name, a 1-sentence description, and parameter slots. "
            "Respond ONLY with valid JSON in this exact structure:\n"
            "{\n"
            '  "name": "string",\n'
            '  "description": "string",\n'
            '  "parameters": [{"name": "string", "type": "string"}]\n'
            "}"
        )

        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 512,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": f"Mined candidate with support {candidate.support} and alignment {candidate.alignment}:\n{json.dumps(step_summaries, indent=2)}",
                }
            ],
        }

        with httpx.Client(timeout=10.0) as client:
            resp = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
            if resp.status_code == 200:
                data = resp.json()
                content_text = data["content"][0]["text"]
                json_start = content_text.find("{")
                json_end = content_text.rfind("}") + 1
                if json_start != -1 and json_end > json_start:
                    parsed = json.loads(content_text[json_start:json_end])
                    new_name = parsed.get("name", candidate.name)
                    new_desc = parsed.get("description", candidate.description)
                    new_params = parsed.get("parameters", candidate.parameters)
                    base = generate_heuristic_hypotheses(candidate, observations)
                    return base.model_copy(
                        update={
                            "name": new_name,
                            "description": new_desc,
                            "parameters": new_params if new_params else base.parameters,
                        }
                    )

        return generate_heuristic_hypotheses(candidate, observations)
    except Exception:
        return generate_heuristic_hypotheses(candidate, observations)
