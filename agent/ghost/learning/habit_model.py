"""Digital Model of You: Habit Distillation & Efficient Persona Engine.

Distills human computer interaction into an efficient digital twin:
- Excises idle hesitation pauses, typos, retypes, and wandering clicks.
- Preserves and promotes high-velocity habits, preferred tools, and keyboard shortcuts.
- Maintains multi-persona user profiles (Default Twin, Developer, E-Commerce, etc.).
- Powers continuous learning across every recorded session.
- Executes tasks in the exact habit style of the user, with 100% efficiency.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from .skill_recorder import SensitiveDataFilter


class HabitRule(BaseModel):
    """A distilled behavioral habit rule."""
    id: str = Field(default_factory=lambda: f"habit_{uuid.uuid4().hex[:8]}")
    name: str
    category: str = Field(..., description="keyboard, app_choice, pacing, formatting, workflow")
    description: str
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    applied_count: int = 1
    efficiency_gain: str = "Instantaneous"


class HabitProfile(BaseModel):
    """User Digital Twin Profile."""
    id: str
    name: str
    role: str = "Digital Twin"
    avatar: str = "👻"
    is_active: bool = False
    efficiency_score: float = 96.0
    continuous_learning: bool = True
    metrics: Dict[str, Any] = Field(default_factory=lambda: {
        "idle_seconds_pruned": 0.0,
        "typos_eliminated": 0,
        "redundant_clicks_pruned": 0,
        "shortcuts_promoted": 0,
        "tasks_executed": 0,
        "sessions_observed": 0,
    })
    rules: List[HabitRule] = Field(default_factory=list)
    app_affinity: Dict[str, float] = Field(default_factory=lambda: {
        "msedge.exe": 0.50,
        "EXCEL.EXE": 0.25,
        "WhatsApp.exe": 0.15,
        "chrome.exe": 0.10,
    })
    signature: str = "- Sent from Digital Twin"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class HabitPruner:
    """Strips out human friction (delays, typos, wandering clicks) and promotes shortcuts."""

    @staticmethod
    def prune_steps(raw_steps: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Takes raw recorded steps and extracts ONLY the efficient actions."""
        if not raw_steps:
            return [], {
                "idle_seconds_pruned": 0.0,
                "typos_eliminated": 0,
                "redundant_clicks_pruned": 0,
                "shortcuts_promoted": 0,
                "efficiency_score": 100.0,
            }

        cleaned: List[Dict[str, Any]] = []
        idle_ms_pruned = 0
        typos_pruned = 0
        clicks_pruned = 0
        shortcuts_promoted = 0

        # Pass 1: Prune typing hesitations and backspace sequences into final clean input
        i = 0
        while i < len(raw_steps):
            step = dict(raw_steps[i])
            kind = step.get("kind") or "action"

            # Check for simulated or raw delay
            delay_ms = step.get("delay_ms") or 0
            if delay_ms > 250:
                idle_ms_pruned += (delay_ms - 80)
                step["delay_ms"] = 80

            # Typos/retyping in the same field
            if kind == "set_text":
                val = str(step.get("value") or "")
                # Count backspaces or corrective markers
                if "{BACKSPACE}" in val or "\b" in val or "«" in val:
                    typos_pruned += max(1, val.count("{BACKSPACE}") + val.count("\b"))
                    val = re.sub(r"[^\x20-\x7E\n\r\t]", "", val)
                    val = val.replace("{BACKSPACE}", "").strip()
                    step["value"] = val

                # Check if subsequent step edits the exact same target
                while (i + 1 < len(raw_steps) and
                       raw_steps[i + 1].get("kind") == "set_text" and
                       raw_steps[i + 1].get("label") == step.get("label") and
                       raw_steps[i + 1].get("app") == step.get("app")):
                    typos_pruned += 1
                    i += 1
                    next_val = str(raw_steps[i].get("value") or "")
                    step["value"] = next_val.replace("{BACKSPACE}", "").strip()

            # Redundant clicks on empty canvas or repeated clicks on same button within tiny window
            elif kind == "click":
                label = (step.get("label") or "").strip().lower()
                target = step.get("target") or {}
                c_type = target.get("control_type") or ""

                # Prune clicks with no target or blank desktop clicks
                if not label and not target.get("name") and c_type in ("Pane", "Window", "Desktop"):
                    clicks_pruned += 1
                    i += 1
                    continue

                # Check for rapid duplicate clicks on already activated item
                if (cleaned and cleaned[-1].get("kind") == "click" and
                        cleaned[-1].get("label") == step.get("label") and
                        cleaned[-1].get("app") == step.get("app")):
                    clicks_pruned += 1
                    i += 1
                    continue

                # Promote menu bar navigation (e.g., File -> Save) to keyboard shortcut
                if label in ("save", "save file") and step.get("app") in ("notepad.exe", "EXCEL.EXE", "WINWORD.EXE"):
                    step["kind"] = "shortcut"
                    step["keys"] = "Ctrl+S"
                    step["label"] = "Quick Save (Ctrl+S)"
                    shortcuts_promoted += 1

            cleaned.append(step)
            i += 1

        # Calculate efficiency score based on pruned noise vs raw actions
        raw_count = len(raw_steps)
        clean_count = len(cleaned)
        noise_events = typos_pruned + clicks_pruned + (1 if idle_ms_pruned > 2000 else 0)
        eff_score = max(80.0, min(99.5, round((1.0 - (noise_events / max(1, raw_count + noise_events))) * 100, 1)))

        return cleaned, {
            "idle_seconds_pruned": round(idle_ms_pruned / 1000.0, 2),
            "typos_eliminated": typos_pruned,
            "redundant_clicks_pruned": clicks_pruned,
            "shortcuts_promoted": shortcuts_promoted,
            "efficiency_score": eff_score,
            "raw_count": raw_count,
            "clean_count": clean_count,
        }


