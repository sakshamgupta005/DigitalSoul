# Blinky technical reference for DigitalSoul

Native Shopify adaptation (2026-10-22): read the persistent `_McpChild` and
`_CuaTransport` implementation, `get_window_elements`, and token-bound
`set_value` in `common/python/computer_use/backends/cua_driver.py`; also read
`windows/src-tauri/src/platform/cua.rs` and `windows/python/uia.py`'s fresh
process/window scoping. DigitalSoul now has its own narrow persistent MCP
transport in `agent/ghost/drivers/cua.py` and Shopify adapter in
`agent/ghost/browser/shopify_native.py`. It uses the installed open-source
`cua-driver`, not imports from the Blinky checkout. Only window enumeration,
fresh accessibility reads and token-bound value writes are exposed. Exact
Shopify URL checks, independent readback, cancellation and no automatic Save
remain DigitalSoul-owned. The native path is selected on browser connection
failure only; a browser write failure never triggers a second-engine replay.

Re-read `common/python/tools/browser_use_tool.py`, `browser_agent.py`, and
`browser_controller.py`. The latter's Playwright path is not imported or added
as a dependency. Browser-use's BrowserSession/Actor remains DigitalSoul's
primary browser engine, and model-driven arbitrary action loops are not copied.

Existing Edge attachment (2026-10-22): re-read
`common/python/tools/browser_use_tool.py`'s browser lifecycle. Its fresh
`Browser(channel=...)` does not attach to an already-running signed-in session.
DigitalSoul's interactive Shopify actor instead discovers Edge's user-enabled
`DevToolsActivePort` and uses browser-use's `cdp_url` with `keep_alive=True`.
This avoids copied profiles/cookies and reuses an exact matching product tab.
See [existing Edge setup and verification](docs/SHOPIFY_EDGE.md).

## Voice and workflow adaptation (2026-10-22)

Re-inspected `common/python/computer_use/tools.py`'s `APP_PROTOCOLS` mapping
and `os.startfile` launch path when adding voice **Open WhatsApp/Open Spotify**.
DigitalSoul adapts only the fixed URI mapping; arbitrary transcript-derived
paths and shell commands are rejected. Launch requests remain distinct from
proof of a visible window. Windows permission denials are reported separately
from missing app protocol handlers.

Inventoried the local Blinky source tree with `rg --files` (653 visible files)
and searched across frontend, Rust host, and Python code for speech, wake-word,
Spotify, and browser integrations. This is a repository-wide discovery pass,
not a claim that every line in every source file was audited.

Read the relevant sections of `common/frontend/src/CommandBar.tsx`,
`common/frontend/src/lib/assemblyaiVoice.ts`, and `sarvamStream.ts`: explicit
microphone start/stop, transcript callbacks, audio lifecycle, and wake events.
DigitalSoul adapts the explicit voice state and transcript-routing pattern;
the user selected local faster-whisper instead of those cloud providers.
`agent/ghost/desktop/voice_worker.py` uses CPU int8, bounded RAM audio chunks,
VAD, a five-minute opt-in session, and local-only model loading. Voice matches
select saved catalog entries; existing execution review remains required.

Read `common/ai_docs/09_computer_use_and_spotify.md` and app-launch/Spotify
sections of `common/python/computer_use/tools.py`: Spotify protocol dispatch
and foreground restoration are useful references. The current notch records
Spotify through the existing app-scoped UIA recorder and re-resolves recorded
controls on reviewed replay; it does not copy Blinky's web search for tracks.

Re-read `common/python/tools/browser_use_tool.py` and DigitalSoul's existing
`agent/ghost/drivers/browser.py`. Blinky's model-driven Agent loop is not
copied into deterministic workflow execution. Browser-use remains the existing
BrowserSession/CDP/Actor adapter; UIA browser recordings are not DOM plans.

