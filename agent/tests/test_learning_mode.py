"""Tests for continuous multi-episode learning mode."""
from pathlib import Path
import tempfile
import pytest

from agent.ghost.desktop.learning_mode import LearningEngine, step_to_token, extract_parameter_slots


def test_step_to_token_mapping():
    assert step_to_token({"kind": "open_app", "path": "C:\\Windows\\explorer.exe"}) == "explorer.exe:open"
    assert step_to_token({"kind": "open_url", "path": "brave.exe", "url": "https://www.youtube.com/watch?v=123"}) == "brave.exe:open_url:youtube.com"
    assert step_to_token({"kind": "set_text", "path": "EXCEL.EXE", "label": "Cell B2", "target": {"control_type": "DataItem"}}) == "excel.exe:set_text:DataItem:cell"
    assert step_to_token({"kind": "set_text", "path": "explorer.exe", "label": "Search Box", "target": {"control_type": "Edit"}}) == "explorer.exe:set_text:Edit:search"
    assert step_to_token({"kind": "click", "path": "WhatsApp.exe", "label": "Send", "target": {"control_type": "Button"}}) == "whatsapp.exe:click:Button:confirm"
    assert step_to_token({"kind": "press_enter", "path": "cmd.exe"}) == "cmd.exe:enter"


def test_multi_episode_pattern_learning_and_reinforcement():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "learning.sqlite3"
        engine = LearningEngine(db_path)

        # Demonstration 1: Open Explorer, search "invoices", select first file
        demo1_steps = [
            {"kind": "open_app", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "set_text", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "Search Box", "target": {"control_type": "Edit"}, "value": "invoices"},
            {"kind": "press_enter", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "click", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "invoices_2026.pdf", "target": {"control_type": "ListItem"}},
        ]

        res1 = engine.record_episode(demo1_steps, name="Find Invoices")
        assert res1["action"] == "observed"
        assert len(engine.list_patterns()) == 0

        # Demonstration 2: Open Explorer, search "receipts", select another file
        demo2_steps = [
            {"kind": "open_app", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "set_text", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "Search Box", "target": {"control_type": "Edit"}, "value": "receipts"},
            {"kind": "press_enter", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "click", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "receipts_oct.pdf", "target": {"control_type": "ListItem"}},
        ]

        res2 = engine.record_episode(demo2_steps, name="Find Receipts")
        assert res2["action"] == "discovered"
        assert res2["support"] == 2
        assert res2["alignment"] >= 0.75
        pat_id = res2["pattern_id"]

        patterns = engine.list_patterns()
        assert len(patterns) == 1
        pat = patterns[0]
        assert pat["support"] == 2
        # Variable parameter slots should have been extracted
        param_names = [p["name"] for p in pat["parameters"]]
        assert any("search" in name or "text" in name for name in param_names)

        # Demonstration 3: Same routine again with "payroll"
        demo3_steps = [
            {"kind": "open_app", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "set_text", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "Search Box", "target": {"control_type": "Edit"}, "value": "payroll"},
            {"kind": "press_enter", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe"},
            {"kind": "click", "path": "C:\\Windows\\explorer.exe", "app": "explorer.exe", "label": "payroll_summary.pdf", "target": {"control_type": "ListItem"}},
        ]

        res3 = engine.record_episode(demo3_steps, name="Find Payroll")
        assert res3["action"] == "reinforced"
        assert res3["support"] == 3

        # Test parameter adaptation
        pat = engine.get_pattern(pat_id)
        assert pat["support"] == 3
        # Find the text search param
        text_param = next(p for p in pat["parameters"] if p["kind"] == "text")
        adapted = engine.adapt_pattern_steps(pat_id, {text_param["name"]: "annual_tax_filing"})
        assert adapted[1]["value"] == "annual_tax_filing"

        # Test cleanup
        engine.delete_pattern(pat_id)
        assert len(engine.list_patterns()) == 0
        engine.close()
