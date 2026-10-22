"""Isolated native replay process; the notebook stays responsive to Stop."""
import json
import sys
import time
from pathlib import Path

from .jobs import Jobs
from .whatsapp import WhatsApp


def main():
    payload = json.loads(sys.stdin.readline())
    jobs = Jobs(Path(payload["data"]) / "jobs.sqlite3")
    try:
        while True:
            rows = [row for row in jobs.list() if row["id"] == payload["job_id"]]
            if not rows or rows[0]["state"] != "pending":
                break
            jobs.tick(WhatsApp())
            time.sleep(0.1)
    finally:
        jobs.close()


if __name__ == "__main__":
    main()
