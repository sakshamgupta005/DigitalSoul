import pytest
from pathlib import Path
from agent.ghost.desktop.synthesizer import synthesize_workflow
from agent.ghost.desktop.catalog import Catalog
from agent.ghost.desktop.voice import dispatch, resolve


def test_synthesize_shopify_workflow():
    prompt = "Add product on Shopify: Minimalist Bamboo Keyboard $89.99"
    result = synthesize_workflow(prompt)
    assert result["ok"] is True
    assert result["kind"] == "shopify"
    assert "Keyboard" in result["product"]["title"] or "Bamboo" in result["product"]["title"]
    assert float(result["product"]["price"]) == 89.99
    assert "admin.shopify.com" in result["product"]["store_url"]


def test_synthesize_excel_workflow():
    prompt = "Create an Excel spreadsheet for Monthly Expenses and formulas"
    result = synthesize_workflow(prompt)
    assert result["ok"] is True
    assert result["kind"] == "excel"
    assert "Expenses" in result["excel"]["topic"]
    assert len(result["excel"]["headers"]) >= 3
    assert len(result["excel"]["rows"]) >= 1


def test_synthesize_whatsapp_workflow():
    prompt = "Send a WhatsApp message to Alex saying I will arrive at 7pm tonight"
    result = synthesize_workflow(prompt)
    assert result["ok"] is True
    assert result["kind"] == "whatsapp"
    assert result["recipient"] == "Alex"
    assert "7pm tonight" in result["message"]
    assert result["flow"]["format"] == "digitalsoul.whatsapp-flow.v1"


def test_synthesize_web_search_workflow(tmp_path):
    catalog = Catalog(tmp_path / "workflows.sqlite3")
    prompt = "Open Edge and search for AI autonomous agents"
    result = synthesize_workflow(prompt, catalog=catalog)
    assert result["ok"] is True
    assert result["kind"] == "workflow_created"
    assert len(result["steps"]) == 2
    assert result["steps"][0]["kind"] == "open_app"
    assert result["steps"][1]["kind"] == "open_url"
    assert "search" in result["steps"][1]["url"]

    # Verify saved in catalog
    saved = catalog.get(result["id"])
    assert saved is not None
    assert saved["name"] == result["name"]


def test_synthesize_desktop_notepad(tmp_path):
    catalog = Catalog(tmp_path / "workflows.sqlite3")
    prompt = "Open Notepad and type daily standup summary"
    result = synthesize_workflow(prompt, catalog=catalog)
    assert result["ok"] is True
    assert result["kind"] == "workflow_created"
    assert any(s["kind"] == "open_app" and "notepad" in s["app"].lower() for s in result["steps"])
    assert any(s["kind"] == "set_text" for s in result["steps"])


def test_synthesize_validation_errors():
    with pytest.raises(ValueError, match="Please provide a workflow description"):
        synthesize_workflow("")

    with pytest.raises(ValueError, match="must be 1024 characters or less"):
        synthesize_workflow("a" * 1025)


def test_voice_dispatch_add_workflow(tmp_path):
    catalog = Catalog(tmp_path / "workflows.sqlite3")
    spoken = "Hey Jarvis, add workflow send WhatsApp to Mom saying love you"
    cmd = resolve(spoken, [])
    assert cmd["kind"] == "add_workflow"
    assert "Mom" in cmd["prompt"] or "whatsapp" in cmd["prompt"].lower()

    res = dispatch(spoken, [], catalog=catalog)
    assert res["kind"] == "whatsapp"
    assert res["recipient"] == "Mom"
    assert "love you" in res["message"]
