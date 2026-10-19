"""写剪贴板：Windows 下用 ctypes 直接调系统剪贴板接口（不依赖 tkinter 或第三方包）。"""
import ctypes
import sys
import time

from core.errors import ModuleError

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _supported() -> bool:
    return sys.platform == "win32"


def _api():
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.OpenClipboard.argtypes, u.OpenClipboard.restype = [wintypes.HWND], wintypes.BOOL
    u.CloseClipboard.argtypes, u.CloseClipboard.restype = [], wintypes.BOOL
    u.EmptyClipboard.argtypes, u.EmptyClipboard.restype = [], wintypes.BOOL
    u.SetClipboardData.argtypes, u.SetClipboardData.restype = [wintypes.UINT, wintypes.HANDLE], wintypes.HANDLE
    k.GlobalAlloc.argtypes, k.GlobalAlloc.restype = [wintypes.UINT, ctypes.c_size_t], wintypes.HGLOBAL
    k.GlobalFree.argtypes, k.GlobalFree.restype = [wintypes.HGLOBAL], wintypes.HGLOBAL
    k.GlobalLock.argtypes, k.GlobalLock.restype = [wintypes.HGLOBAL], wintypes.LPVOID
    k.GlobalUnlock.argtypes, k.GlobalUnlock.restype = [wintypes.HGLOBAL], wintypes.BOOL
    return u, k


def _encode(text: str) -> bytes:
    """统一成 Windows 换行，编码成以两个 0 字节结尾的 UTF-16。"""
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    try:
        return text.encode("utf-16-le") + b"\0\0"
    except UnicodeEncodeError as e:  # 例如落单的代理字符
        raise ModuleError("write_failed", f"文字里有无法放进剪贴板的字符：{e}") from None


def _set_text(data: bytes):
    u, k = _api()
    h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise ModuleError("write_failed", "内存不够，放不进剪贴板")
    p = k.GlobalLock(h)
    if not p:
        k.GlobalFree(h)
        raise ModuleError("write_failed", "内存不够，放不进剪贴板")
    ctypes.memmove(p, data, len(data))
    k.GlobalUnlock(h)
    for _ in range(20):  # 别的程序正占着剪贴板时稍等再试，最多约 1 秒
        if u.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        k.GlobalFree(h)
        raise ModuleError("clipboard_busy", "剪贴板正被别的程序占用，打不开；请稍后再试")
    try:
        u.EmptyClipboard()
        if not u.SetClipboardData(CF_UNICODETEXT, h):  # 成功后这块内存归系统管，不能再释放
            k.GlobalFree(h)
            raise ModuleError("write_failed", f"放不进剪贴板（系统错误码 {ctypes.get_last_error()}）")
    finally:
        u.CloseClipboard()


def _check(inputs: dict) -> bytes:
    if not _supported():
        raise ModuleError("unsupported_platform", f"写剪贴板目前只支持 Windows，这台电脑是 {sys.platform}")
    return _encode(inputs["text"])


def run(inputs: dict) -> dict:
    _set_text(_check(inputs))
    return {"chars": len(inputs["text"])}


def dry_run(inputs: dict) -> dict:
    """演练：检查系统和文字能不能放进剪贴板，但不改动剪贴板。"""
    _check(inputs)
    return {"chars": len(inputs["text"])}
