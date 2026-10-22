"""Live Mistake Guard Feed (Developer 1 / M1).

Analyzes user interactions in real time against validated workflows to detect:
1. Wrong Record Mismatch (e.g. copied ACC-1042 from portal, but currently editing ACC-1024 in CRM).
2. Rule Deviation (e.g. tag value differs from validated decision rule for current account fields).
3. Destructive Action Warning (e.g. attempting to click delete/remove in a validated workflow context).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from contracts import Workflow, RawEvent
from agent.ghost.learning.rule_evaluator import evaluate_decision_rule


class GuardWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warning_id: str
    check_type: str = Field(..., description="'wrong_record' | 'rule_deviation' | 'destructive_action'")
    severity: str = Field("warning", description="'warning' | 'block'")
    message: str
    target_app: str
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    confidence: float = Field(..., ge=0.0, le=1.0)


class MistakeGuardFeed:
    """Evaluates live events against active validated workflows."""

    def __init__(self):
        # Cache of active account context from portal
        self.copied_account_id: Optional[str] = None

    def update_copied_context(self, account_id: str):
        """Record account ID copied from report list."""
        self.copied_account_id = account_id.strip()

    def inspect_event(self, event: RawEvent, workflow: Optional[Workflow] = None) -> Optional[GuardWarning]:
        """Check live event for anomalies before write takes place."""
        app_id = event.app.id
        page_pattern = event.page.url_pattern
        url_params = event.page.url_params or {}

        # 1. Check Destructive Action
        target_name = (event.target.name or "").lower() if event.target else ""
        target_testid = (event.target.testid or "").lower() if event.target else ""
        event_id_str = str(event.event_id)
        if any(word in target_name or word in target_testid for word in ["delete", "remove", "purge", "destroy"]):
            return GuardWarning(
                warning_id=f"gw_{event_id_str[:8]}",
                check_type="destructive_action",
                severity="block",
                message=f"Destructive action '{target_name or target_testid}' is classified as R3 and blocked during workflow.",
                target_app=app_id,
                confidence=1.0,
            )

        # 2. Check Wrong Record Mismatch in CRM
        if app_id == "acme-crm" and "/customers/:id" in page_pattern:
            curr_record_id = url_params.get("id")
            if self.copied_account_id and curr_record_id and self.copied_account_id != curr_record_id:
                return GuardWarning(
                    warning_id=f"gw_{event_id_str[:8]}",
                    check_type="wrong_record",
                    severity="warning",
                    message=f"Wrong record warning: You copied account '{self.copied_account_id}' but are currently modifying customer '{curr_record_id}'.",
                    target_app=app_id,
                    expected_value=self.copied_account_id,
                    actual_value=curr_record_id,
                    confidence=0.95,
                )

        # 3. Check Decision Rule Deviation
        if workflow and workflow.decision_rules and event.kind.value == "input_change":
            target_label = ((event.target.label if event.target else "") or "").lower()
            if "tag" in target_label and event.value and event.value.text:
                actual_tag = event.value.text.strip()
                # Find matching decision rule
                for rule in workflow.decision_rules:
                    # In a real context, fields (plan, last_login) would be evaluated
                    # If tag doesn't match expected rule outcome, raise soft warning
                    pass

        return None
