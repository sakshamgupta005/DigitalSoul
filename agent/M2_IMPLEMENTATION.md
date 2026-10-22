# M2 execution backend — implementation and handoff

This implementation belongs to Backend Developer 2: workflow execution, drivers, safety, run controls and remote transport. It follows the 1.1.0 draft schemas and `contracts/REVIEW.md`. It does not implement M1 observation/mining, M3 UI, or M4 shared application/database/demo apps.

## Files and responsibilities

All paths are relative to `C:\Projects\DigitalSoul`.

| File | Implemented behavior |
|---|---|
| `agent/ghost/execution/interpreter.py` | Draft 2020-12 validation, static reference/scope checks, safe expression compilation, named adapter admission, typed user parameters. |
| `agent/ghost/execution/context.py` | Typed outputs and copied per-iteration contexts. |
| `agent/ghost/execution/expressions.py` | Restricted AST evaluation, strict operand types, typed whole-value templates and URL interpolation. |
| `agent/ghost/execution/runtime.py` | Reviewed plan registration, pinned hashes/versions, single active run, recursive loops, decisions, approvals, controls, action intent/receipts, independent outcomes and read-only reconciliation. |
| `agent/ghost/execution/recovery.py` | M2-private SQLite/WAL run/idempotency storage and durable event sequence. |
| `agent/ghost/execution/verification.py` | Pre/postcondition evaluation through driver interfaces. |
| `agent/ghost/execution/canonical.py` | RFC 8785 canonicalization through the required upstream dependency. No custom float/signature serialization fallback. |
| `agent/ghost/drivers/base.py` | Driver integration protocol. |
| `agent/ghost/drivers/browser.py` | Dedicated Chromium profile; registered Acme DOM actions; unique, consistent alternative locators; decision/draft drift checks; fresh mutation response matching; read-only outcome/reconciliation adapters. |
| `agent/ghost/drivers/browser_network.py` | browser-use CDP observer, managed-target request filtering and fresh mutation response correlation. |
| `agent/ghost/integrations/omniroute.py` | Local gateway health/models/inference checks and gateway-backed browser-use analyst model factory. |
| `agent/ghost/integrations/connections.py` | Bounded live browser-use/OmniRoute connection probes with secret-safe JSON output. |
| `agent/ghost/drivers/uia.py` | Optional, process-allowlisted Windows UIA reads with fresh wrappers. Native mutation is not enabled. |
| `agent/ghost/drivers/input_fallback.py` | Explicitly blocks coordinate input until a grounding adapter is reviewed. |
| `agent/ghost/safety/policy.py` | App origins/path families, server-side action capability registrations, traversal checks and effective risk. |
| `agent/ghost/safety/risk.py` | R3 blocking and approval gates for R1/R2. |
| `agent/ghost/safety/approvals.py` | Versioned previews, SHA-256 effect binding, expiry and exact approval matching. |
| `agent/ghost/safety/guard.py` | Fresh target/context comparison. |
| `agent/ghost/safety/killswitch.py` | Latched stop and tracked task cancellation. |
| `agent/ghost/safety/credentials.py` | Per-user DPAPI-protected local token/desktop identity on Windows; owner-only files on other platforms. |
| `agent/ghost/api/run_routes.py` | Start/list/read/approve/reject/pause/resume/cancel/reconcile and emergency-stop routes. |
| `agent/ghost/api/remote_routes.py` | Optional authenticated local pairing start/confirmation and device list/revocation. |
| `agent/ghost/api/m2_app.py` | Standalone composition host, Bearer/Host/Origin validation, bounded strict JSON, CLI and optional relay wiring. |
| `agent/ghost/remote/pairing.py` | Five-minute high-entropy capability, phone key-possession proof, exact fingerprint confirmation, immutable device-key binding and durable nonce consumption. |
| `agent/ghost/remote/signing.py` | Shared command schema, strict JSON, signed routing fields, Ed25519, freshness and replay checks; signed desktop responses using a separate domain. |
| `agent/ghost/remote/service.py` | Signed command dispatch, minimal remote status, key revocation before actions, local-only approval default. |
| `agent/ghost/remote/relay_client.py` | Outbound WSS transport, authenticated initial frame, bounded messages and reconnection without command resend. |
| `relay/app.py` | Provisioned token-authenticated, rate-limited WebSocket routing with an explicit allowed-pair graph. |
| `agent/tests/` | Runtime, API, crypto, pairing, recovery and browser tests; optional no-IO test scheduler. |

