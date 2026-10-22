"""Local speech: select reviewed workflows or open explicitly supported applications."""
import json
import os
import queue
import re
import subprocess
import threading
import time
import sys

APPS = {"whatsapp": ("WhatsApp", "whatsapp:"), "spotify": ("Spotify", "spotify:")}


def normalized(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def resolve(text, workflows, *, require_wake=False, awake=False):
    if not isinstance(text, str) or len(text) > 512:
        raise ValueError("Use a command of at most 512 characters")
    command = normalized(text)
    wake = command.startswith("hey jarvis ") or command == "hey jarvis"
    if wake:
        command = command[len("hey jarvis"):].strip()
    if require_wake and not wake and not awake:
        return {"kind": "ignored"}
    if command in ("", "wake up") and wake:
        return {"kind": "wake", "message": "I'm listening. Say Open WhatsApp, Open Spotify, or a saved workflow name."}
    if command in ("stop listening", "go to sleep"):
        return {"kind": "sleep"}
    app_match = re.fullmatch(r"(?:please )?(?:open|launch|start) (whatsapp|whats app|what s app|spotify)(?: please)?", command)
    if app_match:
        return {"kind": "open_app", "app": "spotify" if app_match[1] == "spotify" else "whatsapp"}
    candidates = {command}
    for prefix in ("do the workflow ", "run the workflow ", "do workflow ", "run workflow ", "do ", "run "):
        if command.startswith(prefix):
            command = command[len(prefix):]
            candidates.add(command)
            break
    if command.endswith(" workflow"):
        candidates.add(command[:-len(" workflow")])
    matches = [w for w in workflows if normalized(w["name"]) in candidates]
    if len(matches) > 1:
        return {"kind": "ambiguous", "message": "Several workflows have that name. Choose one in Workflows."}
    if matches:
        return {"kind": "workflow", "id": matches[0]["id"], "name": matches[0]["name"]}
    return {"kind": "unknown", "message": "Say Open WhatsApp, Open Spotify, or the exact name of a saved workflow."}


def launch_app(app, opener=None):
    """Use a fixed registered app protocol, never a transcript as a path or shell command."""
    if app not in APPS:
        raise ValueError("This voice app command is not supported")
    name, protocol = APPS[app]
    if opener is None:
        if os.name != "nt":
            raise ValueError("Voice app launching currently requires Windows")
        opener = os.startfile
    try:
        opener(protocol)
    except PermissionError:
        return {"kind": "error", "code": "app_launch_denied", "message": f"Windows denied opening {name}. Run DigitalSoul from your normal PowerShell instead of the restricted agent session."}
    except OSError:
        return {"kind": "error", "code": "app_unavailable", "message": f"Windows could not open {name}. Check that it is installed and its app protocol is registered."}
    return {"kind": "app_opened", "app": app, "message": f"Opening {name}"}


def dispatch(text, workflows, *, busy=False, require_wake=False, awake=False, opener=None):
    command = resolve(text, workflows, require_wake=require_wake, awake=awake)
    if command["kind"] == "open_app":
        if busy:
            return {"kind": "error", "message": "Stop recording or wait for replay before opening an app by voice."}
        return launch_app(command["app"], opener)
    return command


class Listener:
    """Fixed local worker; no remote speech service, shell text, or audio files."""
    def __init__(self, names):
        if os.name != "nt":
            raise ValueError("The notch microphone is currently supported on Windows")
        self.events = queue.Queue(maxsize=64)
        self.finished = threading.Event()
        self.started = time.monotonic()
        self.awake_until = 0
        self.process = subprocess.Popen([
            sys.executable, "-u", "-m", "agent.ghost.desktop.voice_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=0x08000000)
        self.process.stdin.write(json.dumps({"names": names}, ensure_ascii=True) + "\n")
        self.process.stdin.close()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    value = json.loads(line)
                    self.events.put_nowait(value)
                except (ValueError, queue.Full):
                    continue
        finally:
            self.finished.set()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        self.process.stdout.close()
