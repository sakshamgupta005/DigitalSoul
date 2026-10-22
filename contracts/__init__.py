"""DigitalSoul Contracts Package.

Exposes generated Pydantic v2 models synchronized with contracts/*.schema.json.
"""

from contracts.raw_event_models import (
    RawEvent,
    App,
    Target,
    Value,
    Page,
    Uia,
    Kind,
    Source,
    Shape,
    Type as AppType,
)
from contracts.workflow_models import (
    Workflow,
    WorkflowStep,
    WorkflowParameter,
    StepLocator,
    PrePostCondition,
    DecisionRule,
    OutcomeCheck,
    Provenance,
    Trust,
    Risk,
    Kind as StepKind,
    Origin,
    Type as ParamType,
    Recovery,
    OnFail,
)
from contracts.error_models import (
    GhostError,
    Error,
    Code as ErrorCode,
)
from contracts.command_models import (
    RemoteCommandEnvelope,
    Payload as CommandPayload,
    Type as CommandType,
)

__all__ = [
    # RawEvent
    "RawEvent",
    "App",
    "Target",
    "Value",
    "Page",
    "Uia",
    "Kind",
    "Source",
    "Shape",
    "AppType",
    # Workflow
    "Workflow",
    "WorkflowStep",
    "WorkflowParameter",
    "StepLocator",
    "PrePostCondition",
    "DecisionRule",
    "OutcomeCheck",
    "Provenance",
    "Trust",
    "Risk",
    "StepKind",
    "Origin",
    "ParamType",
    "Recovery",
    "OnFail",
    # Error
    "GhostError",
    "Error",
    "ErrorCode",
    # Command
    "RemoteCommandEnvelope",
    "CommandPayload",
    "CommandType",
]
