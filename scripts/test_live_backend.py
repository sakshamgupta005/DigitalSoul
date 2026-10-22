"""Live smoke test script for the unified DigitalSoul backend on 127.0.0.1:7777."""

import json
import httpx

BASE_URL = "http://127.0.0.1:7777"


def test_live_backend():
    print(f"===========================================================")
    print(f"      DIGITALSOUL: LIVE UNIFIED BACKEND SMOKE TEST         ")
    print(f"===========================================================")

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        # 1. Health check
        print("\n[1] Testing /health ...")
        resp = client.get("/health")
        assert resp.status_code == 200, f"Health check failed: {resp.text}"
        data = resp.json()
        print(f"    Status: {data['status']}")
        print(f"    Version: {data['version']}")
        print(f"    Roles: {data['roles']}")
        assert data["roles"]["m1_learning"] == "active"
        assert data["roles"]["m2_execution"] == "active"
        print("    -> PASS")

        # 2. OpenAPI inspection
        print("\n[2] Checking OpenAPI schema for M1 and M2 endpoints ...")
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        print(f"    Total registered routes: {len(paths)}")
        assert "/api/v1/events/batch" in paths, "M1 batch ingest missing"
        assert "/api/v1/candidates" in paths, "M1 candidates missing"
        assert "/api/v1/stream/tickets" in paths, "M1 tickets missing"
        assert "/api/v1/guard/check" in paths, "M1 guard check missing"
        assert "/api/v1/workflows/{workflow_id}/runs" in paths, "M2 runs missing"
        assert "/api/v1/runs" in paths, "M2 run list missing"
        assert "/api/v1/safety/stop" in paths, "M2 safety stop missing"
        print("    -> PASS: Both M1 (Learning) & M2 (Execution) routes verified!")

        # 3. Observation Settings (M1)
        print("\n[3] Testing M1 Observation Settings ...")
        resp = client.get("/api/v1/settings/observation")
        assert resp.status_code == 200
        settings = resp.json()
        print(f"    Observing: {settings['observing']}")
        print(f"    Allowlist: {settings['allowlist']}")
        print("    -> PASS")

        # 4. Stream Ticket Minting (M1)
        print("\n[4] Testing M1 WebSocket Ticket Issuance ...")
        resp = client.post("/api/v1/stream/tickets")
        assert resp.status_code == 201
        ticket_data = resp.json()
        print(f"    Ticket: {ticket_data['ticket']}")
        print(f"    Expires at: {ticket_data['expires_at']}")
        print(f"    Cache-Control: {resp.headers.get('cache-control')}")
        assert resp.headers.get("cache-control") == "no-store"
        print("    -> PASS")

        # 5. Mistake Guard Check (M1)
        print("\n[5] Testing M1 Mistake Guard Check ...")
        with open("contracts/fixtures/raw_event.valid.1.json") as f:
            sample_event = json.load(f)
        resp = client.post("/api/v1/guard/check", json={"event": sample_event})
        assert resp.status_code == 200
        assert resp.json() is None
        print("    Benign event correctly passed through without warning.")

        # Test destructive action detection
        del_event = dict(sample_event)
        del_event["target"] = {
            "role": "button",
            "name": "Delete Customer Account",
            "testid": "btn-purge-record",
        }
        resp = client.post("/api/v1/guard/check", json={"event": del_event})
        assert resp.status_code == 200
        warning = resp.json()
        assert warning is not None
        assert warning["check_type"] == "destructive_action"
        print(f"    Destructive action correctly intercepted: {warning['message']}")
        print("    -> PASS")

        # 6. Run Listing (M2)
        print("\n[6] Testing M2 Run List ...")
        resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        runs = resp.json()
        print(f"    Active/Historic Runs: {len(runs)}")
        print("    -> PASS")

        # 7. Emergency Stop (M2)
        print("\n[7] Testing M2 Safety Killswitch ...")
        resp = client.post("/api/v1/safety/stop")
        assert resp.status_code == 200
        assert resp.json()["stopped"] is True
        assert resp.json()["kill_switch_engaged"] is True
        print(f"    Killswitch status: {resp.json()}")
        print("    -> PASS")

    print(f"\n===========================================================")
    print(f"      ALL LIVE ENDPOINT CHECKS PASSED SUCCESSFULLY!        ")
    print(f"===========================================================")


if __name__ == "__main__":
    test_live_backend()
