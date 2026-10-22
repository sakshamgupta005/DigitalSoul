from typing import Literal

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class StartRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_version: StrictInt = Field(ge=1)
    mode: Literal["normal", "supervised", "dry_run"] = "supervised"
    parameters: dict = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: StrictStr
    workflow_version: StrictInt
    step_id: StrictStr
    effect_hash: StrictStr = Field(pattern="^[a-f0-9]{64}$")


def run_router(runtime):
    router = APIRouter(prefix="/api/v1", tags=["M2 execution"])

    @router.post("/workflows/{workflow_id}/runs", status_code=201)
    async def start(workflow_id: str, body: StartRunRequest, idempotency_key: str = Header(min_length=1, max_length=128)):
        return await runtime.start(workflow_id, body.workflow_version, body.parameters, body.mode, idempotency_key)

    @router.get("/runs")
    async def runs(limit: int = Query(default=50, ge=1, le=200), status: str | None = None):
        values = runtime.store.all() if status else runtime.store.list(limit)
        return [runtime.public(r) for r in values if not status or r["state"] == status][:limit]

    @router.get("/runs/{run_id}")
    async def get(run_id: str):
        return runtime.public(runtime.store.get(run_id))

    @router.post("/runs/{run_id}/approve")
    async def approve(run_id: str, body: ApprovalRequest):
        return await runtime.control(run_id, "approve", body.model_dump())

    @router.post("/runs/{run_id}/reject")
    async def reject(run_id: str, body: ApprovalRequest):
        return await runtime.control(run_id, "reject", body.model_dump())

    @router.post("/runs/{run_id}/pause")
    async def pause(run_id: str):
        return await runtime.control(run_id, "pause")

    @router.post("/runs/{run_id}/resume")
    async def resume(run_id: str):
        return await runtime.control(run_id, "resume")

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await runtime.control(run_id, "cancel")

    @router.post("/safety/stop")
    async def emergency_stop():
        await runtime.emergency_stop()
        return {"stopped": True, "kill_switch_engaged": True}

    @router.post("/runs/{run_id}/reconcile")
    async def reconcile(run_id: str):
        return await runtime.reconcile(run_id)

    return router
