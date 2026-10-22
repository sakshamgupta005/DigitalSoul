"""Tests for DigitalSoul's Cross-App Chain Orchestrator.

Validates:
- End-to-end "Process today's supplier drop" orchestration.
- Structured input/output handoffs across Excel, validator, Shopify, and WhatsApp.
- Checkpoints, bounded retries, and partial failure handling.
- Anti-hallucination / safety invariant: If Shopify fails, never report success or send misleading messages.
- Row-level validation and inventory totals calculation.
- Explicit approval gating before consequential mutations and messaging.
- Idempotency / duplicate execution protection.
- Graceful blocked state with setup requirements when integrations are unavailable.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.ghost.desktop.chain_orchestrator import (
    ChainExecutionReport,
    CrossAppChainOrchestrator,
    create_sample_supplier_drop_if_missing,
)


@pytest.fixture
def sample_csv(tmp_path):
    csv_file = tmp_path / "supplier_drop.csv"
    content = (
        "Title,SKU,Price,Quantity,Category,Description\n"
        "Mechanical Keyboard,KB-MECH-01,75.00,10,Electronics,Clicky mechanical keyboard\n"
        "Ergonomic Mouse,MS-ERGO-02,40.00,20,Electronics,Vertical ergonomic mouse\n"
        "USB-C Hub,HUB-USBC-03,25.00,30,Accessories,Multi-port adapter\n"
    )
    csv_file.write_text(content, encoding="utf-8")
    return str(csv_file)


@pytest.fixture
def invalid_rows_csv(tmp_path):
    csv_file = tmp_path / "invalid_drop.csv"
    content = (
        "Title,SKU,Price,Quantity\n"
        "Valid Item,SKU-VAL-01,50.00,5\n"
        ",SKU-MISSING-TITLE,20.00,10\n"
        "Bad Price,SKU-BAD-PRICE,-15.00,4\n"
        "Bad Qty,SKU-BAD-QTY,30.00,-2\n"
    )
    csv_file.write_text(content, encoding="utf-8")
    return str(csv_file)


def test_chain_dry_run_success(sample_csv, tmp_path):
    """Test full end-to-end dry-run preview."""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")
    report = orchestrator.execute_chain(
        workbook_path=sample_csv,
        recipient="Alex Store Manager",
        approved=False,
        dry_run=True,
    )

    assert report.status == "preview"
    assert len(report.completed) >= 4  # Excel, Validator, Shopify (preview), Verifier
    assert report.inventory_summary["total_products"] == 3
    assert report.inventory_summary["total_units"] == 60
    assert report.inventory_summary["total_inventory_value"] == 2300.00  # (75*10 + 40*20 + 25*30) = 750 + 800 + 750 = 2300.00
    assert report.whatsapp_draft is not None
    assert "Alex Store Manager" in report.whatsapp_draft["recipient"]
    assert "SUPPLIER DROP PROCESSED" in report.whatsapp_draft["message"]


def test_chain_invalid_rows_handling(invalid_rows_csv, tmp_path):
    """Test filtering of invalid rows, totals calculation, and note in WhatsApp message."""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")
    report = orchestrator.execute_chain(
        workbook_path=invalid_rows_csv,
        dry_run=True,
        approved=False,
    )

    assert report.inventory_summary["total_products"] == 1
    assert report.inventory_summary["invalid_count"] == 3
    assert report.inventory_summary["total_units"] == 5
    assert report.inventory_summary["total_inventory_value"] == 250.00
    assert "3 invalid rows were skipped" in report.whatsapp_draft["message"]


def test_chain_all_rows_invalid_fails_safely(tmp_path):
    """Test that if all rows in a drop are invalid, the chain aborts and downstream steps are skipped."""
    csv_file = tmp_path / "all_bad.csv"
    csv_file.write_text("Title,SKU,Price,Quantity\n,SKU1,-5,-1\n", encoding="utf-8")

    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")
    report = orchestrator.execute_chain(
        workbook_path=str(csv_file),
        dry_run=True,
    )

    assert report.status == "failed"
    assert "validate_totals" in report.failed
    assert "stage_shopify" in report.skipped
    assert "notify_manager_whatsapp" in report.skipped


def test_shopify_failure_never_reports_false_success(sample_csv, tmp_path):
    """CRITICAL SAFETY TEST: If Shopify staging fails, WhatsApp must NEVER report success!"""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")

    # Mock Shopify staging to fail
    failed_staging_result = [
        {"sku": "KB-MECH-01", "title": "Mechanical Keyboard", "ok": False, "error": "Form field timeout on Shopify"},
        {"sku": "MS-ERGO-02", "title": "Ergonomic Mouse", "ok": False, "error": "Form field timeout on Shopify"},
        {"sku": "HUB-USBC-03", "title": "USB-C Hub", "ok": False, "error": "Form field timeout on Shopify"},
    ]

    with patch.object(orchestrator, "step_stage_shopify", return_value=failed_staging_result):
        report = orchestrator.execute_chain(
            workbook_path=sample_csv,
            dry_run=False,
            approved=False,
        )

    # Status must be failed or partially_failed, NOT succeeded!
    assert report.status in ("failed", "partially_failed")
    assert "verify_staged_products" in (report.failed + report.unverified)

    # WhatsApp message must explicitly report an alert, NEVER false success
    wa_msg = report.whatsapp_draft["message"]
    assert "SUPPLIER DROP ALERT - ACTION REQUIRED" in wa_msg
    assert "Issues detected during Shopify staging" in wa_msg
    assert "✅ Status: Staged & Verified" not in wa_msg


def test_approval_gating_prevents_unconsented_whatsapp_send(sample_csv, tmp_path):
    """Verify that approval is required before sending WhatsApp messages."""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")

    # Run with approved=False in live mode
    mock_staging = [
        {"sku": "KB-MECH-01", "title": "Mechanical Keyboard", "ok": True, "status": "staged"},
        {"sku": "MS-ERGO-02", "title": "Ergonomic Mouse", "ok": True, "status": "staged"},
        {"sku": "HUB-USBC-03", "title": "USB-C Hub", "ok": True, "status": "staged"},
    ]
    with patch.object(orchestrator, "step_stage_shopify", return_value=mock_staging):
        report = orchestrator.execute_chain(
            workbook_path=sample_csv,
            approved=False,
            dry_run=False,
        )

    assert report.status == "awaiting_approval"
    wa_step = next(s for s in report.steps if s.step_id == "notify_manager_whatsapp")
    assert wa_step.status == "pending"
    assert "Explicit user approval required" in wa_step.setup_requirement


def test_idempotency_duplicate_execution_cached(sample_csv, tmp_path):
    """Verify duplicate execution returns cached completed run without re-running mutations."""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")
    key = "idem_test_supplier_drop_001"

    report1 = orchestrator.execute_chain(
        workbook_path=sample_csv,
        idempotency_key=key,
        dry_run=True,
    )
    assert report1.status == "preview"

    # Second call with same idempotency key
    report2 = orchestrator.execute_chain(
        workbook_path=sample_csv,
        idempotency_key=key,
        dry_run=True,
    )
    assert report2.chain_id == report1.chain_id
    assert report2.status == report1.status
    assert report2.inventory_summary == report1.inventory_summary


def test_graceful_blocked_state_when_whatsapp_unavailable(sample_csv, tmp_path):
    """Verify clear setup requirement when WhatsApp app is not installed or running."""
    orchestrator = CrossAppChainOrchestrator(runs_dir=tmp_path / "chains")
    mock_staging = [
        {"sku": "KB-MECH-01", "title": "Mechanical Keyboard", "ok": True, "status": "staged"},
        {"sku": "MS-ERGO-02", "title": "Ergonomic Mouse", "ok": True, "status": "staged"},
        {"sku": "HUB-USBC-03", "title": "USB-C Hub", "ok": True, "status": "staged"},
    ]

    with patch.object(orchestrator, "step_stage_shopify", return_value=mock_staging):
        with patch.object(orchestrator, "step_whatsapp_notify", return_value={
            "ok": False,
            "status": "blocked",
            "setup_requirement": "WhatsApp Desktop is not running. Please open WhatsApp Desktop.",
        }):
            report = orchestrator.execute_chain(
                workbook_path=sample_csv,
                approved=True,
                dry_run=False,
            )

    assert report.status == "blocked"
    assert "notify_manager_whatsapp" in report.blocked
    wa_step = next(s for s in report.steps if s.step_id == "notify_manager_whatsapp")
    assert "WhatsApp Desktop is not running" in wa_step.setup_requirement
