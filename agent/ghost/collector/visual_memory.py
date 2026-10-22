"""Visual Memory and Activity Tracker for DigitalSoul.

Combines:
1. High-speed DirectX screen capture via dxcam.
2. Offline native Windows OCR via WinRT OCR (Windows.Media.Ocr).
3. Local model synthesis via OmniRoute (http://127.0.0.1:20128/v1).
4. Persistent SQLite storage for episodic user activity memory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.ghost.collector.ocr_winrt import OcrResult, WinRtOcrEngine
from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.integrations.omniroute import OmniRouteClient, OmniRouteSettings


@dataclass
class VisualMemoryItem:
    id: Optional[int]
    timestamp: str
    app: str
    window_title: str
    action_kind: str
    coords_x: Optional[float]
    coords_y: Optional[float]
    target_name: Optional[str]
    screenshot_path: Optional[str]
    ocr_text: str
    ocr_items: List[Dict[str, Any]]
    intent_summary: Optional[str]
    session_id: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "app": self.app,
            "window_title": self.window_title,
            "action_kind": self.action_kind,
            "coordinates": [self.coords_x, self.coords_y] if self.coords_x is not None else None,
            "target_name": self.target_name,
            "screenshot_path": self.screenshot_path,
            "ocr_text": self.ocr_text,
            "ocr_items_count": len(self.ocr_items),
            "intent_summary": self.intent_summary,
            "session_id": self.session_id,
        }


class VisualMemory:
    """Episodic memory store capturing and indexing user actions, screen states, and OCR."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        screenshots_dir: Optional[str | Path] = None,
        omniroute_client: Optional[OmniRouteClient] = None,
    ):
        base_dir = Path(".ghost/memory")
        base_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = Path(db_path) if db_path else base_dir / "visual_memory.sqlite3"
        self.screenshots_dir = Path(screenshots_dir) if screenshots_dir else base_dir / "screenshots"
        self.capturer = ScreenCapture(output_dir=self.screenshots_dir)
        self.ocr_engine = WinRtOcrEngine()
        self.omniroute = omniroute_client or OmniRouteClient(OmniRouteSettings.from_env())

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS visual_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    app TEXT NOT NULL,
                    window_title TEXT,
                    action_kind TEXT NOT NULL,
                    coords_x REAL,
                    coords_y REAL,
                    target_name TEXT,
                    screenshot_path TEXT,
                    ocr_text TEXT,
                    ocr_json TEXT,
                    intent_summary TEXT,
                    session_id TEXT
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_app ON visual_memories(app);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_time ON visual_memories(timestamp);")
            conn.commit()

    def synthesize_intent(
        self,
        app: str,
        window_title: str,
        action_kind: str,
        ocr_text: str,
        target_name: Optional[str] = None,
    ) -> str:
        """Use OmniRoute to understand what the user is doing, with heuristic fallback."""
        prompt_snippet = (ocr_text[:300].strip()) if ocr_text else "(No visible text detected)"
        target_str = f" on '{target_name}'" if target_name else ""

        # Check if OmniRoute is configured and reachable
        if self.omniroute.settings.model:
            try:
                import httpx
                headers = {"Authorization": f"Bearer {self.omniroute.settings.api_key}"} if self.omniroute.settings.api_key else {}
                url = self.omniroute.settings.base_url.rstrip("/") + "/chat/completions"
                payload = {
                    "model": self.omniroute.settings.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are DigitalSoul's activity observer. Given user actions, active window, and OCR text, "
                                "summarize what the user is doing in ONE concise sentence."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"App: {app}\n"
                                f"Window: {window_title}\n"
                                f"Action: {action_kind}{target_str}\n"
                                f"OCR Visible Screen Text snippet:\n{prompt_snippet}\n\n"
                                "What is the user doing?"
                            ),
                        },
                    ],
                    "max_tokens": 60,
                    "temperature": 0.0,
                }
                with httpx.Client(timeout=3.0) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                        if content:
                            return content
            except Exception:
                pass

        # Deterministic heuristic intent summary
        if action_kind == "click":
            return f"User clicked{target_str} in {window_title or app}"
        elif action_kind == "set_text":
            return f"User entered text into {target_name or 'input field'} in {window_title or app}"
        elif action_kind == "window_switch":
            return f"User switched focus to {window_title or app}"
        return f"User performed {action_kind} in {window_title or app}"

    def capture_and_remember(
        self,
        app: str,
        window_title: str,
        action_kind: str = "observation",
        coords: Optional[Tuple[float, float]] = None,
        target_name: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
        session_id: Optional[str] = None,
        analyze_with_omniroute: bool = True,
    ) -> VisualMemoryItem:
        """Capture screenshot via dxcam, run WinRT OCR, synthesize intent, and save to memory."""
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # 1. Capture screen using dxcam / fallback
        shot_path, img = self.capturer.save_screenshot(prefix="obs", region=region)

        # 2. Run WinRT OCR
        ocr_res: OcrResult = OcrResult(text="", lines=[], words=[])
        if img is not None:
            ocr_res = self.ocr_engine.recognize_image(img)

        # If a click happened and target_name was not provided, look up word at click point
        if coords and not target_name and ocr_res.words:
            matched_word = ocr_res.word_at_point(coords[0], coords[1])
            if matched_word:
                target_name = matched_word.text

        # 3. Intent synthesis (OmniRoute / heuristic)
        intent = None
        if analyze_with_omniroute:
            intent = self.synthesize_intent(
                app=app,
                window_title=window_title,
                action_kind=action_kind,
                ocr_text=ocr_res.text,
                target_name=target_name,
            )

        cx = coords[0] if coords else None
        cy = coords[1] if coords else None
        ocr_items = [w.to_dict() for w in ocr_res.words]

        # 4. Save to SQLite database
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO visual_memories (
                    timestamp, app, window_title, action_kind, coords_x, coords_y,
                    target_name, screenshot_path, ocr_text, ocr_json, intent_summary, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    now_iso,
                    app,
                    window_title,
                    action_kind,
                    cx,
                    cy,
                    target_name,
                    shot_path,
                    ocr_res.text,
                    json.dumps(ocr_items, ensure_ascii=False),
                    intent,
                    session_id,
                ),
            )
            item_id = cursor.lastrowid
            conn.commit()

        return VisualMemoryItem(
            id=item_id,
            timestamp=now_iso,
            app=app,
            window_title=window_title,
            action_kind=action_kind,
            coords_x=cx,
            coords_y=cy,
            target_name=target_name,
            screenshot_path=shot_path,
            ocr_text=ocr_res.text,
            ocr_items=ocr_items,
            intent_summary=intent,
            session_id=session_id,
        )

    def get_recent(self, limit: int = 20) -> List[VisualMemoryItem]:
        """Fetch recent visual memory items in chronological order."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, timestamp, app, window_title, action_kind, coords_x, coords_y,
                       target_name, screenshot_path, ocr_text, ocr_json, intent_summary, session_id
                FROM visual_memories
                ORDER BY id DESC LIMIT ?;
                """,
                (limit,),
            ).fetchall()

        items = []
        for r in reversed(rows):
            items.append(
                VisualMemoryItem(
                    id=r["id"],
                    timestamp=r["timestamp"],
                    app=r["app"],
                    window_title=r["window_title"] or "",
                    action_kind=r["action_kind"],
                    coords_x=r["coords_x"],
                    coords_y=r["coords_y"],
                    target_name=r["target_name"],
                    screenshot_path=r["screenshot_path"],
                    ocr_text=r["ocr_text"] or "",
                    ocr_items=json.loads(r["ocr_json"]) if r["ocr_json"] else [],
                    intent_summary=r["intent_summary"],
                    session_id=r["session_id"],
                )
            )
        return items

    def search_memory(self, query: str, app: Optional[str] = None, limit: int = 20) -> List[VisualMemoryItem]:
        """Search memory by text content or intent."""
        sql = "SELECT * FROM visual_memories WHERE (ocr_text LIKE ? OR intent_summary LIKE ? OR window_title LIKE ?)"
        params: List[Any] = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if app:
            sql += " AND app = ?"
            params.append(app)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        return [
            VisualMemoryItem(
                id=r["id"],
                timestamp=r["timestamp"],
                app=r["app"],
                window_title=r["window_title"] or "",
                action_kind=r["action_kind"],
                coords_x=r["coords_x"],
                coords_y=r["coords_y"],
                target_name=r["target_name"],
                screenshot_path=r["screenshot_path"],
                ocr_text=r["ocr_text"] or "",
                ocr_items=json.loads(r["ocr_json"]) if r["ocr_json"] else [],
                intent_summary=r["intent_summary"],
                session_id=r["session_id"],
            )
            for r in rows
        ]
