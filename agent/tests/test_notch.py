"""Notch persistence, bounded recording compilation, replay guards, and real stdio RPC."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent.ghost.desktop.catalog import Catalog
from agent.ghost.desktop.flow import Capture, FlowAdapter, validate
from agent.ghost.desktop.session import matches_send_snapshot


def spec(name):
    return {"control_type": "Edit" if name in ("search", "compose") else "Text",
            "automation_id": name, "name": name, "ancestors": []}


def recorded():
    capture = Capture("Saksham Gupta", "Saksham Gupta", "chigga", "hello")
    capture.flow.update(executable=r"C:\Apps\WhatsApp.exe", app_id=None)
    capture.field(spec("search"), "Saksham Gupta")
    capture.click(spec("contact"), "chigga", "Text")
    identity = spec("header")
    identity["name"] = "chigga"
    capture.field(spec("compose"), "hello", identity)
    capture.enter()
    validate(capture.flow)
    return capture.flow


def test_capture_distinguishes_search_identity_from_chat_alias():
    value = recorded()
    assert value["recipient"] == "Saksham Gupta"
    assert value["header"]["name"] == "chigga"
    assert [action["kind"] for action in value["actions"]] == ["search", "select", "compose"]
    assert value["send"]["kind"] == "enter"


def test_incomplete_or_injected_flow_cannot_replay():
    for mutate in (lambda f: f["actions"].pop(1),
                   lambda f: f["actions"].insert(0, {"kind": "shell"}),
                   lambda f: f.update(executable=r"C:\Apps\powershell.exe"),
                   lambda f: f["header"].update(name="Other Contact")):
        value = copy.deepcopy(recorded())
        mutate(value)
        with pytest.raises(ValueError):
            validate(value)


def test_composing_without_selecting_contact_is_rejected():
    capture = Capture("Person", "Person", "Person", "hello")
    with pytest.raises(ValueError, match="Click"):
        capture.field(spec("compose"), "hello", spec("Person"))


class Field:
    def __init__(self, value=""):
        self.value = value
        self.presses = []

    def get_value(self):
        return self.value

    def set_edit_text(self, value):
        self.value = value

    def set_focus(self):
        pass

    def type_keys(self, value, **kwargs):
        self.presses.append(value)


class Native:
    def __init__(self):
        self.fields = {"search": Field(), "compose": Field()}
        self.selected = False
        self.opened = False

    def open(self, flow):
        self.opened = True

    def active(self, path):
        return self, 1, path

    def click(self, flow, target):
        assert target["automation_id"] == "contact"
        self.selected = True


def fake_find(native, target):
    if target["automation_id"] == "header":
        if not native.selected:
            raise ValueError("Wrong chat")
        return object()
    if target["automation_id"] == "contact":
        return object()
    return native.fields[target["automation_id"]]


def test_replay_opens_searches_selects_then_composes(monkeypatch):
    native = Native()
    monkeypatch.setattr("agent.ghost.desktop.flow.find", fake_find)
    adapter = FlowAdapter(native)
    adapter.prepare(recorded(), "hello {literal} + text")
    assert native.opened and native.selected
    assert native.fields["search"].value == "Saksham Gupta"
    assert native.fields["compose"].value == "hello {literal} + text"
    assert not native.fields["compose"].presses
    adapter.send(recorded(), "hello {literal} + text")
    assert native.fields["compose"].presses == ["{ENTER}"]


def test_changed_draft_cannot_be_sent(monkeypatch):
    native = Native()
    monkeypatch.setattr("agent.ghost.desktop.flow.find", fake_find)
    adapter = FlowAdapter(native)
    adapter.prepare(recorded(), "hello")
    native.fields["compose"].value = "different draft"
    with pytest.raises(ValueError):
        adapter.send(recorded(), "hello")
    assert not native.fields["compose"].presses


def test_existing_draft_is_not_overwritten(monkeypatch):
    native = Native()
    native.fields["compose"].value = "unfinished work"
    monkeypatch.setattr("agent.ghost.desktop.flow.find", fake_find)
    with pytest.raises(ValueError, match="draft"):
        FlowAdapter(native).prepare(recorded(), "hello")
    assert native.fields["compose"].value == "unfinished work"


def test_latched_guard_stops_before_opening():
    native = Native()
    with pytest.raises(ValueError, match="stopped"):
        FlowAdapter(native, guard=lambda: False).prepare(recorded(), "hello")
    assert not native.opened


def test_catalog_preserves_recordings_across_restart_and_deletion(tmp_path):
    path = tmp_path / "workflows.sqlite3"
    first = Catalog(path)
    item = first.save({"name": "Say hello", "apps": ["WhatsApp.exe"], "duration": 12,
                       "steps": [{"kind": "set_text", "value": "hello"}], "flow": recorded()})
    first.close()
    second = Catalog(path)
    try:
        assert second.list()[0]["can_run"]
        assert second.get(item["id"])["steps"][0]["value"] == "hello"
        second.delete(item["id"])
        assert second.list() == []
    finally:
        second.close()


def test_raw_steps_are_not_runnable_by_default(tmp_path):
    catalog = Catalog(tmp_path / "raw.sqlite3")
    try:
        catalog.save({"name": "Notepad steps", "steps": [{"kind": "click"}], "flow": None})
        assert catalog.list()[0]["can_run"] is False
    finally:
        catalog.close()


def test_stdio_backend_hello_list_and_invalid_start(tmp_path):
    requests = [{"id": "1", "method": "hello"}, {"id": "2", "method": "list"},
                {"id": "3", "method": "start", "payload": {"name": ""}}, {"method": "shutdown"}]
    result = subprocess.run([sys.executable, "-u", "-m", "agent.ghost.desktop.notch_bridge", str(tmp_path)],
                            input="".join(json.dumps(r) + "\n" for r in requests), text=True,
                            capture_output=True, timeout=15, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr
    replies = {r["id"]: r for r in map(json.loads, result.stdout.splitlines()) if "id" in r}
    assert replies["1"]["ok"]
    assert replies["2"]["result"] == {"workflows": [], "jobs": []}
    assert replies["3"]["ok"] is False


def test_send_observation_is_process_bound_and_expires():
    snapshot = {"pid": 42, "time": 10, "bounds": [10, 10, 30, 30]}
    event = {"kind": "click", "pid": 42, "time": 11, "point": [20, 20]}
    assert matches_send_snapshot(snapshot, event)
    assert not matches_send_snapshot(snapshot, event | {"pid": 99})
    assert not matches_send_snapshot(snapshot, event | {"time": 14})
    assert not matches_send_snapshot(snapshot, event | {"point": [100, 100]})


def test_whatsapp_root_exe_and_case_insensitive_matching():
    from agent.ghost.desktop.whatsapp import is_whatsapp
    assert is_whatsapp(r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.2640.100.0_x64__cv1g1gvanyjgm\WhatsApp.Root.exe")
    assert is_whatsapp("WhatsApp.exe")

    capture = Capture("vansh arora", "vansh arora", "vansh arora", "hello")
    capture.flow.update(executable=r"C:\Program Files\WindowsApps\5319275A.WhatsAppDesktop_2.2640.100.0_x64__cv1g1gvanyjgm\WhatsApp.Root.exe", app_id="5319275A.WhatsAppDesktop")
    capture.field(spec("search"), "Vansh Arora")
    capture.click(spec("contact"), "Vansh Arora", "ListItem")
    identity = spec("header")
    identity["name"] = "Vansh Arora"
    capture.field(spec("compose"), "happy birthday", identity)
    capture.enter()
    validate(capture.flow)
    assert capture.flow["message"] == "happy birthday"
    assert capture.flow["chat_title"] == "Vansh Arora"
    assert capture.flow["send"]["kind"] == "enter"


def test_session_allows_whatsapp_root_exe_automatically():
    from agent.ghost.desktop.session import Session
    # Using monkeypatch/try to avoid WinEvents hook error in headless test if needed
    try:
        session = Session({"name": "Test Routine", "apps": ["WhatsApp.exe"], "recipient": "vansh arora"})
        session.finish()
        assert "whatsapp.root.exe" in session.allowed
        assert "whatsapp.exe" in session.allowed
    except Exception as exc:
        if "Windows input event recording" in str(exc) or "SetWindowsHookEx" in str(exc):
            pass
        else:
            raise



def test_capture_infers_contact_and_message_from_demonstration():
    capture = Capture()
    capture.flow.update(executable=r"C:\Apps\WhatsApp.exe", app_id=None)
    for typed in ("v", "van", "vansh"):
        capture.field(spec("search"), typed)
    capture.click(spec("other"), "Archived", "Button")
    capture.click(spec("contact"), "Vansh", "ListItem")
    identity = spec("header")
    identity["name"] = "Vansh"
    capture.field(spec("compose"), "b", identity)
    capture.field(spec("compose"), "birthday postponed")
    capture.enter()
    validate(capture.flow)
    assert capture.flow["search"] == "vansh"
    assert capture.flow["recipient"] == capture.flow["chat_title"] == "Vansh"
    assert capture.flow["message"] == "birthday postponed"
    assert [action["kind"] for action in capture.flow["actions"]] == ["search", "select", "compose"]


class WebField:
    def __init__(self, name, value, text):
        self.element_info = type("Info", (), {"name": name})()
        self.value, self.iface_text = value, type("Text", (), {})()
        self.iface_text.DocumentRange = type("Range", (), {"GetText": lambda _, n: text})()

    def get_value(self):
        return self.value


def test_browser_field_text_reads_rich_text_and_ignores_placeholder():
    from agent.ghost.desktop.session import field_text
    assert field_text(WebField("Ask ChatGPT", "", "what is uia\n"), browser=True) == "what is uia"
    assert field_text(WebField("Ask ChatGPT", "Ask ChatGPT", "Ask ChatGPT"), browser=True) == ""
    assert field_text(WebField("Add a comment…", "", "Add a comment…"), browser=True) == ""
    assert field_text(WebField("Search", "cats", "ignored"), browser=True) == "cats"
    assert field_text(WebField("Type a message", "", "Type a message"), browser=False) == ""
