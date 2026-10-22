from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, StrictStr


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: StrictStr


def remote_router(remote):
    router = APIRouter(prefix="/api/v1/remote", tags=["M2 local device management"])

    @router.post("/pair/start", status_code=201)
    async def begin():
        return remote.store.begin(remote.desktop_id, remote.public_key)

    @router.post("/pair/{pairing_id}/confirm")
    async def confirm(pairing_id: str, body: Confirmation):
        return remote.store.confirm(pairing_id, body.fingerprint)

    @router.get("/devices")
    async def devices():
        return remote.store.devices()

    @router.delete("/devices/{device_id}", status_code=204)
    async def revoke(device_id: str):
        await remote.revoke(device_id)
        return Response(status_code=204)

    return router
