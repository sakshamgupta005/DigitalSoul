"""OpenAI-compatible OmniRoute client. Health is distinct from model inference."""
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class OmniRouteSettings:
    base_url: str = "http://127.0.0.1:20128/v1"
    api_key: str = field(default="", repr=False)
    model: str = ""

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip("/") != "/v1"):
            raise ValueError("OmniRoute must use a local /v1 URL without URL credentials")

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(override=False)
        return cls(os.environ.get("OMNIROUTE_BASE_URL", "http://127.0.0.1:20128/v1").rstrip("/"),
                   os.environ.get("OMNIROUTE_API_KEY", ""), os.environ.get("OMNIROUTE_MODEL", ""))


class OmniRouteClient:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or OmniRouteSettings.from_env()
        self.transport = transport

    def _client(self, timeout=3):
        headers = {"Authorization": "Bearer " + self.settings.api_key} if self.settings.api_key else {}
        return httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False,
                            headers=headers, transport=self.transport)

    def probe(self, inference=False):
        base = self.settings.base_url.rstrip("/")
        origin = base.rsplit("/v1", 1)[0]
        report = {"base_url": base, "reachable": False, "health": {}, "models": {},
                  "inference": {"attempted": False, "ok": False}}
        try:
            with self._client() as client:
                for name, url in (("health", origin + "/api/health"), ("models", base + "/models")):
                    response = client.get(url)
                    report["reachable"] = True  # An HTTP error still proves transport connectivity.
                    report[name] = {"status": response.status_code, "ok": response.status_code == 200}
                    if name == "models" and response.status_code == 200:
                        try:
                            data = response.json().get("data", [])
                            report[name]["count"] = len(data) if isinstance(data, list) else 0
                        except (ValueError, AttributeError):
                            report[name]["ok"] = False
                            report[name]["reason"] = "invalid_model_list"
        except httpx.HTTPError as exc:
            # Do not expose URLs with credentials, response bodies, headers, or keys.
            report["error"] = type(exc).__name__
            match = re.search(r"(?:WinError|Errno) (\d+)", str(exc))
            if match:
                report["os_error"] = int(match[1])
        if inference:
            if not self.settings.model:
                report["inference"]["reason"] = "Set OMNIROUTE_MODEL to a configured model or combo"
            else:
                report["inference"] = self.test_inference()
        return report

    def test_inference(self):
        result = {"attempted": True, "ok": False, "model": self.settings.model}
        if not self.settings.model:
            return {**result, "attempted": False, "reason": "model_not_configured"}
        try:
            with self._client(timeout=15) as client:
                response = client.post(self.settings.base_url.rstrip("/") + "/chat/completions", json={
                    "model": self.settings.model, "messages": [{"role": "user", "content": "Reply with only OK."}],
                    "max_tokens": 8, "stream": False,
                })
                result["status"] = response.status_code
                if response.status_code == 200:
                    data = response.json()
                    result["ok"] = bool(data.get("choices", [{}])[0].get("message", {}).get("content"))
        except (httpx.HTTPError, ValueError, IndexError, AttributeError) as exc:
            result["error"] = type(exc).__name__
        return result

    def browser_use_llm(self):
        """For reviewed analyst/element-extraction integrations, not autonomous input."""
        if not self.settings.model:
            raise ValueError("Set OMNIROUTE_MODEL; do not assume a default combo exists")
        from browser_use import ChatOpenAI
        return ChatOpenAI(model=self.settings.model, base_url=self.settings.base_url,
                          api_key=self.settings.api_key or "omniroute-local", temperature=0)
