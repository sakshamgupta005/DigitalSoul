"""Tests for Demonstration Engineering, Sensitive Data Redaction, and SKILL.md integration."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from agent.ghost.learning.skill_recorder import (
    SensitiveDataFilter,
    SkillSynthesizer,
    export_skill_markdown,
    parse_skill_markdown,
)
from agent.ghost.desktop.learning_mode import LearningEngine
from agent.ghost.api.learn_routes import router
from fastapi import FastAPI


# ============================================================================
# 1. Sensitive Data Filter Unit Tests
# ============================================================================

def test_sensitive_data_filter_redacts_keys_and_pii():
    text = (
        "User entered key sk-1234567890abcdef1234567890 and anthropic key "
        "sk-ant-api03-abcdef12345678901234567890-abcdef. "
        "Contact me at alice@example.com or 415-555-2671. SSN is 000-12-3456."
    )
    cleaned, count = SensitiveDataFilter.redact_text(text)
    assert count >= 4
    assert "sk-1234567890abcdef1234567890" not in cleaned
    assert "[REDACTED_OPENAI_KEY]" in cleaned
    assert "[REDACTED_ANTHROPIC_KEY]" in cleaned
    assert "alice@example.com" not in cleaned
    assert "[REDACTED_EMAIL]" in cleaned
    assert "000-12-3456" not in cleaned
    assert "[REDACTED_SSN]" in cleaned


def test_sensitive_data_filter_redacts_steps():
    # Password field step
    pwd_step = {
        "kind": "set_text",
        "app": "portal.exe",
        "label": "Master Password",
        "value": "super_secret_123!",
        "target": {"name": "password_input"},
    }
    cleaned_pwd, count = SensitiveDataFilter.redact_step(pwd_step)
    assert count >= 1
    assert cleaned_pwd["value"] == "[REDACTED_PASSWORD]"

    # URL with secret query parameter
    url_step = {
        "kind": "open_url",
        "app": "chrome.exe",
        "url": "https://service.internal/auth/callback?token=secret_jwt_xyz&session=active",
    }
    cleaned_url, count = SensitiveDataFilter.redact_step(url_step)
    assert count >= 1
    assert "token=[REDACTED]" in cleaned_url["url"]
    assert "session=active" in cleaned_url["url"]


# ============================================================================
# 2. Demonstration Engineering & Synthesizer Tests
# ============================================================================

def test_skill_synthesizer_offline_heuristic():
    synthesizer = SkillSynthesizer()
    steps = [
        {"kind": "open_url", "app": "msedge.exe", "url": "https://portal.acme.com"},
        {"kind": "set_text", "app": "msedge.exe", "label": "Search Invoices", "value": "INV-2026-999"},
        {"kind": "click", "app": "msedge.exe", "label": "Tag High Risk", "target": {"name": "Tag High Risk"}},
    ]
    voice_notes = "Find overdue invoice and tag it as high risk."

    res = synthesizer.synthesize(
        steps=steps,
        voice_notes=voice_notes,
        app_name="msedge",
        use_llm=False,
    )

    candidate = res["candidate"]
    assert candidate.support == 1
    assert len(candidate.steps) == 3
    assert "High risk" in candidate.name or "high risk" in candidate.description.lower()
    assert len(candidate.parameters) == 1
    assert candidate.parameters[0]["default_value"] == "INV-2026-999"

    # Verify standard SKILL.md output
    skill_md = res["skill_markdown"]
    assert skill_md.startswith("---")
    assert "name:" in skill_md
    assert "description:" in skill_md
    assert "## Overview" in skill_md
    assert "## Parameters" in skill_md
    assert "## Instructions" in skill_md
    assert "## Guardrails & Edge Cases" in skill_md


# ============================================================================
# 3. SKILL.md Export & Parsing Round-Trip Tests
# ============================================================================

def test_export_and_parse_skill_markdown_roundtrip():
    synthesizer = SkillSynthesizer()
    steps = [
        {"kind": "click", "app": "crm.exe", "label": "Customers Tab"},
        {"kind": "set_text", "app": "crm.exe", "label": "Customer Name", "value": "Acme Corp"},
        {"kind": "click", "app": "crm.exe", "label": "Save Changes"},
    ]
    res = synthesizer.synthesize(steps=steps, voice_notes="Update customer profile", use_llm=False)
    candidate = res["candidate"]

    # Export to markdown
    md_content = export_skill_markdown(candidate)
    assert "name:" in md_content
    assert "Acme Corp" in md_content

    # Parse back into candidate
    parsed_cand = parse_skill_markdown(md_content)
    assert parsed_cand.name.lower().replace("-", " ") == candidate.name.lower().replace("-", " ")
    assert len(parsed_cand.steps) == 3
    assert len(parsed_cand.parameters) == 1
    assert parsed_cand.parameters[0]["default_value"] == "Acme Corp"


# ============================================================================
# 4. Desktop LearningEngine Skill Integration Tests
# ============================================================================

def test_desktop_learning_engine_skill_export_and_import(tmp_path: Path):
    db_file = tmp_path / "learning.sqlite3"
    engine = LearningEngine(db_file)

    # 1. Record episode with sensitive data and voice notes
    steps = [
        {"kind": "open_app", "app": "notepad.exe"},
        {"kind": "set_text", "app": "notepad.exe", "label": "Editor", "value": "sk-1234567890abcdef1234567890 key here"},
    ]
    rec_res = engine.record_episode(
        steps=steps,
        flow=None,
        voice_notes="Record secret api key for notes.",
    )
    assert rec_res["action"] == "observed"
    ep_id = rec_res["episode_id"]

    # 2. Export episode as SKILL.md
    skill_md = engine.export_skill(ep_id)
    assert "name:" in skill_md
    # Sensitive key should be scrubbed
    assert "sk-1234567890abcdef1234567890" not in skill_md
    assert "[REDACTED_OPENAI_KEY]" in skill_md

    # 3. Import SKILL.md into learned patterns
    import_res = engine.import_skill(skill_md)
    assert import_res["action"] == "imported"
    pat_id = import_res["pattern_id"]

    pat = engine.get_pattern(pat_id)
    assert pat is not None
    assert pat["step_count"] == 2
    engine.close()


# ============================================================================
# 5. FastAPI Endpoints Tests
# ============================================================================

def test_fastapi_skill_recorder_endpoints():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Redact endpoint
    resp = client.post(
        "/api/v1/learning/redact",
        json={"text": "User token is Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.do_not_expose"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "do_not_expose" not in data["text"]
    assert "[REDACTED_JWT]" in data["text"] or "[REDACTED_TOKEN]" in data["text"]

    # 2. Synthesize demonstration
    demo_payload = {
        "steps": [
            {"kind": "open_url", "app": "edge", "url": "https://shop.local"},
            {"kind": "set_text", "app": "edge", "label": "Search Product", "value": "T-Shirt"},
        ],
        "voice_notes": "Search for t-shirt product inventory.",
        "use_llm": False,
        "save_candidate": True,
    }
    resp = client.post("/api/v1/demonstrations/synthesize", json=demo_payload)
    assert resp.status_code == 200
    synth_data = resp.json()
    cand_id = synth_data["candidate"]["candidate_id"]
    assert synth_data["skill_markdown"].startswith("---")
    assert synth_data["voice_notes_used"] is True

    # 3. Export candidate skill
    resp = client.get(f"/api/v1/candidates/{cand_id}/skill")
    assert resp.status_code == 200
    export_data = resp.json()
    assert export_data["candidate_id"] == cand_id
    assert "## Instructions" in export_data["skill_markdown"]

    # 4. Import skill markdown
    sample_skill_md = """---
