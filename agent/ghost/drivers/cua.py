"""Persistent native driver transport adapted from Blinky's _McpChild pattern.

No Blinky runtime imports. Only window reads and snapshot-bound value writes are
exposed here; commands, global keys, coordinates and permission clicks are absent.
"""
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path


class CuaClient:
    ALLOWED = {"list_windows", "get_window_state", "set_value"}

    def __init__(self, binary=None):
        self.binary = binary or os.environ.get("DIGITALSOUL_CUA_DRIVER") or shutil.which("cua-driver")
        if not self.binary:
            candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Cua/cua-driver/bin/cua-driver.exe"
            self.binary = str(candidate) if candidate.is_file() else None
        self.process = None
        self._lock = threading.RLock()
        self._replies = queue.Queue()
        self._next_id = 0

    def _start(self):
        if not self.binary:
            raise RuntimeError("The installed cua-driver could not be found")
        self.process = subprocess.Popen([self.binary, "mcp"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=0x08000000 if os.name == "nt" else 0)
        process = self.process
        replies = self._replies = queue.Queue()
        def read():
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    if "id" in value:
                        replies.put(value)
                except ValueError:
                    pass
            replies.put(None)
        threading.Thread(target=read, daemon=True, name="digitalsoul-cua-reader").start()
        self._request("initialize", {"protocolVersion":"2024-11-05", "capabilities":{},
            "clientInfo":{"name":"digitalsoul", "version":"0.1"}})
        self._write({"jsonrpc":"2.0", "method":"notifications/initialized", "params":{}})

    def _write(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _request(self, method, params):
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc":"2.0", "id":request_id, "method":method, "params":params})
        reply = self._replies.get(timeout=20)
        if reply is None or reply.get("id") != request_id:
            raise RuntimeError("Native driver disconnected or returned an unexpected response")
        if "error" in reply:
            raise RuntimeError("Native driver rejected the request")
        return reply.get("result", {})

    def call(self, tool, arguments=None):
        if tool not in self.ALLOWED:
            raise ValueError("Native tool is outside the reviewed adapter")
        with self._lock:
            try:
                if self.process is None:
                    self._start()
                result = self._request("tools/call", {"name":tool, "arguments":arguments or {}})
                if result.get("isError"):
                    raise RuntimeError("Native action was rejected; no automatic retry")
                data = result.get("structuredContent")
                if data is None:
                    for content in result.get("content", []):
                        if content.get("type") == "text":
                            try:
                                data = json.loads(content["text"])
                                break
                            except ValueError:
                                pass
                if not isinstance(data, dict):
                    raise RuntimeError("Native driver returned no structured result")
                if data.get("ok") is False or data.get("code") in {"background_unavailable", "tool_invocation_failed"}:
                    raise RuntimeError("Native control does not support the requested background action")
                return data
            except (queue.Empty, BrokenPipeError, OSError):
                self.close()
                raise RuntimeError("Native driver connection failed; inspect the form before retrying") from None

    def close(self):
        with self._lock:
            process, self.process = self.process, None
            if process is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=2)
