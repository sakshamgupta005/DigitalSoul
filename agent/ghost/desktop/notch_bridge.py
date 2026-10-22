"""Private Tauri stdio RPC. All native recording stays in the local Windows session."""
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .catalog import Catalog
from .jobs import Jobs
from .learning_mode import LearningEngine
from . import steps as step_replay
from .voice import Listener, dispatch


if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stdin, "reconfigure"):
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass


def accent_color():
    """Windows accent as #rrggbb; Windows can pick it from the wallpaper. None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as key:
            value = winreg.QueryValueEx(key, "AccentColor")[0]  # 0xAABBGGRR
        return "#{:02x}{:02x}{:02x}".format(value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF)
    except OSError:
        return None


def emit(value):
    print(json.dumps(value), flush=True)


def shopify_url(value):
    """browser-use fills and saves whatever page it opens, so only Shopify admin or the local test form."""
    parts = urlsplit(value or "")
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == "admin.shopify.com" or host.endswith(".myshopify.com")):
        return value
    if parts.scheme == "http" and host in ("127.0.0.1", "localhost"):
        return value
    raise ValueError("Use a Shopify admin address (admin.shopify.com or *.myshopify.com)")


def park_ghost():
    """A terminated worker cannot park the replay ghost itself; never leave it on screen."""
    try:
        from .ghost import Ghost
        Ghost().park()
    except Exception:
        pass


def spawn_replay(root, job_data, job_id):
    worker = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.replay_worker"],
        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000 if sys.platform == "win32" else 0, text=True)
    worker.stdin.write(json.dumps({"data": str(job_data), "job_id": job_id}) + "\n")
    worker.stdin.close()
    return worker


def main():
    # On Windows, initialize browser-use's optional native numeric dependency
    # before the stdin reader starts blocking on the pipe. Loading it later
    # during a Shopify command stalls this stdio host on this installation.
    try:
        import numpy  # noqa: F401
    except ImportError:
        pass
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    data = root / ".ghost" / "notch"
    job_data = root / ".ghost" / "desktop"
    data.mkdir(parents=True, exist_ok=True)
    job_data.mkdir(parents=True, exist_ok=True)
    catalog, jobs = Catalog(data / "workflows.sqlite3"), Jobs(job_data / "jobs.sqlite3")
    learning_engine = LearningEngine(data / "learning.sqlite3")
    inbox = queue.Queue()
    session = None
    replay = None
    replay_id = None
    step_run = None
    listener = None
    shopify_session = None
    shopify_future = None
    shopify_request_id = None
    shopify_deadline = 0

    def receive():
        for line in sys.stdin:
            try:
                if len(line) > 65536:
                    raise ValueError("RPC request too large")
                inbox.put(json.loads(line))
            except (ValueError, TypeError):
                emit({"event": "error", "message": "Malformed local request"})
        inbox.put({"method": "shutdown"})

    threading.Thread(target=receive, daemon=True).start()
    last_update = 0

    def save_session():
        nonlocal session
        if not session:
            return None
        finished, session = session, None
        try:
            value = catalog.save(finished.finish())
            try:
                learn_res = learning_engine.record_episode(value.get("steps", []), name=value.get("name"), flow=value.get("flow"))
                if learn_res.get("action") in ("discovered", "reinforced"):
                    emit({"event": "pattern_learned", "result": learn_res})
            except Exception:
                pass
        except Exception as exc:
            # The notch is hidden while recording; an error event is what brings it back.
            emit({"event": "error", "message": f"Recording could not be saved: {str(exc)[:300]}"})
            raise
        emit({"event": "recording_saved", "recording": value})
        return value

    try:
        while True:
            try:
                request = inbox.get(timeout=0.04)
            except queue.Empty:
                request = None
            if request:
                request_id = request.get("id")
                try:
                    method, payload = request["method"], request.get("payload") or {}
                    result = None
                    if method == "shutdown":
                        break
                    if shopify_future is not None and method in (
                            "start", "replay", "schedule", "replay_steps", "voice_start", "shopify_run"):
                        raise ValueError("A Shopify run is active; wait for it or cancel it first")
                    if method == "hello":
                        result = {"platform": sys.platform, "recording": bool(session), "running": replay_id or bool(step_run),
                                  "data": str(data), "version": "0.1.0", "accent": accent_color(),
                                  "patterns_count": len(learning_engine.list_patterns())}
                    elif method == "list":
                        result = {"workflows": catalog.list(), "jobs": jobs.list()}
                    elif method == "detail":
                        result = catalog.get(payload["id"])
                        from .session import clean_steps
                        result["steps"] = clean_steps(result["steps"])  # what you see is exactly what replays
                        result["steps_digest"] = step_replay.digest(result["steps"])
                    elif method == "voice_command":
                        result = dispatch(payload.get("text"), catalog.list(), busy=bool(session or replay or step_run), catalog=catalog)
                    elif method == "workflow_synthesize":
                        from .synthesizer import synthesize_workflow
                        prompt = payload.get("prompt") or ""
                        result = synthesize_workflow(prompt, catalog=catalog)
                    elif method == "voice_start":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for replay before enabling the microphone")
                        if listener:
                            listener.close()
                        listener = Listener([w["name"] for w in catalog.list()])
                        result = {"starting": True}
                    elif method == "voice_stop":
                        if listener:
                            listener.close()
                            listener = None
                        result = {"listening": False}
                    elif method == "start":
                        if session or replay or step_run:
                            raise ValueError("Stop the current recording or replay first")
                        if listener:
                            listener.close()
                            listener = None
                            emit({"event": "voice", "kind": "stopped"})
                        from .session import Session
                        session = Session(payload)
                        result = {"recording": True}
                        emit({"event": "recording_started", "name": session.name})
                    elif method == "stop":
                        result = save_session()
                    elif method == "rename":
                        value = catalog.get(payload["id"])
                        value["name"] = payload["name"]
                        result = catalog.save(value)
                    elif method == "delete":
                        if session:
                            raise ValueError("Stop recording before deleting workflows")
                        catalog.delete(payload["id"])
                    elif method in ("replay", "schedule"):
                        if listener:
                            listener.close()
                            listener = None
                            emit({"event": "voice", "kind": "stopped"})
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        value = catalog.get(payload["id"])
                        flow = value.get("flow")
                        if not flow:
                            raise ValueError(value.get("replay_error") or "This evidence needs an app adapter before replay")
                        if flow.get("format") == "digitalsoul.excel-flow.v1" or flow.get("kind") == "excel":
                            from .excel import execute_excel_workflow
                            exec_payload = dict(flow)
                            if "workbook" in payload:
                                exec_payload["workbook"] = payload["workbook"]
                            if "sheet" in payload:
                                exec_payload["sheet"] = payload["sheet"]
                            result = execute_excel_workflow(exec_payload)
                            emit({"event": "replay_started", "excel": True})
                            emit({"id": request_id, "ok": True, "result": result})
                            continue
                        at = payload.get("at") if method == "schedule" else (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
                        job_id = jobs.schedule(flow, payload.get("message", flow.get("message")), at, payload.get("approved_recipient", ""))
                        if method == "replay":
                            replay, replay_id = spawn_replay(root, job_data, job_id), job_id
                            emit({"event": "replay_started", "job_id": job_id})
                        result = {"job_id": job_id}
                    elif method == "replay_steps":
                        if listener:
                            listener.close()
                            listener = None
                            emit({"event": "voice", "kind": "stopped"})
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        value = catalog.get(payload["id"])
                        # The approval names the exact steps the user reviewed; any change invalidates it.
                        from .session import clean_steps
                        value["steps"] = clean_steps(value["steps"])
                        if payload.get("approved_digest") != step_replay.digest(value["steps"]):
                            raise ValueError("These steps changed since you reviewed them; open the recording again")
                        step_replay.validate(value["steps"])
                        step_run = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.steps"],
                            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000 if sys.platform == "win32" else 0, text=True, encoding="utf-8")
                        step_run.stdin.write(json.dumps({"steps": value["steps"]}, ensure_ascii=False) + "\n")
                        step_run.stdin.close()
                        emit({"event": "replay_started", "steps": True})
                        result = {"started": True}
                    elif method == "cancel":
                        result = {"cancelled": jobs.cancel(payload["id"])}
                    elif method == "stop_jobs":
                        jobs.stop()
                        if shopify_future is not None:
                            shopify_future.cancel()
                        if step_run:
                            step_run.terminate()
                            park_ghost()
                        result = {"stopped": True}
                    elif method == "resume_jobs":
                        jobs.resume()
                    elif method == "excel_inspect":
                        from .excel import inspect_active_excel
                        result = inspect_active_excel()
                    elif method == "excel_generate":
                        from .excel import generate_excel_data
                        result = generate_excel_data(payload)
                    elif method == "excel_run":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        from .excel import execute_excel_workflow
                        result = execute_excel_workflow(payload)
                    elif method == "shopify_generate":
                        from ..browser.shopify import generate_product_fields
                        result = generate_product_fields(payload)
                    elif method == "shopify_run":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        from ..browser.shopify_session import ShopifySession
                        product_data = payload.get("product") or {}
                        click_save = payload.get("click_save", False)
                        if shopify_session is None:
                            shopify_session = ShopifySession()
                        shopify_future = shopify_session.submit(product_data, click_save=click_save,
                                                               headless=payload.get("headless", False))
                        shopify_request_id = request_id
                        shopify_deadline = time.monotonic() + 165
                        emit({"event": "shopify_started", "message": "Connecting to your Edge. Allow its debugging prompt if shown."})
                        # Leave the command loop free for status, cancellation, and shutdown.
                        continue
                    elif method == "shopify_cancel":
                        result = {"cancelled": shopify_future.cancel() if shopify_future is not None else False}
                    elif method == "learning_patterns":
                        result = {"patterns": learning_engine.list_patterns()}
                    elif method == "learning_detail":
                        result = learning_engine.get_pattern(payload["id"])
                    elif method == "learning_execute":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        steps = learning_engine.adapt_pattern_steps(payload["id"], payload.get("parameters"))
                        step_replay.validate(steps)
                        step_run = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.steps"],
                            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=0x08000000 if sys.platform == "win32" else 0, text=True, encoding="utf-8")
                        step_run.stdin.write(json.dumps({"steps": steps}, ensure_ascii=False) + "\n")
                        step_run.stdin.close()
                        emit({"event": "replay_started", "steps": True, "pattern": True})
                        result = {"started": True, "steps_count": len(steps)}
                    elif method == "learning_delete":
                        learning_engine.delete_pattern(payload["id"])
                        result = {"deleted": True}
                    else:
                        raise ValueError("Unknown desktop command")
                    emit({"id": request_id, "ok": True, "result": result})
                except Exception as exc:
                    emit({"id": request_id, "ok": False, "error": str(exc)[:600]})
            if shopify_future is not None:
                if time.monotonic() > shopify_deadline and not shopify_future.done():
                    shopify_future.cancel()
                if shopify_future.done():
                    future, shopify_future = shopify_future, None
                    try:
                        if future.cancelled():
                            raise ValueError("Shopify run stopped. Any filled fields remain unsaved; inspect the form before retrying.")
                        emit({"id": shopify_request_id, "ok": True, "result": future.result()})
                    except Exception as exc:
                        emit({"id": shopify_request_id, "ok": False, "error": str(exc)[:600]})
                    shopify_request_id = None
            if listener:
                for _ in range(16):
                    try:
                        event = listener.events.get_nowait()
                    except queue.Empty:
                        break
                    if event.get("kind") == "transcript":
                        emit({"event": "voice", "kind": "heard", "text": event.get("text", "")})
                        command = dispatch(event.get("text", ""), catalog.list(), require_wake=True,
                                           awake=time.monotonic() < listener.awake_until,
                                           busy=bool(session or replay or step_run))
                        if command["kind"] == "wake":
                            listener.awake_until = time.monotonic() + 12
                        elif command["kind"] == "workflow":
                            listener.awake_until = 0
                        emit({"event": "voice", **command})
                        if command["kind"] in ("sleep", "workflow", "app_opened"):
                            listener.close()
                            listener = None
                            emit({"event": "voice", "kind": "stopped"})
                            break
                    else:
                        if event.get("kind") == "ready":
                            # The mic click authorizes the first command without a wake phrase.
                            listener.awake_until = time.monotonic() + 30
                        emit({"event": "voice", **event})
                if listener and ((listener.finished.is_set() and listener.events.empty()) or time.monotonic() - listener.started > 330):
                    listener.close()
                    listener = None
                    emit({"event": "voice", "kind": "stopped"})
            if session:
                try:
                    reason = session.poll()
                    if reason:
                        save_session()
                    elif time.monotonic() - last_update > 0.5:
                        emit({"event": "recording_progress", "step_count": len(session.steps),
                              "elapsed": round(time.monotonic() - session.started), "last_step": session.steps[-1] if session.steps else None})
                        last_update = time.monotonic()
                except Exception as exc:
                    try:
                        with open(data / "bridge.log", "a", encoding="utf-8") as f:
                            import traceback
                            traceback.print_exc(file=f)
                    except Exception:
                        pass
                    emit({"event": "error", "message": str(exc)[:600]})
                    try:
                        save_session()
                    except Exception:
                        pass  # already reported; the recording is lost but the loop keeps serving
            if replay and replay.poll() is not None:
                emit({"event": "replay_finished", "job_id": replay_id,
                      "job": next((j for j in jobs.list() if j["id"] == replay_id), None)})
                replay, replay_id = None, None
                park_ghost()
            if step_run and step_run.poll() is not None:
                try:
                    outcome = json.loads(step_run.stdout.read().strip().splitlines()[-1])
                except (ValueError, IndexError):
                    outcome = {"ok": False, "detail": "Replay was stopped before it finished."}
                step_run = None
                park_ghost()
                emit({"event": "replay_finished", "steps": True, "job": outcome})
            # Pending scheduled jobs run while the notch is open, without a second worker command.
            if not session and not replay and not step_run and shopify_future is None:
                now = time.time()
                # Past its send window a job is never started; a worker that dies early is not respawned forever.
                due = next((j for j in jobs.list() if j["state"] == "pending" and j["due"] <= now < j["expires"]), None)
                if due:
                    if listener:
                        listener.close()
                        listener = None
                        emit({"event": "voice", "kind": "stopped"})
                    replay, replay_id = spawn_replay(root, job_data, due["id"]), due["id"]
                    emit({"event": "replay_started", "job_id": replay_id})
    finally:
        if shopify_future is not None:
            shopify_future.cancel()
        if shopify_session:
            shopify_session.close()
        if listener:
            listener.close()
        if session:
            save_session()
        if replay:
            jobs.stop()
            replay.terminate()
        if step_run:
            step_run.terminate()
        if replay or step_run:
            park_ghost()
        catalog.close()
        jobs.close()


if __name__ == "__main__":
    main()
