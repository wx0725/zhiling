"""路径相关的共用安全检查（模块、引擎、校验器、网页服务共用，口径一致）。

- safe_path：把使用者/模型给的路径字符串规范成 Path，拒绝含 NUL 的路径，以及把 Windows 保留设备名
  （CON、NUL、COM1… 等）用作某一段的路径。个人自动化工具读写自己电脑上的文件是本职，这里不做沙箱，
  只拦下这类“几乎总是出错或注入信号、不对应真实文件”的名字。
- ref_within：子流程 ref 解析后是否落在允许的目录内（项目根 ROOT 或当前 base 目录之内），
  引擎、校验器、/api/ports 都用它，避免 ref 写成 ../../.. 指向任意路径。
"""
from pathlib import Path

from core.errors import ModuleError
from core.registry import ROOT

# Windows 保留设备名：不分大小写，带不带扩展名都保留（CON.txt 一样被当成设备）。
# 含上标写法 COM¹/COM²/COM³ 等（Windows 也当 COM1~3），以及控制台设备 CONIN$/CONOUT$。
_WIN_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
                "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}


def is_device_name(part: str) -> bool:
    """这一段文件名是否是 Windows 保留的设备名（CON、NUL、COM1…，带不带扩展名都算）。"""
    stem = part.split(".", 1)[0].strip().rstrip(" .").upper()
    return stem in _WIN_DEVICES


def safe_path(raw: str) -> Path:
    """规范路径字符串并做安全检查，返回 Path（不解析绝对/相对，由调用方接 ROOT / OUT_DIR）。
    含 NUL 或以 Windows 保留设备名作为某一段时抛 ModuleError("bad_path", ...)。"""
    text = raw.strip().strip('"').strip()
    if "\x00" in text:
        raise ModuleError("bad_path", "路径里不能含有空字符（NUL）")
    p = Path(text)
    anchor = p.anchor
    for part in p.parts:
        if part == anchor or part in ("/", "\\"):
            continue
        if is_device_name(part):
            raise ModuleError("bad_path", f"“{part}”是 Windows 保留的设备名，不能用作文件或文件夹名")
    return p


def ref_within(path: Path, base_dir: Path) -> bool:
    """子流程 ref 解析后的 path 是否落在 ROOT 或 base_dir 之内（path 已 resolve 或由本函数 resolve）。"""
    p = path.resolve()
    for base in (ROOT, base_dir):
        try:
            b = Path(base).resolve()
        except OSError:
            continue
        if b in p.parents:
            return True
    return False
