# WhatsApp Desktop teaching and scheduled messages (experimental)

This is a standalone, local M2 desktop prototype. It does not implement WhatsApp's
own scheduling API, a WhatsApp Web flow, or a general recorder of every user action.
The guided recording teaches three UIA targets from the user's demonstration:
the verified chat header, message editor and Send button. It never clicks Send
during recording. No live WhatsApp session or delivery has been tested in the
restricted development environment. UIA compatibility must be established on the
actual installed WhatsApp version before scheduling a real message.

## Setup and teaching

Run these commands in a normal PowerShell window, as the same Windows user who
runs WhatsApp. The FastAPI server and OmniRoute are not required for this prototype.

```powershell
cd C:\Projects\DigitalSoul
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[windows,test]"
.\.venv\Scripts\python.exe -m agent.ghost.desktop record --recipient "Saksham"
```

Use the **exact displayed chat header**, including surname if present. Open the
one-to-one chat, inspect the contact profile/phone number to distinguish people
with similar names, then return to the chat and type the requested confirmation.
Enter a harmless draft manually so the Send button appears. Do not send it.

For each prompt, press Enter in the terminal, then switch to WhatsApp within
five seconds and point the mouse at the requested header, message editor or Send
button. There are no global key/mouse hooks and no history/screenshot collection.
The pointer is used only to teach selectors, never for replay. Recorded targets
must resolve uniquely. The header and editor need stable AutomationIds; if your
WhatsApp version does not expose these, teaching fails instead of falling back to
coordinates or a sidebar name match. Unsupported controls need an inspected
version-specific adapter, not weaker guards.

Recording writes `.ghost/desktop/whatsapp-recipe.protected`, encrypted using
Windows per-user DPAPI. It refuses to overwrite an existing recipe. To teach again,
choose a new data directory with `--data .ghost/desktop-new` **before** the command;
use that same directory on every subsequent command. Clear the draft manually.

## Read-only replay check

If startup reports `No module named 'win32api'`, repair **this virtual environment**
even if pip reports a user-level pywin32 install already exists:

```powershell
.\.venv\Scripts\python.exe -m pip install --ignore-installed --no-deps "pywin32>=311,<400"
.\.venv\Scripts\python.exe -c "import win32api, pythoncom; from pywinauto import Desktop; print('Windows imports OK')"
```

Restart recording after this check passes. Do not run the global pywin32
post-install script inside the virtual environment.

```powershell
.\.venv\Scripts\python.exe -m agent.ghost.desktop check --recipe .ghost/desktop/whatsapp-recipe.protected
```

Switch back to the intended WhatsApp chat within five seconds. This verifies the
foreground process, exact chat header, unique empty message editor; it does not
fill or send anything. Send-button availability is checked after filling during
execution, since WhatsApp may hide it while the editor is empty.

## Approve and schedule

For an initial **live send** test, choose two minutes from now. The following
command authorizes sending this exact message to the taught contact at that time:

```powershell
$at = (Get-Date).AddMinutes(2).ToString("o")
.\.venv\Scripts\python.exe -m agent.ghost.desktop schedule `
  --recipe .ghost/desktop/whatsapp-recipe.protected `
  --message "happy birthday" --at $at --approve-recipient "Saksham"
.\.venv\Scripts\python.exe -m agent.ghost.desktop worker
```

For an actual birthday, replace `$at` with an explicit date/time and offset such
as `"2026-12-01T00:00:00+05:30"` (example only, not an inferred birthday).
The exact date and recipient must come from the user; no real job is pre-created
by this implementation. Schedule once: rerunning `schedule` creates another job.

Keep the worker running, Windows awake/unlocked, and the intended chat foreground.
This version does not wake a sleeping PC, open WhatsApp, search/select recipients,
run as a background Windows service, or recur annually. Exact-time delivery cannot
be guaranteed: polling is every 0.5 seconds, followed by UIA work. A job more than
60 seconds late is marked missed, including if preparation exceeds that window.
Existing drafts are never overwritten. The literal message is assigned using UIA,
then the foreground process, recipient and draft are rechecked before Send Invoke.
UI changes/focus changes can still race a final native invocation; avoid interacting
with WhatsApp during execution. This prototype is not an unattended-send guarantee.

```powershell
.\.venv\Scripts\python.exe -m agent.ghost.desktop status
.\.venv\Scripts\python.exe -m agent.ghost.desktop cancel JOB_ID
.\.venv\Scripts\python.exe -m agent.ghost.desktop stop
# Explicitly re-arm for new jobs after a stop:
.\.venv\Scripts\python.exe -m agent.ghost.desktop resume
```

`stop` latches a persistent stop and cancels queued/preparing jobs. It cannot undo
an invocation already begun. Ctrl+C stops the worker; pending jobs stay stored.
This separate worker is **not connected to the FastAPI `/safety/stop` endpoint**;
use its own `stop` command until the shared runtime integration is implemented.
Blocked/cancelled preparations can leave a draft; inspect and clear it manually.

States: `pending`, `preparing`, `dispatching`, `attempted_unverified`, `blocked`,
`uncertain`, `missed`, `cancelled`. `attempted_unverified` means Send was invoked,
not that WhatsApp accepted or delivered it. Check the outgoing bubble/ticks yourself.
Crashes leaving `preparing` or `dispatching` are never automatically replayed;
inspect manually before deciding whether to create another job. Encrypted job
payloads snapshot the reviewed recipe/message; later recipe edits do not alter a
queued job. Scheduling metadata remains local SQLite data.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest agent/tests/test_desktop_jobs.py -q
```

These are fake-adapter and guard tests, not a live WhatsApp recording or send test.
For end-to-end validation, teach your installed client, run the read-only check,
then explicitly approve one test send to a verified consenting contact and inspect
the outgoing message. A real recording and live test remain pending.
