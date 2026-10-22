"""Provisioned relay router. TLS must terminate at the hosting proxy.

Device routing tokens are separate from the local agent token. This module never
creates pairings or decides whether a command is allowed on the desktop.
"""
import asyncio
import hashlib
import secrets
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from agent.ghost.api.m2_app import strict_json


def create_relay(token_hashes, allowed_pairs):
    app = FastAPI(title="DigitalSoul relay")
    connections = {}
    allowed_pairs = set(allowed_pairs)

    @app.websocket("/connect")
    async def connect(socket: WebSocket):
        await socket.accept()
        device = None
        try:
            auth_raw = await asyncio.wait_for(socket.receive_text(), 10)
            if len(auth_raw) > 4096:
                await socket.close(code=1008)
                return
            auth = strict_json(auth_raw)
            device = auth.get("device_id")
            token = auth.get("token")
            if (auth.get("type") != "auth" or not isinstance(device, str) or not isinstance(token, str)
                    or device not in token_hashes
                    or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), token_hashes[device])):
                await socket.close(code=1008)
                return
            if device in connections:
                await socket.close(code=1008)
                return
            connections[device] = socket
            await socket.send_json({"type": "auth_result", "ok": True})
            window, count = time.monotonic(), 0
            while True:
                raw = await socket.receive_text()
                if len(raw.encode("utf-8")) > 65536:
                    await socket.close(code=1009)
                    return
                if time.monotonic() - window > 60:
                    window, count = time.monotonic(), 0
                count += 1
                if count > 120:
                    await socket.close(code=1008)
                    return
                envelope = strict_json(raw)
                destination = envelope.get("to_device")
                if envelope.get("from_device") != device or (device, destination) not in allowed_pairs:
                    await socket.close(code=1008)
                    return
                target = connections.get(destination)
                if target:
                    # No queues, field logging, unsigned synthetic execution results,
                    # or interpretation of commands. Desktop verifies end-to-end.
                    await target.send_text(raw)
        except (WebSocketDisconnect, TimeoutError, ValueError, TypeError, AttributeError):
            pass
        finally:
            if device is not None and connections.get(device) is socket:
                del connections[device]

    return app
