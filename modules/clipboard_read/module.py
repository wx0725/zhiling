"""读剪贴板：Windows 下用 ctypes 直接调系统剪贴板接口（不依赖 tkinter 或第三方包）。"""
import ctypes
import sys
import time

from core.errors import ModuleError

CF_UNICODETEXT = 13


def _supported() -> bool:
    return sys.platform == "win32"


def _api():
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    u.CloseClipboard.argtypes, u.CloseClipboard.restype = [], wintypes.BOOL
    u.IsClipboardFormatAvailable.argtypes, u.IsClipboardFormatAvailable.restype = [wintypes.UINT], wintypes.BOOL
    u.GetClipboardData.argtypes, u.GetClipboardData.restype = [wintypes.UINT], wintypes.HANDLE
    k.GlobalLock.argtypes, k.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
    k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL
    k.GlobalSize.argtypes, k.GlobalSize.restype = [wintypes.HGLOBAL], ctypes.c_size_t
    return u, k


def _open(u):
    for _ in range(20):  # 别的程序正占着剪贴板时稍等再试，最多约 1 秒
        if u.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise ModuleError("clipboard_busy", "剪贴板正被别的程序占用，打不开；请稍后再试")


def _get_text() -> str:
    u, k = _api()
    _open(u)
    try:
        if not u.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""  # 剪贴板里没有文字（空的，或者是图片、文件）
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = k.GlobalLock(h)
        if not p:
            return ""
        try:
            text = ctypes.wstring_at(p, k.GlobalSize(h) // 2)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()
    return text.split("\0", 1)[0]


def run(inputs: dict) -> dict:
    if not _supported():
        raise ModuleError("unsupported_platform", f"读剪贴板目前只支持 Windows，这台电脑是 {sys.platform}")
    text = _get_text().replace("\r\n", "\n")[:max(inputs["max_chars"], 0)]
    return {"text": text, "chars": len(text)}