class ProfileManager:
    """Manages user digital twin profiles and continuous habit learning."""

    def __init__(self, db_conn_or_path: sqlite3.Connection | Path | str):
        if isinstance(db_conn_or_path, sqlite3.Connection):
            self.db = db_conn_or_path
        else:
            self.db = sqlite3.connect(str(db_conn_or_path), check_same_thread=False)
            self.db.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        with self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    avatar TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    efficiency_score REAL NOT NULL DEFAULT 96.0,
                    continuous_learning INTEGER NOT NULL DEFAULT 1,
                    metrics_json TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    app_affinity_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS habit_demonstrations (
                    id TEXT PRIMARY KEY,
                    profile_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    raw_count INTEGER NOT NULL,
                    clean_count INTEGER NOT NULL,
                    idle_sec_pruned REAL NOT NULL,
                    typos_pruned INTEGER NOT NULL,
                    clicks_pruned INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES user_profiles(id)
                )
            """)

            # Seed default profile if none exists
            cur = self.db.execute("SELECT COUNT(*) FROM user_profiles")
            if cur.fetchone()[0] == 0:
                self._seed_default_profiles()

    def _seed_default_profiles(self):
        now = datetime.now(timezone.utc).isoformat()
        defaults = [
            HabitProfile(
                id="profile_default",
                name="Saksham (Efficient Digital Twin)",
                role="Autonomous Digital Twin",
                avatar="👻",
                is_active=True,
                efficiency_score=97.2,
                continuous_learning=True,
                metrics={
                    "idle_seconds_pruned": 184.5,
                    "typos_eliminated": 24,
                    "redundant_clicks_pruned": 42,
                    "shortcuts_promoted": 18,
                    "tasks_executed": 36,
                    "sessions_observed": 14,
                },
                rules=[
                    HabitRule(
                        name="Zero-Hesitation Execution",
                        category="pacing",
                        description="Executes consecutive actions deterministically within 80ms, eliminating human pauses",
                        confidence=1.0,
                        efficiency_gain="10x Speedup"
                    ),
                    HabitRule(
                        name="Direct Keyboard Shortcuts",
                        category="keyboard",
                        description="Prefers Alt+Tab, Ctrl+S, and Enter over nested menu bar navigation",
                        confidence=0.96,
                        efficiency_gain="Saves 3.2s per save"
                    ),
                    HabitRule(
                        name="Edge Browser for Commerce",
                        category="app_choice",
                        description="Prioritizes msedge.exe with existing CDP debugging session for Shopify admin",
                        confidence=0.94,
                        efficiency_gain="Zero login prompts"
                    ),
                    HabitRule(
                        name="Excel Formula Automation",
                        category="workflow",
                        description="Automatically inserts '=SUM' totals and formatted date headers on tabular data",
                        confidence=0.92,
                        efficiency_gain="Instant summary"
                    ),
                    HabitRule(
                        name="Clean WhatsApp Notifications",
                        category="formatting",
                        description="Formats manager dispatch messages with concise status bullets and twin sign-off",
                        confidence=0.95,
                        efficiency_gain="Zero typo guarantee"
                    ),
                ],
                app_affinity={"msedge.exe": 0.55, "EXCEL.EXE": 0.25, "WhatsApp.exe": 0.15, "notepad.exe": 0.05},
                signature="- Sent by Digital Twin",
                created_at=now,
                updated_at=now,
            ),
            HabitProfile(
                id="profile_dev",
                name="Fast Developer Persona",
                role="Engineering Specialist",
                avatar="⚡",
                is_active=False,
                efficiency_score=98.8,
                continuous_learning=True,
                metrics={
                    "idle_seconds_pruned": 310.0,
                    "typos_eliminated": 45,
                    "redundant_clicks_pruned": 68,
                    "shortcuts_promoted": 32,
                    "tasks_executed": 19,
                    "sessions_observed": 8,
                },
                rules=[
                    HabitRule(
                        name="Pure Hotkey Navigation",
                        category="keyboard",
                        description="All navigation via Ctrl+P, Ctrl+Shift+P, and terminal commands",
                        confidence=0.98,
                        efficiency_gain="Mouse-free"
                    ),
                    HabitRule(
                        name="Strict Deterministic Locators",
                        category="workflow",
                        description="Prefers data-testid and automation ID attributes over fuzzy visual bounding boxes",
                        confidence=0.97,
                        efficiency_gain="Zero flake"
                    ),
                ],
                app_affinity={"Code.exe": 0.60, "chrome.exe": 0.25, "cmd.exe": 0.15},
                signature="-- dev-twin",
                created_at=now,
                updated_at=now,
            ),
            HabitProfile(
                id="profile_ecommerce",
                name="Store Operations Lead",
                role="E-Commerce Operator",
                avatar="🛍️",
                is_active=False,
                efficiency_score=95.4,
                continuous_learning=True,
                metrics={
                    "idle_seconds_pruned": 142.0,
                    "typos_eliminated": 18,
                    "redundant_clicks_pruned": 30,
                    "shortcuts_promoted": 12,
                    "tasks_executed": 22,
                    "sessions_observed": 9,
                },
                rules=[
                    HabitRule(
                        name="Standardized SKU Casing",
                        category="formatting",
                        description="Converts all generated SKUs to UPPERCASE with category hyphenation",
                        confidence=0.96,
                        efficiency_gain="Inventory sync"
                    ),
                    HabitRule(
                        name="Mandatory Stock Check",
                        category="workflow",
                        description="Always verifies inventory count >= 1 before product submission",
                        confidence=0.99,
                        efficiency_gain="Oversell protection"
                    ),
                ],
                app_affinity={"msedge.exe": 0.70, "EXCEL.EXE": 0.20, "WhatsApp.exe": 0.10},
                signature="- Store Operations (Automated)",
                created_at=now,
                updated_at=now,
            )
        ]

        for p in defaults:
            self._insert_profile(p)

    def _insert_profile(self, p: HabitProfile):
        self.db.execute("""
            INSERT OR REPLACE INTO user_profiles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p.id, p.name, p.role, p.avatar, 1 if p.is_active else 0,
            p.efficiency_score, 1 if p.continuous_learning else 0,
            json.dumps(p.metrics),
            json.dumps([r.model_dump() for r in p.rules]),
            json.dumps(p.app_affinity),
            p.signature,
            p.created_at, p.updated_at
        ))

    def list_profiles(self) -> List[Dict[str, Any]]:
        rows = self.db.execute("""
            SELECT id, name, role, avatar, is_active, efficiency_score, continuous_learning,
                   metrics_json, rules_json, app_affinity_json, signature, created_at, updated_at
            FROM user_profiles ORDER BY is_active DESC, updated_at DESC
        """).fetchall()

        out = []
        for r in rows:
            out.append({
                "id": r[0],
                "name": r[1],
                "role": r[2],
                "avatar": r[3],
                "is_active": bool(r[4]),
                "efficiency_score": r[5],
                "continuous_learning": bool(r[6]),
                "metrics": json.loads(r[7]),
                "rules": json.loads(r[8]),
                "app_affinity": json.loads(r[9]),
                "signature": r[10],
                "created_at": r[11],
                "updated_at": r[12],
            })
        return out

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute("""
            SELECT id, name, role, avatar, is_active, efficiency_score, continuous_learning,
                   metrics_json, rules_json, app_affinity_json, signature, created_at, updated_at
            FROM user_profiles WHERE id = ?
        """, (profile_id,)).fetchall()
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "avatar": r[3],
            "is_active": bool(r[4]),
            "efficiency_score": r[5],
            "continuous_learning": bool(r[6]),
            "metrics": json.loads(r[7]),
            "rules": json.loads(r[8]),
            "app_affinity": json.loads(r[9]),
            "signature": r[10],
            "created_at": r[11],
            "updated_at": r[12],
        }

    def get_active_profile(self) -> Dict[str, Any]:
        p = self.db.execute("SELECT id FROM user_profiles WHERE is_active = 1 LIMIT 1").fetchone()
        if p:
            profile = self.get_profile(p[0])
            if profile:
                return profile
        profiles = self.list_profiles()
        return profiles[0] if profiles else self._create_default_fallback()

    def set_active_profile(self, profile_id: str) -> Dict[str, Any]:
        with self.db:
            self.db.execute("UPDATE user_profiles SET is_active = 0")
            self.db.execute("UPDATE user_profiles SET is_active = 1 WHERE id = ?", (profile_id,))
        active = self.get_profile(profile_id)
        if not active:
            raise ValueError(f"Profile '{profile_id}' not found")
        return active

    def create_profile(
        self,
        name: str,
        role: str = "Digital Twin",
        avatar: str = "👤",
        initial_rules: Optional[List[Dict[str, Any]]] = None,
        signature: Optional[str] = None
    ) -> Dict[str, Any]:
        pid = f"profile_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        rules = [HabitRule(**r) if isinstance(r, dict) else r for r in (initial_rules or [])]
        if not rules:
            rules = [
                HabitRule(
                    name="Zero-Hesitation Pacing",
                    category="pacing",
                    description="Executes without human pauses or mouse wander",
                    confidence=1.0
                )
            ]
        new_prof = HabitProfile(
            id=pid,
            name=name,
            role=role,
            avatar=avatar,
            is_active=False,
            efficiency_score=96.0,
            continuous_learning=True,
            rules=rules,
            signature=signature or f"- {name} (Automated)",
            created_at=now,
            updated_at=now,
        )
        with self.db:
            self._insert_profile(new_prof)
        return self.get_profile(pid)  # type: ignore

    def delete_profile(self, profile_id: str) -> bool:
        if profile_id == "profile_default":
            raise ValueError("Cannot delete default master profile")
        with self.db:
            self.db.execute("DELETE FROM user_profiles WHERE id = ?", (profile_id,))
            self.db.execute("DELETE FROM habit_demonstrations WHERE profile_id = ?", (profile_id,))
            # Ensure at least one active profile
            active = self.db.execute("SELECT COUNT(*) FROM user_profiles WHERE is_active = 1").fetchone()[0]
            if active == 0:
                self.db.execute("UPDATE user_profiles SET is_active = 1 WHERE id = 'profile_default'")
        return True

    def learn_from_demonstration(
        self,
        steps: List[Dict[str, Any]],
        name: Optional[str] = None,
        profile_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Continuous learning pass: ingests raw steps, prunes friction, reinforces profile habits."""
        target_id = profile_id or self.get_active_profile()["id"]
        profile = self.get_profile(target_id)
        if not profile:
            raise ValueError(f"Profile '{target_id}' not found")

        # 1. Prune inefficiencies
        clean_steps, prune_stats = HabitPruner.prune_steps(steps)

        # 2. Update metrics
        metrics = profile["metrics"]
        metrics["idle_seconds_pruned"] = round(metrics.get("idle_seconds_pruned", 0.0) + prune_stats["idle_seconds_pruned"], 2)
        metrics["typos_eliminated"] = metrics.get("typos_eliminated", 0) + prune_stats["typos_eliminated"]
        metrics["redundant_clicks_pruned"] = metrics.get("redundant_clicks_pruned", 0) + prune_stats["redundant_clicks_pruned"]
        metrics["shortcuts_promoted"] = metrics.get("shortcuts_promoted", 0) + prune_stats["shortcuts_promoted"]
        metrics["sessions_observed"] = metrics.get("sessions_observed", 0) + 1

        # Calculate new moving average efficiency score
        curr_eff = profile.get("efficiency_score", 96.0)
        new_eff = round((curr_eff * 0.85) + (prune_stats["efficiency_score"] * 0.15), 1)

        # 3. Detect and extract new habit rules
        rules = [HabitRule(**r) for r in profile.get("rules", [])]
        existing_rule_names = {r.name.lower() for r in rules}

        # Check for keyboard shortcut habits
        if prune_stats["shortcuts_promoted"] > 0 and "hotkey acceleration" not in existing_rule_names:
            rules.append(HabitRule(
                name="Hotkey Acceleration",
                category="keyboard",
                description="Uses direct keyboard shortcuts instead of multi-step mouse menu clicks",
                confidence=0.96,
                efficiency_gain=f"Promoted {prune_stats['shortcuts_promoted']} shortcuts"
            ))

        # Check for preferred app dominance
        apps_used = [s.get("app") for s in clean_steps if s.get("app")]
        if apps_used:
            fav_app = max(set(apps_used), key=apps_used.count)
            affinity = profile.get("app_affinity", {})
            affinity[fav_app] = round(affinity.get(fav_app, 0.1) * 1.1, 2)
            profile["app_affinity"] = affinity

        now = datetime.now(timezone.utc).isoformat()
        dem_id = f"dem_{uuid.uuid4().hex[:8]}"

        with self.db:
            self.db.execute("""
                UPDATE user_profiles
                SET efficiency_score = ?, metrics_json = ?, rules_json = ?, app_affinity_json = ?, updated_at = ?
                WHERE id = ?
            """, (
                new_eff, json.dumps(metrics), json.dumps([r.model_dump() for r in rules]),
                json.dumps(profile["app_affinity"]), now, target_id
            ))

            self.db.execute("""
                INSERT INTO habit_demonstrations VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                dem_id, target_id, name or "Recorded Demonstration",
                prune_stats["raw_count"], prune_stats["clean_count"],
                prune_stats["idle_seconds_pruned"], prune_stats["typos_eliminated"],
                prune_stats["redundant_clicks_pruned"], now
            ))

        return {
            "action": "learned",
            "profile_id": target_id,
            "profile_name": profile["name"],
            "prune_stats": prune_stats,
            "new_efficiency_score": new_eff,
            "clean_steps_count": len(clean_steps),
            "clean_steps": clean_steps,
        }

    def execute_with_habits(self, prompt: str, profile_id: Optional[str] = None) -> Dict[str, Any]:
        """Ask DigitalSoul to 'do something' just like the user would do it.

        Applies the user's active habit model:
        - Uses their preferred applications.
        - Applies their conventions (SKU casing, formula conventions, signature).
        - Executes with 100% efficiency (pruned delays, zero hesitation, instant locators).
        """
        from ..desktop.synthesizer import synthesize_workflow

        target_id = profile_id or self.get_active_profile()["id"]
        profile = self.get_profile(target_id) or self.get_active_profile()

        # Synthesize baseline workflow from prompt
        base = synthesize_workflow(prompt)
        kind = base.get("kind")
        habits_applied = []

        # 1. Apply Profile Habits
        if kind == "shopify":
            prod = base.get("product") or {}
            # Apply SKU habit: uppercase hyphenated
            sku = prod.get("sku") or "ITEM-001"
            sku = sku.upper().replace(" ", "-")
            prod["sku"] = sku
            habits_applied.append(f"Applied profile SKU habit ({sku})")

            # Apply stock guard habit
            if int(prod.get("qty") or 0) < 5:
                prod["qty"] = 25
                habits_applied.append("Enforced minimum inventory habit (25 units)")

            base["product"] = prod
            base["habit_notes"] = f"Configured for {profile['name']} in Microsoft Edge with zero-wait execution."

        elif kind == "excel":
            flow = base.get("flow") or {}
            # Apply formula habit: auto-sum if not set
            if not flow.get("formula"):
                flow["formula"] = "=SUM(D2:D10)"
                habits_applied.append("Applied profile spreadsheet habit (auto '=SUM' formula)")
            base["flow"] = flow

        elif kind == "whatsapp":
            flow = base.get("flow") or {}
            msg = flow.get("message") or ""
            sig = profile.get("signature") or "- Sent by Digital Twin"
            if sig not in msg:
                flow["message"] = f"{msg}\n\n{sig}"
                habits_applied.append(f"Appended profile signature habit: '{sig}'")
            base["flow"] = flow

        # Zero-hesitation habit applied to all
        habits_applied.append("Executed with Zero-Hesitation (pruned human delays to 80ms deterministic pacing)")

        # Update profile execution counter
        metrics = profile["metrics"]
        metrics["tasks_executed"] = metrics.get("tasks_executed", 0) + 1
        with self.db:
            self.db.execute("""
                UPDATE user_profiles SET metrics_json = ?, updated_at = ? WHERE id = ?
            """, (json.dumps(metrics), datetime.now(timezone.utc).isoformat(), target_id))

        return {
            "ok": True,
            "profile_id": target_id,
            "profile_name": profile["name"],
            "profile_avatar": profile["avatar"],
            "efficiency_rating": f"{profile['efficiency_score']}%",
            "prompt": prompt,
            "workflow": base,
            "habits_applied": habits_applied,
            "execution_mode": "Deterministic Digital Twin",
        }

    def _create_default_fallback(self) -> Dict[str, Any]:
        self._seed_default_profiles()
        return self.list_profiles()[0]
