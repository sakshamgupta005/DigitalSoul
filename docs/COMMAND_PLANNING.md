# App commands and Hermes planning

Use `Open Calculator`, `Open Chrome`, `Open Excel`, or the full name of an
installed Windows app. DigitalSoul reads Get-StartApps, matches names locally,
and launches only a uniquely resolved AppID. It never passes command text to a
shell. WhatsApp and Spotify retain their existing protocol launchers. A launch
request does not prove the application displayed a window.

`Start <workflow name>` opens the saved workflow for review and replay. If no
saved recording matches, the command searches learned patterns. Ambiguous
matches require selection. Review remains required; speech is not an execution
approval. Learned pattern review shows steps and parameters, and its digest is
checked again before replay.

For unfamiliar requests, the real Hermes AIAgent runs in its existing, separate
Python installation and uses the configured OmniRoute model. It proposes up to
five whole demonstrated routines. DigitalSoul accepts only existing IDs, copies
their actual steps, validates them, and saves a new draft for review. It cannot
invent executable paths, controls, field values, or tool code. Requests with no
usable demonstrations ask for a recording instead of inventing a Notepad task.
This is bounded composition of learned actions, not general autonomous operation
of previously unseen interfaces. Browser execution remains browser-use; no new
browser engine is installed.

Hermes has no tools in this integration. Personal context, memory and background
self-modification are disabled. DigitalSoul owns the learning loop: demonstrations
refine patterns; replay worker completion/failure counts persist and inform future
Hermes proposals. These counts are not independently verified business outcomes,
and a composed failure does not identify which constituent routine failed.

## Configuration

Set `DIGITALSOUL_HERMES_CONSENT=1` only with consent to send commands and routine
names through the configured model. Recorded values, screenshots, file paths,
URLs and targeting evidence are omitted. OmniRoute may route to a cloud provider.
Set `OMNIROUTE_MODEL` to a configured model/combo and `OMNIROUTE_API_KEY` to its
authorized gateway key. Keep credentials in the ignored `.env`.

The default Windows Hermes installation is discovered under LocalAppData. Optional
`DIGITALSOUL_HERMES_ROOT` and `DIGITALSOUL_HERMES_PYTHON` override it. The worker
imports Hermes before consuming stdin because Hermes can restart itself into its
managed Python runtime. Planning times out after 45 seconds and cannot perform
desktop actions. No independent Hermes installation is bundled with DigitalSoul.

The local installation was configured with the user's existing `hermes-combo`
and authorized gateway key after explicit consent. The gateway needed its existing
user data directory, not the empty project data directory. Start it with:

```powershell
./scripts/start-omniroute.ps1 -DataDirectory "$env:USERPROFILE/.omniroute"
```

## Verification (2026-10-23)

- Windows inventory returned 263 apps, including Calculator, Excel, Chrome,
  Edge and Notepad. Calculator launched through the inventory path and its process was observed.
- OmniRoute process startup, authenticated health/models HTTP 200, and successful
  model inference were checked separately.
- A real Hermes request using `hermes-combo` selected a supplied demonstration ID.
- Focused tests cover installed-app matching, ambiguity, command injection,
  busy-state rejection, workflow lookup, learned lookup, evidence-only planning,
  invalid model responses, consent and persistent outcome feedback.

No live multi-app workflow or unseen-interface automation has been verified.

The focused suite passed 85 Python tests and four interface tests. The Windows
debug build completed successfully. Relaunch with `run-notch.bat` to use the
rebuilt notch; the launcher now prefers the project build.
