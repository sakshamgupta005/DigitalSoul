"""Local CLI for explicitly taught, one-shot WhatsApp messages."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
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
    if args.command in ("check", "schedule"):
        recipe = json.loads(protect(args.recipe.read_bytes(), decrypt=True))
        validate_recipe(recipe)
        if args.command == "check":
            print("Switch to the recorded WhatsApp chat within 5 seconds. Read-only check.")
            time.sleep(5)
            WhatsApp().check(recipe)
            print("Process, recipient header and empty composer matched. No message sent.")
            return
    jobs = Jobs(args.data / "jobs.sqlite3")
    try:
        if args.command == "schedule":
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
