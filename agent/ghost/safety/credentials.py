"""Per-user DPAPI storage on Windows; owner-only files on other platforms."""
import argparse
import ctypes
import os
import secrets
import sys
from pathlib import Path


def protect(data, decrypt=False):
    if sys.platform != "win32":
        return data
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; current Windows user scope, not machine scope.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def load_or_create(path, generate):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        data = protect(generate())
        # Exclusive creation prevents accidental rotation of an existing identity.
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
    return protect(path.read_bytes(), decrypt=True)


def agent_token(data_dir):
    return load_or_create(Path(data_dir) / "agent-token.protected", lambda: secrets.token_urlsafe(32).encode()).decode()


def main():
    parser = argparse.ArgumentParser(description="Read the current user's local agent token")
    parser.add_argument("--data", type=Path, default=Path(".ghost"))
    args = parser.parse_args()
    print(agent_token(args.data))


if __name__ == "__main__":
    main()
