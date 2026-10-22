"""Microsoft Excel desktop automation workflow and synthesis.

Provides deterministic COM automation and local spreadsheet generation for Excel:
- Live workbook/sheet connection and cell updates via Windows COM (pywin32)
- Offline fallback via pandas DataFrame / CSV / openpyxl
- AI and heuristic data row and formula generation
- Flow derivation from observed Excel desktop sessions
- Active workbook/sheet inspection and foreground window activation
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

FORMAT = "digitalsoul.excel-flow.v1"

_CACHED_EXCEL = None


def get_excel_app(visible: bool = True):
    """Retrieve or spawn a persistent Excel.Application COM instance."""
    global _CACHED_EXCEL
    if _CACHED_EXCEL is not None:
        try:
            # Liveness ping
            _ = _CACHED_EXCEL.Version
            if visible:
                _CACHED_EXCEL.Visible = True
            return _CACHED_EXCEL
        except Exception:
            _CACHED_EXCEL = None

    import win32com.client
    import pythoncom

    pythoncom.CoInitialize()
    excel = None
    try:
        excel = win32com.client.GetActiveObject("Excel.Application")
    except Exception:
        excel = win32com.client.Dispatch("Excel.Application")

    if visible:
        excel.Visible = True
        try:
            excel.UserControl = True
        except Exception:
            pass

    _CACHED_EXCEL = excel
    return excel


def inspect_active_excel() -> Dict[str, Any]:
    """Inspect currently active Excel workbook and sheet."""
    try:
        excel = get_excel_app(visible=False)
        if excel and excel.Workbooks.Count > 0:
            wb = excel.ActiveWorkbook
            ws = wb.ActiveSheet
            headers = []
            try:
                for c in range(1, min(ws.UsedRange.Columns.Count + 1, 20)):
                    val = ws.Cells(1, c).Value
                    if val is not None:
                        headers.append(str(val))
            except Exception:
                pass
            return {
                "active": True,
                "workbook": wb.Name,
                "sheet": ws.Name,
                "rows_count": ws.UsedRange.Rows.Count,
                "cols_count": ws.UsedRange.Columns.Count,
                "headers": ", ".join(headers),
            }
        return {"active": False, "detail": "No open workbooks"}
    except Exception as exc:
        return {"active": False, "detail": str(exc)}


def clean_cell_name(name: str) -> str:
    """Normalize Excel cell coordinate e.g. '"A1"' -> 'A1' or 'Cell A1' -> 'A1'."""
    name = (name or "").strip().strip('"').strip("'")
    m = re.search(r"\b([A-Z]{1,3}\d{1,7})\b", name, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return name.upper() if re.match(r"^[A-Z]{1,3}\d{1,7}$", name, re.IGNORECASE) else name


def validate_excel_recipe(flow: Dict[str, Any]) -> None:
    if not isinstance(flow, dict):
        raise ValueError("Excel workflow must be a dictionary")
    if flow.get("format") != FORMAT:
        raise ValueError(f"Expected format {FORMAT}")
    if not flow.get("cells") and not flow.get("rows"):
        raise ValueError("Excel workflow must contain cells or rows to write")


def derive_excel_flow(steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Derive an Excel workflow from observed desktop steps touching EXCEL.EXE."""
    excel_steps = [s for s in steps if "excel" in (s.get("app") or "").lower() or "excel" in (s.get("path") or "").lower()]
    if not excel_steps:
        return None

    workbook = "Active Workbook"
    sheet = "Sheet1"
    cells: List[Dict[str, Any]] = []

    for step in excel_steps:
        title = step.get("title") or ""
        if " - excel" in title.lower():
            workbook = title.split(" - Excel")[0].strip()

        kind = step.get("kind")
        if kind == "set_text":
            label = step.get("label") or ""
            target_name = (step.get("target") or {}).get("name") or step.get("field") or ""
            cell_ref = clean_cell_name(target_name) or clean_cell_name(label)
            val = step.get("value", "")
            if cell_ref and re.match(r"^[A-Z]{1,3}\d{1,7}$", cell_ref):
                cells.append({"cell": cell_ref, "value": val})
            elif val:
                next_row = len(cells) + 1
                cells.append({"cell": f"A{next_row}", "value": val})

    if not cells:
        return None

    return {
        "format": FORMAT,
        "kind": "excel",
        "workbook": workbook,
        "sheet": sheet,
        "cells": cells,
        "summary": f"Populate {len(cells)} cells in Excel ({workbook})"
    }


