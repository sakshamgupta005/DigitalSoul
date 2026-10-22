"""Workflow Compiler: Compiles a validated candidate and user approvals into a versioned Workflow JSON."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from contracts import (
    Workflow,
    WorkflowStep,
    WorkflowParameter,
    StepLocator,
    PrePostCondition,
    DecisionRule,
    OutcomeCheck,
    Provenance,
    Recovery,
)
from agent.ghost.learning.models import WorkflowCandidate, CandidateStep


def compile_candidate_step(step: CandidateStep) -> WorkflowStep:
    """Recursively convert a CandidateStep to a valid contract WorkflowStep."""
    body_steps: Optional[List[WorkflowStep]] = None
    if step.body:
        body_steps = [compile_candidate_step(child) for child in step.body]

    recovery = Recovery(
        retries=step.recovery.retries,
        on_fail=step.recovery.on_fail,
    )

    return WorkflowStep(
        step_id=step.step_id,
        kind=step.kind,  # type: ignore[arg-type]
        verb=step.verb,
        app=step.app,
        locators=step.locators,
        input=step.input,
        output_name=step.output_name,
        output_type=step.output_type,
        pre=step.pre,
        post=step.post,
        risk=step.risk,  # type: ignore[arg-type]
        required=step.required,
        origin=step.origin,  # type: ignore[arg-type]
        recovery=recovery,
        loop_iterator=step.loop_iterator,
        body=body_steps,
    )


def compile_workflow(
    candidate: WorkflowCandidate,
    name: Optional[str] = None,
    description: Optional[str] = None,
    version: int = 1,
    remote_allowed: bool = False,
    parameters: Optional[List[Dict[str, Any]]] = None,
    decision_rules: Optional[List[Dict[str, Any]]] = None,
    outcome_checks: Optional[List[Dict[str, Any]]] = None,
) -> Workflow:
    """Compile a validated candidate into an official Workflow instance."""
    wf_id = candidate.candidate_id.replace("cand_", "wf_")

    provenance = Provenance(
        evidence_episode_ids=candidate.evidence_episode_ids,
        support=candidate.support,
        alignment=candidate.alignment,
    )

    # Compile steps
    compiled_steps = [compile_candidate_step(s) for s in candidate.steps]

    # Compile parameters
    params_data = parameters if parameters is not None else candidate.parameters
    compiled_params: List[WorkflowParameter] = []
    for p in params_data:
        compiled_params.append(
            WorkflowParameter(
                name=p["name"],
                type=p["type"],
                source=p.get("source", "user"),
                origin=p.get("origin", "inferred"),
                confirmed=p.get("confirmed", True),
                default_value=p.get("default_value"),
            )
        )

    # Compile decision rules
    rules_data = decision_rules if decision_rules is not None else [r.model_dump(by_alias=True) for r in candidate.decision_rules]
    compiled_rules: List[DecisionRule] = []
    for r in rules_data:
        compiled_rules.append(
            DecisionRule(
                id=r["id"],
                expr=r["expr"],
                then=r["then"],
                else_=r.get("else") or r.get("else_"),
                origin=r.get("origin", "inferred"),
                supported_runs=r.get("supported_runs", candidate.support),
                contradicting_runs=r.get("contradicting_runs", 0),
                confirmed=r.get("confirmed", True),
            )
        )

    # Compile outcome checks
    checks_data = outcome_checks if outcome_checks is not None else candidate.outcome_checks
    compiled_checks: List[OutcomeCheck] = []
    for c in checks_data:
        compiled_checks.append(
            OutcomeCheck(
                type=c["type"],
                app=c["app"],
                check=c["check"],
                args=c.get("args", {}),
            )
        )

    # Instantiate and validate Workflow
    workflow = Workflow(
        workflow_id=wf_id,
        version=version,
        name=name or candidate.name,
        description=description or candidate.description,
        trust="validated",  # Compiling via user approval promotes to validated
        provenance=provenance,
        parameters=compiled_params,
        steps=compiled_steps,
        decision_rules=compiled_rules,
        outcome_checks=compiled_checks,
        remote_allowed=remote_allowed,
    )

    return workflow
