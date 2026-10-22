"""FastAPI routes for Developer 1 (M1 — Learning & Intelligence).

Implements durable SQLite persistence, WebSocket real-time streaming,
observation controls, candidate discovery, and the Mistake Guard feed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from contracts import RawEvent, Workflow, Trust
from agent.ghost.db.database import Database
from agent.ghost.learning.models import NormalizedAction, Episode, WorkflowCandidate
from agent.ghost.learning.normalizer import normalize_batch
from agent.ghost.learning.segmenter import segment_into_episodes
from agent.ghost.learning.miner import mine_workflow_candidates
from agent.ghost.learning.loop_folder import fold_loops
from agent.ghost.learning.hypothesis import enhance_candidate_with_llm
from agent.ghost.learning.compiler import compile_workflow
from agent.ghost.learning.recommendations import generate_recommendation_card
from agent.ghost.learning.guard_feed import MistakeGuardFeed, GuardWarning
from agent.ghost.learning.skill_recorder import (
    SensitiveDataFilter,
    SkillSynthesizer,
    export_skill_markdown,
    parse_skill_markdown,
    ActionCompactor,
    SkillQualityReport,
    SkillQualityEvaluator,
    SkillOptimizer,
)




router = APIRouter(prefix="/api/v1", tags=["learning"])
db = Database()
guard = MistakeGuardFeed()


# ============================================================================
# WebSocket Connection & Stream Manager
# ============================================================================

class StreamManager:
    """Manages active WebSocket subscribers and emits versioned event envelopes."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.valid_tickets: Dict[str, datetime] = {}
        self.sequence: int = 1

    def create_ticket(self) -> str:
        ticket = str(uuid.uuid4())
        expiry = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.valid_tickets[ticket] = expiry
        return ticket

    def consume_ticket(self, ticket: str) -> bool:
        if not ticket or ticket not in self.valid_tickets:
            return False
        expiry = self.valid_tickets.pop(ticket)
        return datetime.now(timezone.utc) <= expiry

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, event_type: str, data: Dict[str, Any]):
        envelope = {
            "event_id": str(uuid.uuid4()),
            "sequence": self.sequence,
            "type": event_type,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data": data,
        }
        self.sequence += 1
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_text(json.dumps(envelope))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


stream_manager = StreamManager()


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


class TicketResponse(BaseModel):
    ticket: str
    expires_at: str


class GuardCheckRequest(BaseModel):
    event: RawEvent
    active_workflow_id: Optional[str] = None


class DemonstrationSynthesizeRequest(BaseModel):
    steps: List[Dict[str, Any]]
    voice_notes: Optional[str] = None
    screen_context: Optional[str] = None
    app_name: Optional[str] = None
    use_llm: bool = True
    save_candidate: bool = True


class DemonstrationSynthesizeResponse(BaseModel):
    candidate: WorkflowCandidate
    skill_markdown: str
    redacted_count: int
    voice_notes_used: bool


class SkillExportResponse(BaseModel):
    candidate_id: str
    skill_markdown: str


class SkillImportRequest(BaseModel):
    skill_markdown: str


class RedactRequest(BaseModel):
    text: Optional[str] = None
    step: Optional[Dict[str, Any]] = None


class RedactResponse(BaseModel):
    text: Optional[str] = None
    step: Optional[Dict[str, Any]] = None
    redacted_count: int


class SkillEvaluateRequest(BaseModel):
    candidate_id: Optional[str] = None
    skill_markdown: Optional[str] = None


class SkillOptimizeRequest(BaseModel):
    steps: List[Dict[str, Any]]
    voice_notes: Optional[str] = None
    screen_context: Optional[str] = None
    app_name: Optional[str] = None
    save_candidate: bool = True


class SkillOptimizeResponse(BaseModel):
    candidate: WorkflowCandidate
    skill_markdown: str
    quality_report: SkillQualityReport
    compacted_steps_count: int
    original_steps_count: int




