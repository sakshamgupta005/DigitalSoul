# DigitalSoul Windows notch

A Tauri 2 transparent, frameless, always-on-top window at the monitor's top centre.
Click the compact bar to expand the recording controls, step review and workflow
library. Blinky's transparent overlay/window patterns were consulted. Hitodama
was supplied as visual inspiration, but its site could not be visually inspected
in the build environment; this is an independent implementation, not a verified clone.

## Run from this checkout

Requires Windows 11, Rust/MSVC build tools, WebView2 and the project's Python venv.
Install the native Python extras once in a normal PowerShell terminal:

```powershell
cd C:\Projects\DigitalSoul
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[windows,test]"
.\scripts\start-notch.ps1
```

The launcher uses Cargo directly; there is no frontend development server, network
port or OmniRoute requirement. Node/npm is optional for the alternative CLI:

```powershell
cd desktop
npm install
npm run dev
```

`npm run build` produces a developer executable, not a standalone installer.
The Rust host locates `.venv` and `agent` in this checkout. A packaged distribution
still needs an embedded Python runtime/resources and installation testing. If the
checkout moves after compilation, set `DIGITALSOUL_PROJECT_DIR` to its new path.

## Record and review

1. Expand the notch and choose **New recording**.
2. Name the routine and choose executable names to observe. Only `WhatsApp.exe`
   is selected by default. Add `notepad.exe` etc. explicitly for other apps.
3. For WhatsApp replay, enter the search/contact name, exact displayed chat header
   if different, and the message you intend to demonstrate. Verify the contact's
   identity/phone number in WhatsApp yourself; names alone do not distinguish people.
4. Start recording. The notch collapses. Open/activate WhatsApp, type the full
   search name, **click** the verified search result, type the message, pause briefly,
   then send once if you intend to demonstrate a send. This sends a real message.
5. Press **F8** or the stop square in the notch. The app saves the recording and
   opens its steps. It does not start replay automatically.

Observed app activations are stored as `open_app` evidence, clicks as fresh UIA
selectors, and edit values as coalesced text changes. OS launcher/start-menu clicks
are not captured: replay opens the app using its observed executable/AUMID. There
is no stored mouse-pixel replay. The input hooks retain only scoped left-clicks
and Enter, plus the F8 stop key. Typed text is read from allowed-app UIA edit
controls; UIA password fields are skipped. No screenshot/history upload or LLM call
is performed. Selected app fields can contain private information; inspect steps
before sharing them. Capture stops at five minutes/500 steps.

Recordings survive restart in `.ghost/notch/workflows.sqlite3`, with per-user DPAPI
encrypted payloads on Windows. This catalog is separate from M1's in-memory learned
Workflow API. Raw steps are evidence, not a contract `Workflow` or execution approval.
Other apps can be recorded and reviewed, but need specific guarded adapters before
Run is enabled. Partial/failed WhatsApp recordings stay visible with the reason
they cannot replay; they are not presented as executable workflows.

## Replay and schedules

A complete WhatsApp flow needs observed search, contact selection, matching chat
header, exact message, and Send click/Enter. Open its steps, choose **Review & run**,
review the contact/message and approve a one-shot replay or a local date/time.
Replay restores/opens WhatsApp, re-resolves the recorded controls, searches, selects
the contact, verifies the chat header, fills literal text, and checks it before Send.
The same contact name in a list cannot substitute for the recorded chat header.
Do not interact with WhatsApp during replay; desktop UI changes can race native input.
An existing message draft blocks replay instead of being overwritten.

The notch runs due jobs while open. Keep Windows awake/unlocked and the app open;
this version does not wake the PC or install a Windows service. Schedules more than
60 seconds late expire. Activity shows authoritative job states; `attempted_unverified`
means a Send action was attempted, not confirmed delivery. Inspect WhatsApp's outgoing
message/ticks. Possible sends are never automatically retried after failure/restart.
Activity's **Stop queued desktop jobs** latches the standalone desktop stop, cancelling
pending/preparing jobs; **Re-arm worker** explicitly resets it. An invocation already
begun cannot be undone. This desktop worker is separate from FastAPI `/safety/stop`.

WhatsApp/UIA versions differ. Unsupported editors, missing accessible names, stale
targets or ambiguous headers block replay; current-client live recording and sending
must be verified on the user's desktop. No real contact has been messaged by the
automated test suite. Closing the app stops its worker; pending future jobs remain
stored, and expired ones are handled when the worker next starts.

## Terminal recording remains available

```powershell
.\.venv\Scripts\python.exe -m agent.ghost.desktop record-flow --recipient "Saksham Gupta" --message "hello"
# If the actual displayed header differs:
.\.venv\Scripts\python.exe -m agent.ghost.desktop record-flow --recipient "Saksham Gupta" --chat-title "chigga" --message "hello"
```

The CLI prints the saved `.protected` recipe path and also saves the steps into
the notch library. Replay only after reviewing the demonstrated recipient/message:

```powershell
.\.venv\Scripts\python.exe -m agent.ghost.desktop replay --recipe PATH_PRINTED_BY_RECORDING --approve-recipient "Saksham Gupta"
```

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe contracts/validate.py
node --check desktop/ui/app.js
cargo check --manifest-path desktop/src-tauri/Cargo.toml
```

Tests include persistence/restart/deletion, raw-recording trust separation, ordered
replay with a fake native boundary, changed/existing-draft guards, cancellation,
and actual JSON stdio request/response handling. These do not establish live
WhatsApp UI compatibility, delivery or visual fidelity of the Windows notch.
# Local voice commands

The notch mic uses opt-in, local **faster-whisper**. Say “Hey Jarvis, do
Morning music” to find a recording named “Morning music”, review its steps,
then approve replay. Install the optional voice dependencies and download the
model once using [the voice setup guide](../docs/VOICE_WORKFLOWS.md).
Spotify is available in the recording app picker. Open
`DigitalSoul-Blinky.code-workspace` at the repository root to browse both projects.
