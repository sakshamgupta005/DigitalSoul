# DigitalSoul

DigitalSoul observes consented, repeated work and compiles reviewed workflows for desktop execution. The M2 execution backend is implemented here; M1 learning, M3 frontend and M4 shared application/demo infrastructure integrate through the contracts.

Start with [the M2 implementation and handoff](agent/M2_IMPLEMENTATION.md), [shared contracts](CONTRACTS.md), and [contract corrections](contracts/REVIEW.md). [Blinky](BLINKY_REFERENCE.md) is the previous-product technical reference.

## Run the M2 backend

From `C:\Projects\DigitalSoul` in PowerShell, using a normal local Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\.venv\Scripts\python.exe -m agent.ghost.api.m2_app --workflow contracts/fixtures/workflow.valid.json
```

The host binds to `127.0.0.1:7777`. Loading a workflow file is an explicit local admission of a reviewed plan; HTTP clients cannot upload a plan and grant themselves trust. Browser execution uses **browser-use 0.13.11's CDP/Actor API**, a dedicated profile under `.ghost/chrome` and registered Acme app adapters. It detects installed Windows Edge/Chrome; optionally set `DIGITALSOUL_BROWSER_EXECUTABLE` to an installed browser. It requires M4's demo apps at `http://portal.acme.local` and `http://crm.acme.local`; those apps and hostname mappings are not included here. Static `dry_run` works without them and does not open a browser.

Read the local token into a variable, then create a dry run:

```powershell
$token = .\.venv\Scripts\python.exe -m agent.ghost.safety.credentials --data .ghost
$headers = @{ Authorization = "Bearer $token"; 'Idempotency-Key' = [guid]::NewGuid().ToString() }
$body = @{ workflow_version = 4; mode = 'dry_run'; parameters = @{} } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:7777/api/v1/workflows/wf_at_risk_seg/runs' -Headers $headers -ContentType 'application/json' -Body $body
```

Use `mode = 'supervised'` once the demo apps are running. Fetch run state from `GET /api/v1/runs/{run_id}`; supply its pending approval's `approval_id`, `workflow_version`, `step_id`, and `effect_hash` to `POST /api/v1/runs/{run_id}/approve`. Inspect `preview_data` before approving. Every R2 save/post has its own approval; earlier approvals cannot authorize later loop iterations.

## Checks

```powershell
.\.venv\Scripts\python.exe contracts/validate.py
.\.venv\Scripts\python.exe -m pytest -q
```

The suite exercises the complete browser driver using simulated browser-use Actor objects, independent outcome data, and CDP event tests. A separate real browser-use smoke test starts an isolated browser and evaluates `1 + 1` through CDP. Neither is a test against M4's real demo applications.

The current Codex Windows sandbox hangs in Python's `socketpair()` before an asyncio event loop starts, so real browser/network testing could not run here. A test-only scheduler can exercise coroutine state logic and in-process ASGI endpoints without OS IO:

```powershell
$env:DIGITALSOUL_TEST_NO_IO = '1'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:DIGITALSOUL_TEST_NO_IO
```

This mode explicitly skips the real browser-use smoke test. Never use its scheduler to run the backend. See the handoff for remaining integration and platform gates.

## OmniRoute and connection tests

The installed OmniRoute CLI can be launched on loopback using the project launcher:

```powershell
.\scripts\start-omniroute.ps1 -Probe
```

This uses an isolated `.ghost/omniroute` profile and leaves the server running until Ctrl+C. It does not modify or copy the credentials/provider database in your existing OmniRoute profile. Configure the new dashboard and its management password before adding provider credentials; it has its own account/model setup. To use your existing configured gateway instead, start your usual OmniRoute instance and run only the connection probe below. Do not run both gateways on port 20128.

Copy `.env.example` to `.env` and set `OMNIROUTE_API_KEY` and `OMNIROUTE_MODEL` to actual configured values. Existing environment variables take precedence; keys are never written to the diagnostic report. Browser-use remains deterministic for workflow input; `OmniRouteClient.browser_use_llm()` supplies a gateway-backed `ChatOpenAI` for optional analyst integrations.

```powershell
.\.venv\Scripts\python.exe -m agent.ghost.integrations.connections --browser --inference --output .ghost/connection-report.json
```

The probe separates gateway HTTP connectivity, model-list authentication, model inference, installed browser-use version, and a real CDP/Actor connection. In this sandbox, OmniRoute reports startup ready but Python HTTP requests fail with Windows socket permission error 10013; browser startup is blocked in `socketpair()`. A separate browser-tool dashboard attempt timed out too. No live model completion or browser connection is claimed. See [the migration and connection evidence](docs/ADR_0001_browser_use.md).
