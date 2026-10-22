"""Continuous multi-episode learning mode for DigitalSoul.

Discovers repeated workflows across multiple sessions, detects parameter slots
from observed variations, and reinforces learned patterns with every repetition.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

from ..learning.miner import needleman_wunsch_alignment
from ..learning.skill_recorder import SensitiveDataFilter, export_skill_markdown, parse_skill_markdown
from .catalog import short, understand_steps



def step_to_token(step: Dict[str, Any]) -> str:
    """Map a desktop UI step to a canonical semantic token for sequence alignment."""
    app = PureWindowsPath(step.get("path") or step.get("app") or "app").name.lower()
    kind = step.get("kind") or "action"
    target = step.get("target") or {}
    c_type = target.get("control_type") or "Control"
    label = (step.get("label") or "").strip().lower()

    if kind == "open_app":
        return f"{app}:open"
    if kind == "open_url":
        url = step.get("url") or ""
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower() if url else "web"
        return f"{app}:open_url:{domain}"
    if kind == "set_text":
        field_role = "search" if "search" in label else ("cell" if "cell" in label else "input")
        return f"{app}:set_text:{c_type}:{field_role}"
    if kind == "click":
        role = "button"
        if c_type in ("ListItem", "DataItem", "TreeItem"):
            role = "item"
        elif c_type == "Hyperlink":
            role = "link"
        elif label in ("send", "submit", "save", "ok", "confirm"):
            role = "confirm"
        return f"{app}:click:{c_type}:{role}"
    if kind == "press_enter":
        return f"{app}:enter"
    return f"{app}:{kind}"


def extract_parameter_slots(
    episodes: List[Dict[str, Any]],
    aligned_indices: List[List[Optional[int]]]
) -> List[Dict[str, Any]]:
    """Identify variable slots where input values or file targets varied across demonstrations."""
    slots = []
    if not aligned_indices or len(episodes) < 2:
        return slots

    num_steps = len(aligned_indices[0])
    for step_col in range(num_steps):
        step_variants = []
        for ep_idx, ep in enumerate(episodes):
            step_idx = aligned_indices[ep_idx][step_col]
            if step_idx is not None and step_idx < len(ep.get("steps", [])):
                step_variants.append(ep["steps"][step_idx])

        if len(step_variants) < 2:
            continue

        first = step_variants[0]
        kind = first.get("kind")
        if kind == "set_text":
            values = list(dict.fromkeys(s.get("value", "") for s in step_variants if s.get("value")))
            param_name = (first.get("label") or "input").lower().replace(" ", "_")
            if param_name in ("text_field", "control", "input"):
                param_name = f"text_entry_{step_col + 1}"
            slots.append({
                "step_index": step_col,
                "name": param_name,
                "kind": "text",
                "label": first.get("label") or "Input text",
                "default": values[-1] if values else "",
                "observed_values": values,
                "is_variable": len(values) > 1,
            })
        elif kind == "click" and (first.get("target") or {}).get("control_type") in ("ListItem", "DataItem"):
            labels = list(dict.fromkeys(s.get("label", "") for s in step_variants if s.get("label")))
            slots.append({
                "step_index": step_col,
                "name": f"item_selection_{step_col + 1}",
                "kind": "item",
                "label": "Selected item",
                "default": labels[-1] if labels else "",
                "observed_values": labels,
                "is_variable": len(labels) > 1,
            })
        elif kind == "open_url":
            urls = list(dict.fromkeys(s.get("url", "") for s in step_variants if s.get("url")))
            slots.append({
                "step_index": step_col,
                "name": f"url_{step_col + 1}",
                "kind": "url",
                "label": "Target URL",
                "default": urls[-1] if urls else "",
                "observed_values": urls,
                "is_variable": len(urls) > 1,
            })

    return slots


class LearningEngine:
    """Manages continuous episodic memory and mines recurring patterns across sessions."""

    def __init__(self, db_path: Path | str):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        with self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    name TEXT,
                    created_at TEXT,
                    steps_json TEXT NOT NULL,
                    tokens_json TEXT NOT NULL,
                    summary_json TEXT
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    support INTEGER NOT NULL DEFAULT 1,
                    alignment REAL NOT NULL DEFAULT 1.0,
                    skeleton_json TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    evidence_episodes_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def record_episode(
        self,
        steps: List[Dict[str, Any]],
        name: Optional[str] = None,
        flow: Optional[Dict[str, Any]] = None,
        voice_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an observed workflow episode, extract tokens, and match against patterns or past episodes."""
        from .session import clean_steps
        raw_steps = clean_steps(steps)
        if not raw_steps:
            return {"action": "ignored", "detail": "Empty or non-substantive step list"}

        # On-device privacy filtering (inspired by microsoft/skill-recorder)
        steps = []
        for s in raw_steps:
            clean_s, _ = SensitiveDataFilter.redact_step(s)
            steps.append(clean_s)

        scrubbed_voice, _ = SensitiveDataFilter.redact_text(voice_notes) if voice_notes else (None, 0)

        ep_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        tokens = [step_to_token(s) for s in steps]
        understanding = understand_steps(steps, flow=flow, name=name)
        if scrubbed_voice:
            understanding["voice_notes"] = scrubbed_voice
            if not name:
                name = scrubbed_voice.split(".")[0].strip()[:60].capitalize()

        with self.db:
            self.db.execute(
                "INSERT INTO episodes VALUES (?,?,?,?,?,?,?)",
                (ep_id, str(uuid.uuid4()), name or understanding.get("intent") or "Demonstration",
                 now, json.dumps(steps, ensure_ascii=False), json.dumps(tokens), json.dumps(understanding))
            )


        # 1. Check if this aligns with any existing learned pattern
        patterns = self.list_patterns()
        best_pattern = None
        best_sim = 0.0

        for pat in patterns:
            pat_tokens = [step_to_token(s) for s in pat["skeleton"]]
            sim, _ = needleman_wunsch_alignment(tokens, pat_tokens)
            if sim > best_sim and sim >= 0.65:
                best_sim = sim
                best_pattern = pat

        if best_pattern:
            return self._reinforce_pattern(best_pattern, ep_id, steps, tokens, best_sim, now)

        # 2. Check if this aligns with any past unclustered episode (forming a new pattern)
        rows = self.db.execute(
            "SELECT id, name, steps_json, tokens_json FROM episodes WHERE id != ? ORDER BY created_at DESC LIMIT 50",
            (ep_id,)
        ).fetchall()

        for past_id, past_name, past_steps_json, past_tokens_json in rows:
            past_tokens = json.loads(past_tokens_json)
            sim, _ = needleman_wunsch_alignment(tokens, past_tokens)
            if sim >= 0.65:
                past_steps = json.loads(past_steps_json)
                return self._create_pattern(
                    ep1={"id": past_id, "name": past_name, "steps": past_steps, "tokens": past_tokens},
                    ep2={"id": ep_id, "name": name, "steps": steps, "tokens": tokens},
                    similarity=sim,
                    timestamp=now
                )

        return {
            "action": "observed",
            "episode_id": ep_id,
            "tokens_count": len(tokens),
            "detail": "Observed initial demonstration. DigitalSoul is monitoring for repeated patterns."
        }

    def _reinforce_pattern(
        self,
        pattern: Dict[str, Any],
        new_ep_id: str,
        new_steps: List[Dict[str, Any]],
        new_tokens: List[str],
        similarity: float,
        timestamp: str
    ) -> Dict[str, Any]:
        """Reinforce an existing pattern with an additional demonstration."""
        pat_id = pattern["id"]
        evidence = pattern.get("evidence_episodes", [])
        if new_ep_id not in evidence:
            evidence.append(new_ep_id)

        new_support = pattern["support"] + 1
        new_alignment = round((pattern["alignment"] * pattern["support"] + similarity) / new_support, 3)

        # Update variable parameter slots based on differences
        pat_skeleton = pattern["skeleton"]
        # Align new steps to skeleton
        sim, pairs = needleman_wunsch_alignment([step_to_token(s) for s in pat_skeleton], new_tokens)
        aligned_pairs_idx = []
        new_step_ptr = 0
        for skel_tok, new_tok in pairs:
            if skel_tok is not None:
                if new_tok is not None and new_step_ptr < len(new_steps):
                    aligned_pairs_idx.append(new_step_ptr)
                    new_step_ptr += 1
                else:
                    aligned_pairs_idx.append(None)
            elif new_tok is not None:
                new_step_ptr += 1

        existing_params = {p["step_index"]: p for p in pattern.get("parameters", [])}
        for skel_idx, new_idx in enumerate(aligned_pairs_idx):
            if new_idx is not None and new_idx < len(new_steps) and skel_idx < len(pat_skeleton):
                skel_s = pat_skeleton[skel_idx]
                new_s = new_steps[new_idx]
                val_skel = skel_s.get("value") or skel_s.get("label") or skel_s.get("url")
                val_new = new_s.get("value") or new_s.get("label") or new_s.get("url")
                if val_skel and val_new and val_skel != val_new:
                    param = existing_params.get(skel_idx) or {
                        "step_index": skel_idx,
                        "name": f"param_{skel_idx + 1}",
                        "kind": "variable",
                        "label": skel_s.get("label") or f"Step {skel_idx + 1}",
                        "observed_values": [val_skel]
                    }
                    if val_new not in param.get("observed_values", []):
                        param.setdefault("observed_values", []).append(val_new)
                    param["is_variable"] = True
                    param["default"] = val_new
                    existing_params[skel_idx] = param

        params_list = sorted(existing_params.values(), key=lambda p: p["step_index"])

        with self.db:
            self.db.execute("""
                UPDATE learned_patterns
                SET support = ?, alignment = ?, parameters_json = ?, evidence_episodes_json = ?, updated_at = ?
                WHERE id = ?
            """, (new_support, new_alignment, json.dumps(params_list), json.dumps(evidence), timestamp, pat_id))

        return {
            "action": "reinforced",
            "pattern_id": pat_id,
            "name": pattern["name"],
            "support": new_support,
            "alignment": new_alignment,
            "parameters": params_list,
            "detail": f"Reinforced '{pattern['name']}' from {new_support} repeated demonstrations ({int(new_alignment * 100)}% consistency)."
        }

    def _create_pattern(
        self,
        ep1: Dict[str, Any],
        ep2: Dict[str, Any],
        similarity: float,
        timestamp: str
    ) -> Dict[str, Any]:
        """Synthesize a new learned pattern from two aligning episodes."""
        pat_id = f"pattern_{uuid.uuid4().hex[:8]}"
        tokens1, tokens2 = ep1["tokens"], ep2["tokens"]
        sim, pairs = needleman_wunsch_alignment(tokens1, tokens2)

        # Build skeleton from matching steps
        steps1, steps2 = ep1["steps"], ep2["steps"]
        idx1, idx2 = 0, 0
        skeleton: List[Dict[str, Any]] = []
        aligned_col_1: List[Optional[int]] = []
        aligned_col_2: List[Optional[int]] = []

        for t1, t2 in pairs:
            if t1 is not None and t2 is not None:
                # Aligned step: use latest step definition
                s = dict(steps2[idx2])
                skeleton.append(s)
                aligned_col_1.append(idx1)
                aligned_col_2.append(idx2)
                idx1 += 1
                idx2 += 1
            elif t1 is not None:
                idx1 += 1
            elif t2 is not None:
                idx2 += 1

        if not skeleton:
            skeleton = [dict(s) for s in steps2]
            aligned_col_1 = list(range(len(steps1)))
            aligned_col_2 = list(range(len(steps2)))

        params = extract_parameter_slots([ep1, ep2], [aligned_col_1, aligned_col_2])
        understanding = understand_steps(skeleton, name=ep2.get("name"))
        name = ep2.get("name") or understanding.get("intent") or "Learned Routine"

        with self.db:
            self.db.execute("""
                INSERT INTO learned_patterns VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                pat_id, name, understanding.get("intent") or name, 2, round(similarity, 3),
                json.dumps(skeleton, ensure_ascii=False), json.dumps(params),
                json.dumps([ep1["id"], ep2["id"]]), timestamp, timestamp
            ))

        return {
            "action": "discovered",
            "pattern_id": pat_id,
            "name": name,
            "support": 2,
            "alignment": round(similarity, 3),
            "parameters": params,
            "detail": f"Learned pattern '{name}' from 2 matching demonstrations ({int(similarity * 100)}% consistency)."
        }

    def list_patterns(self) -> List[Dict[str, Any]]:
        """List all discovered patterns and skills learned from repeated demonstrations."""
        rows = self.db.execute("""
            SELECT id, name, intent, support, alignment, skeleton_json, parameters_json, evidence_episodes_json, created_at, updated_at
            FROM learned_patterns ORDER BY support DESC, updated_at DESC
        """).fetchall()

        patterns = []
        for r in rows:
            patterns.append({
                "id": r[0],
                "name": r[1],
                "intent": r[2],
                "support": r[3],
                "alignment": r[4],
                "skeleton": json.loads(r[5]),
                "parameters": json.loads(r[6]),
                "evidence_episodes": json.loads(r[7]),
                "created_at": r[8],
                "updated_at": r[9],
                "step_count": len(json.loads(r[5]))
            })
        return patterns

    def get_pattern(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.execute("""
            SELECT id, name, intent, support, alignment, skeleton_json, parameters_json, evidence_episodes_json, created_at, updated_at
            FROM learned_patterns WHERE id = ?
        """, (pattern_id,)).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "intent": row[2],
            "support": row[3],
            "alignment": row[4],
            "skeleton": json.loads(row[5]),
            "parameters": json.loads(row[6]),
            "evidence_episodes": json.loads(row[7]),
            "created_at": row[8],
            "updated_at": row[9],
            "step_count": len(json.loads(row[5]))
        }

    def adapt_pattern_steps(self, pattern_id: str, overrides: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Inject customized parameter values into the pattern's skeleton steps for execution."""
        pattern = self.get_pattern(pattern_id)
        if not pattern:
            raise ValueError(f"Learned pattern '{pattern_id}' not found")

        overrides = overrides or {}
        steps = [dict(s) for s in pattern["skeleton"]]
        params = {p["name"]: p for p in pattern.get("parameters", [])}

        for param_name, new_val in overrides.items():
            if param_name in params:
                step_idx = params[param_name].get("step_index")
                if step_idx is not None and step_idx < len(steps):
                    target_step = steps[step_idx]
                    if target_step["kind"] == "set_text":
                        target_step["value"] = str(new_val)
                    elif target_step["kind"] == "open_url":
                        target_step["url"] = str(new_val)
                    elif target_step["kind"] == "click":
                        target_step["label"] = str(new_val)
                        if "target" in target_step and isinstance(target_step["target"], dict):
                            target_step["target"]["name"] = str(new_val)

        return steps

    def export_skill(self, item_id: str) -> str:
        """Export a learned pattern or recorded demonstration to standard Microsoft SKILL.md format."""
        pattern = self.get_pattern(item_id)
        if pattern:
            return export_skill_markdown(pattern)
        row = self.db.execute("SELECT id, name, steps_json, summary_json FROM episodes WHERE id = ?", (item_id,)).fetchone()
        if row:
            steps = json.loads(row[2])
            summary = json.loads(row[3]) if row[3] else {}
            return export_skill_markdown({
                "name": row[1],
                "intent": summary.get("intent", row[1]),
                "steps": steps,
                "parameters": summary.get("parameters", []),
                "support": 1,
            })
        raise ValueError(f"Pattern or episode '{item_id}' not found")

    def import_skill(self, markdown_text: str) -> Dict[str, Any]:
        """Import a standard Microsoft SKILL.md document into learned patterns."""
        candidate = parse_skill_markdown(markdown_text)
        desktop_steps = []
        for s in candidate.steps:
            target_name = s.locators[0].value if s.locators else "Element"
            desktop_steps.append({
                "kind": "click" if s.verb == "click" else ("set_text" if s.verb in ("set_field", "set_text") else "action"),
                "app": s.app,
                "label": target_name,
                "value": s.input if isinstance(s.input, str) else "",
                "target": {"name": target_name},
            })
        now = datetime.now(timezone.utc).isoformat()
        pat_id = f"pattern_{uuid.uuid4().hex[:8]}"
        params = candidate.parameters or []
        with self.db:
            self.db.execute("""
                INSERT INTO learned_patterns VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                pat_id, candidate.name, candidate.description, 1, 1.0,
                json.dumps(desktop_steps, ensure_ascii=False), json.dumps(params),
                json.dumps([f"import_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"]),
                now, now
            ))
        return {
            "action": "imported",
            "pattern_id": pat_id,
            "name": candidate.name,
            "step_count": len(desktop_steps),
            "parameters": params,
        }

    def evaluate_skill(self, item_id: str) -> Dict[str, Any]:
        """Evaluate locator resilience, guardrails, and privacy posture of a pattern or episode."""
        from ..learning.skill_recorder import SkillQualityEvaluator, parse_skill_markdown
        skill_md = self.export_skill(item_id)
        candidate = parse_skill_markdown(skill_md)
        report = SkillQualityEvaluator.evaluate(candidate)
        return report.model_dump()

    def optimize_skill(self, item_id: str) -> Dict[str, Any]:
        """Optimize a pattern or episode with noise compaction, multi-strategy locators, and quality scoring."""
        from ..learning.skill_recorder import SkillOptimizer
        pattern = self.get_pattern(item_id)
        steps = pattern["skeleton"] if pattern else None
        if not steps:
            row = self.db.execute("SELECT steps_json FROM episodes WHERE id = ?", (item_id,)).fetchone()
            if row:
                steps = json.loads(row[0])
        if not steps:
            raise ValueError(f"Pattern or episode '{item_id}' not found")
        return SkillOptimizer.optimize(steps)


    def delete_pattern(self, pattern_id: str):
        with self.db:
            self.db.execute("DELETE FROM learned_patterns WHERE id = ?", (pattern_id,))

    def record_outcome(self, source_ids, ok):
        # Worker completion is not independent verification of a business outcome.
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS routine_outcomes (source_id TEXT PRIMARY KEY, completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0)")
            for source_id in set(source_ids):
                self.db.execute("INSERT INTO routine_outcomes VALUES (?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET completed=completed+excluded.completed, failed=failed+excluded.failed",
                                (source_id, int(ok is True), int(ok is not True)))

    def outcome_summary(self, source_id):
        self.db.execute("CREATE TABLE IF NOT EXISTS routine_outcomes (source_id TEXT PRIMARY KEY, completed INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0)")
        row = self.db.execute("SELECT completed, failed FROM routine_outcomes WHERE source_id=?", (source_id,)).fetchone()
        return {"worker_completed": row[0], "worker_failed": row[1]} if row else {}

    def close(self):
        self.db.close()

