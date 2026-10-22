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
controls; UIA password fields are skipped. The current visual-memory addition can capture screenshots/OCR and request
OmniRoute synthesis; its separate consent/redaction gates are still incomplete.
See [verification findings](../docs/VALIDATION.md) before recording private work. Selected app fields can contain private information; inspect steps
before sharing them. Capture stops at five minutes/500 steps.

Recordings survive restart in `.ghost/notch/workflows.sqlite3`, with per-user DPAPI
encrypted payloads on Windows. This catalog is separate from M1's SQLite-backed learned
Workflow API. Raw steps are evidence, not a contract `Workflow` or execution approval.
Other apps can be recorded and reviewed, but need specific guarded adapters before
Run is enabled. Partial/failed WhatsApp recordings stay visible with the reason
they cannot replay; they are not presented as executable workflows.

## Everything mode, hidden notch and F8

Choose **Everything** (`*`) in the app picker to record across all apps instead of a
named list. DigitalSoul's own windows (the notch and the ghost) are never recorded.
While recording, the notch hides completely so it is not in the way. Press **F8**
anywhere to stop: the recording is saved and the notch comes back with its steps.
The five-minute limit, a recorder error or a failed save also bring the notch back;
the Rust host re-shows it on those backend events even if the page misses them.

In a browser, each page the address bar settles on becomes an `open_url` step with
the exact address. Typing, Enter and link clicks that led to that page within
10 seconds are folded into it, so replay opens the page directly instead of
re-typing a search. YouTube watch/shorts pages are saved with `t=<seconds>s`, so
replay resumes the video where you last saw it playing.

## Replaying steps with the ghost

**Replay steps** on a recording runs it again in a separate worker process. The
approval is bound to a digest of the exact steps you reviewed; if they change, the
replay is refused until you reopen the recording. Each step re-finds its control
through UI Automation in the recorded app; recorded pixels are never used.

The "ghost" is a small click-through overlay window that is never focused. It flies
to each control and acts there through UIA patterns (Invoke, Select, Toggle,
default action). Your mouse pointer is not moved. A control that offers none of
those patterns stops the replay. Press **F8** or **Stop queued desktop jobs** to
stop; the ghost is parked off-screen when the replay ends or is stopped.

## WhatsApp send routines

A recording in which you type into WhatsApp's "Type a message to <name>" box is
saved as a send routine (`digitalsoul.whatsapp-send.v1`), even if you did not
press Send. **Review & run** lets you change both the recipient and the message:
the name you approve is searched and its chat must open before anything is typed.
Recorded search/select flows (`digitalsoul.whatsapp-flow.v1`) are retargeted the
same way. Recipient and message must be single-line (no control characters).

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

## Limitations

- Only one recording or replay runs at a time. A scheduled job that falls due
  during a recording or replay waits, and is skipped once it is 60 seconds late.
- **Shopify** runs block the backend while the browser works (up to 4 minutes);
  F8 and Stop are not handled until it returns. Only `admin.shopify.com`,
  `*.myshopify.com` or the local test form can be opened.
- Name-based retargeting trusts WhatsApp search. Two contacts with the same
  display name cannot be told apart; check the recipient after sending.
- Replay needs the recorded apps to expose UIA controls. Canvas-drawn or
  custom-drawn UIs (many games, some Electron screens) cannot be replayed.
- Windows must stay unlocked and awake; there is no service or wake timer.

## Checks

The latest working-tree verification linked the Windows developer executable,
passed Rust checking and all four compact-mic UI tests. See
[the verification report](../docs/VALIDATION.md) for commands, backend test counts,
blocked live checks and contract gaps. Compilation does not verify window
appearance, microphone recognition or personal-app replay.

```powershell
.\.venv\Scripts\python.exe -m pytest agent/tests -q -p no:cacheprovider --ignore=agent/tests/test_remote.py
.\.venv\Scripts\python.exe contracts/validate.py
node --check desktop/ui/app.js
cd desktop; node --test tests/voice.test.cjs; cd ..
cargo build --manifest-path desktop/src-tauri/Cargo.toml
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
