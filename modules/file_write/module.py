import os
import re
import tempfile
from pathlib import Path

from core.errors import ModuleError
from core.registry import ROOT

OUT_DIR = ROOT / "outputs"
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".log", ".html"}
MODES = ("error", "overwrite", "append")
_BAD_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')  # Windows 文件名里不允许的字符（盘符的冒号单独处理）


def _target(inputs: dict) -> Path:
    """检查参数并算出保存位置；真正写入和演练共用，演练也能发现“已存在”“路径不合法”等问题。"""
    mode = inputs["if_exists"]
    if mode not in MODES:
        raise ModuleError("invalid_option", f"if_exists 只能是 {' / '.join(MODES)}，收到 {mode!r}")
    p = Path(inputs["path"].strip().strip('"'))
    if not p.is_absolute():
        p = OUT_DIR / p
    if p.suffix.lower() not in TEXT_TYPES:
        raise ModuleError("unsupported_type", f"只能保存为 {' '.join(sorted(TEXT_TYPES))} 文件，收到 {p.suffix or '没有扩展名'}")
    bad = [part for part in p.parts[1:] if _BAD_NAME.search(part)]  # parts[0] 是盘符或根
    if bad or p.name.rstrip(" .") != p.name:
        raise ModuleError("write_failed", f"文件名或文件夹名不合法：{bad[0] if bad else p.name}")
    if p.is_dir():
        raise ModuleError("write_failed", f"{p} 是一个文件夹")
    for parent in p.parents:  # 已存在的上级路径必须是文件夹
        if parent.exists():
            if not parent.is_dir():
                raise ModuleError("write_failed", f"{parent} 是文件，不能在它下面保存")
            break
    if p.exists() and mode == "error":
        raise ModuleError("exists", f"{p} 已经存在；要覆盖或追加请把 if_exists 设为 overwrite 或 append")
    return p.resolve()


def _encode(text: str) -> bytes:
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as e:  # 例如落单的代理字符
        raise ModuleError("write_failed", f"文字里有无法保存的字符：{e}") from None


def run(inputs: dict) -> dict:
    p = _target(inputs)
    data = _encode(inputs["text"])  # 先编码：失败时还没有碰任何文件
    mode = inputs["if_exists"]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "error":
            with p.open("xb") as f:  # 独占创建：检查之后被别人抢先建好，也不会覆盖
                f.write(data)
        elif mode == "append":
            with p.open("ab") as f:
                f.write(data)
        else:  # 覆盖：先写临时文件，成功后再替换，失败时原文件不变
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".wf-", suffix=".tmp")  # 短名字：目标文件名很长时也不超长
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, p)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
    except FileExistsError:
        raise ModuleError("exists", f"{p} 已经存在；要覆盖或追加请把 if_exists 设为 overwrite 或 append") from None
    except OSError as e:
        raise ModuleError("write_failed", f"写不进 {p}：{e}") from None
    return {"path": str(p), "chars": len(inputs["text"])}


def dry_run(inputs: dict) -> dict:
    """演练：检查参数、算出位置，但不创建文件夹、不写文件。生成时的运行检查用它代替 run。"""
    p = _target(inputs)
    _encode(inputs["text"])
    return {"path": str(p), "chars": len(inputs["text"])}