## API surface

All local routes require `Authorization: Bearer <local token>`; browser Origin must be explicitly allowed. The default allowed Origin/Host is `http://127.0.0.1:7777`. There is no unauthenticated health/data endpoint. The standalone host provides FastAPI OpenAPI for these implemented endpoints, not the whole four-member system.

| Method | Path | Body / behavior |
|---|---|---|
| POST | `/api/v1/workflows/{id}/runs` | `{workflow_version, mode, parameters}` plus required `Idempotency-Key`. |
| GET | `/api/v1/runs` | `limit` 1–200, optional `status`; currently no cursor. |
| GET | `/api/v1/runs/{id}` | Authoritative public state, checkpoint, approvals and outcomes; private plan/authorization internals omitted. |
| POST | `/api/v1/runs/{id}/approve` | `{approval_id, workflow_version, step_id, effect_hash}`. |
| POST | `/api/v1/runs/{id}/reject` | Same binding fields; cancels the run. |
| POST | `/api/v1/runs/{id}/pause` | Cancels the current driver coroutine, invalidates approval, retains uncertain mutation intent. |
| POST | `/api/v1/runs/{id}/resume` | Rechecks the pinned registration and policy; refuses unresolved mutation intent. |
| POST | `/api/v1/runs/{id}/cancel` | Terminal cancelled state; cannot undo completed external effects. |
| POST | `/api/v1/runs/{id}/reconcile` | Local-only read of a registered external-effect adapter; marks an independently confirmed effect complete without replay. |
| POST | `/api/v1/safety/stop` | Latches the kill switch and cancels nonterminal runs. Reset requires explicit local runtime integration. |

The runtime uses a process-local async control lock and SQLite uniqueness for idempotency. Run **one desktop-agent process/worker** per data/profile directory. Multi-process desktop ownership is not implemented and must not be enabled in deployment.

## Execution and recovery semantics

`Runtime.register(plan, trusted=False)` is a local integration hook for M1/M4 to admit a reviewed, validated plan. A client's `trust` JSON cannot grant autonomous execution. `trusted=True` must come from the server's reviewed trust store. Input workflow objects are copied and runs pin an immutable plan/hash.

Read outputs and decisions are restored by step-instance keys that include the nested loop path. Loop input lists are frozen, bounded and unique. Required failures never skip. Read/navigation retries are bounded; mutation retries are disabled. `on_fail=abort` terminates on a handled step error; optional skipping and conditions on loop/decision containers are rejected at compilation until supported. Use explicit assert steps for those conditions.

Draft writes and external effects persist intent before action. Successful postcondition verification, receipt and step completion are checkpointed before moving on. Uncertain effects block normal resume. The Acme save/post adapters can confirm the expected tag or the unique run-specific summary through independent GET requests. Unconfirmed effects remain blocked; no automatic retry or unconditional "mark successful" API exists.

After process restart, nonterminal runs become paused or blocked, and all approvals expire. Resume restores the last completed navigation per app. Fresh decision/form checks may pause if page restoration lost an unsaved draft; an operator must restore the expected draft locally before resuming. The runtime does not silently post a newly empty form. Draft-edit reconciliation and general native-app recovery still need app-specific adapters.

`dry_run` performs static admission only, opens no browser and returns `completed_unverified`. It cannot predict output-dependent branches or certify end-to-end behavior.

## M4 and frontend integration

- Mount `run_router(runtime)` into the shared authenticated application; do not run a second desktop worker alongside this standalone host.
- Replace the M2-private RunStore through its interface with shared persistence when M4's migrations/models exist. No M4-owned database or app/bus files were modified.
- Supply a synchronous `event_sink(event)` to Runtime to hand notifications to the shared bus. Event sequence is durable; the in-memory event list is bounded. A sink should enqueue without blocking or mutating the runtime. REST snapshots remain authoritative.
- Stream tickets, the shared WebSocket stream, full shared OpenAPI/generated models, audit integration and cursor pagination remain M4 integration work. Do not mistake the in-memory diagnostic event list for a durable event log.
- M3 can poll run snapshots immediately and render each `pending_approval.preview_data`. Client confirmations do not replace agent-side checks.
- M1 should emit the existing fixture's adapter shapes: `data-account-ids` contains a JSON string array; customer identity uses `data-testid="customer-record-id"`; customer decision fields use `customer-plan` and `last-login-days`; tagging uses `select_option` on `tag-select`.
- Demo mutation APIs are `PATCH /api/customers/{id}` and `POST /api/team/notes`. Independent reads are `GET /api/customers/{id}` returning `{tag: string}` and `GET /api/team/notes` returning `[{body: string}]`.

