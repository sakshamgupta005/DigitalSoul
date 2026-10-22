"""Local, opt-in Windows speech. Transcripts select workflows, never approve effects."""
import json
import os
import queue
import re
import subprocess
import threading
import time
import sys


def normalized(text):
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "with", "from", "by", "of",
    "and", "or", "my", "me", "this", "that", "it", "please", "can", "could", "you",
    "i", "want", "need", "like", "would", "just", "now", "hey", "jarvis", "workflow", "routine"
}

CONVERSATIONAL_PREFIXES = (
    "could you please ", "can you please ", "would you please ", "please can you ",
    "could you ", "can you ", "would you ", "i want you to ", "i want to ", "i need to ",
    "help me to ", "help me ", "please ",
    "do the workflow ", "run the workflow ", "execute the workflow ", "start the workflow ",
    "do workflow ", "run workflow ", "execute workflow ", "start workflow ",
    "do the ", "run the ", "execute the ", "start the ", "open the ", "launch the ",
    "do ", "run ", "execute ", "start ", "open ", "launch ", "perform ", "find "
)


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
        return {"kind": "wake", "message": "I'm listening. Say a saved workflow name."}
    if command in ("stop listening", "go to sleep"):
        return {"kind": "sleep"}
    candidates = {command}
    for prefix in ("do the workflow ", "run the workflow ", "do workflow ", "run workflow ", "do ", "run "):
        if command.startswith(prefix):
            candidates.add(command[len(prefix):])
            break
    if command.endswith(" workflow"):
        candidates.add(command[:-len(" workflow")])

    # Strip conversational prefixes to extract intent core
    core = command
    changed = True
    while changed:
        changed = False
        for prefix in CONVERSATIONAL_PREFIXES:
            if core.startswith(prefix):
                core = core[len(prefix):].strip()
                changed = True
                break
    for suffix in (" workflow", " routine", " please", " now"):
        if core.endswith(suffix):
            core = core[:-len(suffix)].strip()
    if core:
        candidates.add(core)

    # 1. Exact name match (highest precedence)
    matches = [w for w in workflows if normalized(w.get("name", "")) in candidates]
    if len(matches) > 1:
        return {"kind": "ambiguous", "message": "Several workflows have that name. Choose one in Workflows."}
    if matches:
        return {"kind": "workflow", "id": matches[0]["id"], "name": matches[0]["name"]}

    # 2. Score candidate workflows by intent, substrings, and token overlap
    scores = []
    query_tokens = [t for t in core.split() if t not in STOP_WORDS]
    if not query_tokens:
        query_tokens = core.split()

    for w in workflows:
        w_name = normalized(w.get("name", ""))
        w_intent = normalized(w.get("intent", ""))
        score = 0

        # High confidence: intent matches candidate or core
        if w_intent and (w_intent in candidates or w_intent == core):
            score = 90
        # Substring in name or intent
        elif w_name and (w_name in command or core in w_name):
            score = 80
        elif w_intent and (w_intent in command or core in w_intent):
            score = 75
        else:
            # Token overlap against name, intent, apps, parameters, steps
            target_tokens = set(w_name.split())
            if w_intent:
                target_tokens.update(w_intent.split())
            for app in w.get("apps", []):
                target_tokens.update(re.sub(r"[^\w\s]", " ", app.casefold().replace(".exe", "")).split())
            for p in w.get("parameters", []):
                if isinstance(p, dict):
                    target_tokens.update(re.sub(r"[^\w\s]", " ", p.get("name", "").casefold()).split())
            for s in w.get("steps", []):
                if isinstance(s, dict):
                    if s.get("target"):
                        target_tokens.update(re.sub(r"[^\w\s]", " ", s["target"].casefold()).split())
                    if s.get("explanation"):
                        target_tokens.update(re.sub(r"[^\w\s]", " ", s["explanation"].casefold()).split())

            target_tokens -= STOP_WORDS
            if query_tokens and target_tokens:
                overlap = sum(1 for t in query_tokens if t in target_tokens)
                ratio = overlap / len(query_tokens)
                if ratio >= 0.5:
                    score = int(40 + ratio * 30)

        if score >= 60:
            scores.append((score, w))

    if scores:
        scores.sort(key=lambda item: item[0], reverse=True)
        top_score = scores[0][0]
        top_items = [w for s, w in scores if s == top_score]
        if len(top_items) > 1:
            return {"kind": "ambiguous", "message": "Several workflows match that ask. Choose one in Workflows."}
        return {"kind": "workflow", "id": top_items[0]["id"], "name": top_items[0]["name"]}

    return {"kind": "unknown", "message": "No exact saved workflow matches. Say its name as shown in Workflows."}


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
