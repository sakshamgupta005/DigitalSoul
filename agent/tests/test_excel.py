"""Tests for Microsoft Excel workflow automation, AI synthesis, and flow derivation."""

import json
import os
from pathlib import Path
import pytest

from agent.ghost.desktop.excel import (
    FORMAT,
    clean_cell_name,
    derive_excel_flow,
    generate_excel_data,
    execute_excel_workflow,
    inspect_active_excel,
    validate_excel_recipe,
)
from agent.ghost.desktop.catalog import Catalog, understand_steps, summarize


def test_clean_cell_name():
    assert clean_cell_name('"A1"') == "A1"
    assert clean_cell_name("'B12'") == "B12"
    assert clean_cell_name("Cell C4") == "C4"
    assert clean_cell_name("d9") == "D9"
    assert clean_cell_name("AA100") == "AA100"


def test_generate_excel_data_templates():
    # Expenses
    expenses = generate_excel_data({"topic": "Monthly Expenses"})
    assert expenses["sheet"] == "Expenses"
    assert "Cost" in expenses["headers"]
    assert "=SUM(" in expenses["formula"]
    assert "Cloud Compute Hosting" in expenses["rows"]

    # Sales
    sales = generate_excel_data({"topic": "Quarterly Sales Report"})
    assert sales["sheet"] == "Sales"
    assert "Invoice ID" in sales["headers"]
    assert "Acme Corp" in sales["rows"]
    assert "=SUM(" in sales["formula"]

    # Inventory
    inventory = generate_excel_data({"topic": "Warehouse Stock"})
    assert inventory["sheet"] == "Inventory"
    assert "SKU" in inventory["headers"]
    assert "WGT-001" in inventory["rows"]

    # Tasks
    tasks = generate_excel_data({"topic": "Sprint Tasks"})
    assert tasks["sheet"] == "Tasks"
    assert "Task ID" in tasks["headers"]


def test_derive_excel_flow_from_steps():
    steps = [
        {"kind": "open_app", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "title": "Budget.xlsx - Excel"},
        {"kind": "click", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "label": "Cell A1", "target": {"control_type": "DataItem", "name": "A1"}},
        {"kind": "set_text", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "label": "Cell A1", "value": "Revenue", "target": {"control_type": "DataItem", "name": "A1"}},
        {"kind": "click", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "label": "Cell B1", "target": {"control_type": "DataItem", "name": "B1"}},
        {"kind": "set_text", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "label": "Cell B1", "value": "5000", "target": {"control_type": "DataItem", "name": "B1"}},
    ]
    flow = derive_excel_flow(steps)
    assert flow is not None
    assert flow["format"] == FORMAT
    assert flow["workbook"] == "Budget.xlsx"
    assert len(flow["cells"]) == 2
    assert flow["cells"][0] == {"cell": "A1", "value": "Revenue"}
    assert flow["cells"][1] == {"cell": "B1", "value": "5000"}

    validate_excel_recipe(flow)


def test_catalog_understands_excel_workflow(tmp_path):
    catalog = Catalog(tmp_path / "catalog.sqlite3")
    flow = {
        "format": FORMAT,
        "kind": "excel",
        "workbook": "Q3_Financials.xlsx",
        "sheet": "Summary",
        "cells": [
            {"cell": "A1", "value": "Metric"},
            {"cell": "B1", "value": "Value"},
            {"cell": "A2", "value": "Net Profit"},
            {"cell": "B2", "value": "120000"}
        ]
    }
    steps = [
        {"kind": "open_app", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "title": "Q3_Financials.xlsx - Excel"},
        {"kind": "set_text", "app": "EXCEL.EXE", "path": r"C:\Program Files\Office\EXCEL.EXE", "label": "Cell A1", "value": "Metric", "target": {"control_type": "DataItem", "name": "A1"}}
    ]

    understanding = understand_steps(steps, flow=flow)
    assert "Q3_Financials.xlsx" in understanding["intent"]
    assert "Summary" in understanding["summary"]
    assert len(understanding["parameters"]) == 3

    summ = summarize({"flow": flow, "steps": steps})
    assert summ[0]["text"] == "Q3_Financials.xlsx"
    assert "4 cells" in summ[1]["text"]

    item = catalog.save({
        "name": "Populate Q3 Excel",
        "steps": steps,
        "flow": flow,
        "apps": ["EXCEL.EXE"],
        "duration": 5.2
    })
    assert item["can_run"] if "can_run" in item else True

    listings = catalog.list()
    assert len(listings) == 1
    assert listings[0]["can_run"] is True
    assert listings[0]["used"] == ["EXCEL.EXE"]
    catalog.close()


def test_execute_excel_workflow_standalone(tmp_path, monkeypatch):
    def _mock_no_com(visible=True):
        raise RuntimeError("COM unavailable in standalone mode test")

    monkeypatch.setattr("agent.ghost.desktop.excel.get_excel_app", _mock_no_com)
    # Set USERPROFILE to tmp_path so desktop directory is redirected
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "Desktop").mkdir(parents=True, exist_ok=True)

    payload = {
        "workbook": "TestWorkbook.xlsx",
        "sheet": "TestSheet",
        "headers": "ID, Name, Score",
        "rows": "1, Alice, 95\n2, Bob, 88\n3, Charlie, 92",
        "formula": "=AVERAGE(C2:C4)",
        "open_window": False,
    }
    result = execute_excel_workflow(payload)
    assert result["ok"] is True
    assert result["rows_written"] == 3
    assert result["mode"] == "standalone_file"


def test_inspect_active_excel_contract():
    info = inspect_active_excel()
    assert isinstance(info, dict)
    assert "active" in info
