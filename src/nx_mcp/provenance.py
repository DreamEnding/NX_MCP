"""Standard-library-only checkout and executable provenance for NX validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def git_state(checkout: Path) -> tuple[str, bool]:
    values = []
    for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain")):
        result = subprocess.run(
            ["git", *arguments],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        values.append(result.stdout.strip())
    return values[0], bool(values[1])


if sys.platform == "win32":

    def file_version(path: Path) -> str:
        """Read an installed executable's fixed version using the Windows version API."""
        import ctypes
        from ctypes import wintypes

        if sys.platform != "win32":
            raise OSError("Windows file version resources are required")
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.UINT),
        ]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size)
        pointer = wintypes.LPVOID()
        length = wintypes.UINT()
        if not version.GetFileVersionInfoW(
            str(path), 0, size, buffer
        ) or not version.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not pointer.value or length.value < 13 * ctypes.sizeof(wintypes.DWORD):
            raise ValueError("Executable has no fixed file version")
        info = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
        if info[0] != 0xFEEF04BD:
            raise ValueError("Executable has an invalid version signature")
        parts = [info[2] >> 16, info[2] & 0xFFFF, info[3] >> 16, info[3] & 0xFFFF]
        while len(parts) > 2 and parts[-1] == 0:
            parts.pop()
        return ".".join(str(part) for part in parts)

else:

    def file_version(path: Path) -> str:
        raise OSError("Windows file version resources are required")
