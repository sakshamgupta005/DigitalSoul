from fastapi.testclient import TestClient

from agent.ghost.api.m2_app import create_app
from .helpers import runtime

TOKEN = "a" * 40


def test_api_auth_origin_and_strict_json(tmp_path):
    value = runtime(tmp_path)
    with TestClient(create_app(value, TOKEN), base_url="http://127.0.0.1:7777") as client:
        assert client.get("/api/v1/runs").status_code == 401
        headers = {"Authorization": "Bearer " + TOKEN}
        assert client.get("/api/v1/runs", headers=headers).status_code == 200
        assert client.get("/api/v1/runs", headers={**headers, "Origin": "null"}).status_code == 403
        assert client.get("/api/v1/runs", headers={**headers, "Host": "evil.example"}).status_code == 403
        assert client.post("/api/v1/workflows/wf_at_risk_seg/runs", headers={**headers, "Content-Type": "application/json", "Idempotency-Key": "key"},
                           content='{"workflow_version":4,"mode":"dry_run","mode":"normal"}').status_code == 422
        assert client.post("/api/v1/workflows/wf_at_risk_seg/runs", headers={**headers, "Idempotency-Key": "key"},
                           json={"workflow_version": 4, "mode": "dry_run"}).status_code == 201
        assert client.get("/api/v1/runs", headers=headers).json()[0]["workflow_version"] == 4
    value.store.close()
