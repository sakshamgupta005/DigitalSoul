# Blinky technical reference for DigitalSoul

Notch implementation: consulted `common/src-tauri/tauri.conf.json` and the
`common/src-tauri/src/lib.rs` transparent/always-on-top overlay setup, plus
`common/python/utils/ui_map_cache.py` stable-reference/window-signature patterns.
DigitalSoul's `desktop/` uses a small top-centre Tauri window and private Python
stdio bridge. Replay resolves fresh UIA controls and binds chat identity; raw
recordings are not executable trust grants. No Blinky runtime dependency was added.

WhatsApp desktop prototype (2026-10-22): re-inspected local
`windows/python/uia.py` before adding `agent/ghost/desktop/whatsapp.py`.
Adapted fresh process-scoped UIA resolution and unique control matching; no Blinky
code was imported. Teaching stores selected controls rather than global key logs
or message history. The native write adapter is experimental and has not been
verified against an installed WhatsApp UI; see `docs/WHATSAPP_DESKTOP.md`.

Recorded on 2026-10-22 at the user's request. Our previous product is located at `https://github.com/KingSahil/blinky`if you cant find the files in my local folders just open the repo. It is a closely related desktop assistant and should inform DigitalSoul implementation decisions.

These notes come from reading local architecture/security documents and selected source files. Blinky was not built, executed or security-tested during this review. Source paths below are relative to that checkout; source behavior should be rechecked before reuse.

## Product relationship

Blinky provides desktop tutoring and action execution with a Tauri/Rust host, React frontend, Python orchestration, Windows/Linux adapters, and an Expo mobile companion. DigitalSoul adds sustained observation, repeated-workflow discovery, evidence-backed decisions, reviewed typed plans, earned trust, and durable execution records.

The current DigitalSoul architecture is a loopback FastAPI agent with a browser extension, web/PWA dashboard, browser-use CDP/Actor and UIA execution, and a signed relay protocol. The user explicitly selected browser-use on 2026-10-22; see [the browser-use decision](docs/ADR_0001_browser_use.md). Referencing Blinky does not select Tauri, native mobile, its ports, or its protocols for this project.

## Inspected implementation references

| Area | Blinky source | Application to DigitalSoul |
|---|---|---|
| browser-use integration | `common/python/tools/browser_use_tool.py` | Inspected for Browser/Agent lifecycle and model selection. DigitalSoul uses the same browser-use package's deterministic Actor layer and routes optional analyst calls through OmniRoute; it does not copy Blinky's unrestricted Agent loop or direct provider credentials. |
| Windows UI Automation | `windows/python/uia.py` | Reference process-scoped reads, fresh COM element resolution, filtering of overlay windows, and bounded tree traversal. Adapt for consented capture and UIA driver reads. |
| Stable UI identity and cache | `common/python/utils/ui_map_cache.py` | Reference `window_signature`, `preserve_stable_refs`, changed-region merging and cache expiry. Re-resolve against fresh evidence before mutation; a cache ref is not a durable record ID. |
| UI result contracts | `common/frontend/src/lib/types.ts` | `OcrItem` carries bounds, confidence, source/control metadata and matching diagnostics; `TutorStep` carries `target_ref`. Use these concepts in internal driver results and debugging views, with explicit schema changes if exposed on the wire. |
| Bounded execution loop | `common/frontend/src/lib/autopilot.ts` | Reference observe/act callbacks, attempt limits, post-action observation and failure reporting. Put authoritative execution, risk and cancellation in the DigitalSoul agent. |
| Learned knowledge | `common/python/computer_use/recipes.py` | The module explicitly stores learned contextual knowledge rather than replayable step sequences. It provides registry/versioning context, but cannot directly serve as DigitalSoul's executable Workflow format. It imports `fcntl`, requiring platform adaptation for Windows. |
| Desktop/mobile bridge | `common/src-tauri/src/websocket.rs` | Reference request correlation, status/result streaming and explicit auth responses. DigitalSoul's versioned envelopes, single-use approval binding, replay protection and signed routing fields remain authoritative. |
| Process and platform boundaries | `local-docs/ARCHITECTURE.md` | Reference separation of privileged host, UI, Python worker/daemon and platform adapters. Keep machine-readable worker stdout separate from human logs and preserve request IDs across boundaries. |
| Transport and security lessons | `local-docs/SECURITY-AND-RISKS.md` | Review the existing risk register before adapting remote transport, generated tools, cancellation or privacy behavior. Its findings are source-review notes, not proof of the current security of every code path. |

