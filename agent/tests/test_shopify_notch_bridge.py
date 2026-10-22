"""Exercise the real notch stdio loop while browser automation is waiting."""
import json
import subprocess
import sys
from pathlib import Path


def test_notch_answers_status_and_cancel_while_shopify_is_waiting(tmp_path):
    script = '''
import sys
from concurrent.futures import Future
from agent.ghost.browser import shopify_session
class WaitingSession:
    def submit(self, *args, **kwargs):
        self.future = Future()
        return self.future
    def close(self):
        self.future.cancel()
shopify_session.ShopifySession = WaitingSession
from agent.ghost.desktop import notch_bridge
notch_bridge.main()
'''
    messages = [
        {"id": "product", "method": "shopify_run", "payload": {"product": {"title": "Test"}}},
        {"id": "status", "method": "hello"},
        {"id": "library", "method": "list"},
        {"id": "cancel", "method": "shopify_cancel"},
        {"method": "shutdown"},
    ]
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2],
                            input="".join(json.dumps(m) + "\n" for m in messages),
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    responses = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    replies = {r["id"]: r for r in responses if "id" in r}
    assert replies["status"]["ok"] and replies["library"]["ok"]
    assert replies["cancel"]["result"]["cancelled"]
    assert replies["product"]["ok"] is False
    assert "unsaved" in replies["product"]["error"]
    order = [r.get("id") for r in responses]
    assert order.index("status") < order.index("product")
    assert any(r.get("event") == "shopify_started" for r in responses)
