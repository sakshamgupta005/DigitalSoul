# ADR 0001: browser-use and local OmniRoute

Decision date: 2026-10-22. The user explicitly requested browser-use for browser automation and asked to run OmniRoute and test connections.

## Decision

Replace the former browser driver with browser-use 0.13.11's BrowserSession/Actor API. Project code and tests contain no direct Playwright imports or dependency. Browser-use is already installed locally and exposes the required Actor page/element methods. The driver uses a dedicated local profile, disables cloud sessions and optional extension downloads, and detects installed Edge/Chrome or an explicit executable.

Keep the reviewed workflow interpreter, typed outputs, risk/approval gates, run state machine and checkpoints. Browser-use executes admitted steps; an unrestricted model Agent cannot bypass those gates. OmniRoute is the local OpenAI-compatible model gateway for optional analyst integrations; its configured model or combo is explicit. No default combo is assumed to exist.

The user-supplied Blinky reference was inspected at `common/python/tools/browser_use_tool.py`. That module uses `Browser` and an autonomous `Agent` with direct Gemini/OpenAI/Groq credentials. DigitalSoul adapts the browser-use lifecycle, uses deterministic Actor calls for reviewed workflows, and keeps model configuration behind the OmniRoute client.

## Implementation

- `agent/ghost/drivers/browser.py`: Actor navigation, CSS/role/text/label/XPath matching, backend-node identity, field reads/fill/select/click, target/evidence drift guards and independent read-only outcomes.
- `agent/ghost/drivers/browser_network.py`: separate CDP observer; avoids replacing browser-use's single-handler event registrations, blocks off-origin requests on managed targets, and correlates fresh mutation requests/responses before accepting a 2xx condition.
- `agent/ghost/integrations/omniroute.py`: local gateway settings, health/models/inference probes, secret-safe reports and a gateway-backed browser-use ChatOpenAI factory.
- `agent/ghost/integrations/connections.py`: bounded isolated browser-use probe and an optional minimal synthetic inference request. Browser-test timeout covers event-loop initialization as well as browser startup.
- `scripts/start-omniroute.ps1`: loopback-only launch using installed OmniRoute, optional same-job connection probes, persistent service lifetime and an isolated project data directory.

The Actor target-ID seam is pinned to the inspected 0.13.11 implementation. Upgrades need adapter and live-CDP regression tests. The current adapter supports managed top-level pages and bounded DOM searches, not arbitrary frames/popups; enabling new tab/frame capabilities needs interception coverage and contract tests first.

## Connection evidence

| Check | Observed result |
|---|---|
| Installed browser-use | 0.13.11; import and required Actor methods verified. |
| Installed OmniRoute | 3.8.51. |
| Gateway startup | CLI/Next.js reported ready on `127.0.0.1:20128`; SQLite initialized under `.ghost/omniroute`. |
| HTTP health/model list from Python | Connection blocked with `WinError 10013`, including gateway/probe launched in the same process job. HTTP endpoint success/authentication could not be verified. |
| Dashboard through browser tool | `net::ERR_CONNECTION_TIMED_OUT`. |
| Real browser-use session | Probe timed out; captured stack identified Python's Windows `_fallback_socketpair` during event-loop creation, before CDP connected. |
| Model inference | Not attempted: no model/combo selected in the project environment. No user/provider credentials were copied into the isolated profile. |
| Unit/adapter suite | 25 passed, 1 live-browser test skipped in explicit no-IO mode. These tests do not prove live connections. |
| Shared contract fixtures | 46 checks pass. |

`.ghost/connection-report.json` contains the latest sanitized live probe results. Startup readiness is not an HTTP connectivity or inference success claim. Run the launcher/probe from a normal Windows terminal to complete the live gates. The isolated profile requires its own provider/key/model configuration, or use the existing gateway and only run the probe.

## Outstanding integration gates

Real CDP startup/navigation/click/fill and request interception; real demo workflow execution; redirect/frame/popup containment; cookie-scoped authenticated outcome reads; gateway authentication/model-list success; one synthetic model completion through a configured route. The no-IO scheduler and mocked services cannot satisfy those gates.
