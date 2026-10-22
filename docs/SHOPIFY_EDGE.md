# Shopify in your existing Edge session

Interactive Shopify automation uses browser-use 0.13.11's BrowserSession/Actor
API and attaches to your regular running Edge. It does not launch a replacement
profile or copy cookies. Headless practice tests still use isolated profiles.

1. In the Edge window where Shopify is already signed in, open
   `edge://inspect/#remote-debugging`.
2. Enable **Allow remote debugging for this browser instance**. If Edge asks
   whether to allow a debugging connection, review and allow it yourself.
3. Keep exactly one intended Shopify new-product tab open. In DigitalSoul choose
   **Add Shopify product**, review the fields and choose **Fill & review product**.

DigitalSoul reads `%LOCALAPPDATA%/Microsoft/Edge/User Data/DevToolsActivePort` and
connects directly to its local WebSocket endpoint. The permission-based endpoint
may not expose `/json/version`; testing only HTTP port 9222 is insufficient.
The app reuses the exact matching product tab without reloading it. Multiple
matching tabs block execution to avoid choosing the wrong unsaved form.
Disconnecting leaves Edge open. Live Save remains blocked pending M2 effect-bound
approval integration; the filled form requires review and manual Save in Shopify.

The desktop bridge now holds one browser-use session on a continuously running
background event loop across product runs. The HTTP host likewise holds sessions
until application shutdown. Do not create/stop a BrowserSession for every product:
`keep_alive=True` preserves the browser process, but `stop()` still closes CDP and
Edge can request permission again at the next connection. A new app/browser
session or a broken connection may still require a new user approval. The upstream
recommendation is longer connections, not automatically accepting the dialog:
[connection approval discussion](https://github.com/ChromeDevTools/chrome-devtools-mcp/issues/825).

For a nonstandard Edge installation, set `DIGITALSOUL_EDGE_USER_DATA_DIR` to its
user-data root, or `DIGITALSOUL_EDGE_CDP_URL` to an explicit local HTTP/WebSocket
debug endpoint. Neither option copies or decrypts login cookies.

The old fixed port 9222 may belong to `.ghost/edge_user_data`, a different Edge
profile. Its Shopify login state is independent of your ordinary profile.
Interactive runs fail with setup guidance when regular Edge has no endpoint;
they do not fall back to that separate profile.

## Verification

`python -m pytest agent/tests/test_edge_session.py -q` passed all 11 tests:
endpoint discovery/validation, missing-setting guidance, existing-tab reuse,
no isolated-browser fallback, and disconnect without closing Edge. Those are
unit tests, not proof of a live Shopify fill. Live verification requires the
user-enabled endpoint and a signed-in product tab.

Live test on 2026-10-22: connected to the regular Edge permission endpoint,
reused the one existing `gzn0vr-qn/products/new` tab, and filled the synthetic
title, iframe description, price `99.00` (store displays INR), and quantity `0`.
Independent page reads matched all four values; Save was not clicked. The
description's hidden backing textarea remained empty, so this proves visible
editor content, not saved Shopify data. Evidence: `.ghost/shopify-live-result.json`.
Follow-up persistence/discovery/desktop-bridge tests: 35 passed. Repeated live
runs on the new persistent service have not been verified; no extra connection
was initiated just to repeat Edge's approval prompt.

References:

- [Microsoft: Auto-connect to a running Edge instance](https://github.com/MicrosoftDocs/edge-developer/blob/main/microsoft-edge/web-platform/devtools-mcp-server.md#auto-connect-to-a-running-edge-instance)
- [browser-use browser parameters](https://docs.browser-use.com/open-source/customize/browser/all-parameters)

## Notch execution correction — 2026-10-22

Reproduced a real stdio-host hang with the browser-use worker, even though the
standalone form filler passed. Its initial video-watchdog import stalled while
loading NumPy after the stdin-reader thread had started. Initializing the optional
native dependency before that reader starts resolved the reproduced failure.
The notch now submits browser work asynchronously, continues answering status and
cancel commands, and keeps expand/collapse usable during the run. Progress,
validation failures and field errors appear beside the Shopify submit button.

The DOM adapter handles open shadow roots, TinyMCE's public editor API when
available, revealed Compare-at/Cost/SKU inputs, and exact custom-picker selections.
Unsupported or ambiguous options remain errors instead of reported success.
Optional fields no longer receive unrelated defaults when opening the form.

Focused final verification: **38 Python tests and 6 frontend tests passed**.
The Python selection includes two opt-in real-browser CDP tests against synthetic
data: a shadow-root/reveal/status/vendor form, and an actual subprocess running
the notch stdio bridge, persistent browser-use worker and 10-field product request.
The latter also proves that status replies arrive before browser work completes.
These runs used an existing local test-browser endpoint, not the live Shopify
store. They do not establish real-store Save or complete taxonomy compatibility.
Evidence: `.ghost/shopify-final-verified.log`. The normal project launcher rebuilt
and reopened `C:/Users/sahil/.notch-target/debug/digitalsoul-notch.exe` successfully.
