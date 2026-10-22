# Build and contract verification — 2026-10-22

Verified the working files in `C:\Projects\DigitalSoul` on `main`. Four paths
were unmerged at the start; by final inspection those entries were cleared and
HEAD was `950d255`. This verification pass did not switch branches, resolve Git
index entries, commit or push. Its own changes are the documentation listed below.

Updated `README.md`, `desktop/README.md`, `agent/M2_IMPLEMENTATION.md`,
`contracts/README.md`, `contracts/REVIEW.md` and this report. Runtime source was
not changed; outstanding findings below are intentionally reported as unresolved.

## Results

| Check | Result | Evidence and limits |
|---|---|---|
| Contract validator | 46 passed | Schemas, fixtures, static semantics, embedded JSON synchronization and regression mutations. |
| Selected backend/learning suite | 122 passed, 1 skipped | Interpreter, simulated Actor/CDP, standalone API auth, approvals, recovery, crypto, relay, catalog/stdio, voice routing and learning units. Exact exclusions below. |
| OCR data-structure unit test | 1 passed | Bounding boxes and text lookup; no live OCR claim. |
| Frontend syntax | Passed | `node --check desktop/ui/app.js`. |
| Frontend behavior | 4 passed | Compact mic loading/error/wake, RPC failure, workflow review and app feedback under a simulated DOM. |
| Rust check | Passed | `cargo check --manifest-path desktop/src-tauri/Cargo.toml --locked`. |
| Windows developer build | Passed | `cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked` linked `desktop/src-tauri/target/debug/digitalsoul-notch.exe`. |
| Python compilation | Passed | `python -m compileall -q agent relay contracts`. |
| Dependency consistency | Failed | `pip check` reports missing/pinned dependencies from unrelated packages exposed by this environment, including onnx2tf. Do not treat this venv as a clean reproducible installation. |
| Live browser-use probe | Blocked | Version 0.13.11 installed; asyncio startup stalls in Windows `socketpair()`. No live CDP connection proved. |
| OmniRoute HTTP | Blocked | WinError 10013. This invocation did not start a gateway and does not establish service startup readiness. |
| OmniRoute inference | Not attempted | `OMNIROUTE_MODEL` unset; no model/combo was invented. |
| Learning API TestClient suite | Timed out | Stopped after a bounded 20-second subprocess attempt; normal OS event-loop integration remains unverified. |
| Acme HTTP suite | Failed due to environment | 27 tests ran; loopback requests fail with WinError 10013. Retry used `.ghost/test-temp` after the first attempt also hit restricted default temporary-directory cleanup. |

Cargo copied incremental files after hard-link warnings; checking and linking
still succeeded. The normal full pytest attempt did not finish and was stopped.
The selected suite is not a full-suite pass.

## Repeat the checks

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe contracts/validate.py
node --check desktop/ui/app.js
node --test desktop/tests/*.test.cjs
cargo check --manifest-path desktop/src-tauri/Cargo.toml --locked
cargo build --manifest-path desktop/src-tauri/Cargo.toml --locked
.\.venv\Scripts\python.exe -m compileall -q agent relay contracts
```

Exact restricted-environment test selection used here:

```powershell
$env:DIGITALSOUL_TEST_NO_IO = '1'
try {
    .\.venv\Scripts\python.exe -m pytest -q --ignore=agent/tests/test_shopify_browser_use.py --ignore=agent/tests/test_visual_memory.py --ignore=tests/learning/test_api_endpoints.py
} finally {
    Remove-Item Env:DIGITALSOUL_TEST_NO_IO -ErrorAction SilentlyContinue
}
.\.venv\Scripts\python.exe -m pytest agent/tests/test_visual_memory.py::test_ocr_data_structures -q
```

The no-IO scheduler is test-only. It cannot validate network, browsers,
thread-backed TestClient or microphones. On a normal Windows terminal, repeat:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m unittest discover -s demo-apps/tests -v
.\.venv\Scripts\python.exe -m agent.ghost.integrations.connections --browser --inference --output .ghost/connection-report.json
```

Configure an actual `OMNIROUTE_MODEL` and gateway API key before inference.
Service startup, successful HTTP responses and successful inference are separate
gates. Screen/browser integration tests should use an isolated synthetic-data session.

## Outstanding contract gaps

These findings were inspected or reproduced during verification; they were not
fixed in this documentation pass. JSON schemas and `contracts/REVIEW.md` remain authoritative.

| Area | Observed behavior | Required integration |
|---|---|---|
| Shared `agent/ghost/app.py` | No Bearer/Host authentication middleware; wildcard CORS. In-process ASGI probes returned 200 for unauthenticated OpenAPI and accepted foreign-origin OPTIONS, reflecting `https://foreign.invalid`. | Reuse M2's authentication boundary for all shared data/control routes, exact Host/Origin and configured extension IDs. |
| Generated RawEvent model | Synthetic `redacted=true`, `shape=secret_omitted`, non-null text passes `RawEvent.model_validate`; JSON Schema rejects the same object. | Enforce conditional constraints at ingress and test generated-model parity. |
| Learning event ingestion | Validates/inserts each item sequentially, allowing partial persistence if a later event fails. | Validate the whole batch, then use one transaction with content-aware duplicate/conflict semantics. |
| Workflow editing/admission | Learning PUT stores client workflow objects without `If-Match` or server-owned trust reset. Learning validation does not register the plan with the M2 runtime. | Optimistic concurrency, immutable reviewed versions and explicit reviewed-plan registration. |
| Stream tickets | Single-use but not Origin/principal bound; expiry appends `Z` to an offset-bearing datetime. | Bind issue/upgrade to authenticated principal and allowed Origin; emit one UTC `Z`. |
| Shopify | Direct model-provider selection and a browser-use `Agent` fallback can fill/click Save. `/shopify/run` bypasses M2 effect-bound approvals. | Deterministic BrowserSession/CDP/Actor adapter, OmniRoute-only analyst calls, agent-owned risk/approval and independent outcomes. |
| Visual memory | Recorder defaults capture on and can request synthesis without separate LLM consent. Failed capture can return a mock frame as ordinary evidence. | Explicit capture/LLM consent, source redaction and an unavailable-capture result distinct from test imagery. |
| Acme integration | Demo apps exist on 7701/7702, but fixture origins use port 80. Some HTML/API conventions differ from the driver. | Origin mapping, adapter conventions and a real-browser seeded run with independent outcome reads. |

The standalone `agent.ghost.api.m2_app` has tested Bearer/Host/Origin checks.
Those tests do not establish the same protections on the separate shared host.

## Remaining live gates

No real WhatsApp message was sent, Shopify product saved, microphone recording
started or personal workflow replayed. The notch executable was built but not
launched: its normal bridge can process existing due jobs. Visual appearance,
speech recognition, real screen capture/OCR, phone WebCrypto interoperability,
installer packaging and full Acme browser execution remain unverified.

The output is a checkout-backed developer executable, not a standalone installer.
Use `.\scripts\start-notch.ps1` for interactive testing on the intended desktop,
after reviewing pending schedules. That launcher stops an existing notch process.