name: imported-test-skill
description: Tests skill importation from standard SKILL.md.
---

## Overview
Demonstration imported workflow.

## Parameters
- **`user_id`** (`string`): Default `USR_001`. Variable parameter slot.

## Instructions
1. CLICK on 'Login Button'.
2. SET_FIELD on 'User Input' with value `USR_001`.

## Guardrails & Edge Cases
- Fail on missing login form.
"""
    resp = client.post("/api/v1/skills/import", json={"skill_markdown": sample_skill_md})
    assert resp.status_code == 201
    imported_cand = resp.json()
    assert "Imported Test Skill" in imported_cand["name"]
    assert len(imported_cand["steps"]) == 2
    assert len(imported_cand["parameters"]) == 1
    assert imported_cand["parameters"][0]["name"] == "user_id"

    # 5. Evaluate skill endpoint
    eval_resp = client.post("/api/v1/skills/evaluate", json={"candidate_id": cand_id})
    assert eval_resp.status_code == 200
    report = eval_resp.json()
    assert report["score"] >= 60
    assert report["grade"] in ("A+", "A", "B")
    assert report["ready_for_execution"] is True

    # 6. Optimize skill endpoint
    opt_payload = {
        "steps": [
            {"kind": "click", "app": "crm.exe", "label": "Customer Box"},
            {"kind": "set_text", "app": "crm.exe", "label": "Customer Box", "value": "Acme Global"},
            {"kind": "click", "app": "crm.exe", "label": "Submit"},
        ],
        "voice_notes": "Input customer and submit.",
        "save_candidate": True,
    }
    opt_resp = client.post("/api/v1/skills/optimize", json=opt_payload)
    assert opt_resp.status_code == 200
    opt_data = opt_resp.json()
    # Click before set_text was compacted
    assert opt_data["compacted_steps_count"] == 2
    assert opt_data["original_steps_count"] == 3
    assert opt_data["quality_report"]["score"] >= 70


# ============================================================================
# 6. Action Compactor & Optimization Unit Tests
# ============================================================================

def test_action_compactor_merges_and_cleans():
    from agent.ghost.learning.skill_recorder import ActionCompactor

    raw_steps = [
        {"kind": "click", "app": "app.exe", "label": "Username"},
        {"kind": "set_text", "app": "app.exe", "label": "Username", "value": "u"},
        {"kind": "set_text", "app": "app.exe", "label": "Username", "value": "user123"},
        {"kind": "click", "app": "app.exe", "label": "Login"},
        {"kind": "click", "app": "app.exe", "label": "Login"},  # duplicate rapid click
    ]

    compacted = ActionCompactor.compact(raw_steps)
    assert len(compacted) == 2
    assert compacted[0]["kind"] == "set_text"
    assert compacted[0]["value"] == "user123"
    assert compacted[1]["kind"] == "click"
    assert compacted[1]["label"] == "Login"


def test_skill_quality_evaluator_scores_dimensions():
    from agent.ghost.learning.skill_recorder import SkillOptimizer, SkillQualityEvaluator

    steps = [
        {"kind": "open_url", "app": "portal", "url": "https://company.org"},
        {"kind": "set_text", "app": "portal", "label": "User Query", "value": "Financial Report"},
        {"kind": "click", "app": "portal", "label": "Delete Cache"},
    ]
    opt_res = SkillOptimizer.optimize(steps=steps, voice_notes="Search report and delete cached entries.")
    candidate = opt_res["candidate"]

    # Verify risk assignment
    delete_step = next(s for s in candidate.steps if "Delete" in str(s.locators[0].value))
    assert delete_step.risk == "R2"

    report = SkillQualityEvaluator.evaluate(candidate)
    assert report.score >= 80
    assert report.ready_for_execution is True
    assert "privacy" in report.dimension_scores
    assert report.dimension_scores["privacy"] == 25


def test_desktop_learning_engine_evaluate_and_optimize(tmp_path: Path):
    from agent.ghost.desktop.learning_mode import LearningEngine

    db_file = tmp_path / "learning.sqlite3"
    engine = LearningEngine(db_file)

    steps = [
        {"kind": "click", "app": "calc.exe", "label": "Number One"},
        {"kind": "click", "app": "calc.exe", "label": "Plus"},
        {"kind": "click", "app": "calc.exe", "label": "Number Two"},
        {"kind": "click", "app": "calc.exe", "label": "Equals"},
    ]
    rec_res = engine.record_episode(steps=steps, voice_notes="Calculate 1 plus 2 in Calculator.")
    ep_id = rec_res["episode_id"]

    # 1. Evaluate episode skill
    eval_rep = engine.evaluate_skill(ep_id)
    assert eval_rep["score"] >= 60
    assert eval_rep["ready_for_execution"] is True

    # 2. Optimize episode skill
    opt_res = engine.optimize_skill(ep_id)
    assert opt_res["compacted_steps_count"] == 4
    assert opt_res["quality_report"]["score"] >= 75
    engine.close()

