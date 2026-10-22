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
    step_sources = []
    step_outbox = queue.Queue()
    step_outcome = None
    listener = None
    shopify_session = None
    shopify_future = None
    shopify_request_id = None
    shopify_deadline = 0

    def launch_steps(steps_payload):
        nonlocal step_run, step_outcome
        step_outcome = None
        step_run = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.steps"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0, text=True, encoding="utf-8")
        step_run.stdin.write(json.dumps({"steps": steps_payload}, ensure_ascii=False) + "\n")
        step_run.stdin.close()
        def reader(out):
            for line in iter(out.readline, ""):
                line = line.strip()
                if line:
                    try:
                        step_outbox.put(json.loads(line))
                    except Exception:
                        pass
        threading.Thread(target=reader, args=(step_run.stdout,), daemon=True).start()

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
                emit({"event": "profile_updated", "profile": learning_engine.get_active_profile()})
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
                            "start", "replay", "schedule", "replay_steps", "voice_start", "shopify_run", "learning_execute", "workflow_synthesize"):
                        raise ValueError("A Shopify run is active; wait for it or cancel it first")
                    if method == "hello":
                        result = {"platform": sys.platform, "recording": bool(session), "running": replay_id or bool(step_run),
                                  "data": str(data), "version": "0.1.0", "accent": accent_color(),
                                  "patterns_count": len(learning_engine.list_patterns()),
                                  "profiles_count": len(learning_engine.list_profiles())}
                    elif method == "list":
                        result = {"workflows": catalog.list(), "jobs": jobs.list()}
                    elif method == "detail":
                        result = catalog.get(payload["id"])
                        from .session import clean_steps
                        result["steps"] = clean_steps(result["steps"])  # what you see is exactly what replays
                        result["steps_digest"] = step_replay.digest(result["steps"])
                    elif method == "voice_command":
                        result = dispatch(payload.get("text"), catalog.list(), busy=bool(session or replay or step_run or shopify_future), catalog=catalog, learning=learning_engine)
                    elif method == "workflow_synthesize":
                        if session or replay or step_run:
                            raise ValueError("Wait for the active recording or replay before planning")
                        from .synthesizer import synthesize_workflow
                        prompt = payload.get("prompt") or ""
                        result = synthesize_workflow(prompt, catalog=catalog, learning=learning_engine)
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
                        step_sources = value.get("source_routines") or ["recording:" + value["id"]]
                        launch_steps(value["steps"])
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
                        product_data = payload.get("product") or {}
                        product_data["store_url"] = shopify_url(product_data.get("store_url"))
                        from ..browser.shopify_session import ShopifySession
                        click_save = payload.get("click_save", False)
                        if shopify_session is None:
                            try:
                                shopify_session = ShopifySession(event_sink=emit)
                            except TypeError:
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
                        result = {"patterns": [dict(p, approved_digest=step_replay.digest({"steps": p["skeleton"], "parameters": p["parameters"]})) for p in learning_engine.list_patterns()]}
                    elif method == "learning_detail":
                        result = learning_engine.get_pattern(payload["id"])
                        if result:
                            result["approved_digest"] = step_replay.digest({"steps": result["skeleton"], "parameters": result["parameters"]})
                    elif method == "learning_execute":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        pattern = learning_engine.get_pattern(payload["id"])
                        if not pattern or payload.get("approved_digest") != step_replay.digest({"steps": pattern["skeleton"], "parameters": pattern["parameters"]}):
                            raise ValueError("The learned steps changed; review this pattern again")
                        steps = learning_engine.adapt_pattern_steps(payload["id"], payload.get("parameters"))
                        step_replay.validate(steps)
                        step_sources = ["pattern:" + payload["id"]]
                        launch_steps(steps)
                        emit({"event": "replay_started", "steps": True, "pattern": True})
                        result = {"started": True, "steps_count": len(steps)}
                    elif method == "learning_delete":
                        learning_engine.delete_pattern(payload["id"])
                        result = {"deleted": True}
                    elif method == "gform_inspect":
                        from ..browser.gform_actor import GFormActor
                        result = GFormActor.inspect_url(payload.get("url", ""))
                    elif method == "gform_run":
                        if session or replay or step_run:
                            raise ValueError("Stop recording or wait for the active replay")
                        from ..browser.gform_actor import GFormActor
                        actor = GFormActor(headless=payload.get("headless", False))
                        try:
                            result = asyncio.run(actor.fill_and_review(
                                payload.get("url", ""),
                                payload.get("items", []),
                                click_submit=payload.get("click_submit", False),
                                save_to_memory=payload.get("save_memory", True)
                            ))
                        finally:
                            asyncio.run(actor.close())
                    elif method == "gform_memory_get":
                        from ..browser.gform_memory import GFormMemory
                        result = {"profile": GFormMemory().profile}
                    elif method == "gform_memory_save":
                        from ..browser.gform_memory import GFormMemory
                        result = {"profile": GFormMemory().save(payload.get("profile", {}))}
                    elif method == "export_skill":
                        result = {"skill_markdown": learning_engine.export_skill(payload["id"])}
                    elif method == "import_skill":
                        result = learning_engine.import_skill(payload["skill_markdown"])
                    elif method == "list_hermes_skills":
                        from ..learning.hermes_sync import list_hermes_skills
                        result = {"skills": list_hermes_skills()}
                    elif method == "import_hermes_skill":
                        from ..learning.hermes_sync import import_hermes_skill_to_patterns
                        result = import_hermes_skill_to_patterns(payload["skill_name"], learning_engine)
                    elif method == "demonstration_synthesize":
                        from ..learning.skill_recorder import SkillSynthesizer
                        synth = SkillSynthesizer()
                        res = synth.synthesize(
                            steps=payload.get("steps", []),
                            voice_notes=payload.get("voice_notes"),
                            screen_context=payload.get("screen_context"),
                            app_name=payload.get("app_name"),
                        )
                        result = {
                            "candidate": res["candidate"].model_dump(mode="json"),
                            "skill_markdown": res["skill_markdown"],
                            "redacted_count": res["redacted_count"],
                        }

                    elif method == "evaluate_skill":
                        result = learning_engine.evaluate_skill(payload["id"])

                    elif method == "optimize_skill":
                        result = learning_engine.optimize_skill(payload["id"])
                    elif method == "profile_list":
                        result = {
                            "profiles": learning_engine.list_profiles(),
                            "active_profile": learning_engine.get_active_profile()
                        }
                    elif method == "profile_get":
                        result = learning_engine.get_profile(payload["id"])
                    elif method == "profile_set_active":
                        active = learning_engine.set_active_profile(payload["id"])
                        emit({"event": "profile_changed", "profile": active})
                        result = active
                    elif method == "profile_create":
                        created = learning_engine.create_profile(
                            name=payload.get("name", "New Profile"),
                            role=payload.get("role", "Digital Twin"),
                            avatar=payload.get("avatar", "👤"),
                            initial_rules=payload.get("initial_rules"),
                            signature=payload.get("signature")
                        )
                        emit({"event": "profile_created", "profile": created})
                        result = created
                    elif method == "profile_delete":
                        result = {"deleted": learning_engine.delete_profile(payload["id"])}
                    elif method == "profile_execute":
                        result = learning_engine.execute_with_habits(
                            payload.get("prompt", ""),
                            profile_id=payload.get("profile_id")
                        )
                    elif method == "hud_demo":
                        if session or replay or step_run:
                            raise ValueError("Stop the active recording or replay first")
                        def run_demo():
                            time.sleep(0.1)
                            emit({"event": "hud_started"})
                            time.sleep(0.4)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 1, "total_steps": 3,
                                "label": "Product Title",
                                "rect": {"left": 280, "top": 150, "width": 360, "height": 44},
                                "status": "Targeting: Product Title"
                            })
                            time.sleep(0.7)
                            emit({
                                "event": "hud_acting",
                                "step_index": 1, "total_steps": 3,
                                "action": "set_text",
                                "label": "Product Title",
                                "rect": {"left": 280, "top": 150, "width": 360, "height": 44},
                                "status": "Typing 'Sony WH-1000XM5 Wireless Headphones'"
                            })
                            time.sleep(0.8)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 2, "total_steps": 3,
                                "label": "Price Field",
                                "rect": {"left": 420, "top": 340, "width": 190, "height": 45},
                                "status": "Targeting: Price Field"
                            })
                            time.sleep(0.7)
                            emit({
                                "event": "hud_acting",
                                "step_index": 2, "total_steps": 3,
                                "action": "verify",
                                "label": "Price Field",
                                "rect": {"left": 420, "top": 340, "width": 190, "height": 45},
                                "status": "Verifying price: $348.00"
                            })
                            time.sleep(0.8)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 3, "total_steps": 3,
                                "label": "Save Product Button",
                                "rect": {"left": 720, "top": 520, "width": 150, "height": 42},
                                "status": "Targeting: Save Product"
                            })
                            time.sleep(0.7)
                            emit({
                                "event": "hud_acting",
                                "step_index": 3, "total_steps": 3,
                                "action": "click",
                                "label": "Save Product Button",
                                "rect": {"left": 720, "top": 520, "width": 150, "height": 42},
                                "status": "Clicking Save Product"
                            })
                            time.sleep(0.8)
                            emit({
                                "event": "replay_finished",
                                "steps": True,
                                "job": {"ok": True, "detail": "Live Ghost Pilot HUD demo executed successfully across 3 targets."}
                            })
                        threading.Thread(target=run_demo, daemon=True).start()
                        result = {"demo_started": True}
                    elif method == "self_heal_demo":
                        if session or replay or step_run:
                            raise ValueError("Stop the active recording or replay first")
                        obstacle_mode = payload.get("obstacle", "cookie")
                        def run_heal_demo():
                            time.sleep(0.1)
                            emit({"event": "hud_started"})
                            time.sleep(0.5)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 1, "total_steps": 3,
                                "label": "Shopify Staging Form",
                                "is_browser_level": True,
                                "status": "Opening practice product form..."
                            })
                            time.sleep(0.8)

                            if obstacle_mode == "security":
                                # Strict safeguard demo: refusal to auto-dismiss security boundaries
                                emit({
                                    "event": "hud_obstacle",
                                    "kind": "security_boundary",
                                    "label": "Security Boundary: Authorize Payment ($450)",
                                    "recoverable": False,
                                    "reason": "Security boundary detected: payment confirmation and admin password required.",
                                    "rect": {"left": 360, "top": 240, "width": 460, "height": 280},
                                    "details": "Automated bypass is strictly forbidden for security & payment controls."
                                })
                                time.sleep(1.2)
                                emit({
                                    "event": "hud_paused_for_help",
                                    "reason": "Security boundary requires human authorization. Automated dismissal blocked."
                                })
                                return

                            # Recoverable obstacle demo: cookie consent banner
                            emit({
                                "event": "hud_obstacle",
                                "kind": "cookie_banner",
                                "label": "Cookie & Privacy Consent Banner",
                                "recoverable": True,
                                "rect": {"left": 380, "top": 260, "width": 440, "height": 220},
                                "details": "Blocking element: #cookie-consent-overlay | Safe Action: Accept All Cookies"
                            })
                            time.sleep(1.2)
                            emit({
                                "event": "hud_recovering",
                                "strategy": "dismiss_cookie_banner",
                                "status": "Recovering: Dismissing Cookie Banner via 'Accept All Cookies' (attempt 1/2)",
                                "rect": {"left": 480, "top": 410, "width": 160, "height": 38},
                                "label": "Accept All Cookies"
                            })
                            time.sleep(1.0)
                            emit({
                                "event": "hud_retrying_verification",
                                "status": "Retrying verification: Confirming banner cleared and form interactable...",
                                "attempt": 1,
                                "max_attempts": 2
                            })
                            time.sleep(0.9)
                            emit({
                                "event": "hud_acting",
                                "status": "Obstacle resolved • Resuming workflow",
                                "action": "resume",
                                "label": "Resumed"
                            })
                            time.sleep(0.6)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 2, "total_steps": 3,
                                "label": "Product Title",
                                "rect": {"left": 280, "top": 240, "width": 380, "height": 40},
                                "status": "Targeting: Product Title"
                            })
                            time.sleep(0.7)
                            emit({
                                "event": "hud_acting",
                                "step_index": 2, "total_steps": 3,
                                "action": "set_text",
                                "label": "Product Title",
                                "rect": {"left": 280, "top": 240, "width": 380, "height": 40},
                                "status": "Typing: Premium Organic Cotton Tee"
                            })
                            time.sleep(0.8)
                            emit({
                                "event": "hud_targeting",
                                "step_index": 3, "total_steps": 3,
                                "label": "Save Product Button",
                                "rect": {"left": 660, "top": 180, "width": 120, "height": 36},
                                "status": "Targeting: Save"
                            })
                            time.sleep(0.7)
                            emit({
                                "event": "hud_acting",
                                "step_index": 3, "total_steps": 3,
                                "action": "click",
                                "label": "Save Product Button",
                                "rect": {"left": 660, "top": 180, "width": 120, "height": 36},
                                "status": "Clicking Save"
                            })
                            time.sleep(0.8)
                            emit({
                                "event": "replay_finished",
                                "steps": True,
                                "job": {"ok": True, "detail": "Self-healing execution demo succeeded: obstacle resolved and form saved."}
                            })
                        threading.Thread(target=run_heal_demo, daemon=True).start()
                        result = {"demo_started": True, "mode": obstacle_mode}
                    elif method == "screen_scan":
                        from .screen_workflow import scan_screen_and_propose
                        active_window_only = payload.get("active_window_only", True)
                        cleanup_image = payload.get("cleanup_image", True)
                        proposal = scan_screen_and_propose(
                            active_window_only=active_window_only,
                            cleanup_image=cleanup_image,
                            custom_image_path=payload.get("image_path"),
                            mock_ocr_text=payload.get("mock_ocr_text"),
                        )
                        result = proposal.to_dict()
                    elif method in ("chain_plan", "chain_run"):
                        from .chain_orchestrator import CrossAppChainOrchestrator
                        orchestrator = CrossAppChainOrchestrator()
                        dry_run = method == "chain_plan" or payload.get("dry_run", False)
                        approved = payload.get("approved", False)
                        rep = orchestrator.execute_chain(
                            workbook_path=payload.get("workbook_path"),
                            store_url=payload.get("store_url"),
                            recipient=payload.get("recipient", "Store Manager"),
                            approved=approved,
                            dry_run=dry_run,
                            idempotency_key=payload.get("idempotency_key"),
                        )
                        result = rep.to_dict()
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
                        try:
                            command = dispatch(event.get("text", ""), catalog.list(), require_wake=True,
                                               awake=time.monotonic() < listener.awake_until,
                                               busy=bool(session or replay or step_run or shopify_future), catalog=catalog, learning=learning_engine)
                        except Exception as exc:
                            command = {"kind": "command_error", "message": f"Could not handle voice command: {str(exc)[:200]}"}
                        if command["kind"] == "wake":
                            listener.awake_until = time.monotonic() + 12
                        elif command["kind"] == "workflow":
                            listener.awake_until = 0
                        emit({"event": "voice", **command})
                        if command["kind"] == "sleep":
                            listener.close()
                            listener = None
                            emit({"event": "voice", "kind": "stopped"})
                            break
                        elif command["kind"] in ("workflow", "app_opened", "add_workflow"):
                            listener.awake_until = time.monotonic() + 20
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
            while not step_outbox.empty():
                try:
                    msg = step_outbox.get_nowait()
                    if "event" in msg:
                        emit(msg)
                    elif "ok" in msg:
                        step_outcome = msg
                except queue.Empty:
                    break
            if replay and replay.poll() is not None:
                emit({"event": "replay_finished", "job_id": replay_id,
                      "job": next((j for j in jobs.list() if j["id"] == replay_id), None)})
                replay, replay_id = None, None
                park_ghost()
            if step_run and step_run.poll() is not None:
                time.sleep(0.04)
                while not step_outbox.empty():
                    try:
                        msg = step_outbox.get_nowait()
                        if "event" in msg:
                            emit(msg)
                        elif "ok" in msg:
                            step_outcome = msg
                    except queue.Empty:
                        break
                outcome = step_outcome or {"ok": False, "detail": "Replay was stopped before it finished."}
                learning_engine.record_outcome(step_sources, outcome.get("ok") is True)
                step_sources = []
                step_run = None
                step_outcome = None
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
