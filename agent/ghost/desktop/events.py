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
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE

        class Mouse(ctypes.Structure):
            _fields_ = [("point", wintypes.POINT), ("data", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        class Key(ctypes.Structure):
            _fields_ = [("vk", wintypes.DWORD), ("scan", wintypes.DWORD),
                        ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]

        def enqueue(kind, point=None):
            try:
                hwnd = user.GetForegroundWindow()
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if kind != "stop" and not self.everything and pid.value not in self.pids:
                    return
                try:
                    self.queue.put_nowait({"kind": kind, "pid": pid.value, "point": point, "time": time.monotonic()})
                except queue.Full:
                    try:
                        self.queue.get_nowait()
                        self.queue.put_nowait({"kind": kind, "pid": pid.value, "point": point, "time": time.monotonic()})
                    except Exception:
                        pass
            except Exception:
                pass

        @callback_type
        def mouse(code, message, data):
            try:
                if code >= 0 and message == 0x0201:  # WM_LBUTTONDOWN
                    event = ctypes.cast(data, ctypes.POINTER(Mouse)).contents
                    if RECORD_INJECTED or not event.flags & 1:  # ignore injected mouse events
                        enqueue("click", (event.point.x, event.point.y))
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
