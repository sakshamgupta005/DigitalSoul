"""Integration tests for Learning Engine API endpoints & WebSocket security."""

import json
from fastapi.testclient import TestClient
from agent.ghost.app import app
from agent.ghost.api.learn_routes import db, stream_manager
from agent.ghost.learning.models import WorkflowCandidate, CandidateStep
from contracts import Workflow

client = TestClient(app)


def test_stream_ticket_issuance():
    resp = client.post("/api/v1/stream/tickets")
    assert resp.status_code == 201
    assert resp.headers.get("Cache-Control") == "no-store"
    data = resp.json()
    assert "ticket" in data
    assert "expires_at" in data

    ticket = data["ticket"]
    assert ticket in stream_manager.valid_tickets

    # Consuming the ticket works once
    assert stream_manager.consume_ticket(ticket) is True
    # Second consume must fail (single-use)
    assert stream_manager.consume_ticket(ticket) is False


def test_websocket_requires_valid_ticket():
    # Attempting to connect without a ticket should be rejected with 1008 policy violation
    with pytest_raises_websocket_closure():
        with client.websocket_connect("/api/v1/stream") as ws:
            pass

    # Issuing a valid ticket and connecting succeeds
    ticket_resp = client.post("/api/v1/stream/tickets")
    ticket = ticket_resp.json()["ticket"]

    with client.websocket_connect(f"/api/v1/stream?ticket={ticket}") as ws:
        # Connected successfully
        pass

    # Trying to reuse same ticket should be rejected
    with pytest_raises_websocket_closure():
        with client.websocket_connect(f"/api/v1/stream?ticket={ticket}") as ws:
            pass


def pytest_raises_websocket_closure():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    return pytest.raises(WebSocketDisconnect)


def test_observation_settings_endpoints():
    get_resp = client.get("/api/v1/settings/observation")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "observing" in data
    assert "allowlist" in data

    # Update settings
    put_resp = client.put(
        "/api/v1/settings/observation",
        json={"observing": False, "allowlist": ["acme-portal"], "paused_until": "2026-10-22T12:00:00Z"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["observing"] is False
    assert put_resp.json()["allowlist"] == ["acme-portal"]


def test_guard_check_endpoint():
    with open("contracts/fixtures/raw_event.valid.1.json") as f:
        event_data = json.load(f)

    # Benign event check
    resp = client.post("/api/v1/guard/check", json={"event": event_data})
    assert resp.status_code == 200
    assert resp.json() is None

    # Destructive action event check
    event_data_del = dict(event_data)
    event_data_del["target"] = {
        "role": "button",
        "name": "Delete Customer Account",
        "testid": "btn-purge-record",
    }
    resp_del = client.post("/api/v1/guard/check", json={"event": event_data_del})
    assert resp_del.status_code == 200
    warning = resp_del.json()
    assert warning is not None
    assert warning["check_type"] == "destructive_action"
    assert warning["severity"] == "block"


def test_workflow_validation_lifecycle():
    # Save a candidate with valid step and outcome check matching schema minItems
    cand = WorkflowCandidate(
        candidate_id="cand_lifecycle_001",
        name="Lifecycle Candidate",
        description="Testing validate lifecycle",
        support=3,
        alignment=1.0,
        steps=[
            CandidateStep(
                step_id="s1",
                kind="action",
                verb="click",
                app="acme-crm",
                locators=[],
            )
        ],
        outcome_checks=[
            {
                "type": "app_api",
                "app": "acme-crm",
                "check": "record_exists",
                "args": {"id": "1042"},
            }
        ],
    )
    db.save_candidate(cand)

    # Promote candidate to draft workflow
    prom_resp = client.post("/api/v1/candidates/cand_lifecycle_001/promote")
    assert prom_resp.status_code == 200
    wf = prom_resp.json()
    wf_id = wf["workflow_id"]
    assert wf["trust"] == "candidate"
    assert wf["version"] == 1

    # Validate workflow
    val_resp = client.post(
        f"/api/v1/workflows/{wf_id}/validate",
        json={
            "name": "Approved Lifecycle Workflow",
            "remote_allowed": True,
        },
    )
    assert val_resp.status_code == 200
    validated_wf = val_resp.json()
    assert validated_wf["trust"] == "validated"
    assert validated_wf["version"] == 2
    assert validated_wf["remote_allowed"] is True
    assert validated_wf["name"] == "Approved Lifecycle Workflow"


def test_workflow_validation_rejects_unconfirmed():
    # Save a candidate with an unconfirmed parameter
    cand = WorkflowCandidate(
        candidate_id="cand_unconfirmed_001",
        name="Unconfirmed Candidate",
        description="Testing unconfirmed rejection",
        support=3,
        alignment=1.0,
        steps=[
            CandidateStep(
                step_id="s1",
                kind="action",
                verb="click",
                app="acme-crm",
                locators=[],
            )
        ],
        parameters=[
            {
                "name": "account_ids",
                "type": "string[]",
                "source": "user",
                "origin": "inferred",
                "confirmed": False,  # Not confirmed!
            }
        ],
        outcome_checks=[
            {
                "type": "app_api",
                "app": "acme-crm",
                "check": "record_exists",
                "args": {"id": "1042"},
            }
        ],
    )
    db.save_candidate(cand)

    # Promote candidate to draft workflow
    prom_resp = client.post("/api/v1/candidates/cand_unconfirmed_001/promote")
    assert prom_resp.status_code == 200
    wf_id = prom_resp.json()["workflow_id"]

    # Attempt to validate -> should be rejected with 422
    val_resp = client.post(
        f"/api/v1/workflows/{wf_id}/validate",
        json={"name": "Will Fail"},
    )
    assert val_resp.status_code == 422
    assert "unconfirmed" in val_resp.json()["detail"].lower()
