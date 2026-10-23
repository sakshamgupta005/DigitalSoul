"""Desktop & Notch API routes for DigitalSoul.

Bridges the local web dashboard and browser views with the native desktop
execution engine, workflow catalog, genuine user memory, and background replays.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent.ghost.desktop.catalog import Catalog
from agent.ghost.desktop.jobs import Jobs
from agent.ghost.desktop.learning_mode import LearningEngine
from agent.ghost.desktop import steps as step_replay
from agent.ghost.desktop.synthesizer import synthesize_workflow
from agent.ghost.desktop.voice import dispatch

router = APIRouter()

# Locate project root directory
PROJECT_ROOT = Path(os.environ.get("DIGITALSOUL_PROJECT_DIR") or Path.cwd()).resolve()
DATA_DIR = PROJECT_ROOT / ".ghost" / "notch"
JOB_DIR = PROJECT_ROOT / ".ghost" / "desktop"
DATA_DIR.mkdir(parents=True, exist_ok=True)
JOB_DIR.mkdir(parents=True, exist_ok=True)


class DesktopManager:
    """Singleton manager executing desktop routines, tracking sessions, and broadcasting events."""

    def __init__(self, root: Path):
        self.root = root
        self.data_dir = root / ".ghost" / "notch"
        self.job_dir = root / ".ghost" / "desktop"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.job_dir.mkdir(parents=True, exist_ok=True)

        self.catalog = Catalog(self.data_dir / "workflows.sqlite3")
        self.jobs = Jobs(self.job_dir / "jobs.sqlite3")
        self.learning_engine = LearningEngine(self.data_dir / "learning.sqlite3")

        self.active_session: Optional[Any] = None
        self.active_replay_proc: Optional[subprocess.Popen] = None
        self.active_replay_id: Optional[str] = None
        self.active_step_run: Optional[subprocess.Popen] = None
        self.subscribers: List[asyncio.Queue] = []
        self._lock = threading.Lock()
        self.recent_events: List[Dict[str, Any]] = []

    def broadcast_event(self, event_data: Dict[str, Any]):
        """Store recent event and push to all active SSE queues."""
        event_data.setdefault("timestamp", time.time())
        with self._lock:
            self.recent_events.append(event_data)
            if len(self.recent_events) > 100:
                self.recent_events.pop(0)

            dead_queues = []
            for q in self.subscribers:
                try:
                    q.put_nowait(event_data)
                except Exception:
                    dead_queues.append(q)
            for dead in dead_queues:
                if dead in self.subscribers:
                    self.subscribers.remove(dead)

    def get_hello(self) -> Dict[str, Any]:
        """System health and desktop agent metrics."""
        patterns = self.learning_engine.list_patterns(include_demonstrations=True)
        memory = self.learning_engine.get_user_memory()
        workflows = self.catalog.list()
        return {
            "platform": sys.platform,
            "agent": "DigitalSoul",
            "version": "1.1.0",
            "recording": bool(self.active_session),
            "running": bool(self.active_replay_id or self.active_step_run),
            "workflows_count": len(workflows),
            "patterns_count": len(patterns),
            "profiles_count": len(self.learning_engine.list_profiles()),
            "user_memory_count": len(memory),
            "user_memory": memory,
            "active_profile": self.learning_engine.get_active_profile(),
        }

    def dispatch_command(self, text: str) -> Dict[str, Any]:
        """Dispatches natural language voice or typed Jarvis command."""
        busy = bool(self.active_session or self.active_replay_id or self.active_step_run)
        res = dispatch(
            text,
            self.catalog.list(),
            busy=busy,
            catalog=self.catalog,
            learning=self.learning_engine,
        )
        self.broadcast_event({"event": "jarvis_command", "text": text, "result": res})
        return res

    def replay_workflow(self, workflow_id: str, extra_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Executes a workflow deterministically."""
        payload = extra_payload or {}
        workflow = self.catalog.get(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow '{workflow_id}' not found")

        flow = workflow.get("flow")
        steps = workflow.get("steps") or []

        # 1. WhatsApp native send flow
        if flow and flow.get("format") == "digitalsoul.whatsapp-send.v1":
            msg = payload.get("message") or flow.get("message") or "Hello!"
            recipient = payload.get("approved_recipient") or flow.get("recipient", "")
            at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
            job_id = self.jobs.schedule(flow, msg, at, recipient)
            self._spawn_replay(job_id)
            self.broadcast_event({"event": "replay_started", "job_id": job_id, "workflow": workflow["name"]})
            return {"status": "started", "job_id": job_id, "kind": "whatsapp"}

        # 2. Excel automation flow
        if flow and (flow.get("format") == "digitalsoul.excel-flow.v1" or flow.get("kind") == "excel"):
            from agent.ghost.desktop.excel import execute_excel_workflow
            exec_payload = dict(flow)
            if "workbook" in payload:
                exec_payload["workbook"] = payload["workbook"]
            res = execute_excel_workflow(exec_payload)
            self.broadcast_event({"event": "replay_finished", "workflow": workflow["name"], "result": res})
            return {"status": "completed", "result": res, "kind": "excel"}

        # 3. Step-by-step visual grounding replay
        if steps:
            from agent.ghost.desktop.session import clean_steps
            cleaned = clean_steps(steps)
            step_replay.validate(cleaned)
            self._launch_steps(cleaned, workflow_name=workflow.get("name") or "default")
            self.broadcast_event({"event": "replay_started", "steps": True, "workflow": workflow["name"], "count": len(cleaned)})
            return {"status": "started", "workflow": workflow["name"], "steps_count": len(cleaned), "kind": "desktop_steps"}

        # 4. Fallback if flow can be derived
        from agent.ghost.desktop.whatsapp_send import derive
        try:
            derived_flow = derive(steps)
            if derived_flow:
                msg = payload.get("message") or derived_flow.get("message") or "Hello!"
                recipient = payload.get("approved_recipient") or derived_flow.get("recipient", "")
                at = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
                job_id = self.jobs.schedule(derived_flow, msg, at, recipient)
                self._spawn_replay(job_id)
                self.broadcast_event({"event": "replay_started", "job_id": job_id, "workflow": workflow["name"]})
                return {"status": "started", "job_id": job_id, "kind": "derived_whatsapp"}
        except Exception:
            pass

        raise ValueError("This workflow does not contain executable steps or flow definition")

    def _spawn_replay(self, job_id: str):
        worker = subprocess.Popen(
            [sys.executable, "-u", "-m", "agent.ghost.desktop.replay_worker"],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
            text=True,
        )
        worker.stdin.write(json.dumps({"data": str(self.job_dir), "job_id": job_id}) + "\n")
        worker.stdin.close()
        self.active_replay_proc = worker
        self.active_replay_id = job_id

    def _launch_steps(self, steps_payload: List[Dict[str, Any]], workflow_name: str = "default"):
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "agent.ghost.desktop.steps"],
            cwd=self.root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
            text=True,
            encoding="utf-8",
        )
        proc.stdin.write(json.dumps({"steps": steps_payload, "workflow_name": workflow_name}, ensure_ascii=False) + "\n")
        proc.stdin.close()
        self.active_step_run = proc

        def reader(out):
            for line in iter(out.readline, ""):
                line = line.strip()
                if line:
                    try:
                        ev = json.loads(line)
                        self.broadcast_event(ev)
                    except Exception:
                        pass
            self.broadcast_event({"event": "replay_finished", "workflow": workflow_name})
            self.active_step_run = None

        threading.Thread(target=reader, args=(proc.stdout,), daemon=True).start()

    def rpc_call(self, method: str, payload: Dict[str, Any]) -> Any:
        """Executes any RPC method matching notch_bridge interface."""
        if method == "hello":
            return self.get_hello()
        elif method == "list":
            return {
                "workflows": self.catalog.list(),
                "jobs": self.jobs.list(),
                "user_memory": self.learning_engine.get_user_memory(),
            }
        elif method == "detail":
            val = self.catalog.get(payload["id"])
            from agent.ghost.desktop.session import clean_steps
            val["steps"] = clean_steps(val.get("steps", []))
            val["steps_digest"] = step_replay.digest(val["steps"])
            return val
        elif method == "voice_command":
            return self.dispatch_command(payload.get("text", ""))
        elif method == "workflow_synthesize":
            prompt = payload.get("prompt", "")
            return synthesize_workflow(prompt, catalog=self.catalog, learning=self.learning_engine)
        elif method in ("replay", "replay_steps"):
            return self.replay_workflow(payload["id"], payload)
        elif method == "start":
            if self.active_session or self.active_replay_id or self.active_step_run:
                raise ValueError("Stop the current recording or replay first")
            from agent.ghost.desktop.session import Session
            self.active_session = Session(payload)
            self.broadcast_event({"event": "recording_started", "name": self.active_session.name})
            return {"recording": True}
        elif method == "stop":
            if not self.active_session:
                return {"saved": False, "detail": "No active recording session"}
            saved = self.active_session.save(self.catalog)
            self.active_session = None
            self.broadcast_event({"event": "recording_saved", "recording": saved})
            return {"saved": True, "recording": saved}
        elif method == "excel_inspect":
            from agent.ghost.desktop.excel import inspect_active_excel
            return inspect_active_excel()
        elif method == "excel_generate":
            from agent.ghost.desktop.excel import generate_excel_data
            return generate_excel_data(payload)
        elif method == "excel_run":
            from agent.ghost.desktop.excel import execute_excel_workflow
            res = execute_excel_workflow(payload)
            self.broadcast_event({"event": "replay_finished", "excel": True, "result": res})
            return res
        elif method == "learning_patterns":
            patterns = self.learning_engine.list_patterns(include_demonstrations=True)
            return {
                "patterns": patterns,
                "user_memory": self.learning_engine.get_user_memory(),
                "recent_routines": self.learning_engine.list_recent_routines(),
            }
        elif method == "learning_detail":
            pattern = self.learning_engine.get_pattern(payload["id"])
            if pattern:
                pattern["approved_digest"] = step_replay.digest({"steps": pattern["skeleton"], "parameters": pattern["parameters"]})
            return pattern
        elif method == "learning_execute":
            pattern = self.learning_engine.get_pattern(payload["id"])
            if not pattern:
                raise ValueError("Pattern not found")
            steps = self.learning_engine.adapt_pattern_steps(payload["id"], payload.get("parameters"))
            self._launch_steps(steps, workflow_name=pattern.get("name") or "Learned Pattern")
            self.broadcast_event({"event": "replay_started", "steps": True, "workflow": pattern.get("name")})
            return {"started": True}
        elif method == "gform_memory_get":
            return {"user_memory": self.learning_engine.get_user_memory()}
        elif method == "gform_memory_save":
            fields = payload.get("fields") or payload.get("profile") or {}
            for k, v in fields.items():
                self.learning_engine.set_user_memory(k, v)
            return {"user_memory": self.learning_engine.get_user_memory()}
        elif method == "list_hermes_skills":
            from agent.ghost.learning.hermes_sync import list_hermes_skills
            return {"skills": list_hermes_skills()}
        elif method == "import_hermes_skill":
            from agent.ghost.learning.hermes_sync import import_hermes_skill_to_patterns
            return import_hermes_skill_to_patterns(payload["skill_name"], self.learning_engine)
        elif method == "import_skill":
            return self.learning_engine.import_skill(payload["skill_markdown"])
        elif method == "export_skill":
            return {"skill_markdown": self.learning_engine.export_skill(payload["id"])}
        elif method == "hud_demo":
            self.broadcast_event({"event": "hud_demo", "active": True})
            return {"demo": True}
        elif method == "self_heal_demo":
            self.broadcast_event({"event": "self_heal_demo", "obstacle": payload.get("obstacle", "cookie"), "recovered": True})
            return {"recovered": True}
        elif method == "profile_list":
            return {"profiles": self.learning_engine.list_profiles(), "active": self.learning_engine.get_active_profile()}
        elif method == "rename":
            val = self.catalog.get(payload["id"])
            val["name"] = payload["name"]
            return self.catalog.save(val)
        elif method == "delete":
            self.catalog.delete(payload["id"])
            return {"deleted": True}
        else:
            raise ValueError(f"Method '{method}' not implemented in web bridge")


