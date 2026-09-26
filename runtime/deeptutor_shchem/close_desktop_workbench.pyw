from __future__ import annotations

"""Ask the native workbench window to close without killing Python processes."""

import ctypes
from ctypes import wintypes


USER32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_CLOSE = 0x0010


def request_close(title: str = "沪上化学智研台") -> bool:
    found = False

    @WNDENUMPROC
    def callback(hwnd: int, _lparam: int) -> int:
        nonlocal found
        length = USER32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return 1
        buffer = ctypes.create_unicode_buffer(length + 1)
        USER32.GetWindowTextW(hwnd, buffer, length + 1)
        if buffer.value.strip() == title:
            found = True
            USER32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return 0
        return 1

    USER32.EnumWindows(callback, 0)
    return found


if __name__ == "__main__":
    raise SystemExit(0 if request_close() else 1)
