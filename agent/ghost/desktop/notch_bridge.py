"""Private Tauri stdio RPC. All native recording stays in the local Windows session."""
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import Catalog
from .jobs import Jobs
from . import steps as step_replay


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


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()
    data = root / ".ghost" / "notch"
    job_data = root / ".ghost" / "desktop"
    data.mkdir(parents=True, exist_ok=True)
    job_data.mkdir(parents=True, exist_ok=True)
    catalog, jobs = Catalog(data / "workflows.sqlite3"), Jobs(job_data / "jobs.sqlite3")
    inbox = queue.Queue()
    session = None
    replay = None
    replay_id = None
    step_run = None

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
        value = catalog.save(finished.finish())
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
                    if method == "hello":
                        result = {"platform": sys.platform, "recording": bool(session), "running": replay_id,
                                  "data": str(data), "version": "0.1.0"}
                    elif method == "list":
                        result = {"workflows": catalog.list(), "jobs": jobs.list()}
                    elif method == "detail":
                        result = catalog.get(payload["id"])
                        result["steps_digest"] = step_replay.digest(result["steps"])
                    elif method == "start":
                        if session or replay or step_run:
                            raise ValueError("Stop the current recording or replay first")
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
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        value = catalog.get(payload["id"])
                        flow = value.get("flow")
                        if not flow:
                            raise ValueError(value.get("replay_error") or "This evidence needs an app adapter before replay")
                        at = payload.get("at") if method == "schedule" else (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
                        job_id = jobs.schedule(flow, payload.get("message", flow["message"]), at, payload.get("approved_recipient", ""))
                        if method == "replay":
                            replay = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.replay_worker"],
                                cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=0x08000000 if sys.platform == "win32" else 0, text=True)
                            replay.stdin.write(json.dumps({"data": str(job_data), "job_id": job_id}) + "\n")
                            replay.stdin.close()
                            replay_id = job_id
                            emit({"event": "replay_started", "job_id": job_id})
                        result = {"job_id": job_id}
                    elif method == "replay_steps":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        value = catalog.get(payload["id"])
                        # The approval names the exact steps the user reviewed; any change invalidates it.
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
                        if step_run:
                            step_run.terminate()
                        result = {"stopped": True}
                    elif method == "resume_jobs":
                        jobs.resume()
                    else:
                        raise ValueError("Unknown desktop command")
                    emit({"id": request_id, "ok": True, "result": result})
                except Exception as exc:
                    emit({"id": request_id, "ok": False, "error": str(exc)[:600]})
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
                    emit({"event": "error", "message": str(exc)[:600]})
                    save_session()
            if replay and replay.poll() is not None:
                emit({"event": "replay_finished", "job_id": replay_id,
                      "job": next((j for j in jobs.list() if j["id"] == replay_id), None)})
                replay, replay_id = None, None
            if step_run and step_run.poll() is not None:
                try:
                    outcome = json.loads(step_run.stdout.read().strip().splitlines()[-1])
                except (ValueError, IndexError):
                    outcome = {"ok": False, "detail": "Replay was stopped before it finished."}
                step_run = None
                emit({"event": "replay_finished", "steps": True, "job": outcome})
            # Pending scheduled jobs run while the notch is open, without a second worker command.
            if not session and not replay and not step_run:
                now = time.time()
                due = next((j for j in jobs.list() if j["state"] == "pending" and j["due"] <= now), None)
                if due:
                    replay = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.replay_worker"],
                        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=0x08000000 if sys.platform == "win32" else 0, text=True)
                    replay.stdin.write(json.dumps({"data": str(job_data), "job_id": due["id"]}) + "\n")
                    replay.stdin.close()
                    replay_id = due["id"]
                    emit({"event": "replay_started", "job_id": replay_id})
    finally:
        if session:
            save_session()
        if replay:
            jobs.stop()
            replay.terminate()
        if step_run:
            step_run.terminate()
        catalog.close()
        jobs.close()


if __name__ == "__main__":
    main()
