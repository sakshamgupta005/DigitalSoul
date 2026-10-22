"""Bounded live connection probes; no private pages or workflow inputs are used."""
import argparse
import asyncio
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .omniroute import OmniRouteClient, OmniRouteSettings


async def browser_probe():
    from browser_use import Browser
    from ..drivers.browser import browser_executable
    with tempfile.TemporaryDirectory(prefix="browser-probe-", dir=Path(".ghost")) as profile:
        browser = Browser(user_data_dir=profile, executable_path=browser_executable(),
                          headless=True, is_local=True, use_cloud=False,
                          enable_default_extensions=False, auto_download_pdfs=False,
                          downloads_path=str(Path(profile) / "downloads"))
        try:
            await browser.start()
            page = await browser.new_page()
            result = await page.evaluate("() => 1 + 1")
            return {"ok": result == "2", "cdp": "connected", "actor_result": result}
        finally:
            await browser.kill()


def main():
    parser = argparse.ArgumentParser(description="Probe browser-use and local OmniRoute")
    parser.add_argument("--model", help="Configured model/combo for a minimal inference test")
    parser.add_argument("--inference", action="store_true", help="Send a synthetic eight-token test prompt")
    parser.add_argument("--browser", action="store_true", help="Start an isolated browser and test CDP/Actor")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--browser-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    Path(".ghost").mkdir(exist_ok=True)
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    if args.browser_child:
        # faulthandler shows whether timeout happened before or after asyncio starts.
        import faulthandler
        faulthandler.dump_traceback_later(12)
        try:
            result = asyncio.run(asyncio.wait_for(browser_probe(), 15))
        except Exception as exc:
            result = {"ok": False, "error": type(exc).__name__}
        finally:
            faulthandler.cancel_dump_traceback_later()
        print("DIGITALSOUL_PROBE=" + json.dumps(result))
        return
    settings = OmniRouteSettings.from_env()
    if args.model:
        settings = OmniRouteSettings(settings.base_url, settings.api_key, args.model)
    report = {"omniroute": OmniRouteClient(settings).probe(inference=args.inference)}
    try:
        report["browser_use"] = {"installed": True, "version": importlib.metadata.version("browser-use"), "tested": False}
    except importlib.metadata.PackageNotFoundError:
        report["browser_use"] = {"installed": False, "tested": False}
    if args.browser and report["browser_use"]["installed"]:
        try:
            child = subprocess.run([sys.executable, "-m", "agent.ghost.integrations.connections", "--browser-child"],
                                   capture_output=True, text=True, timeout=20)
            marker = next((line for line in child.stdout.splitlines() if line.startswith("DIGITALSOUL_PROBE=")), None)
            report["browser_use"].update(tested=True, **(json.loads(marker.split("=", 1)[1]) if marker else {"ok": False, "reason": "child_exited_without_result"}))
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            reason = "windows_socketpair_blocked" if "_fallback_socketpair" in stderr else "browser_start_timeout"
            report["browser_use"].update(tested=True, ok=False, reason=reason)
    encoded = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
