import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from relay.app import create_relay


def test_relay_authenticates_routes_and_blocks_sender_spoofing():
    tokens = {"phone": "phone-routing-token", "desktop": "desktop-routing-token"}
    hashes = {device: hashlib.sha256(token.encode()).hexdigest() for device, token in tokens.items()}
    with TestClient(create_relay(hashes, {("phone", "desktop"), ("desktop", "phone")})) as client:
        with client.websocket_connect("/connect") as wrong:
            wrong.send_json({"type": "auth", "device_id": "phone", "token": "invalid"})
            with pytest.raises(WebSocketDisconnect):
                wrong.receive_json()
        with client.websocket_connect("/connect") as desktop, client.websocket_connect("/connect") as phone:
            for device, socket in (("desktop", desktop), ("phone", phone)):
                socket.send_json({"type": "auth", "device_id": device, "token": tokens[device]})
                assert socket.receive_json() == {"type": "auth_result", "ok": True}
            envelope = {"from_device": "phone", "to_device": "desktop", "kind": "command", "payload": {}}
            phone.send_text(json.dumps(envelope))
            assert desktop.receive_json() == envelope
            phone.send_json({**envelope, "from_device": "desktop"})
            with pytest.raises(WebSocketDisconnect):
                phone.receive_json()
