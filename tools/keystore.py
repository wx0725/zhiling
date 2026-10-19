"""API Key 的安全保存：放进操作系统的凭据管理器，不写进配置文件、日志或导出的工作流。

- Windows：Windows 凭据管理器（advapi32 的 CredWrite / CredRead / CredDelete），条目名 "Weavling/<环境变量名>"，
  只有当前 Windows 账户能读。
- 其他系统：装了 keyring 库就用它（macOS 钥匙串、Linux Secret Service）；没装时 available() 为假，界面提示改用环境变量。
读取时先找凭据管理器里保存的（使用者在界面里填的就是要用的那个），没有再用环境变量（原来用环境变量的人不受影响）。"""
import os
import sys

SERVICE = "Weavling"


class KeystoreError(OSError):
    """没能保存或删除（系统不支持、被拒绝）"""


def _target(name: str) -> str:
    return f"{SERVICE}/{name}"


def available() -> bool:
    if sys.platform == "win32":
        return True
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def get(name: str) -> str:
    """凭据管理器里保存的 name，没有就用同名环境变量；都没有为空字符串"""
    if not name:
        return ""
    try:
        saved = _read(name)
    except Exception:  # noqa: BLE001  读不到就当没保存过
        saved = None
    return saved or os.environ.get(name, "")


def has_saved(name: str) -> bool:
    try:
        return bool(_read(name))
    except Exception:  # noqa: BLE001
        return False


def put(name: str, value: str) -> None:
    value = (value or "").strip()
    if not name or not value:
        raise KeystoreError("API Key 不能为空")
    if sys.platform == "win32":
        _win_write(_target(name), value)
        return
    try:
        import keyring
    except ImportError:
        raise KeystoreError("这台电脑上没有可用的凭据管理器：请改用环境变量 " + name) from None
    try:
        keyring.set_password(SERVICE, name, value)
    except Exception as e:  # noqa: BLE001
        raise KeystoreError(f"没能保存到凭据管理器：{e}") from None


def delete(name: str) -> None:
    if sys.platform == "win32":
        _win_delete(_target(name))
        return
    try:
        import keyring
        keyring.delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001  本来就没有
        pass


def _read(name: str):
    if sys.platform == "win32":
        return _win_read(_target(name))
    try:
        import keyring
    except ImportError:
        return None
    return keyring.get_password(SERVICE, name)


# ---------- Windows 凭据管理器 ----------
def _win():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, adv, CREDENTIAL


CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE, ERROR_NOT_FOUND = 1, 2, 1168


def _win_write(target: str, value: str) -> None:
    ctypes, adv, CREDENTIAL = _win()
    blob = value.encode("utf-8")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=target, CredentialBlobSize=len(blob),
                      CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                      Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=SERVICE)
    if not adv.CredWriteW(ctypes.byref(cred), 0):
        raise KeystoreError(f"没能保存到 Windows 凭据管理器（错误 {ctypes.get_last_error()}）")


def _win_read(target: str):
    ctypes, adv, CREDENTIAL = _win()
    p = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-8", errors="replace")
    finally:
        adv.CredFree(p)


def _win_delete(target: str) -> None:
    ctypes, adv, _ = _win()
    if not adv.CredDeleteW(target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
        raise KeystoreError(f"没能从 Windows 凭据管理器删除（错误 {ctypes.get_last_error()}）")
