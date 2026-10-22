"""End-to-End Test and Demonstration Script for Developer 1 (M1 — Learning & Intelligence).

Simulates:
1. Recording 4 distinct sessions of the 'Weekly at-risk customer segmentation' task.
2. Ingesting raw events into the Agent via /api/v1/events/batch.
3. Automatically running:
   - Action normalization & locator bundle creation
   - Episode segmentation (idle gaps)
   - Needleman-Wunsch sequence alignment across episodes
   - Loop folding across repeated customer records
   - Hypothesis generation & AST decision rule verification
   - Recommendation card scoring
4. User validation & compilation into an official, versioned Workflow JSON.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import sys
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from agent.ghost.app import app
from contracts import RawEvent, Workflow


client = TestClient(app)


def generate_mock_event(
    kind: str,
    app_id: str,
    url_pattern: str,
    role: str,
    name: str,
    testid: str,
    value_text: str = None,
    session_id: str = None,
    ts: str = None,
) -> Dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "ts": ts or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "extension",
        "kind": kind,
        "app": {
            "id": app_id,
            "type": "web",
            "domain": f"{app_id}.local",
            "process": "chrome.exe",
            "window_title": f"{app_id.upper()} Dashboard",
        },
        "target": {
            "role": role,
            "name": name,
            "testid": testid,
            "label": name,
            "css": f"#{testid}",
            "xpath": f"//*[@data-testid='{testid}']",
            "uia": None,
            "bbox": [100, 200, 150, 40],
        },
        "value": {
            "redacted": value_text is None,
            "shape": "text:short" if value_text else "none",
            "text": value_text,
        },
        "page": {
            "url_pattern": url_pattern,
            "url_params": None,
        },
        "session_id": session_id or str(uuid.uuid4()),
    }


def create_customer_segmentation_session(session_num: int, base_time: datetime) -> List[Dict[str, Any]]:
    """Creates a realistic recorded session of the customer segmentation workflow."""
    sess_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"session_00{session_num}"))
    events = []
    t = base_time

    # Step 1: Open Portal Reports
    t += timedelta(seconds=10)
    events.append(generate_mockEvent := generate_mock_event(
        kind="navigate", app_id="acme-portal", url_pattern="/reports/overdue",
        role="navigation", name="Overdue Invoices", testid="nav-overdue",
        session_id=sess_id, ts=t.isoformat() + "Z"
    ))

    # Step 2: Copy Overdue Accounts
    t += timedelta(seconds=15)
    events.append(generate_mock_event(
        kind="clipboard_copy", app_id="acme-portal", url_pattern="/reports/overdue",
        role="table", name="Account IDs List", testid="account-ids-table",
        session_id=sess_id, ts=t.isoformat() + "Z"
    ))

    # Steps 3-5: Loop over 3 customers (1042, 1043, 1044)
    customers = [
        {"id": "1042", "plan": "Pro", "login": 45, "tag": "At-risk – High"},
        {"id": "1043", "plan": "Basic", "login": 12, "tag": "At-risk – Watch"},
        {"id": "1044", "plan": "Pro", "login": 60, "tag": "At-risk – High"},
    ]

    for c in customers:
        # Navigate to customer
        t += timedelta(seconds=12)
        events.append(generate_mock_event(
            kind="navigate", app_id="acme-crm", url_pattern="/customers/:id",
            role="page", name=f"Customer {c['id']}", testid="cust-view",
            session_id=sess_id, ts=t.isoformat() + "Z"
        ))

        # Select Tag
        t += timedelta(seconds=8)
        events.append(generate_mock_event(
            kind="input_change", app_id="acme-crm", url_pattern="/customers/:id",
            role="combobox", name="Tag", testid="tag-select",
            value_text=c["tag"], session_id=sess_id, ts=t.isoformat() + "Z"
        ))

        # Click Save
        t += timedelta(seconds=6)
        events.append(generate_mock_event(
            kind="click", app_id="acme-crm", url_pattern="/customers/:id",
            role="button", name="Save Customer", testid="save-btn",
            session_id=sess_id, ts=t.isoformat() + "Z"
        ))

    # Step 6: Post summary note on Portal
    t += timedelta(seconds=20)
    events.append(generate_mock_event(
        kind="click", app_id="acme-portal", url_pattern="/team-notes",
        role="button", name="Post Team Summary", testid="post-summary-btn",
        session_id=sess_id, ts=t.isoformat() + "Z"
    ))

    return events


def run_test():
    print("=================================================================")
    print("      DIGITALSOUL: DEVELOPER 1 (M1) PIPELINE DEMONSTRATION       ")
    print("=================================================================")
    print("\n[Stage 1] Ingesting 4 Recorded Sessions of the Demo Workflow...")

    base = datetime(2026, 10, 22, 9, 0, 0)
    total_events = 0

    for i in range(1, 5):
        session_time = base + timedelta(hours=i * 24)  # 1 session per day
        session_events = create_customer_segmentation_session(i, session_time)
        total_events += len(session_events)

        resp = client.post("/api/v1/events/batch", json=session_events)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        print(f"  -> Session {i} ingested: {data['ingested']} events, {data['duplicates']} duplicates")

    print(f"\n[Stage 2] Checking Mined Candidates & Recommendation Cards...")
    resp = client.get("/api/v1/candidates")
    assert resp.status_code == 200
    candidates = resp.json()

    print(f"  -> Discovered {len(candidates)} candidate workflow(s)!")
    assert len(candidates) >= 1

    cand_item = candidates[0]
    cand = cand_item["candidate"]
    rec = cand_item["recommendation"]

    print(f"\n  Candidate ID:   {cand['candidate_id']}")
    print(f"  Workflow Name:  {cand['name']}")
    print(f"  Support Count:  {cand['support']} matching episodes")
    print(f"  Alignment:      {cand['alignment']:.0%}")
    print(f"  Steps Count:    {len(cand['steps'])} steps (after loop folding)")
    print(f"\n  Recommendation Card:")
    print(f"  \"{rec['headline']}\"")

    # Inspect Steps
    print("\n[Stage 3] Inspecting Discovered Plan Structure & Folded Loops:")
    for s in cand["steps"]:
        if s["kind"] == "loop":
            loop_param = s["loop_iterator"]["source_param"]
            body_len = len(s["body"])
            print(f"    [STEP {s['step_id']}] LOOP over '{loop_param}' ({body_len} steps per iteration):")
            for bs in s["body"]:
                print(f"        -> {bs['step_id']}: {bs['verb']} on {bs['app']} (Risk: {bs['risk']})")
        else:
            print(f"    [STEP {s['step_id']}] {s['verb']} on {s['app']} (Risk: {s['risk']})")

    # Inspect Inferred Decision Rules
    print("\n[Stage 4] Decision Rule Hypotheses & Local Support Verification:")
    for rule in cand.get("decision_rules", []):
        print(f"  Rule ID:   {rule['id']}")
        print(f"  Condition: IF {rule['expr']}")
        print(f"             THEN '{rule['then']}' ELSE '{rule['else']}'")
        print(f"  Evidence:  {rule['supported_runs']} supported runs, {rule['contradicting_runs']} contradicting")

    # Step 4: Promote to Draft
    print(f"\n[Stage 5] Promoting Candidate to Draft Workflow...")
    resp = client.post(f"/api/v1/candidates/{cand['candidate_id']}/promote")
    assert resp.status_code == 200
    draft_wf = resp.json()
    print(f"  -> Draft created: {draft_wf['workflow_id']} (Trust: {draft_wf['trust']})")

    # Step 5: User Validation
    print(f"\n[Stage 6] User Review & Validation Approval...")
    val_payload = {
        "name": "Weekly at-risk customer segmentation (Approved)",
        "description": "Approved production recipe for overdue customer tagging.",
        "remote_allowed": True,
    }
    resp = client.post(f"/api/v1/workflows/{draft_wf['workflow_id']}/validate", json=val_payload)
    assert resp.status_code == 200
    validated_wf = resp.json()

    print(f"  -> Approved! Workflow version: {validated_wf['version']}")
    print(f"  -> Trust Rung: {validated_wf['trust'].upper()}")
    print(f"  -> Remote Control Enabled: {validated_wf['remote_allowed']}")

    # Verify that the final workflow matches the strict Pydantic model
    validated_model = Workflow.model_validate(validated_wf)
    print("\n[Result] Workflow JSON passed 100% contract schema validation!")
    print(validated_model.model_dump_json(indent=2, by_alias=True)[:600] + "\n  ... (truncated)")

    print("\n=================================================================")
    print("      ALL TESTS PASSED: M1 PIPELINE VERIFIED SUCCESSFULLY!       ")
    print("=================================================================")


if __name__ == "__main__":
    run_test()