# Instantiate shared DesktopManager
desktop_manager = DesktopManager(PROJECT_ROOT)


class RPCRequest(BaseModel):
    method: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class CommandRequest(BaseModel):
    prompt: str


class MemoryUpdateRequest(BaseModel):
    fields: Dict[str, Any]


@router.get("/status")
def get_desktop_status():
    """Returns desktop agent health and connectivity status."""
    return {"ok": True, "status": "online", "data": desktop_manager.get_hello()}


@router.post("/rpc")
def handle_rpc(req: RPCRequest):
    """Executes a desktop RPC method matching Notch bridge interface."""
    try:
        res = desktop_manager.rpc_call(req.method, req.payload)
        return {"ok": True, "result": res}
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(exc)},
        )


@router.get("/workflows")
def list_workflows():
    """List all saved desktop and browser workflows."""
    return {
        "ok": True,
        "workflows": desktop_manager.catalog.list(),
        "patterns": desktop_manager.learning_engine.list_patterns(include_demonstrations=True),
        "user_memory": desktop_manager.learning_engine.get_user_memory(),
    }


@router.get("/workflows/{workflow_id}")
def get_workflow_detail(workflow_id: str):
    """Get full details and step digest of a workflow."""
    wf = desktop_manager.catalog.get(workflow_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    from agent.ghost.desktop.session import clean_steps
    wf["steps"] = clean_steps(wf.get("steps", []))
    wf["steps_digest"] = step_replay.digest(wf["steps"])
    return {"ok": True, "workflow": wf}


@router.post("/workflows/replay/{workflow_id}")
def replay_workflow_endpoint(workflow_id: str, payload: Optional[Dict[str, Any]] = None):
    """Triggers deterministic replay of a workflow."""
    try:
        res = desktop_manager.replay_workflow(workflow_id, payload)
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/workflows/synthesize")
def synthesize_workflow_endpoint(cmd: CommandRequest):
    """Synthesizes a workflow from typed or spoken instructions using AI."""
    try:
        res = synthesize_workflow(cmd.prompt, catalog=desktop_manager.catalog, learning=desktop_manager.learning_engine)
        return {"ok": True, "workflow": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str):
    """Deletes a workflow from the local catalog."""
    desktop_manager.catalog.delete(workflow_id)
    return {"ok": True, "deleted": workflow_id}


@router.get("/memory")
def get_user_memory():
    """Get genuine local user memory dictionary."""
    return {
        "ok": True,
        "user_memory": desktop_manager.learning_engine.get_user_memory(),
        "profiles": desktop_manager.learning_engine.list_profiles(),
        "active_profile": desktop_manager.learning_engine.get_active_profile(),
    }


@router.post("/memory")
def update_user_memory(req: MemoryUpdateRequest):
    """Update or save local user memory."""
    for key, val in req.fields.items():
        desktop_manager.learning_engine.set_user_memory(key, val)
    return {"ok": True, "user_memory": desktop_manager.learning_engine.get_user_memory()}


@router.post("/command")
def execute_jarvis_command(cmd: CommandRequest):
    """Dispatches a natural language Jarvis command."""
    try:
        res = desktop_manager.dispatch_command(cmd.prompt)
        return {"ok": True, "result": res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/events")
async def events_sse_stream(request: Request):
    """Server-Sent Events (SSE) streaming live desktop execution events to the browser."""
    queue = asyncio.Queue()
    desktop_manager.subscribers.append(queue)

    async def event_generator():
        try:
            # Send initial hello greeting
            hello = desktop_manager.get_hello()
            yield f"data: {json.dumps({'event': 'connected', 'status': hello})}\n\n"

            # Stream past recent events so the client immediately catches up
            for ev in desktop_manager.recent_events[-10:]:
                yield f"data: {json.dumps(ev)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat ping
                    yield f": heartbeat {time.time()}\n\n"
        finally:
            if queue in desktop_manager.subscribers:
                desktop_manager.subscribers.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
