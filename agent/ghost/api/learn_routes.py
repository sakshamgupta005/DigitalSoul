"""FastAPI routes for Developer 1 (M1 — Learning & Intelligence).

Implements all M1 REST endpoints specified in CONTRACTS.md & REVIEW.md:
- POST /api/v1/events/batch
- GET/PUT /api/v1/settings/observation
- GET /api/v1/candidates
- POST /api/v1/candidates/{id}/dismiss
- POST /api/v1/candidates/{id}/promote
- GET /api/v1/workflows
- GET/PUT /api/v1/workflows/{id}
- POST /api/v1/workflows/{id}/validate
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from contracts import RawEvent, Workflow
from agent.ghost.learning.models import NormalizedAction, Episode, WorkflowCandidate
from agent.ghost.learning.normalizer import normalize_batch
from agent.ghost.learning.segmenter import segment_into_episodes
from agent.ghost.learning.miner import mine_workflow_candidates
from agent.ghost.learning.loop_folder import fold_loops
from agent.ghost.learning.hypothesis import enhance_candidate_with_llm
from agent.ghost.learning.compiler import compile_workflow
from agent.ghost.learning.recommendations import generate_recommendation_card


router = APIRouter(prefix="/api/v1", tags=["learning"])


# ============================================================================
# In-Memory State Store for M1
# ============================================================================

class LearningState:
    def __init__(self):
        self.observing: bool = True
        self.allowlist: List[str] = ["acme-portal", "acme-crm", "portal.acme.local", "crm.acme.local"]
        self.paused_until: Optional[str] = None
        self.raw_events_by_id: Dict[str, RawEvent] = {}
        self.episodes: List[Episode] = []
        self.candidates_by_id: Dict[str, WorkflowCandidate] = {}
        self.workflows_by_id: Dict[str, Workflow] = {}


state = LearningState()


# ============================================================================
# Request / Response Models
# ============================================================================

class BatchIngestResponse(BaseModel):
    ingested: int
    duplicates: int


class ObservationSettings(BaseModel):
    observing: bool
    allowlist: List[str]
    paused_until: Optional[str] = None


class CandidateResponse(BaseModel):
    candidate: WorkflowCandidate
    recommendation: Dict[str, Any]


class ValidationRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parameters: Optional[List[Dict[str, Any]]] = None
    decision_rules: Optional[List[Dict[str, Any]]] = None
    outcome_checks: Optional[List[Dict[str, Any]]] = None
    remote_allowed: bool = False


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/events/batch", response_model=BatchIngestResponse, status_code=status.HTTP_200_OK)
async def ingest_events_batch(request: Request) -> BatchIngestResponse:
    """Ingest a batch of up to 200 sanitized RawEvents from extension or desktop collector."""
    # Check max payload size (1 MiB)
    body_bytes = await request.body()
    if len(body_bytes) > 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload exceeds maximum allowed size of 1 MiB",
        )

    try:
        data = json.loads(body_bytes)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Malformed JSON: {e}")

    # Can be a list of events directly or an object with 'events' field
    events_raw = data if isinstance(data, list) else data.get("events", [])
    if not isinstance(events_raw, list) or len(events_raw) == 0:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Batch must contain at least 1 event")
    if len(events_raw) > 200:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Batch cannot exceed 200 events")

    ingested_count = 0
    duplicate_count = 0
    new_events: List[RawEvent] = []

    for item in events_raw:
        try:
            event = RawEvent.model_validate(item)
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Event validation failed: {e}")

        # Deduplication check
        if event.event_id in state.raw_events_by_id:
            existing = state.raw_events_by_id[event.event_id]
            if existing.model_dump() == event.model_dump():
                duplicate_count += 1
                continue
            else:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Conflicting content for existing event_id: {event.event_id}",
                )

        state.raw_events_by_id[event.event_id] = event
        new_events.append(event)
        ingested_count += 1

    # Normalize newly ingested events into actions & segment into episodes
    if new_events:
        actions = normalize_batch(new_events)
        new_episodes = segment_into_episodes(actions, allowed_apps=state.allowlist)
        state.episodes.extend(new_episodes)

        # Trigger sequence mining when we have accumulated episodes
        if len(state.episodes) >= 3:
            candidates = mine_workflow_candidates(state.episodes)
            for c in candidates:
                # Apply loop folding
                folded_steps = fold_loops(c.steps)
                c_folded = c.model_copy(update={"steps": folded_steps})
                # Apply hypothesis generator
                enhanced = enhance_candidate_with_llm(c_folded)
                state.candidates_by_id[enhanced.candidate_id] = enhanced

    return BatchIngestResponse(ingested=ingested_count, duplicates=duplicate_count)


@router.get("/settings/observation", response_model=ObservationSettings)
def get_observation_settings() -> ObservationSettings:
    """Read observation toggle, allowlist, and pause status."""
    return ObservationSettings(
        observing=state.observing,
        allowlist=state.allowlist,
        paused_until=state.paused_until,
    )


@router.put("/settings/observation", response_model=ObservationSettings)
def update_observation_settings(settings: ObservationSettings) -> ObservationSettings:
    """Update observation settings or pause observation."""
    state.observing = settings.observing
    state.allowlist = settings.allowlist
    state.paused_until = settings.paused_until
    return get_observation_settings()


@router.get("/candidates", response_model=List[CandidateResponse])
def list_candidates() -> List[CandidateResponse]:
    """List mined candidates along with computed recommendation cards."""
    results: List[CandidateResponse] = []
    for cand in state.candidates_by_id.values():
        rec = generate_recommendation_card(cand, state.episodes)
        results.append(CandidateResponse(candidate=cand, recommendation=rec))
    return results


@router.post("/candidates/{candidate_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_candidate(candidate_id: str):
    """Dismiss a candidate recommendation permanently."""
    if candidate_id not in state.candidates_by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    state.candidates_by_id[candidate_id] = state.candidates_by_id[candidate_id].model_copy(
        update={"dismissed": True}
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/candidates/{candidate_id}/promote", response_model=Workflow)
def promote_candidate(candidate_id: str) -> Workflow:
    """Promote a discovered candidate to a draft workflow."""
    if candidate_id not in state.candidates_by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    cand = state.candidates_by_id[candidate_id]
    wf = compile_workflow(cand, version=1)
    state.workflows_by_id[wf.workflow_id] = wf
    return wf


@router.get("/workflows", response_model=List[Workflow])
def list_workflows() -> List[Workflow]:
    """List all workflows."""
    return list(state.workflows_by_id.values())


@router.get("/workflows/{workflow_id}", response_model=Workflow)
def get_workflow(workflow_id: str) -> Workflow:
    """Get workflow by ID."""
    if workflow_id not in state.workflows_by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return state.workflows_by_id[workflow_id]


@router.put("/workflows/{workflow_id}", response_model=Workflow)
def update_workflow(workflow_id: str, updated: Workflow) -> Workflow:
    """Update workflow definition."""
    if workflow_id not in state.workflows_by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    state.workflows_by_id[workflow_id] = updated
    return updated


@router.post("/workflows/{workflow_id}/validate", response_model=Workflow)
def validate_workflow(workflow_id: str, payload: ValidationRequest) -> Workflow:
    """User validates and approves draft workflow -> compiles execution plan, bumps version."""
    if workflow_id not in state.workflows_by_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    existing = state.workflows_by_id[workflow_id]
    new_version = existing.version + 1

    # Bump version and mark as validated
    validated_wf = existing.model_copy(
        update={
            "version": new_version,
            "trust": "validated",
            "name": payload.name or existing.name,
            "description": payload.description or existing.description,
            "remote_allowed": payload.remote_allowed,
        }
    )
    state.workflows_by_id[workflow_id] = validated_wf
    return validated_wf
