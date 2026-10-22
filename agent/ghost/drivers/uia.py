"""Fresh, process-scoped UIA reads. Native mutation requires an app adapter."""
import asyncio
import sys

from ..execution.errors import GhostError


class UIAReader:
    def __init__(self, allowed_pids):
        self.allowed_pids = frozenset(allowed_pids)

    async def read(self, pid, automation_id, control_type):
        if sys.platform != "win32" or pid not in self.allowed_pids:
            raise GhostError("POLICY_BLOCKED", "Native process is not registered", 403)

        def read_fresh():
            try:
                from pywinauto import Application
            except ImportError as exc:
                raise GhostError("APP_UNREACHABLE", "Install the Windows optional dependencies", 503) from exc
            # Blinky lesson: scope to process and resolve fresh COM wrappers per read.
            app = Application(backend="uia").connect(process=pid, timeout=2)
            matches = []
            for window in app.windows():
                matches.extend(window.descendants(auto_id=automation_id, control_type=control_type))
            matches = [item for item in matches if item.is_visible()]
            if len(matches) != 1:
                code = "LOCATOR_AMBIGUOUS" if matches else "LOCATOR_NOT_FOUND"
                raise GhostError(code, "Native target must resolve uniquely")
            return matches[0].window_text()

        return await asyncio.to_thread(read_fresh)