`DigitalSoul-Blinky.code-workspace` references both checkouts without copying
Blinky or adding it as a runtime dependency. No Blinky source files were changed.
See [voice setup and limits](docs/VOICE_WORKFLOWS.md).

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
| browser-use integration | `common/python/tools/browser_use_tool.py` | Inspected for Browser/Agent lifecycle and model selection. DigitalSoul strictly uses the same browser-use package's deterministic Actor layer and routes optional analyst calls through OmniRoute; it does not copy Blinky's unrestricted Agent loop or direct provider credentials, and introduces no Playwright dependencies. |
| Windows UI Automation | `windows/python/uia.py` | Reference process-scoped reads, fresh COM element resolution, filtering of overlay windows, and bounded tree traversal. Adapt for consented capture and UIA driver reads. |
| OmniParser & Icon Grounding | `common/python/ocr/omniparser.py`, `common/python/ocr/icon_detect.py`, `common/python/utils/icon_text_merge.py` | Inspected for visual desktop component identification. Uses Microsoft OmniParser v2 and lightweight ONNX icon detector (YOLO11m via `onnxruntime`) merged with nearby OCR text. Provides a vision fallback for desktop surfaces lacking clean UIA trees (e.g. Canvas, taskbar app icons, DirectX). |
| Laya Intent Router | `common/python/ai/laya_router.py` | Inspected for fast (~30ms) System 1 non-autoregressive query and domain routing (`NandhaKishorM/laya`) paired with deterministic structural regex safeguards. Dispatches queries to Browser-Use, WhatsApp, or Desktop Automation without cloud LLM roundtrip latency. |
| CUA Actuator & Overlay | `windows/src-tauri/src/platform/cua.rs`, `common/python/computer_use/backends/cua_driver.py` | Inspected for background desktop clicks and cursor overlay via persistent `cua-driver mcp` stdio child. Mandatory `delivery_mode="background"` attempts UIA accessibility hit-testing without stealing focus or moving the physical cursor; only escalates to foreground on `background_unavailable`. |
| Stable UI identity and cache | `common/python/utils/ui_map_cache.py` | Reference `window_signature`, `preserve_stable_refs`, changed-region merging and cache expiry. Re-resolve against fresh evidence before mutation; a cache ref is not a durable record ID. |
| UI result contracts | `common/frontend/src/lib/types.ts` | `OcrItem` carries bounds, confidence, source/control metadata and matching diagnostics; `TutorStep` carries `target_ref`. Use these concepts in internal driver results and debugging views, with explicit schema changes if exposed on the wire. |
| Bounded execution loop | `common/frontend/src/lib/autopilot.ts` | Reference observe/act callbacks, attempt limits, post-action observation and failure reporting. Put authoritative execution, risk and cancellation in the DigitalSoul agent. |
| Learned knowledge | `common/python/computer_use/recipes.py` | The module explicitly stores learned contextual knowledge rather than replayable step sequences. It provides registry/versioning context, but cannot directly serve as DigitalSoul's executable Workflow format. It imports `fcntl`, requiring platform adaptation for Windows. |
| Desktop/mobile bridge | `common/src-tauri/src/websocket.rs` | Reference request correlation, status/result streaming and explicit auth responses. DigitalSoul's versioned envelopes, single-use approval binding, replay protection and signed routing fields remain authoritative. |
| Process and platform boundaries | `local-docs/ARCHITECTURE.md` | Reference separation of privileged host, UI, Python worker/daemon and platform adapters. Keep machine-readable worker stdout separate from human logs and preserve request IDs across boundaries. |
| Transport and security lessons | `local-docs/SECURITY-AND-RISKS.md` | Review the existing risk register before adapting remote transport, generated tools, cancellation or privacy behavior. Its findings are source-review notes, not proof of the current security of every code path. |

Additional files identified for later focused inspection include `common/python/ui_observer.py`, `common/python/computer_use/vision_verifier.py`, `common/python/tests/test_ui_map_cache.py`, `common/python/tests/test_vision_verifier.py`, `common/src-tauri/src/tls_identity.rs`, and `common/src-tauri/src/transport.rs`. Their names/locations were found; their implementations were not reviewed in this pass.

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

## Installed apps and Hermes commands (2026-10-23)

Re-read `common/python/computer_use/tools.py` app protocols, known executable
paths, `find_start_app` and AppsFolder launching. Adapted installed-name lookup
into DigitalSoul's `desktop/apps.py`: fixed Get-StartApps discovery, unique name
matching, and inventory-only shell namespace targets. No transcript-derived
executable or shell command is allowed. Existing fresh UIA resolution and reviewed
step replay remain authoritative. Hermes plans compositions of demonstrated
routines; it cannot supply new locators or invoke desktop/browser tools. See
[command planning](docs/COMMAND_PLANNING.md). Blinky was not modified.

## Windows Search app recordings (2026-10-23)

Re-inspected Blinky's `windows/python/uia.py` shell-region scoping and
`common/python/computer_use/tools.py` app identity launch paths. DigitalSoul
now accepts only Explorer folder window classes for Explorer capture/replay;
Start, taskbar and desktop surfaces are not folder windows. Input events carry
their original top-level window and process, including mouse hit-test ownership.
Initial foreground context and incidental Explorer visits require an actual
interaction before becoming steps. Observed non-shell app transitions survive
cleanup even in mixed routines. Store app identities remain the replay targets.
No Blinky files were modified. Regression tests use fake windows/events; a live
Windows Search launch demonstration remains necessary to verify native behavior.

## WhatsApp preparation diagnostics (2026-10-23)

Rechecked Blinky's Windows UIA import/availability handling in
`windows/python/uia.py`. DigitalSoul keeps its own bounded preparation and
reviewed-recipient checks. Preparation failures now carry allowlisted stages
and fixed remediation text; provider errors, contact names and message text
are not persisted in job details. Known flow failures do not invoke a fallback
adapter. Existing historical generic errors cannot be reconstructed. Tests use
fake adapters and do not send WhatsApp messages.

## Live WhatsApp WebView2 targeting (2026-10-23)

Consulted Blinky's `common/python/computer_use/backends/cua_driver.py` for
window-scoped Cua snapshots. Live Cua enumeration exposed WhatsApp.Root.exe's
WinUI frame and its separately owned msedgewebview2.exe content window. The
outer frame UIA walk timed out; the renderer exposed the named search Edit.
`desktop/whatsapp_window.py` now selects the content window only after resolving
its process ancestry to the recorded WhatsApp executable. Multiple content
windows fail closed. Contact results collapse ancestor/descendant UIA duplicates
but reject separate same-name contacts. Generic composers cannot establish the
recipient. Cua is used for window discovery; existing guarded keyboard/UIA
execution remains in place. UFO's hybrid inspection approach informed diagnosis;
UFO was not installed or introduced as an autonomous executor.

Live verification: searched the user-approved contact, resolved its unique row,
opened the chat and verified its recipient-labelled composer. One approved test
message was dispatched through the durable Jobs state machine. An independent
Cua screenshot showed the exact outgoing message, an empty composer and two grey
ticks. The durable job correctly remains attempted_unverified because the current
job adapter does not automate delivery-receipt verification. No automatic retry
was made after dispatch.
