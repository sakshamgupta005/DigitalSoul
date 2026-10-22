"""Standalone M2 host; does not replace the M4-owned ghost/app.py."""
import argparse
import json
import secrets
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..drivers.browser import BrowserDriver
from ..execution.errors import GhostError
from ..execution.recovery import RunStore
from ..execution.runtime import Runtime
from ..safety.policy import acme_policy
from .run_routes import run_router


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("Non-finite number")

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)

    def depth(value, level=0):
        if level > 32:
            raise ValueError("JSON depth exceeded")
        if isinstance(value, dict):
            for child in value.values():
                depth(child, level + 1)
        elif isinstance(value, list):
            for child in value:
                depth(child, level + 1)
        elif type(value) is float:
            import math
            if not math.isfinite(value):
                raise ValueError("Non-finite number")

    depth(result)
    return result


def create_app(runtime, token, allowed_origins=("http://127.0.0.1:7777",), remote=None):
    if len(token) < 32:
        raise ValueError("Agent token must contain at least 32 characters")

    @asynccontextmanager
    async def lifespan(app):
        import asyncio
        task = asyncio.create_task(app.state.relay_client.run()) if getattr(app.state, "relay_client", None) else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.close()

    app = FastAPI(title="DigitalSoul M2", version="0.1.0", lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(GhostError)
    async def ghost_error(request, exc):
        return JSONResponse(exc.wire(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        error = GhostError("VALIDATION_ERROR", "Request does not match the endpoint contract", 422)
        return JSONResponse(error.wire(), status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = "NOT_FOUND" if exc.status_code == 404 else "VALIDATION_ERROR"
        error = GhostError(code, "Endpoint unavailable or request method unsupported", exc.status_code)
        return JSONResponse(error.wire(), status_code=exc.status_code)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        headers = {"Cache-Control": "no-store"}
        if origin in allowed_origins:
            headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
        try:
            if host not in ("127.0.0.1:7777", "127.0.0.1"):
                raise GhostError("POLICY_BLOCKED", "Unexpected service host", 403)
            if origin is not None and origin not in allowed_origins:
                raise GhostError("POLICY_BLOCKED", "Origin is not allowed", 403)
            if request.method == "OPTIONS":
                requested = request.headers.get("access-control-request-method", "")
                if origin not in allowed_origins or requested not in ("GET", "POST", "DELETE"):
                    raise GhostError("POLICY_BLOCKED", "Preflight is not allowed", 403)
                headers.update({"Access-Control-Allow-Methods": "GET, POST, DELETE",
                                "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key"})
                return JSONResponse({}, status_code=200, headers=headers)
            expected = "Bearer " + token
            if not secrets.compare_digest(request.headers.get("authorization", ""), expected):
                raise GhostError("UNAUTHORIZED", "Agent authentication required", 401)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise GhostError("VALIDATION_ERROR", "Request exceeds 1 MiB", 413)
            if body:
                try:
                    strict_json(bytes(body))
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise GhostError("VALIDATION_ERROR", "Request must be bounded strict JSON", 422) from exc
            request._body = bytes(body)
            response = await call_next(request)
            response.headers.update(headers)
            return response
        except GhostError as exc:
            return JSONResponse(exc.wire(), status_code=exc.status, headers=headers)

    app.include_router(run_router(runtime))
    if remote is not None:
        from .remote_routes import remote_router
        app.include_router(remote_router(remote))
    return app


def main():
    parser = argparse.ArgumentParser(description="Standalone M2 host; local reviewed plans only")
    parser.add_argument("--workflow", type=Path, required=True, help="Explicitly reviewed workflow JSON")
    parser.add_argument("--data", type=Path, default=Path(".ghost"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--relay-url", help="Optional provisioned wss:// relay endpoint")
    parser.add_argument("--desktop-id", default="desktop_primary")
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    from ..safety.credentials import agent_token, load_or_create
    token = os.environ.get("DIGITALSOUL_AGENT_TOKEN") or agent_token(args.data)
    policy = acme_policy()
    store = RunStore(args.data / "m2.sqlite3")
    driver = BrowserDriver(policy, profile=str(args.data / "chrome"), headless=args.headless)
    runtime = Runtime(driver, policy, store)
    runtime.register(strict_json(args.workflow.read_bytes()))
    remote = None
    if args.relay_url:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
        from ..remote.pairing import RemoteStore
        from ..remote.service import RemoteService
        identity = load_or_create(args.data / "desktop-key.protected", lambda: Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
        remote = RemoteService(args.desktop_id, Ed25519PrivateKey.from_private_bytes(identity),
                               RemoteStore(args.data / "remote.sqlite3"), runtime)
    app = create_app(runtime, token, remote=remote)
    if remote:
        from ..remote.relay_client import RelayClient
        relay_token = os.environ.get("DIGITALSOUL_RELAY_TOKEN")
        if not relay_token:
            raise ValueError("Set a separate DIGITALSOUL_RELAY_TOKEN to enable the relay")
        app.state.relay_client = RelayClient(args.relay_url, args.desktop_id, relay_token, remote)
    import uvicorn
    try:
        uvicorn.run(app, host="127.0.0.1", port=7777, workers=1)
    finally:
        store.close()
        if remote:
            remote.store.close()


if __name__ == "__main__":
    main()
