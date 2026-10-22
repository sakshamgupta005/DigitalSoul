"""Approval rules for retargeted WhatsApp jobs and notch bridge request checks. Fakes only: nothing is sent."""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent.ghost.desktop.jobs import Jobs
from agent.ghost.desktop.notch_bridge import shopify_url
from agent.tests.test_notch import recorded


NOW = datetime(2026, 10, 22, 10, tzinfo=timezone.utc)
AT = (NOW + timedelta(seconds=30)).isoformat()
ROOT = Path(__file__).resolve().parents[2]


def send_recipe():
    return {"format": "digitalsoul.whatsapp-send.v1", "recipient": "Vansh Arora", "search": "Vansh",
            "message": "hello", "executable": r"C:\Apps\WhatsApp.exe", "send": {"kind": "enter"}}


class Recorder:
    def __init__(self):
        self.sent = []

    def prepare(self, recipe, message):
        pass

    def send(self, recipe, message):
        self.sent.append((recipe, message))


@pytest.fixture
def store(tmp_path):
    jobs = Jobs(tmp_path / "jobs.sqlite3", clock=lambda: NOW)
    yield jobs
    jobs.close()


def dispatched(store):
    adapter = Recorder()
    store.tick(adapter, now=NOW + timedelta(seconds=30))
    return adapter.sent[0]


def test_send_recipe_goes_to_the_approved_person(store):
    store.schedule(send_recipe(), "see you at 5", AT, " Saksham ", now=NOW)
    recipe, message = dispatched(store)
    assert (recipe["recipient"], recipe["search"], message) == ("Saksham", "Saksham", "see you at 5")


def test_send_recipe_same_person_keeps_recorded_search(store):
    store.schedule(send_recipe(), "hi", AT, "vansh arora", now=NOW)
    recipe, _ = dispatched(store)
    assert recipe == send_recipe()


@pytest.mark.parametrize("who", ["", "   ", None, "Two\nLines", "x" * 129])
def test_send_recipe_rejects_unusable_recipient(store, who):
    with pytest.raises(ValueError):
        store.schedule(send_recipe(), "hi", AT, who, now=NOW)
    assert store.list() == []


def test_flow_routine_is_retargeted_to_the_approved_person(store):
    store.schedule(recorded(), "hello", AT, "Saksham", now=NOW)
    recipe, _ = dispatched(store)
    assert recipe["retarget"] and recipe["chat_title"] == "Saksham" and recipe["header"]["name"] == "Saksham"


def test_flow_routine_rejects_control_characters_in_recipient(store):
    with pytest.raises(ValueError):
        store.schedule(recorded(), "hello", AT, "Saksham\tGupta", now=NOW)


@pytest.mark.parametrize("at", [None, 12345, ""])
def test_schedule_time_must_be_text_with_offset(store, at):
    with pytest.raises(ValueError):
        store.schedule(send_recipe(), "hi", at, "Vansh Arora", now=NOW)


def test_latched_stop_blocks_retargeted_jobs(store):
    store.stop()
    with pytest.raises(ValueError, match="latched"):
        store.schedule(send_recipe(), "hi", AT, "Saksham", now=NOW)


@pytest.mark.parametrize("url", ["https://admin.shopify.com/store/x/products/new",
                                 "https://my-shop.myshopify.com/admin/products/new",
                                 "http://127.0.0.1:7777/api/v1/shopify/test-form"])
def test_shopify_url_accepts_admin_and_local_test_form(url):
    assert shopify_url(url) == url


@pytest.mark.parametrize("url", ["file:///C:/Users/me/secrets.html", "javascript:alert(1)",
                                 "http://admin.shopify.com/store/x", "https://admin.shopify.com.evil.test/",
                                 "https://evil.test/?admin.shopify.com", "https://myshopify.com.evil.test/", ""])
def test_shopify_url_rejects_other_pages(url):
    with pytest.raises(ValueError):
        shopify_url(url)


def bridge(tmp_path, requests, linger=0.0):
    process = subprocess.Popen([sys.executable, "-u", "-m", "agent.ghost.desktop.notch_bridge", str(tmp_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=ROOT)
    process.stdin.write("".join(json.dumps(r) + "\n" for r in requests))
    process.stdin.flush()
    time.sleep(linger)  # lets the bridge loop look for due jobs before it is told to stop
    out, err = process.communicate(json.dumps({"method": "shutdown"}) + "\n", timeout=20)
    assert process.returncode == 0, err
    return [json.loads(line) for line in out.splitlines()]


def test_bridge_rejects_bad_requests_without_side_effects(tmp_path):
    lines = bridge(tmp_path, [
        {"id": "1", "method": "rm -rf"},
        {"id": "2", "method": "shopify_run", "payload": {"product": {"store_url": "file:///C:/x.html"}}},
        {"id": "3", "method": "schedule", "payload": {"id": "missing", "approved_recipient": "x"}},
        {"id": "4", "method": "replay_steps", "payload": {"id": "missing", "approved_digest": ""}},
    ])
    replies = {r["id"]: r for r in lines if "id" in r}
    assert all(replies[i]["ok"] is False for i in "1234")
    assert "Shopify admin" in replies["2"]["error"]
    assert not any(r.get("event") == "replay_started" for r in lines)


def test_bridge_never_starts_a_job_past_its_send_window(tmp_path):
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    data = tmp_path / ".ghost" / "desktop"
    data.mkdir(parents=True)
    jobs = Jobs(data / "jobs.sqlite3", clock=lambda: past)
    jobs.schedule(send_recipe(), "late", (past + timedelta(seconds=5)).isoformat(), "Vansh Arora")
    jobs.close()
    lines = bridge(tmp_path, [{"id": "1", "method": "hello"}], linger=0.5)
    assert not any(r.get("event") == "replay_started" for r in lines)
