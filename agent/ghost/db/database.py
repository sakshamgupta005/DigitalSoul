"""SQLite persistence layer for DigitalSoul (Developer 1 / M1).

Configured with WAL mode for high concurrency and zero external dependencies.
Stores raw events, episodes, candidates, workflows, and settings.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from contracts import RawEvent, Workflow
from agent.ghost.learning.models import Episode, WorkflowCandidate

# Register UUID adapter for sqlite3 so UUID objects serialize smoothly
sqlite3.register_adapter(uuid.UUID, str)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "ghost.db"


class Database:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode and foreign keys as mandated by spec
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def init_db(self):
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS raw_events (
                    event_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    app_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_session ON raw_events(session_id);
                CREATE INDEX IF NOT EXISTS idx_events_ts ON raw_events(ts);

                CREATE TABLE IF NOT EXISTS episodes (
                    episode_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    start_ts TEXT NOT NULL,
                    end_ts TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    tokens_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflow_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    support INTEGER NOT NULL,
                    alignment REAL NOT NULL,
                    dismissed INTEGER NOT NULL DEFAULT 0,
                    candidate_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    trust TEXT NOT NULL,
                    remote_allowed INTEGER NOT NULL DEFAULT 0,
                    canonical_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL
                );
            """)

    # --- Raw Events ---

    def insert_raw_event(self, event: RawEvent) -> bool:
        """Insert a RawEvent. Returns False if duplicate. Raises ValueError if conflicting."""
        event_dict = event.model_dump(mode="json")
        payload = json.dumps(event_dict, sort_keys=True)
        event_id_str = str(event.event_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT payload_json FROM raw_events WHERE event_id = ?",
                (event_id_str,)
            ).fetchone()

            if row:
                if row["payload_json"] == payload:
                    return False  # Identical duplicate (no-op)
                raise ValueError(f"Conflicting content for existing event_id: {event_id_str}")

            conn.execute(
                """
                INSERT INTO raw_events (event_id, ts, session_id, source, kind, app_id, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id_str,
                    str(event.ts),
                    str(event.session_id),
                    event.source.value if hasattr(event.source, "value") else str(event.source),
                    event.kind.value if hasattr(event.kind, "value") else str(event.kind),
                    event.app.id,
                    payload,
                )
            )
            return True

    def get_raw_event(self, event_id: str | uuid.UUID) -> Optional[RawEvent]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT payload_json FROM raw_events WHERE event_id = ?", (str(event_id),)).fetchone()
            if row:
                return RawEvent.model_validate_json(row["payload_json"])
            return None

    # --- Episodes ---

    def save_episodes(self, episodes: List[Episode]):
        with self._get_connection() as conn:
            for ep in episodes:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO episodes (episode_id, session_id, start_ts, end_ts, actions_json, tokens_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ep.episode_id,
                        ep.session_id,
                        ep.start_ts,
                        ep.end_ts,
                        json.dumps([a.model_dump(mode="json") for a in ep.actions]),
                        json.dumps(ep.tokens),
                    )
                )

    def get_all_episodes(self) -> List[Episode]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM episodes ORDER BY start_ts ASC").fetchall()
            result: List[Episode] = []
            for r in rows:
                actions_data = json.loads(r["actions_json"])
                tokens_data = json.loads(r["tokens_json"])
                result.append(
                    Episode(
                        episode_id=r["episode_id"],
                        session_id=r["session_id"],
                        start_ts=r["start_ts"],
                        end_ts=r["end_ts"],
                        actions=actions_data,
                        tokens=tokens_data,
                    )
                )
            return result

    # --- Workflow Candidates ---

    def save_candidate(self, candidate: WorkflowCandidate):
        cand_dict = candidate.model_dump(mode="json")
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_candidates (candidate_id, name, support, alignment, dismissed, candidate_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.candidate_id,
                    candidate.name,
                    candidate.support,
                    candidate.alignment,
                    1 if candidate.dismissed else 0,
                    json.dumps(cand_dict),
                )
            )

    def get_all_candidates(self) -> List[WorkflowCandidate]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT candidate_json FROM workflow_candidates").fetchall()
            return [WorkflowCandidate.model_validate_json(r["candidate_json"]) for r in rows]

    def dismiss_candidate(self, candidate_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE workflow_candidates SET dismissed = 1 WHERE candidate_id = ?",
                (candidate_id,)
            )
            if cursor.rowcount > 0:
                # Also update inside the json blob
                row = conn.execute("SELECT candidate_json FROM workflow_candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
                if row:
                    cand = json.loads(row["candidate_json"])
                    cand["dismissed"] = True
                    conn.execute("UPDATE workflow_candidates SET candidate_json = ? WHERE candidate_id = ?", (json.dumps(cand), candidate_id))
                return True
            return False

    # --- Workflows ---

    def save_workflow(self, workflow: Workflow):
        wf_dict = workflow.model_dump(mode="json", by_alias=True)
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        trust_str = workflow.trust.value if hasattr(workflow.trust, "value") else str(workflow.trust)
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflows (workflow_id, version, name, trust, remote_allowed, canonical_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow.workflow_id,
                    workflow.version,
                    workflow.name,
                    trust_str,
                    1 if workflow.remote_allowed else 0,
                    json.dumps(wf_dict),
                    now_iso,
                )
            )

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT canonical_json FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if row:
                return Workflow.model_validate_json(row["canonical_json"])
            return None

    def get_all_workflows(self) -> List[Workflow]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT canonical_json FROM workflows ORDER BY updated_at DESC").fetchall()
            return [Workflow.model_validate_json(r["canonical_json"]) for r in rows]

    # --- Settings ---

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._get_connection() as conn:
            row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row["value_json"])
            return default

    def set_setting(self, key: str, value: Any):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value_json) VALUES (?, ?)",
                (key, json.dumps(value))
            )