def generate_excel_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generates structured table headers, rows, and summary formula from a prompt or template."""
    topic = (payload.get("topic") or payload.get("title") or "Monthly Expenses").strip()
    topic_lower = topic.lower()

    if any(w in topic_lower for w in ["sale", "revenue", "order", "invoice"]):
        headers = ["Date", "Invoice ID", "Client", "Service / Product", "Amount ($)"]
        rows = [
            ["2026-10-22", "INV-1001", "Acme Corp", "Enterprise AI Workflow", "3,500.00"],
            ["2026-10-22", "INV-1002", "Nexus Labs", "Cloud Automation Agent", "1,850.00"],
            ["2026-10-23", "INV-1003", "Starlight Media", "Browser Orchestration", "2,200.00"],
            ["2026-10-23", "INV-1004", "Quantum Dynamics", "Custom Script Integration", "950.00"],
        ]
        formula = "=SUM(E2:E5)"
        sheet = "Sales"
    elif any(w in topic_lower for w in ["inventory", "stock", "product", "warehouse"]):
        headers = ["SKU", "Item Name", "Category", "Quantity", "Unit Cost ($)"]
        rows = [
            ["WGT-001", "Precision Sensor Module", "Hardware", "140", "15.50"],
            ["WGT-002", "Smart Relay Controller", "Hardware", "65", "42.00"],
            ["CBL-003", "Shielded Industrial Cable", "Accessories", "300", "4.80"],
            ["PWR-004", "24V Power Supply Unit", "Power", "45", "28.00"],
        ]
        formula = "=SUM(D2:D5)"
        sheet = "Inventory"
    elif any(w in topic_lower for w in ["task", "project", "sprint", "roadmap"]):
        headers = ["Task ID", "Description", "Assignee", "Status", "Hours Estimated"]
        rows = [
            ["TSK-101", "Deploy DigitalSoul Notch Bridge", "Dev Lead", "Completed", "8"],
            ["TSK-102", "Implement Excel Automation Workflow", "Core Team", "In Progress", "6"],
            ["TSK-103", "WebView2 Accessibility Verification", "QA", "In Progress", "4"],
            ["TSK-104", "OmniRoute Gateway Benchmarking", "AI Team", "Pending", "5"],
        ]
        formula = "=SUM(E2:E5)"
        sheet = "Tasks"
    else:
        # Default: Expense Tracker
        headers = ["Date", "Expense Item", "Category", "Cost ($)"]
        rows = [
            ["2026-10-22", "Cloud Compute Hosting", "Infrastructure", "125.00"],
            ["2026-10-22", "Domain & Security SSL", "Security", "45.00"],
            ["2026-10-23", "Local Inference GPU Server", "Hardware", "650.00"],
            ["2026-10-23", "Office Supplies & Cabling", "Operations", "85.50"],
        ]
        formula = "=SUM(D2:D5)"
        sheet = "Expenses"

    csv_rows = "\n".join([", ".join(f'"{c}"' if "," in str(c) else str(c) for c in r) for r in rows])
    return {
        "title": topic,
        "sheet": sheet,
        "headers": ", ".join(headers),
        "rows": csv_rows,
        "formula": formula,
    }


def execute_excel_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute Excel workflow either by live COM automation or fallback spreadsheet generation."""
    workbook_name = payload.get("workbook", "").strip() or "Active Workbook"
    sheet_name = payload.get("sheet", "").strip() or "Sheet1"
    headers_raw = payload.get("headers", "")
    rows_raw = payload.get("rows", "")
    formula = payload.get("formula", "").strip()
    cells = payload.get("cells", [])
    open_window = payload.get("open_window", True)

    # Parse headers and rows
    headers: List[str] = []
    if isinstance(headers_raw, list):
        headers = [str(h).strip() for h in headers_raw]
    elif isinstance(headers_raw, str) and headers_raw.strip():
        headers = [h.strip() for h in headers_raw.split(",")]

    parsed_rows: List[List[Any]] = []
    if isinstance(rows_raw, list):
        parsed_rows = rows_raw
    elif isinstance(rows_raw, str) and rows_raw.strip():
        reader = csv.reader(io.StringIO(rows_raw))
        for row in reader:
            if row:
                parsed_rows.append([c.strip() for c in row])

    # Convert cells to row updates if headers/rows empty
    if not headers and not parsed_rows and cells:
        for item in cells:
            ref = clean_cell_name(item.get("cell", ""))
            val = item.get("value", "")
            m = re.match(r"^([A-Z]+)(\d+)$", ref)
            if m:
                col_letters, row_num = m.group(1), int(m.group(2))
                while len(parsed_rows) < row_num:
                    parsed_rows.append([])
                r = parsed_rows[row_num - 1]
                col_idx = 0
                for char in col_letters:
                    col_idx = col_idx * 26 + (ord(char) - ord('A') + 1)
                col_idx -= 1
                while len(r) <= col_idx:
                    r.append("")
                r[col_idx] = val

    # Attempt 1: Live COM Automation via win32com
    try:
        excel = get_excel_app(visible=True)

        wb = None
        if workbook_name and workbook_name != "Active Workbook" and os.path.exists(workbook_name):
            try:
                wb = excel.Workbooks.Open(workbook_name)
            except Exception:
                pass

        if wb is None:
            if excel.Workbooks.Count > 0:
                wb = excel.ActiveWorkbook
            else:
                wb = excel.Workbooks.Add()

        # Select or create sheet
        ws = None
        try:
            ws = wb.Worksheets(sheet_name)
        except Exception:
            try:
                ws = wb.ActiveSheet
            except Exception:
                ws = wb.Worksheets.Add()
                ws.Name = sheet_name

        ws.Activate()

        # Determine start row (append if existing rows present)
        last_row = 0
        try:
            last_row = ws.Cells(ws.Rows.Count, 1).End(-4162).Row  # xlUp = -4162
            if last_row == 1 and ws.Cells(1, 1).Value is None:
                last_row = 0
        except Exception:
            last_row = 0

        if last_row > 0:
            start_row = last_row + 1
        else:
            start_row = 1
            if headers:
                for col_idx, h in enumerate(headers, start=1):
                    cell = ws.Cells(start_row, col_idx)
                    cell.Value = h
                    cell.Font.Bold = True
                start_row = 2

        current_row = start_row
        for r in parsed_rows:
            for col_idx, val in enumerate(r, start=1):
                # Auto-cast numeric values
                clean_v = str(val).replace(",", "").replace("$", "").strip()
                try:
                    num_val = float(clean_v) if "." in clean_v else int(clean_v)
                    ws.Cells(current_row, col_idx).Value = num_val
                except ValueError:
                    ws.Cells(current_row, col_idx).Value = val
            current_row += 1

        if formula:
            if "=" in formula:
                m = re.search(r"([A-Z]+)\d+:([A-Z]+)\d+", formula)
                if m:
                    col_str = m.group(1)
                    target_ref = f"{col_str}{current_row}"
                    adjusted_formula = f"=SUM({col_str}2:{col_str}{current_row - 1})" if "SUM" in formula.upper() else formula
                    formula_cell = ws.Range(target_ref)
                    formula_cell.Formula = adjusted_formula
                    formula_cell.Font.Bold = True
                    try:
                        # Add 'Total' label to the left of the formula cell
                        col_idx = ord(col_str[0]) - ord('A') + 1
                        if col_idx > 1:
                            lbl_cell = ws.Cells(current_row, col_idx - 1)
                            lbl_cell.Value = "Total"
                            lbl_cell.Font.Bold = True
                    except Exception:
                        pass
                else:
                    target_ref = f"A{current_row}"
                    formula_cell = ws.Range(target_ref)
                    formula_cell.Formula = formula

        try:
            ws.Columns.AutoFit()
        except Exception:
            pass

        # Bring window to front
        if open_window:
            try:
                import ctypes
                hwnd = getattr(excel, "Hwnd", None)
                if hwnd:
                    user = ctypes.windll.user32
                    user.keybd_event(0x12, 0, 0, 0)
                    user.keybd_event(0x12, 0, 2, 0)
                    user.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                    user.SetForegroundWindow(hwnd)
            except Exception:
                pass

        return {
            "ok": True,
            "mode": "live_com",
            "workbook": wb.Name if wb else "Active",
            "sheet": ws.Name if ws else sheet_name,
            "rows_written": len(parsed_rows),
            "detail": f"Successfully updated Excel sheet '{sheet_name}' with {len(parsed_rows)} rows."
        }
    except Exception as com_err:
        # Attempt 2: Standalone local file generation with pandas / csv
        try:
            desktop_dir = Path(os.environ.get("USERPROFILE", ".")) / "Desktop"
            if not desktop_dir.exists():
                desktop_dir = Path(".")

            has_openpyxl = False
            try:
                import openpyxl  # noqa: F401
                has_openpyxl = True
            except ImportError:
                has_openpyxl = False

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            if has_openpyxl:
                import pandas as pd
                df = pd.DataFrame(parsed_rows, columns=headers if headers and len(headers) == len(parsed_rows[0] if parsed_rows else []) else None)
                out_file = desktop_dir / f"{sheet_name or 'Sheet'}_{timestamp}.xlsx"
                df.to_excel(out_file, index=False)
            else:
                out_file = desktop_dir / f"{sheet_name or 'Sheet'}_{timestamp}.csv"
                with open(out_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if headers:
                        writer.writerow(headers)
                    writer.writerows(parsed_rows)
                    if formula:
                        writer.writerow(["Formula / Total", formula])

            if sys.platform == "win32" and open_window:
                try:
                    os.startfile(str(out_file))
                except Exception:
                    pass
            return {
                "ok": True,
                "mode": "standalone_file",
                "file": str(out_file),
                "rows_written": len(parsed_rows),
                "detail": f"Saved spreadsheet: {out_file.name} ({len(parsed_rows)} rows)."
            }
        except Exception as file_err:
            return {
                "ok": False,
                "error": f"Excel execution failed: {com_err}; File fallback failed: {file_err}"
            }
