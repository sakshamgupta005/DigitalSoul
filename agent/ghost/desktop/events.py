"""Session-scoped Win32 click/Enter/F8 events; no typed-key logging."""
import ctypes
import os
import queue
import threading
import time
from ctypes import wintypes

# Test harnesses only: also record synthesized input, so automated end-to-end runs can act like a user.
RECORD_INJECTED = os.environ.get("DIGITALSOUL_RECORD_INJECTED") == "1"


class WinEvents:
    def __init__(self, everything=False):
        self.everything = everything
        self.pids = set()
        self.queue = queue.Queue(maxsize=4096)
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.error = None
        self._callbacks = []

    def __enter__(self):
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.error:
            self.closed.set()
            raise RuntimeError("Windows input recording unavailable in this session") from self.error
        return self

    def __exit__(self, *args):
        self.closed.set()
        self.thread.join(timeout=3)

    def _pump(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD]
        user.SetWindowsHookExW.restype = wintypes.HANDLE
        user.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user.CallNextHookEx.restype = ctypes.c_ssize_t
        user.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.WindowFromPoint.argtypes = [wintypes.POINT]
        user.WindowFromPoint.restype = wintypes.HWND
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE

        class Mouse(ctypes.Structure):
            _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        class Key(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        user.GetKeyState.argtypes = [ctypes.c_int]
        user.GetKeyState.restype = ctypes.c_short

        def enqueue(kind, point=None, **extra):
            try:
                hwnd = user.GetForegroundWindow()
                if point is not None:
                    hit = user.WindowFromPoint(wintypes.POINT(*point))
                    hwnd = user.GetAncestor(hit, 2) if hit else 0  # GA_ROOT, before the click activates it
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if kind != "stop" and not self.everything and pid.value not in self.pids:
                    return
                item = {"kind": kind, "pid": pid.value, "hwnd": hwnd, "point": point, "time": time.monotonic(), **extra}
                try:
                    self.queue.put_nowait(item)
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                        self.queue.put_nowait(item)
                    except Exception:
                        pass
            except Exception:
                pass

        last_left_click = 0.0
        last_left_pos = (0, 0)

        @callback_type
        def mouse(code, message, data):
            nonlocal last_left_click, last_left_pos
            try:
                if code >= 0:
                    event = ctypes.cast(data, ctypes.POINTER(Mouse)).contents
                    if RECORD_INJECTED or not event.flags & 1:  # ignore injected mouse events
                        pos = (event.point.x, event.point.y)
                        now = time.monotonic()
                        if message == 0x0201:  # WM_LBUTTONDOWN
                            if now - last_left_click <= 0.38 and abs(pos[0] - last_left_pos[0]) < 6 and abs(pos[1] - last_left_pos[1]) < 6:
                                enqueue("double_click", pos)
                            else:
                                enqueue("click", pos)
                            last_left_click = now
                            last_left_pos = pos
                        elif message == 0x0204:  # WM_RBUTTONDOWN
                            enqueue("right_click", pos)
                        elif message == 0x020A:  # WM_MOUSEWHEEL
                            raw_delta = (event.data >> 16) & 0xFFFF
                            if raw_delta > 0x7FFF:
                                raw_delta -= 0x10000
                            enqueue("scroll", pos, delta=raw_delta)
            except Exception:
                pass
            return user.CallNextHookEx(None, code, message, data)

        @callback_type
        def keyboard(code, message, data):
            try:
                if code >= 0 and message == 0x0100:  # WM_KEYDOWN
                    event = ctypes.cast(data, ctypes.POINTER(Key)).contents
                    if RECORD_INJECTED or not event.flags & 0x10:
                        if event.vk == 0x77:
                            enqueue("stop")  # F8, anywhere, stops recording
                        elif event.vk == 0x0D:
                            enqueue("enter")
                        elif event.vk == 0x09:
                            enqueue("tab")
                        elif event.vk == 0x1B:
                            enqueue("escape")
                        elif user.GetKeyState(0x11) & 0x8000:
                            vk = event.vk
                            if 0x41 <= vk <= 0x5A:
                                enqueue("hotkey", combo=f"^{chr(vk).lower()}")
            except Exception:
                pass
            return user.CallNextHookEx(None, code, message, data)

        self._callbacks = [mouse, keyboard]

        hooks = []
        try:
            for kind, callback in ((14, mouse), (13, keyboard)):
                hook = user.SetWindowsHookExW(kind, callback, kernel.GetModuleHandleW(None), 0)
                if not hook:
                    raise ctypes.WinError(ctypes.get_last_error())
                hooks.append(hook)
            self.ready.set()
            message = wintypes.MSG()
            while not self.closed.is_set():
                while user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    user.TranslateMessage(ctypes.byref(message))
                    user.DispatchMessageW(ctypes.byref(message))
                time.sleep(0.005)
        except Exception as exc:
            self.error = exc
            self.ready.set()
        finally:
            for hook in hooks:
                user.UnhookWindowsHookEx(hook)
