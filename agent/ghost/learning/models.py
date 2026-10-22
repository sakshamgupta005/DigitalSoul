"""Domain models for the Learning & Intelligence Engine (Developer 1 / M1)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field
from contracts import StepLocator, Value, DecisionRule, Recovery as StepRecovery, PrePostCondition, OnFail


class NormalizedAction(BaseModel):
    """Semantic action derived from one or more RawEvents."""
    model_config = ConfigDict(extra="forbid")

    action_id: str
    session_id: str
    ts: str
    verb: Literal["navigate", "click", "set_field", "read_field", "submit", "focus", "window_switch"]
    app_id: str
    app_type: Literal["web", "native"]
    page_url_pattern: str
    target_signature: str = Field(..., description="Stable selector signature, e.g. 'testid:tag-select' or 'button:Save'")
    token: str = Field(..., description="Mining token: app:page:verb:target_signature")
    locators: List[StepLocator] = Field(default_factory=list, description="Ranked locators bundle")
    value: Value
    raw_event_ids: List[str] = Field(default_factory=list)
    bbox: Optional[List[float]] = None


class Episode(BaseModel):
    """A segmented task session containing an ordered sequence of normalized actions."""
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    session_id: str
    start_ts: str
    end_ts: str
    actions: List[NormalizedAction] = Field(default_factory=list)
    tokens: List[str] = Field(default_factory=list)


class CandidateStep(BaseModel):
    """Step inside a WorkflowCandidate."""
    model_config = ConfigDict(extra="forbid")

    step_id: str
    kind: Literal["action", "loop", "decision", "assert"]
    verb: Optional[str] = None
    app: Optional[str] = None
    locators: Optional[List[StepLocator]] = None
    input: Optional[Any] = None
    output_name: Optional[str] = None
    output_type: Optional[Literal["string", "string[]", "number", "boolean"]] = None
    pre: List[PrePostCondition] = Field(default_factory=list)
    post: List[PrePostCondition] = Field(default_factory=list)
    risk: Literal["R0", "R1", "R2", "R3"] = "R0"
    required: bool = True
    origin: Literal["observed", "inferred", "user_defined"] = "observed"
    recovery: StepRecovery = Field(default_factory=lambda: StepRecovery(retries=0, on_fail=OnFail.pause))
    # Loop fields:
    loop_iterator: Optional[Dict[str, Any]] = None
    body: Optional[List[CandidateStep]] = None


class WorkflowCandidate(BaseModel):
    """Discovered candidate workflow ready for review or LLM hypothesis enhancement."""
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    name: str
    description: str
    support: int = Field(..., ge=1, description="Number of observed episodes matching this pattern")
    alignment: float = Field(..., ge=0.0, le=1.0, description="Pairwise sequence alignment score")
    evidence_episode_ids: List[str] = Field(default_factory=list)
    steps: List[CandidateStep] = Field(default_factory=list)
    decision_rules: List[DecisionRule] = Field(default_factory=list)
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    outcome_checks: List[Dict[str, Any]] = Field(default_factory=list)
    dismissed: bool = False
