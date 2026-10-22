"""Local CLI for explicitly taught, one-shot WhatsApp messages."""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .jobs import Jobs
from .whatsapp import WhatsApp, teach, validate_recipe
from ..safety.credentials import protect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path(".ghost/desktop"))
    commands = parser.add_subparsers(dest="command", required=True)
    record = commands.add_parser("record", help="Guided read-only teaching; never sends")
    record.add_argument("--recipient", required=True, help="Exact verified chat header")
    flow_record = commands.add_parser("record-flow", help="Record opening WhatsApp, search, contact click, message and Send")
    flow_record.add_argument("--recipient", required=True, help="Person/contact to search for")
    flow_record.add_argument("--chat-title", help="Exact chat header if different from the search name")
    flow_record.add_argument("--message", default="hello")
    flow_record.add_argument("--seconds", type=int, default=120)
    replay = commands.add_parser("replay", help="Approve and replay a recorded recipe once")
    replay.add_argument("--recipe", type=Path, required=True)
    replay.add_argument("--message", help="Override the recorded message; explicit approval covers the override")
    replay.add_argument("--approve-recipient", required=True)
    for command in ("check", "schedule"):
        sub = commands.add_parser(command)
        sub.add_argument("--recipe", type=Path, required=True)
        if command == "schedule":
            sub.add_argument("--message", required=True)
            sub.add_argument("--at", required=True, help="ISO date/time with offset, e.g. 2026-12-01T00:00:00+05:30")
            sub.add_argument("--approve-recipient", required=True, help="Approve this exact recipient, message and time")
    commands.add_parser("status")
    commands.add_parser("stop", help="Latch desktop stop and cancel pending jobs")
    commands.add_parser("resume", help="Explicitly re-arm the desktop worker; cancelled jobs stay cancelled")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id")
    commands.add_parser("worker", help="Keep running in the logged-in Windows session; Ctrl+C stops")
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    if args.command == "record-flow":
        from .session import Session
        from .catalog import Catalog
        if not 10 <= args.seconds <= 300:
            raise ValueError("Recording duration must be 10–300 seconds")
        session = Session({"name": f"Message {args.recipient}"[:80], "apps": ["WhatsApp.exe"],
                           "recipient": args.recipient, "chat_title": args.chat_title or args.recipient,
                           "message": args.message})
        print("Recording is active. Open WhatsApp, search the full contact name, CLICK the verified result,")
        print(f"type {args.message!r}, pause briefly, then send once. This demonstration sends a real message.")
        print("F8 stops. Only WhatsApp is observed; no repeated name-confirmation prompt.")
        deadline = time.monotonic() + args.seconds
        try:
            while time.monotonic() < deadline:
                if session.poll() or (session.capture and session.capture.done):
                    break
                time.sleep(0.04)
        except KeyboardInterrupt:
            pass
        finally:
            recording = session.finish()
        catalog_dir = args.data.parent / "notch"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        catalog = Catalog(catalog_dir / "workflows.sqlite3")
        try:
            recording = catalog.save(recording)
        finally:
            catalog.close()
        if not recording["flow"]:
            raise ValueError(f"Steps saved in the notch, but replay is not ready: {recording['replay_error']}")
        path = args.data / f"whatsapp-flow-{recording['id']}.protected"
        with path.open("xb") as output:
            output.write(protect(json.dumps(recording["flow"]).encode()))
        print(f"Replay recipe saved: {path}")
        return
    if args.command == "record":
        path = args.data / "whatsapp-recipe.protected"
        if path.exists():
            raise ValueError("A recipe already exists; choose a different --data directory to teach again")
        recipe = teach(args.recipient)
        # No chat history, screenshots or keystrokes; recipe contains selected UI labels only.
        with path.open("xb") as output:
            output.write(protect(json.dumps(recipe).encode()))
        print(f"Recipe saved: {path}")
        return
    if args.command in ("check", "schedule", "replay"):
        recipe = json.loads(protect(args.recipe.read_bytes(), decrypt=True))
        validate_recipe(recipe)
        if args.command == "check":
            if recipe.get("format") == "digitalsoul.whatsapp-flow.v1":
                print("Flow format and recipient validated. Replay will re-resolve its UI controls; nothing sent.")
                return
            print("Switch to the recorded WhatsApp chat within 5 seconds. Read-only check.")
            time.sleep(5)
            WhatsApp().check(recipe)
            print("Process, recipient header and empty composer matched. No message sent.")
            return
    jobs = Jobs(args.data / "jobs.sqlite3")
    try:
        if args.command == "replay":
            message = args.message or recipe.get("message")
            if not message:
                raise ValueError("Supply --message for a legacy taught recipe")
            job_id = jobs.schedule(recipe, message, (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), args.approve_recipient)
            print(f"Approved replay job {job_id}; opening the recorded routine.")
            try:
                while next(j for j in jobs.list() if j["id"] == job_id)["state"] == "pending":
                    jobs.tick(WhatsApp(), job_id=job_id)
                    time.sleep(0.1)
            except KeyboardInterrupt:
                jobs.cancel(job_id)
                raise
            print(json.dumps(next(j for j in jobs.list() if j["id"] == job_id)))
        elif args.command == "schedule":
            job_id = jobs.schedule(recipe, args.message, args.at, args.approve_recipient)
            print(f"Scheduled {job_id}. Start worker; inspect status to see the outcome.")
        elif args.command == "status":
            for job in jobs.list():
                job["due"] = datetime.fromtimestamp(job["due"], timezone.utc).isoformat()
                job["expires"] = datetime.fromtimestamp(job["expires"], timezone.utc).isoformat()
                print(json.dumps(job))
        elif args.command == "cancel":
            print("Cancelled" if jobs.cancel(args.job_id) else "Not cancellable; inspect status")
        elif args.command == "stop":
            jobs.stop()
            print("Desktop stop latched; queued jobs cancelled. An already invoked send cannot be undone.")
        elif args.command == "resume":
            jobs.resume()
            print("Desktop worker re-armed. Cancelled jobs remain cancelled.")
        else:
            print("Worker active. Keep Windows awake/unlocked and the recorded WhatsApp chat foreground.")
            print("Ctrl+C stops the worker. It will never retry a potentially sent message.")
            adapter = WhatsApp()
            while True:
                job_id = jobs.tick(adapter)
                if job_id:
                    print(f"Processed {job_id}; use status for result")
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("Worker stopped. Pending jobs remain stored; late jobs expire after 60 seconds.")
    finally:
        jobs.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
