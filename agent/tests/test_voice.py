import pytest

from agent.ghost.desktop.voice import dispatch, launch_app, resolve


WORKFLOWS = [{"id": "spotify", "name": "Open Spotify"}, {"id": "hello", "name": "Say hello"}]


def test_recorded_object_targets_do_not_crash_spoken_command_lookup():
    flows = [{"id": "recorded", "name": "Office check", "steps": [
        {"target": {"name": "Inventory products", "control_type": "Edit"}},
        {"target": {"nested": {"value": "ignored"}}}]}]
    assert resolve("Hey Jarvis inventory products", flows)["id"] == "recorded"
    assert resolve("Hey Jarvis completely unrelated words", flows)["kind"] == "unknown"


def test_voice_requires_wake_and_exact_saved_name():
    assert resolve("open spotify", WORKFLOWS, require_wake=True)["kind"] == "ignored"
    assert resolve("Hey Jarvis, do Open Spotify!", WORKFLOWS, require_wake=True) == {
        "kind": "workflow", "id": "spotify", "name": "Open Spotify"}
    assert resolve("Hey Jarvis delete everything", WORKFLOWS, require_wake=True)["kind"] == "unknown"


def test_wake_followup_and_sleep():
    assert resolve("Hey Jarvis, wake up.", WORKFLOWS, require_wake=True)["kind"] == "wake"
    assert resolve("run Say hello", WORKFLOWS, require_wake=True, awake=True)["id"] == "hello"
    assert resolve("Hey Jarvis stop listening", WORKFLOWS, require_wake=True)["kind"] == "sleep"


def test_ambiguous_name_does_not_choose_first():
    items = WORKFLOWS + [{"id": "other", "name": "Open Spotify!"}]
    assert resolve("Hey Jarvis do Open Spotify", items)["kind"] == "ambiguous"


@pytest.mark.parametrize("text", [None, 4, "x" * 513])
def test_invalid_command(text):
    with pytest.raises(ValueError):
        resolve(text, WORKFLOWS)


def test_dictation_only_selects_no_execution_grant():
    result = resolve("Hey Jarvis, do workflow Say hello.", WORKFLOWS)
    assert result == {"kind": "workflow", "id": "hello", "name": "Say hello"}


def test_workflow_suffix():
    assert resolve("Hey Jarvis do Say hello workflow", WORKFLOWS)["id"] == "hello"
    assert resolve("Hey Jarvis morning workflow", [{"id": "a", "name": "Morning workflow"}])["id"] == "a"


def test_conversational_asks_and_intent_understanding():
    flows = [
        {"id": "dl", "name": "Organize Files", "intent": "Browse downloads and check files in File Explorer", "apps": ["explorer.exe"]},
        {"id": "msg", "name": "Send Update", "intent": "Open WhatsApp and send message to team", "apps": ["WhatsApp.exe"]},
    ]
    # Conversational phrasing with wake word
    assert resolve("Hey Jarvis, please do Open Spotify", WORKFLOWS)["id"] == "spotify"
    assert resolve("Hey Jarvis, can you do Say hello please", WORKFLOWS)["id"] == "hello"
    # Intent-based ask matching
    assert resolve("Hey Jarvis, check files in downloads", flows)["id"] == "dl"
    assert resolve("Hey Jarvis, send a message to team on whatsapp", flows)["id"] == "msg"



def test_saved_workflow_voice_lookup_over_real_stdio(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from agent.ghost.desktop.catalog import Catalog

    data = tmp_path / ".ghost" / "notch"
    data.mkdir(parents=True)
    catalog = Catalog(data / "workflows.sqlite3")
    item = catalog.save({"name": "Morning music", "steps": [], "apps": ["Spotify.exe"]})
    catalog.close()
    requests = [
        {"id": "1", "method": "voice_command", "payload": {"text": "Hey Jarvis do Morning music"}},
        {"id": "2", "method": "list"},
        {"method": "shutdown"},
    ]
    result = subprocess.run([sys.executable, "-m", "agent.ghost.desktop.notch_bridge", str(tmp_path)],
        input="".join(json.dumps(r) + "\n" for r in requests), capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[2], timeout=15)
    assert result.returncode == 0, result.stderr
    replies = {r["id"]: r for r in map(json.loads, result.stdout.splitlines()) if "id" in r}
    assert replies["1"]["result"] == {"kind": "workflow", "id": item["id"], "name": "Morning music"}
    assert replies["2"]["result"]["jobs"] == []  # No execution or approval from speech.


def test_worker_missing_model_reports_setup_without_opening_mic(monkeypatch, capsys, tmp_path):
    import io
    import json
    import sys
    from types import SimpleNamespace
    from agent.ghost.desktop import voice_worker

    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=None))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{}\n'))
    monkeypatch.setenv("DIGITALSOUL_WHISPER_MODEL", str(tmp_path / "absent"))
    voice_worker.main()
    result = json.loads(capsys.readouterr().out)
    assert result["kind"] == "error"
    assert "--download" in result["message"]


@pytest.mark.parametrize("phrase", ["Open WhatsApp", "Hey Jarvis open whats app", "Please launch WhatsApp please", "start what's app"])
def test_open_whatsapp_uses_fixed_protocol(phrase):
    launches = []
    result = dispatch(phrase, [], opener=launches.append)
    assert result["kind"] == "app_opened"
    assert launches == ["whatsapp:"]


def test_app_launch_requires_wake_outside_mic_command_window():
    launches = []
    assert dispatch("open whatsapp", [], require_wake=True, opener=launches.append)["kind"] == "ignored"
    assert launches == []
    assert dispatch("open whatsapp", [], require_wake=True, awake=True, opener=launches.append)["kind"] == "app_opened"
    assert launches == ["whatsapp:"]


def test_app_command_does_not_allow_arbitrary_shell_or_send():
    launches = []
    for text in ("open powershell", "open whatsapp and send hello", "open whatsapp; calc", "open C:/evil.exe"):
        assert dispatch(text, [], opener=launches.append)["kind"] == "unknown"
    assert dispatch("open whatsapp", [], busy=True, opener=launches.append)["kind"] == "error"
    assert launches == []
    with pytest.raises(ValueError):
        launch_app("powershell", launches.append)


def test_explicit_workflow_prefix_keeps_review_required():
    assert dispatch("do Open Spotify", WORKFLOWS, opener=lambda _: pytest.fail("Unexpected launch"))["kind"] == "workflow"


def test_protocol_failure_is_reported():
    def missing(_):
        raise OSError("No protocol handler")
    assert launch_app("whatsapp", missing)["code"] == "app_unavailable"


def test_launch_permission_failure_is_distinct_from_missing_app():
    def denied(_):
        raise PermissionError("Access denied")
    assert launch_app("whatsapp", denied)["code"] == "app_launch_denied"
