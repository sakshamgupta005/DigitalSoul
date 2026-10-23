"""Hermes Agent Skill Sync & Importer for DigitalSoul.

Enables copying and adapting skills, tools, and workflows directly from NousResearch/hermes-agent
into DigitalSoul's learned skills repository and execution engine.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .skill_recorder import parse_skill_markdown

logger = logging.getLogger(__name__)

# Default locations where Hermes Agent repo may reside locally
HERMES_LOCAL_CANDIDATES = [
    Path(os.environ.get("HERMES_AGENT_PATH", "")),
    Path(r"C:\Users\rspma\.gemini\antigravity-ide\brain\be449b5b-48eb-4da7-ab28-904a40ad4f4d\scratch\hermes-agent"),
    Path(os.path.expanduser("~/.hermes")),
    Path("scratch/hermes-agent"),
]

HERMES_GITHUB_RAW = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main"

# Curated skills of high value for DigitalSoul desktop and browser automation
CURATED_HERMES_SKILLS = [
    {"name": "computer-use", "category": "autonomous-ai-agents", "desc": "Universal background desktop driving & element indexing"},
    {"name": "youtube-content", "category": "media", "desc": "Direct YouTube transcripts, summaries, and video link processing"},
    {"name": "blocked-page-recovery", "category": "web", "desc": "Fallback ladder for 403/429/blocked pages & WAFs"},
    {"name": "systematic-debugging", "category": "software-development", "desc": "Disciplined failure reproduction and root-cause tracing"},
]

FALLBACK_CURATED_SKILLS = {
    "computer-use": """---
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
""",
    "youtube-content": """---
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
}


def get_hermes_repo_root() -> Optional[Path]:
    """Return local path to hermes-agent checkout if present."""
    for p in HERMES_LOCAL_CANDIDATES:
        if p and str(p).strip() and str(p) != "." and p.is_dir():
            if (p / "skills").is_dir() and (p / "agent").is_dir():
                return p.resolve()
    return None


def get_digital_soul_skills_dir() -> Path:
    """Return root directory where DigitalSoul keeps imported skills."""
    root = Path(__file__).resolve().parent.parent.parent.parent
    skills_dir = root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def list_hermes_skills() -> List[Dict[str, Any]]:
    """List available skills from Hermes Agent (either locally scanned or curated catalog)."""
    repo = get_hermes_repo_root()
    skills_found: Dict[str, Dict[str, Any]] = {}

    # Seed curated first
    for item in CURATED_HERMES_SKILLS:
        skills_found[item["name"]] = {
            "name": item["name"],
            "category": item["category"],
            "description": item["desc"],
            "is_curated": True,
            "installed": (get_digital_soul_skills_dir() / item["name"]).exists(),
        }

    # If local checkout exists, scan all skills directories
    if repo:
        skills_dir = repo / "skills"
        if skills_dir.exists():
            for skill_md in skills_dir.rglob("SKILL.md"):
                skill_folder = skill_md.parent
                skill_name = skill_folder.name
                category = skill_folder.parent.name
                
                # Read description from frontmatter
                desc = ""
                try:
                    text = skill_md.read_text(encoding="utf-8-sig")[:1000]
                    for line in text.splitlines():
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip("\"'")
                            break
                except Exception:
                    pass

                skills_found[skill_name] = {
                    "name": skill_name,
                    "category": category,
                    "description": desc or skills_found.get(skill_name, {}).get("description", "Hermes Agent skill"),
                    "is_curated": skill_name in [c["name"] for c in CURATED_HERMES_SKILLS],
                    "installed": (get_digital_soul_skills_dir() / skill_name).exists(),
                    "local_path": str(skill_folder),
                }

    return sorted(list(skills_found.values()), key=lambda x: (not x["is_curated"], x["name"]))


def copy_hermes_skill(skill_name: str, target_dir: Optional[Path] = None) -> Tuple[bool, str, Optional[Path]]:
    """Copy an entire skill from Hermes Agent into DigitalSoul's skills folder.
    
    Returns: (success, message, destination_path)
    """
    dest_root = target_dir or get_digital_soul_skills_dir()
    dest_skill_dir = dest_root / skill_name

    repo = get_hermes_repo_root()
    source_folder: Optional[Path] = None

    if repo:
        skills_dir = repo / "skills"
        for candidate in skills_dir.rglob(skill_name):
            if candidate.is_dir() and (candidate / "SKILL.md").exists():
                source_folder = candidate
                break

    if source_folder and source_folder.exists():
        if dest_skill_dir.exists():
            shutil.rmtree(dest_skill_dir)
        shutil.copytree(source_folder, dest_skill_dir)
        return True, f"Copied '{skill_name}' from local Hermes Agent to {dest_skill_dir}", dest_skill_dir

    # Fallback to fetching SKILL.md from GitHub raw for curated skills
    curated = next((c for c in CURATED_HERMES_SKILLS if c["name"] == skill_name), None)
    if curated:
        dest_skill_dir.mkdir(parents=True, exist_ok=True)
        url = f"{HERMES_GITHUB_RAW}/skills/{curated['category']}/{curated['name']}/SKILL.md"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DigitalSoul-Importer/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8")
                (dest_skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
            return True, f"Fetched '{skill_name}' directly from Hermes GitHub to {dest_skill_dir}", dest_skill_dir
        except Exception as exc:
            # Fallback to local curated template if network is unavailable or timed out
            fallback_text = FALLBACK_CURATED_SKILLS.get(skill_name)
            if fallback_text:
                (dest_skill_dir / "SKILL.md").write_text(fallback_text, encoding="utf-8")
                return True, f"Loaded curated offline template for '{skill_name}' into {dest_skill_dir}", dest_skill_dir
            return False, f"Failed to fetch '{skill_name}' from GitHub: {exc}", None

    return False, f"Skill '{skill_name}' not found in local Hermes repo or curated catalog", None


def import_hermes_skill_to_patterns(skill_name: str, learning_engine: Any) -> Dict[str, Any]:
    """Copy a Hermes skill and immediately import its workflow into DigitalSoul's learned patterns."""
    dest_skill_dir = get_digital_soul_skills_dir() / skill_name
    if not (dest_skill_dir / "SKILL.md").exists():
        ok, msg, path = copy_hermes_skill(skill_name)
        if not ok or not path:
            raise ValueError(msg)
        dest_skill_dir = path

    skill_md_text = (dest_skill_dir / "SKILL.md").read_text(encoding="utf-8-sig")
    res = learning_engine.import_skill(skill_md_text)
    res["hermes_skill"] = skill_name
    return res


def sync_all_curated_skills() -> Dict[str, Any]:
    """Ensure all curated Hermes skills are copied locally into DigitalSoul."""
    results = {}
    for c in CURATED_HERMES_SKILLS:
        ok, msg, _ = copy_hermes_skill(c["name"])
        results[c["name"]] = {"success": ok, "message": msg}
    return results
