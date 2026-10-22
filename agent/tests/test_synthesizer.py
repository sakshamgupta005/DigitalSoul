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


@pytest.mark.parametrize("prompt", ["Open Edge and search for AI autonomous agents", "Open Notepad and type daily standup summary", "Prepare a completely unknown task"])
def test_unknown_task_needs_demonstration_instead_of_invented_targets(tmp_path, prompt):
    catalog = Catalog(tmp_path / "workflows.sqlite3")
    try:
        result = synthesize_workflow(prompt, catalog=catalog)
        assert result["kind"] == "unknown"
        assert catalog.list() == []
    finally:
        catalog.close()


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
