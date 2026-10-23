"""Local encrypted recordings. Raw desktop evidence never grants execution trust."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from ..safety.credentials import protect


def short(text, limit=34):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def clock(seconds):
    """A video position as m:ss, or h:mm:ss for long videos."""
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def understand_steps(steps, flow=None, name=None):
    """Derive semantic goal, variable parameters, and contextual step explanations from observed actions."""
    from .session import clean_steps
    steps = clean_steps(steps)
    if flow and (flow.get("format") == "digitalsoul.excel-flow.v1" or flow.get("kind") == "excel"):
        wb = flow.get("workbook", "Workbook")
        sheet = flow.get("sheet", "Sheet1")
        cells = flow.get("cells", [])
        return {
            "intent": f"Update Excel workbook '{wb}' ({len(cells)} cells)",
            "summary": f"Populated {len(cells)} cells in Excel sheet '{sheet}'",
            "parameters": [
                {"name": "workbook", "value": wb, "type": "string", "source": "workbook_selection"},
                {"name": "sheet", "value": sheet, "type": "string", "source": "sheet_selection"},
                {"name": "cells_count", "value": str(len(cells)), "type": "number", "source": "cell_updates"}
            ],
            "explanations": [
                f"Connected to Excel workbook '{wb}'",
                f"Selected sheet '{sheet}'",
                f"Populated {len(cells)} cells with updated values",
                "Applied cell values and verified updates"
            ]
        }

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
        app = {"explorer.exe": "File Explorer", "calculatorapp.exe": "Calculator", "spotify.exe": "Spotify",
               "whatsapp.root.exe": "WhatsApp", "whatsapp.exe": "WhatsApp", "msedge.exe": "Edge"}.get(
            raw_app.lower(), raw_app.replace(".exe", "").capitalize())
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

        elif kind == "open_url":
            resume = step.get("resume")
            page = f"“{title}”" if title else (label or step.get("url"))
            explanations.append(f"Opened {page}" + (f" at {clock(resume)}" if resume else ""))
            parameters.append({"name": "url", "value": step.get("url"), "type": "url", "source": "browser"})

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
    elif any(p.get("type") == "url" for p in parameters):
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


def summarize(value, limit=3):
    """The few things that tell recordings apart: who and what for a send, else typed text and clicks."""
    flow = value.get("flow")
    if flow and (flow.get("format") == "digitalsoul.excel-flow.v1" or flow.get("kind") == "excel"):
        res = [{"kind": "to", "text": flow.get("workbook", "Excel")}]
        cells = flow.get("cells", [])
        if cells:
            res.append({"kind": "typed", "text": f"{len(cells)} cells: {cells[0].get('cell')}={short(cells[0].get('value', ''), 12)}"})
        return res
    if flow:
        return [{"kind": "to", "text": flow["recipient"]}, {"kind": "message", "text": short(flow["message"])}]
    result = []
    for step in value.get("steps", []):
        if step["kind"] == "open_url" and step.get("url"):
            resume = step.get("resume")
            text = short(step.get("title") or step.get("label") or step["url"], 30) + (f" @ {clock(resume)}" if resume else "")
            item = {"kind": "url", "text": text}
        elif step["kind"] == "set_text" and step.get("value"):
            item = {"kind": "typed", "text": short(step["value"])}
        elif step["kind"] == "click" and step.get("label") and step["label"] != "Text field":
            item = {"kind": "click", "text": short(step["label"], 26)}
        else:
            continue
        if item not in result:
            result.append(item)
        if len(result) == limit:
            break
    return result


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
        from .session import clean_steps
        if isinstance(value.get("steps"), list):
            value["steps"] = clean_steps(value["steps"])
        if not isinstance(value.get("steps"), list) or len(value["steps"]) > 500:
            raise ValueError("Recording exceeds the 500-step limit")
        value["name"] = value["name"].strip()
        value["understanding"] = understand_steps(value.get("steps", []), value.get("flow"), value.get("name"))
        data = protect(json.dumps(value, ensure_ascii=False).encode())
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recordings VALUES (?,?)", (value["id"], data))
        return value

    def get(self, recording_id):
        row = self.db.execute("SELECT payload FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not row:
            raise ValueError("Recording not found")
        val = json.loads(protect(row[0], decrypt=True))
        from .session import clean_steps
        if isinstance(val.get("steps"), list):
            val["steps"] = clean_steps(val["steps"])
        if "understanding" not in val or any(app in str(val.get("understanding", "")).lower() for app in ("searchhost", "svchost")):
            val["understanding"] = understand_steps(val.get("steps", []), val.get("flow"), val.get("name"))
        return val

    def list(self):
        """Library rows: metadata plus a short, local-only summary of what the recording did."""
        result = []
        for row in self.db.execute("SELECT id FROM recordings"):
            value = self.get(row[0])
            understanding = value.get("understanding") or understand_steps(value.get("steps", []), value.get("flow"), value.get("name"))
            steps = value.get("steps", [])
            from .session import is_system_host
            used = list(dict.fromkeys(step["app"] for step in steps if step.get("app") and not is_system_host(step)))
            result.append({key: value.get(key) for key in
                           ("id", "name", "created_at", "apps", "duration", "replay_error")}
                          | {"step_count": len(steps), "can_run": bool(value.get("flow")),
                             "recipient": (value.get("flow") or {}).get("recipient"),
                             "intent": understanding.get("intent"),
                             "understanding": understanding,
                             "used": used,
                             "summary": summarize(value)})
        return sorted(result, key=lambda r: r["created_at"], reverse=True)

    def delete(self, recording_id):
        with self.db:
            self.db.execute("DELETE FROM recordings WHERE id=?", (recording_id,))

    def close(self):
        self.db.close()
