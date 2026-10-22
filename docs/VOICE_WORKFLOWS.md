# Local voice workflows

The notch microphone uses open-source **faster-whisper**, not Wispr Flow.
It runs the English `small.en` model with CTranslate2 on CPU using int8.
There is no API key or cloud speech upload. Audio is buffered in RAM for at
most ten seconds per utterance; microphone sessions expire after five minutes.
This is phrase-based wake detection after transcription, not a dedicated
low-power wake-word model. CPU use and response time depend on your machine.

## Setup and use

Run from `C:\Projects\DigitalSoul`:

```powershell
.\scripts\setup-voice.ps1
.\scripts\start-notch.ps1 -Offline
```

The download command explicitly fetches `Systran/faster-whisper-small.en` from
Hugging Face into `.ghost/models/faster-whisper-small.en`. Normal listening
uses only the local model directory and does not download a model automatically.
Set `DIGITALSOUL_WHISPER_MODEL` before launching to use another compatible local
English CTranslate2 model directory. Installation requires compatible wheels for
your Python version; Python 3.12 or 3.13 is an alternative if dependencies do not
offer wheels for your interpreter. Allow microphone access for desktop apps in
Windows Settings and choose the intended default input device.

1. Save a recording with a distinct name, such as **Morning music**.
2. Click the microphone in the notch. Wait for **Listening locally**.
   For thirty seconds after the mic becomes ready you can speak a command
   directly, without a wake phrase: **Open WhatsApp** or **Open Spotify**.
3. Say **Hey Jarvis, do Morning music**, or **Hey Jarvis, wake up**, followed
   within twelve seconds by **Morning music**.
4. The compact notch shows **Ready: Morning music**. Click the brand to expand
   the saved recording, review it, then approve replay. Mic activation, wake
   phrases, setup errors, and matches do not automatically expand the notch.
5. Click the microphone again or say **Hey Jarvis, stop listening** to stop.

Matching ignores capitalization and punctuation. It does not guess similar
names. Duplicate names require selection in the workflow library. Voice input
never grants approval or changes a workflow. Starting recording, replay, or a
due scheduled send stops the microphone. Nothing listens at application launch.
Spoken commands are not saved as a new recording or uploaded to a model gateway.

App-opening commands use only fixed registered Windows protocols (`whatsapp:`
and `spotify:`). They open the app without sending messages or starting playback.
They are handled directly; they do not need a recorded workflow. Say **do Open
Spotify** to select a saved workflow with that name instead. Supported aliases
include **launch WhatsApp**, **start Spotify**, and **open whats app**.
The command field shows the most recently recognized phrase, and the compact
notch shows transcription and launch results. If Windows reports access denied
when the app was launched inside a restricted agent session, launch DigitalSoul
from your normal PowerShell. A missing app handler is reported separately.

The Jarvis command field also accepts typed or externally dictated text. That
lets you use Windows dictation or Wispr Flow as a text input method, without
depending on a private service API. It is not a Wispr desktop-action recorder.

If the notch shows **Voice setup needed**, expand it manually to read the full
help, or hover the mic for the message. Run `scripts/setup-voice.ps1` in your
normal PowerShell. It installs into the project venv, checks imports, and
downloads the model, using a writable project temporary directory. Pip DNS
failures for `pypi.org` mean installation did not reach the package index;
they do not establish that faster-whisper is incompatible with your Python.

## Spotify and browsers

Choose **Spotify** under New recording, then open Spotify and demonstrate the
routine. F8 saves its app activation and supported UIA steps in the same local
catalog as other recordings. Review **Replay steps** before execution. Spotify
must expose uniquely resolvable accessibility controls; this does not claim
support for every playback surface, song search, or canvas control.

Desktop browser observations are still UIA recordings. They are not silently
converted into DOM selectors. DigitalSoul's existing reviewed browser workflow
executor in `agent/ghost/drivers/browser.py` uses browser-use's BrowserSession/
Actor APIs and policy checks. Automatic conversion of arbitrary desktop browser
recordings into those contract workflows remains separate work. No unrestricted
Blinky Agent loop or Playwright dependency is introduced by voice lookup.

## Blinky reference

Open `DigitalSoul-Blinky.code-workspace` to see both local projects side by side.
Blinky stays a reference checkout; no dependency folders, credentials, or runtime
are copied into DigitalSoul. See `BLINKY_REFERENCE.md` for the inspected source.

## Validation limits

Command resolution, ambiguity, wake gating, stdio transport, and worker startup
errors can be tested without microphone hardware. Actual speech accuracy,
microphone access, Spotify playback, and native UI replay require testing on the
interactive Windows desktop with the installed model. A successful unit test is
not proof of a real message send or audio transcription.