## Optional remote integration

The default CLI is local-only. Set a separate `DIGITALSOUL_RELAY_TOKEN`, provision the relay's route graph and start with `--relay-url wss://<host>/connect --desktop-id <id>` to instantiate remote services. No relay was deployed or connection established in this environment. Local-agent tokens never travel to the relay.

`relay.create_relay(token_hashes, allowed_pairs)` accepts a map of device ID to lowercase SHA-256 routing-token digest and a set of directional `(sender, destination)` pairs. Host behind TLS with proxy-level message/connection limits. Provision both directions for a phone/desktop pair; routing authorization is separate from desktop key pairing. Browser/native clients first send `{type:"auth", device_id, token}`. These are provisioning hooks, not an automated account service.

The local pairing-start endpoint returns the desktop public key and one-use pairing capability for M3 to encode in a QR. A provisioned phone sends a `pair_claim` envelope with `protocol_version`, `msg_id`, `from_device`, `to_device`, `kind` and `payload`. The payload is `{pairing_id, capability, device_id, public_key, signature}`. Its signature covers `UTF8("digitalghost.pair.v1\n") + JCS(payload_without_signature)`. The claimed device ID must equal the sender. The desktop returns a signed response and requires local fingerprint confirmation before storing the phone as active. This pairing wire extension needs M3/M4 schema sign-off; command envelopes continue to use the existing shared command schema.

Desktop responses are signed over `UTF8("digitalghost.response.v1\n") + JCS(envelope_without_sig)` and correlate by `command_id` (the pairing message ID for pairing claims). M3 must verify the pinned desktop key, destination, request correlation, version and freshness. WSS/signatures do not encrypt payloads end-to-end.

Remote `run_workflow` accepts only plans deriving all inputs locally. Remote approval/rejection defaults to denied because safe non-sensitive phone previews have not been registered. Local approval remains available. Remote status omits parameters, form values and previews. Device revocation cancels associated nonterminal runs and is checked again before each action. Consumed nonces/command IDs persist across restart; the current implementation retains them without pruning and rejects duplicates. Durable remote result lookup is still pending; a lost response must be followed by a fresh signed status query, never a blind resend with a new ID.

## Validation and remaining gates

The current sandbox passed 25 M2 tests with the explicit no-IO scheduler and 46 existing contract checks. M2 tests include complete fixture execution against a deterministic app boundary and simulated browser-use Actor objects, CDP event correlation, gateway HTTP mocks, approval drift/expiry/wrong binding, idempotency, single-run ownership, cancellation, kill switch, crash recovery, read-only reconciliation, restricted expressions, origin/host/auth validation, Ed25519 tampering, UTC expiry, atomic nonce races/restart, pairing key replacement rejection, signed/minimized status and in-process relay authentication/routing/spoof rejection.

One real browser-use test was skipped because Python's normal Windows event loop hangs in `socketpair()` in this sandbox. A minimal `asyncio.run(asyncio.sleep(0))` reproduces the environment problem before application code. The live browser-use probe independently captured the same stack. OmniRoute reached startup-ready state, but Python HTTP probes returned WinError 10013 and a browser-tool dashboard request timed out. See [the browser-use migration decision and connection evidence](../docs/ADR_0001_browser_use.md). Real CDP, demo APIs, UIA and DPAPI must be exercised on a normal Windows runtime. The no-IO scheduler is confined to tests and cannot perform network/subprocess IO.

Also pending: independent M4 safety/e2e suite; WebCrypto interoperability vectors; UIA mutation drivers and coordinate grounding; OS lock/logout monitoring and native tray/hotkey integration; relay deployment/TLS/load testing; phone UI and schema sign-off for pairing/responses; shared audit/bus persistence. Until those gates pass, this is an M2 development backend, not a certified unattended desktop agent.