# ============================================================================
# Endpoints
# ============================================================================

@router.post("/events/batch", response_model=BatchIngestResponse, status_code=status.HTTP_200_OK)
async def ingest_events_batch(request: Request) -> BatchIngestResponse:
    """Ingest a batch of up to 200 sanitized RawEvents and persist them durably."""
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

        # Durable insertion with deduplication & conflict detection
        try:
            is_new = db.insert_raw_event(event)
            if is_new:
                ingested_count += 1
                new_events.append(event)
            else:
                duplicate_count += 1
        except ValueError as err:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(err))

    # Process newly ingested events into episodes and candidates
    if new_events:
        actions = normalize_batch(new_events)
        obs_settings = db.get_setting("observation_settings", {})
        allowed = obs_settings.get("allowlist", ["acme-portal", "acme-crm"])
        new_episodes = segment_into_episodes(actions, allowed_apps=allowed)

        if new_episodes:
            db.save_episodes(new_episodes)
            all_episodes = db.get_all_episodes()

            # Sequence mining
            if len(all_episodes) >= 3:
                candidates = mine_workflow_candidates(all_episodes)
                for c in candidates:
                    folded_steps = fold_loops(c.steps)
                    c_folded = c.model_copy(update={"steps": folded_steps})
                    enhanced = enhance_candidate_with_llm(c_folded)
                    db.save_candidate(enhanced)

                    # Broadcast candidate discovered over WebSocket
                    await stream_manager.broadcast("candidate.created", {
                        "candidate_id": enhanced.candidate_id,
                        "name": enhanced.name,
                        "support": enhanced.support,
                        "alignment": enhanced.alignment,
                    })

    return BatchIngestResponse(ingested=ingested_count, duplicates=duplicate_count)


@router.get("/settings/observation", response_model=ObservationSettings)
def get_observation_settings() -> ObservationSettings:
    """Read observation toggle, allowlist, and pause status from SQLite."""
    default = {
        "observing": True,
        "allowlist": ["acme-portal", "acme-crm", "portal.acme.local", "crm.acme.local"],
        "paused_until": None,
    }
    saved = db.get_setting("observation_settings", default)
    return ObservationSettings(**saved)


@router.put("/settings/observation", response_model=ObservationSettings)
async def update_observation_settings(settings: ObservationSettings) -> ObservationSettings:
    """Update observation settings or pause observation."""
    data = settings.model_dump()
    db.set_setting("observation_settings", data)
    await stream_manager.broadcast("observation.status", data)
    return settings


@router.get("/candidates", response_model=List[CandidateResponse])
def list_candidates() -> List[CandidateResponse]:
    """List mined candidates along with computed recommendation cards."""
    all_episodes = db.get_all_episodes()
    candidates = db.get_all_candidates()
    results: List[CandidateResponse] = []
    for cand in candidates:
        rec = generate_recommendation_card(cand, all_episodes)
        results.append(CandidateResponse(candidate=cand, recommendation=rec))
    return results


