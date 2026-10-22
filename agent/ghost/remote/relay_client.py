import asyncio
import json
from urllib.parse import urlsplit

from ..execution.errors import GhostError


class RelayClient:
    """Outbound TLS connection. No implicit retry/resend of control messages."""
    def __init__(self, url, device_id, token, service):
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Relay must use a configured WSS URL without credentials/query")
        self.url, self.device_id, self.token, self.service = url, device_id, token, service

    async def run(self):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        delay = 1
        while True:
            try:
                async with connect(self.url, max_size=65536, open_timeout=10, ping_timeout=20) as socket:
                    await socket.send(json.dumps({"type": "auth", "device_id": self.device_id, "token": self.token}))
                    auth = json.loads(await asyncio.wait_for(socket.recv(), 10))
                    if auth != {"type": "auth_result", "ok": True}:
                        raise GhostError("UNAUTHORIZED", "Relay authentication failed", 403)
                    delay = 1
                    async for raw in socket:
                        try:
                            response = await self.service.handle(raw)
                        except GhostError:
                            # Unauthenticated messages receive no authoritative response.
                            continue
                        await socket.send(json.dumps(response, ensure_ascii=False))
            except asyncio.CancelledError:
                raise
            except GhostError:
                raise
            except (OSError, TimeoutError, ConnectionClosed):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