Additional files identified for later focused inspection include `common/python/ui_observer.py`, `common/python/computer_use/vision_verifier.py`, `common/python/tests/test_ui_map_cache.py`, `common/python/tests/test_vision_verifier.py`, `common/src-tauri/src/tls_identity.rs`, `common/src-tauri/src/transport.rs`, and `windows/src-tauri/src/platform/cua.rs`. Their names/locations were found; their implementations were not reviewed in this pass.

## Adaptation decisions

1. **Ground every action in current evidence.** Preserve stable refs only within a compatible app/window observation. Refresh after navigation, window changes or layout drift. Recheck stable business record identity independently of UI refs.
2. **Carry coordinate metadata explicitly.** Blinky's UIA comments and frontend types distinguish window, screenshot and screen geometry. Any DigitalSoul fallback must carry origin, scale and coordinate-space information, rather than assuming screenshot pixels equal physical screen pixels. Primary locators remain DOM/UIA-based.
3. **Make execution agent-owned.** Blinky's frontend autopilot is useful interaction code. DigitalSoul must continue enforcing the reviewed plan, risk, approvals and cancellation even if its dashboard disconnects or a different client connects.
4. **Learn data, then compile a plan.** Adapt observation and registry ideas into RawEvent → normalized actions → episodes → candidates → reviewed Workflow. Context injected into an LLM is not evidence that an executable plan has been validated. Generated executable tools are outside the current workflow DSL.
5. **Treat remote control as a separate authorization boundary.** Blinky's documented release transport uses pinned WSS/HTTPS for a native companion. A browser PWA has different certificate and key capabilities; do not assume native pinning code transfers directly. Use DigitalSoul's authenticated relay and signed command contract.
6. **Keep sensitive observations local by default.** Redaction must cover UI labels, URLs, titles and screenshots as well as field values. User consent for local observation does not authorize external model uploads.

## Behaviors requiring replacement or further work

The inspected `autopilot.ts` classifies safety partly through instruction keywords and reports some cancellation paths as `complete`. DigitalSoul requires structured agent-side risk decisions and a distinct cancelled terminal state. A client-side confirmation or stopped animation cannot prove a worker stopped executing.

Blinky's security document reports development LAN modes with optional authentication, remote commands without server-side confirmation, generated tool source mutation, and incomplete cancellation propagation. These are lessons to check during adaptation. DigitalSoul requires authenticated control in every mode, effect-bound approval, a constrained reviewed DSL, and cancellation propagated to the driver/tool boundary.

Blinky's architecture document also describes process-local file-transfer recovery. DigitalSoul needs durable workflow checkpoints and external-effect reconciliation across process restarts. Reusing the transport pattern alone does not provide those guarantees.

## Suggested implementation order

- M1: inspect UIA/observer and cache code when implementing consented collection and normalization. Define observation identity and redaction before collecting long-lived data.
- M2: adapt target resolution and platform interfaces behind DigitalSoul's drivers; enforce fresh pre/postconditions, risk, approvals and cancellation in the agent.
- M3: reuse interaction ideas for progress and evidence views; render the agent's authoritative state and pending approvals from DigitalSoul contracts.
- M4: preserve these reference notes, add explicit adapter contracts where needed, and test migrated behavior against DigitalSoul's fixtures and runtime gates.

No Blinky modules have been imported or copied into DigitalSoul by this documentation change. Reuse should be intentional and include its dependencies, platform assumptions, relevant tests and contract mapping.