@router.post("/candidates/{candidate_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_candidate(candidate_id: str):
    """Dismiss a candidate recommendation permanently."""
    success = db.dismiss_candidate(candidate_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/candidates/{candidate_id}/promote", response_model=Workflow)
def promote_candidate(candidate_id: str) -> Workflow:
    """Promote a discovered candidate to a draft workflow."""
    candidates = {c.candidate_id: c for c in db.get_all_candidates()}
    if candidate_id not in candidates:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    cand = candidates[candidate_id]
    wf = compile_workflow(cand, version=1)
    db.save_workflow(wf)
    return wf


@router.get("/workflows", response_model=List[Workflow])
def list_workflows() -> List[Workflow]:
    """List all saved workflows."""
    return db.get_all_workflows()


@router.get("/workflows/{workflow_id}", response_model=Workflow)
def get_workflow(workflow_id: str) -> Workflow:
    """Get workflow by ID."""
    wf = db.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    return wf


@router.put("/workflows/{workflow_id}", response_model=Workflow)
def update_workflow(workflow_id: str, updated: Workflow) -> Workflow:
    """Update workflow definition."""
    existing = db.get_workflow(workflow_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
    db.save_workflow(updated)
    return updated


@router.post("/workflows/{workflow_id}/validate", response_model=Workflow)
def validate_workflow(workflow_id: str, payload: ValidationRequest) -> Workflow:
    """User validates and approves draft workflow -> compiles execution plan, bumps version.

    Enforces REVIEW.md requirements:
    - Rejects missing/unconfirmed parameters and rules.
    - Runs semantic checks (step ID uniqueness, loop scopes, parameter bindings).
    - Increments version and sets trust to 'validated'.
    """
    existing = db.get_workflow(workflow_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")

    new_version = existing.version + 1

    validated_wf = existing.model_copy(
        update={
            "version": new_version,
            "trust": Trust.validated,
            "name": payload.name or existing.name,
            "description": payload.description or existing.description,
            "remote_allowed": payload.remote_allowed,
        }
    )

    # 1. Enforce confirmed parameters and rules (REVIEW.md line 15)
    for p in (validated_wf.parameters or []):
        if not p.confirmed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Parameter '{p.name}' is unconfirmed. All parameters must be confirmed at validation.",
            )

    for r in (validated_wf.decision_rules or []):
        if not r.confirmed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Decision rule '{r.id}' is unconfirmed. All rules must be confirmed at validation.",
            )

    # 2. Run static semantic verification (REVIEW.md lines 13-17)
    from contracts.validate import semantic_errors
    wf_dict = validated_wf.model_dump(mode="json", by_alias=True)
    errs = semantic_errors(wf_dict)
    if errs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Workflow failed semantic validation: {'; '.join(errs)}",
        )

    db.save_workflow(validated_wf)
    return validated_wf


# ============================================================================
# Mistake Guard & Live Stream Endpoints
# ============================================================================

@router.post("/guard/check", response_model=Optional[GuardWarning])
def check_mistake_guard(req: GuardCheckRequest) -> Optional[GuardWarning]:
    """Evaluate a live event against mistake patterns (wrong record, destructive action)."""
    active_wf = db.get_workflow(req.active_workflow_id) if req.active_workflow_id else None
    return guard.inspect_event(req.event, active_wf)


@router.post("/stream/tickets", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def issue_stream_ticket(response: Response) -> TicketResponse:
    """Issue a single-use 30-second ticket for WebSocket upgrade."""
    response.headers["Cache-Control"] = "no-store"
    ticket = stream_manager.create_ticket()
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat() + "Z"
    return TicketResponse(ticket=ticket, expires_at=expires_at)


@router.websocket("/stream")
async def websocket_stream(websocket: WebSocket, ticket: Optional[str] = None):
    """Real-time event stream for live dashboard and companion updates."""
    # Enforce single-use ticket as mandated by CONTRACTS.md
    if not ticket or not stream_manager.consume_ticket(ticket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await stream_manager.connect(websocket)
    try:
        while True:
            # Keep-alive heartbeat listener
            await websocket.receive_text()
    except WebSocketDisconnect:
        stream_manager.disconnect(websocket)
    except Exception:
        stream_manager.disconnect(websocket)


# ============================================================================
# Demonstration Engineering & Skill Recorder Endpoints (inspired by microsoft/skill-recorder)
# ============================================================================

@router.post("/demonstrations/synthesize", response_model=DemonstrationSynthesizeResponse)
async def synthesize_demonstration(payload: DemonstrationSynthesizeRequest) -> DemonstrationSynthesizeResponse:
    """Demonstration Engineering: transforms demonstrated steps and voice commentary into an agent skill."""
    synthesizer = SkillSynthesizer()
    result = synthesizer.synthesize(
        steps=payload.steps,
        voice_notes=payload.voice_notes,
        screen_context=payload.screen_context,
        app_name=payload.app_name,
        use_llm=payload.use_llm,
    )
    cand = result["candidate"]
    if payload.save_candidate:
        db.save_candidate(cand)
        await stream_manager.broadcast("candidate.created", {
            "candidate_id": cand.candidate_id,
            "name": cand.name,
            "support": cand.support,
            "alignment": cand.alignment,
        })
    return DemonstrationSynthesizeResponse(
        candidate=cand,
        skill_markdown=result["skill_markdown"],
        redacted_count=result["redacted_count"],
        voice_notes_used=result["voice_notes_used"],
    )


@router.get("/candidates/{candidate_id}/skill", response_model=SkillExportResponse)
def export_candidate_skill(candidate_id: str) -> SkillExportResponse:
    """Export a candidate as a standard Microsoft SKILL.md document."""
    candidates = {c.candidate_id: c for c in db.get_all_candidates()}
    if candidate_id not in candidates:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    cand = candidates[candidate_id]
    skill_md = export_skill_markdown(cand)
    return SkillExportResponse(candidate_id=candidate_id, skill_markdown=skill_md)


@router.post("/skills/import", response_model=WorkflowCandidate, status_code=status.HTTP_201_CREATED)
async def import_skill_document(payload: SkillImportRequest) -> WorkflowCandidate:
    """Import a standard Microsoft SKILL.md document directly into DigitalSoul."""
    try:
        candidate = parse_skill_markdown(payload.skill_markdown)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid SKILL.md: {exc}")
    db.save_candidate(candidate)
    await stream_manager.broadcast("candidate.created", {
        "candidate_id": candidate.candidate_id,
        "name": candidate.name,
        "support": candidate.support,
        "alignment": candidate.alignment,
    })
    return candidate


@router.post("/learning/redact", response_model=RedactResponse)
def redact_telemetry(payload: RedactRequest) -> RedactResponse:
    """On-device sensitive data scrubber for telemetry or steps."""
    total = 0
    out_text = None
    out_step = None
    if payload.text is not None:
        out_text, count = SensitiveDataFilter.redact_text(payload.text)
        total += count
    if payload.step is not None:
        out_step, count = SensitiveDataFilter.redact_step(payload.step)
        total += count
    return RedactResponse(text=out_text, step=out_step, redacted_count=total)


@router.post("/skills/evaluate", response_model=SkillQualityReport)
def evaluate_skill_quality(payload: SkillEvaluateRequest) -> SkillQualityReport:
    """Evaluate skill quality, locator resilience, and privacy posture."""
    if payload.candidate_id:
        candidates = {c.candidate_id: c for c in db.get_all_candidates()}
        if payload.candidate_id not in candidates:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
        cand = candidates[payload.candidate_id]
    elif payload.skill_markdown:
        try:
            cand = parse_skill_markdown(payload.skill_markdown)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid SKILL.md: {exc}")
    else:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Provide candidate_id or skill_markdown")

    return SkillQualityEvaluator.evaluate(cand)


@router.post("/skills/optimize", response_model=SkillOptimizeResponse)
async def optimize_skill(payload: SkillOptimizeRequest) -> SkillOptimizeResponse:
    """Run automated compaction, multi-strategy locator hardening, and quality scoring."""
    res = SkillOptimizer.optimize(
        steps=payload.steps,
        voice_notes=payload.voice_notes,
        screen_context=payload.screen_context,
        app_name=payload.app_name,
    )
    cand = res["candidate"]
    if payload.save_candidate:
        db.save_candidate(cand)
        await stream_manager.broadcast("candidate.created", {
            "candidate_id": cand.candidate_id,
            "name": cand.name,
            "support": cand.support,
            "alignment": cand.alignment,
        })
    return SkillOptimizeResponse(
        candidate=cand,
        skill_markdown=res["skill_markdown"],
        quality_report=SkillQualityReport(**res["quality_report"]),
        compacted_steps_count=res["compacted_steps_count"],
        original_steps_count=res["original_steps_count"],
    )


