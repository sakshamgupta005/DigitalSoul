"""Visual Memory and Activity Tracker for DigitalSoul.

Combines:
1. High-speed DirectX screen capture via dxcam.
2. Offline native Windows OCR via WinRT OCR (Windows.Media.Ocr).
3. Visual action markings (bounding boxes, click ripples, badges, crops).
4. Local model synthesis via OmniRoute (http://127.0.0.1:20128/v1).
5. Persistent SQLite storage and VLM training dataset export.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from agent.ghost.collector.ocr_winrt import OcrResult, WinRtOcrEngine
from agent.ghost.collector.screen_capture import ScreenCapture
from agent.ghost.collector.visual_annotator import VisualAnnotator
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
    marked_screenshot_path: Optional[str]
    focus_crop_path: Optional[str]
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
            "marked_screenshot_path": self.marked_screenshot_path,
            "focus_crop_path": self.focus_crop_path,
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
        session_id: Optional[str] = None,
    ):
        base_dir = Path(".ghost/memory")
        base_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = Path(db_path) if db_path else base_dir / "visual_memory.sqlite3"
        self.session_id = session_id
        if screenshots_dir:
            self.screenshots_dir = Path(screenshots_dir)
        elif session_id:
            safe_session = re.sub(r"[^\w.-]+", "_", session_id).strip("_")
            self.screenshots_dir = base_dir / "screenshots" / (safe_session or "default")
        else:
            self.screenshots_dir = base_dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)
        self.capturer = ScreenCapture(output_dir=self.screenshots_dir)
        self.ocr_engine = WinRtOcrEngine()
        self.annotator = VisualAnnotator()
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
                    marked_screenshot_path TEXT,
                    focus_crop_path TEXT,
                    ocr_text TEXT,
                    ocr_json TEXT,
                    intent_summary TEXT,
                    session_id TEXT
                );
                """
            )
            # Automatic schema migration for existing databases
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(visual_memories);")
            columns = {row[1] for row in cursor.fetchall()}
            if "marked_screenshot_path" not in columns:
                cursor.execute("ALTER TABLE visual_memories ADD COLUMN marked_screenshot_path TEXT;")
            if "focus_crop_path" not in columns:
                cursor.execute("ALTER TABLE visual_memories ADD COLUMN focus_crop_path TEXT;")

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
        annotate_screen: bool = True,
        image: Optional[Any] = None,
    ) -> VisualMemoryItem:
        """Capture screenshot via dxcam, annotate markings, run WinRT OCR, and save memory."""
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # 1. Capture screen using dxcam / fallback
        if image is not None:
            shot_path, img = self.capturer.save_screenshot(image=image, prefix="obs", region=region)
        else:
            shot_path, img = self.capturer.save_screenshot(prefix="obs", region=region)

        # 2. Run WinRT OCR
        ocr_res: OcrResult = OcrResult(text="", lines=[], words=[])
        if img is not None:
            ocr_res = self.ocr_engine.recognize_image(img)

        # 3. Locate target bounding box and element name
        target_bbox: Optional[Tuple[float, float, float, float]] = None
        if coords and not target_name and ocr_res.words:
            matched_word = ocr_res.word_at_point(coords[0], coords[1])
            if matched_word:
                target_name = matched_word.text
                b = matched_word.box
                target_bbox = (b.x, b.y, b.x + b.width, b.y + b.height)
        elif target_name and ocr_res.words:
            for w in ocr_res.words:
                if target_name.lower() in w.text.lower():
                    b = w.box
                    target_bbox = (b.x, b.y, b.x + b.width, b.y + b.height)
                    break

        ocr_items = [w.to_dict() for w in ocr_res.words]

        # 4. Generate representative markings (marked image & localized focus crop)
        marked_path = None
        crop_path = None
        if img is not None and annotate_screen:
            stem = Path(shot_path).stem if shot_path else f"obs_{uuid.uuid4().hex[:8]}"

            # Generate visually marked screenshot (click ripple, box, badge)
            marked_img = self.annotator.annotate_action(
                image=img,
                coords=coords,
                bbox=target_bbox,
                action_kind=action_kind,
                label=target_name,
                ocr_words=ocr_items,
            )
            marked_file = self.screenshots_dir / f"{stem}_marked.png"
            marked_img.save(marked_file, format="PNG")
            marked_path = str(marked_file)

            # Generate high-resolution localized focus crop
            crop_img = self.annotator.create_focus_crop(
                image=img,
                target_point=coords,
                bbox=target_bbox,
                crop_size=(520, 360),
            )
            crop_file = self.screenshots_dir / f"{stem}_crop.png"
            crop_img.save(crop_file, format="PNG")
            crop_path = str(crop_file)

        # 5. Intent synthesis (OmniRoute / heuristic)
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

        # 6. Save to SQLite database
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO visual_memories (
                    timestamp, app, window_title, action_kind, coords_x, coords_y,
                    target_name, screenshot_path, marked_screenshot_path, focus_crop_path,
                    ocr_text, ocr_json, intent_summary, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                    marked_path,
                    crop_path,
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
            marked_screenshot_path=marked_path,
            focus_crop_path=crop_path,
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
                       target_name, screenshot_path, marked_screenshot_path, focus_crop_path,
                       ocr_text, ocr_json, intent_summary, session_id
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
                    marked_screenshot_path=r["marked_screenshot_path"],
                    focus_crop_path=r["focus_crop_path"],
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
                marked_screenshot_path=r["marked_screenshot_path"],
                focus_crop_path=r["focus_crop_path"],
                ocr_text=r["ocr_text"] or "",
                ocr_items=json.loads(r["ocr_json"]) if r["ocr_json"] else [],
                intent_summary=r["intent_summary"],
                session_id=r["session_id"],
            )
            for r in rows
        ]

    def export_as_training_dataset(self, output_dir: Optional[Union[str, Path]] = None) -> int:
        """Exports all visual memories into standard JSONL VLM training format."""
        from agent.ghost.collector.training_data import TrainingDatasetExporter, TrainingSample

        exporter = TrainingDatasetExporter(output_dir=output_dir)
        items = self.get_recent(limit=1000)
        samples = []

        for idx, item in enumerate(items, start=1):
            if not item.screenshot_path or not os.path.exists(item.screenshot_path):
                continue

            cx = item.coords_x or 0.0
            cy = item.coords_y or 0.0
            sample = TrainingSample(
                sample_id=f"mem_{item.id}_{uuid.uuid4().hex[:6]}",
                workflow_name=item.session_id or "Desktop Activity",
                step_index=idx,
                app=item.app,
                window_title=item.window_title,
                action_type=item.action_kind,
                target_label=item.target_name or "Element",
                coords_pixel=[cx, cy] if item.coords_x is not None else None,
                coords_norm=[round(cx / 1920.0, 4), round(cy / 1080.0, 4)] if item.coords_x is not None else None,
                bbox_pixel=None,
                bbox_norm=None,
                instruction_prompt=f"Perform {item.action_kind} on {item.target_name or 'target'} in {item.app}.",
                intent_description=item.intent_summary or f"User performed {item.action_kind}",
                raw_screenshot_path=item.screenshot_path or "",
                marked_screenshot_path=item.marked_screenshot_path or "",
                focus_crop_path=item.focus_crop_path or "",
                ocr_tokens=item.ocr_items,
                created_at=item.timestamp,
            )
            samples.append(sample)

        return exporter.export_batch(samples)
