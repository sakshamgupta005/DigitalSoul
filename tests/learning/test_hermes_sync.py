"""Unit tests for Hermes Agent integration, skill copying, and repetition guard."""

from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.ghost.api.learn_routes import router
from agent.ghost.desktop.learning_mode import LearningEngine
from agent.ghost.learning.hermes_sync import (
    list_hermes_skills,
    copy_hermes_skill,
    import_hermes_skill_to_patterns,
    get_digital_soul_skills_dir,
)
from agent.ghost.learning.skill_recorder import parse_skill_markdown
from agent.ghost.safety.repetition_guard import (
    is_repetition_dominated,
    detect_action_loop,
)


# ============================================================================
# 1. Repetition Guard Tests (adapted from Hermes Agent)
# ============================================================================

def test_repetition_dominated_text():
    # Short text should fail-open
    assert not is_repetition_dominated("Short message.")
    
    # Non-repetitive realistic text > 400 chars
    normal_text = (
        "In this step we inspect the user interface to identify elements. "
        "First we check the address bar and verify that the current URL is youtube.com. "
        "Next we click on the search field and type our query. "
        "Then we submit the search by pressing Enter and wait for the results grid to render. "
        "Finally we select the first video thumbnail and open the video stream directly without "
        "redundant intermediate navigation steps."
    )
    assert not is_repetition_dominated(normal_text)

    # Repeating line loop (>400 chars)
    repeating_line = "Clicking button next and waiting for response.\n" * 15
    assert is_repetition_dominated(repeating_line)


def test_action_loop_detection():
    # Normal diverse actions
    normal_actions = [
        {"kind": "open_url", "url": "https://youtube.com"},
        {"kind": "click", "label": "Search"},
        {"kind": "set_text", "label": "Search Box", "value": "music"},
        {"kind": "click", "label": "First Video"},
    ]
    assert not detect_action_loop(normal_actions)

    # Runaway scrolling loop (identical consecutive actions)
    scroll_loop = [{"kind": "scroll", "label": "Window", "value": "down"}] * 5
    assert detect_action_loop(scroll_loop)

    # 2-step cyclical pattern (A B A B A B)
    cycle_loop = [
        {"kind": "click", "label": "Next Page"},
        {"kind": "scroll", "label": "Body"},
        {"kind": "click", "label": "Next Page"},
        {"kind": "scroll", "label": "Body"},
        {"kind": "click", "label": "Next Page"},
        {"kind": "scroll", "label": "Body"},
    ]
    assert detect_action_loop(cycle_loop)


# ============================================================================
# 2. Parsing Hermes Agent SKILL.md Files
# ============================================================================

def test_parse_hermes_computer_use_skill():
    sample_hermes_skill = """---
name: "computer-use"
description: "Drive the desktop background-first; escalate on signal."
version: 2.1.0
platforms: [macos, windows, linux]
metadata:
  hermes:
    tags: [computer-use, desktop, automation]
---

# Computer Use (universal, any-model, cross-platform)

Drive desktop background-first.

## The canonical workflow

1. computer_use(action="capture", mode="som", app="chrome")
2. computer_use(action="click", element=7)
3. computer_use(action="click", element=7, capture_after=True)

## Prerequisites
- **`TIMEOUT`** (integer): Default `30`
"""
    cand = parse_skill_markdown(sample_hermes_skill)
    assert cand.name == "Computer Use"
    assert "Drive the desktop background-first" in cand.description
    assert len(cand.steps) >= 3
    assert cand.steps[0].verb == "capture"
    assert cand.steps[1].verb == "click"
    assert len(cand.parameters) >= 1
    assert cand.parameters[0]["name"] == "TIMEOUT"


def test_parse_hermes_youtube_content_skill():
    sample_youtube = """---
name: youtube-content
description: "YouTube transcripts to summaries, threads, blogs."
version: 1.0.0
---

# YouTube Content Tool

## Workflow

1. **Fetch** the transcript using `terminal` from https://youtube.com/watch?v=123.
2. **Validate**: confirm the output is non-empty.
3. **Transform** into the requested output format.
4. **Verify**: re-read the transformed output.
"""
    cand = parse_skill_markdown(sample_youtube)
    assert cand.name == "Youtube Content"
    assert "YouTube transcripts" in cand.description
    assert len(cand.steps) >= 4
    assert cand.steps[0].verb == "open_url"


# ============================================================================
# 3. Hermes Sync & SQLite Import Tests
# ============================================================================

def test_hermes_sync_and_db_import(tmp_path: Path):
    # 1. List skills
    skills = list_hermes_skills()
    assert len(skills) > 0
    skill_names = [s["name"] for s in skills]
    assert "computer-use" in skill_names
    assert "youtube-content" in skill_names

    # 2. Copy a skill
    dest_dir = tmp_path / "skills"
    ok, msg, path = copy_hermes_skill("computer-use", target_dir=dest_dir)
    assert ok is True
    assert path is not None
    assert (path / "SKILL.md").exists()

    # 3. Import into LearningEngine SQLite database
    db_file = tmp_path / "learning.sqlite3"
    engine = LearningEngine(db_file)
    skill_md = (path / "SKILL.md").read_text(encoding="utf-8-sig")
    res = engine.import_skill(skill_md)
    assert res["action"] == "imported"
    pat = engine.get_pattern(res["pattern_id"])
    assert pat is not None
    assert pat["name"] == "Computer Use"
    assert pat["step_count"] >= 1
    engine.close()


# ============================================================================
# 4. FastAPI Hermes Endpoints Test
# ============================================================================

def test_fastapi_hermes_routes():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # GET /api/v1/skills/hermes
    resp = client.get("/api/v1/skills/hermes")
    assert resp.status_code == 200
    data = resp.json()
    assert "skills" in data
    assert any(s["name"] == "computer-use" for s in data["skills"])
