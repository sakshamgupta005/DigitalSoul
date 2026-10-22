"""Local encrypted recordings. Raw desktop evidence never grants execution trust."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..safety.credentials import protect


def short(text, limit=34):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def understand_steps(steps, flow=None, name=None):
    """Derive semantic goal, variable parameters, and contextual step explanations from observed actions."""
    if flow:
        recipient = flow.get("recipient", "contact")
        msg = flow.get("message", "")
        send_kind = flow.get("send", {}).get("kind", "Enter") if isinstance(flow.get("send"), dict) else "Enter"
        return {
            "intent": f"Send WhatsApp message to {recipient}",
            "summary": f"Sent \"{short(msg, 28)}\" to {recipient} via WhatsApp",
            "parameters": [
                {"name": "recipient", "value": recipient, "type": "string", "source": "contact_selection"},
                {"name": "message", "value": msg, "type": "string", "source": "message_input"}
            ],
            "explanations": [
                "Opened WhatsApp messenger",
                f"Searched for contact '{flow.get('search', recipient)}'",
                f"Selected verified chat with '{recipient}'",
                f"Composed message: \"{msg}\"",
                f"Dispatched message via {send_kind}"
            ]
        }

    explanations = []
    parameters = []
    apps_touched = []
    files_selected = []
    text_entries = []
    folders_visited = []

    for step in steps:
        kind = step.get("kind")
        raw_app = step.get("app") or "App"
        app = "File Explorer" if raw_app.lower() == "explorer.exe" else raw_app.replace(".exe", "").capitalize()
        if app not in apps_touched:
            apps_touched.append(app)
        title = (step.get("title") or "").strip()
        label = (step.get("label") or "").strip()
        value = (step.get("value") or "").strip()

        if kind == "open_app":
            if "explorer" in raw_app.lower() and title:
                folders_visited.append(title)
                explanations.append(f"Navigated to '{title}' folder in File Explorer")
            elif title:
                explanations.append(f"Switched to {app} ({title})")
            else:
                explanations.append(f"Opened {app}")

        elif kind == "click":
            target = step.get("target") or {}
            c_type = target.get("control_type", "")
            if c_type in ("ListItem", "DataItem", "TreeItem") or ("." in label and not label.endswith(".")):
                files_selected.append(label)
                explanations.append(f"Selected item '{label}'")
            elif label.lower() in ("send", "submit", "save", "ok", "confirm", "next", "apply"):
                explanations.append(f"Confirmed action by clicking '{label}'")
            elif label.lower() in ("close", "cancel", "back", "exit"):
                explanations.append(f"Dismissed / navigated back via '{label}'")
            elif label and label != "Text field":
                explanations.append(f"Clicked '{label}' in {app}")
            else:
                explanations.append(f"Focused text field in {app}")

        elif kind == "set_text":
            text_entries.append((label, value))
            if "search" in label.lower() or "search" in str(step.get("target", {})).lower():
                explanations.append(f"Searched for '{value}'")
                parameters.append({"name": "query", "value": value, "type": "string", "source": "search"})
            elif "address" in label.lower() or "http" in value.lower() or "www." in value.lower():
                explanations.append(f"Navigated URL to '{value}'")
                parameters.append({"name": "url", "value": value, "type": "url", "source": "navigation"})
            else:
                explanations.append(f"Typed \"{value}\" into {label or 'input'}")
                param_name = label.lower().replace(" ", "_") if label and label != "Text field" else f"input_{len(parameters)+1}"
                parameters.append({"name": param_name, "value": value, "type": "string", "source": "typing"})

        elif kind == "press_enter":
            explanations.append("Submitted input with Enter ⏎")
        else:
            explanations.append(f"Performed {kind} in {app}")

    # Synthesize intent
    if "File Explorer" in apps_touched:
        if files_selected and folders_visited:
            intent = f"Selected '{files_selected[-1]}' in {folders_visited[-1]} folder"
        elif files_selected:
            intent = f"Selected '{files_selected[-1]}' in File Explorer"
        elif folders_visited:
            intent = f"Browsed '{folders_visited[-1]}' in File Explorer"
        else:
            intent = name or "File Explorer routine"
    elif text_entries and any("search" in l.lower() for l, _ in text_entries):
        searches = [v for l, v in text_entries if "search" in l.lower()]
        intent = f"Searched for '{searches[-1]}' in {', '.join(apps_touched)}"
    elif text_entries and any("url" in p.get("type", "") for p in parameters):
        urls = [p["value"] for p in parameters if p.get("type") == "url"]
        intent = f"Navigated to {urls[-1]}"
    elif files_selected:
        intent = f"Interacted with '{files_selected[-1]}' in {', '.join(apps_touched)}"
    elif apps_touched:
        intent = f"Automated workflow in {', '.join(apps_touched)}"
    else:
        intent = name or "Recorded routine"

    if files_selected:
        parameters.append({"name": "target_item", "value": files_selected[-1], "type": "file_or_folder", "source": "selection"})
    if folders_visited:
        parameters.append({"name": "folder", "value": folders_visited[-1], "type": "path", "source": "location"})

    return {
        "intent": intent,
        "parameters": parameters,
        "explanations": explanations,
        "apps": apps_touched
    }


class Catalog:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS recordings (id TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.db.commit()

    def save(self, recording):
        value = dict(recording)
        value.setdefault("id", str(uuid.uuid4()))
        value.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if not isinstance(value.get("name"), str) or not 1 <= len(value["name"].strip()) <= 80:
            raise ValueError("Use a recording name of 1–80 characters")
        if not isinstance(value.get("steps"), list) or len(value["steps"]) > 500:
            raise ValueError("Recording exceeds the 500-step limit")
        value["name"] = value["name"].strip()
        data = protect(json.dumps(value, ensure_ascii=False).encode())
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recordings VALUES (?,?)", (value["id"], data))
        return value

    def get(self, recording_id):
        row = self.db.execute("SELECT payload FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not row:
            raise ValueError("Recording not found")
        return json.loads(protect(row[0], decrypt=True))

    def list(self):
        """Library rows: metadata plus a short, local-only summary of what the recording did."""
        result = []
        for row in self.db.execute("SELECT id FROM recordings"):
            value = self.get(row[0])
            result.append({key: value.get(key) for key in
                           ("id", "name", "created_at", "apps", "duration", "replay_error")}
                          | {"step_count": len(value["steps"]), "can_run": bool(value.get("flow")),
                             "recipient": (value.get("flow") or {}).get("recipient"),
                             "used": list(dict.fromkeys(step["app"] for step in value["steps"] if step.get("app"))),
                             "summary": summarize(value)})
        return sorted(result, key=lambda r: r["created_at"], reverse=True)

    def delete(self, recording_id):
        with self.db:
            self.db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))

    def close(self):
        self.db.close()
