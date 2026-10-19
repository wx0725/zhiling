import csv
import io
import os
import re
import tempfile
from pathlib import Path

from core.errors import ModuleError
from core.paths import is_device_name, safe_path
from core.registry import ROOT
from core.text import to_text

OUT_DIR = ROOT / "outputs"
MODES = ("error", "overwrite", "append")
_BAD_NAME = re.compile(r'[<>:"|?*\x00-\x1f]')


def _target(inputs: dict) -> Path:
    mode = inputs["if_exists"]
    if mode not in MODES:
        raise ModuleError("invalid_option", f"if_exists 只能是 {' / '.join(MODES)}，收到 {mode!r}")
    p = safe_path(inputs["path"])
    if not p.is_absolute():
        p = OUT_DIR / p
    if p.suffix.lower() != ".csv":
        raise ModuleError("unsupported_type", f"只能保存为 .csv 文件（Excel 能直接打开），收到 {p.suffix or '没有扩展名'}")
    bad = [part for part in p.parts[1:] if _BAD_NAME.search(part) or is_device_name(part)]
    if bad or p.name.rstrip(" .") != p.name:
        raise ModuleError("write_failed", f"文件名或文件夹名不合法：{bad[0] if bad else p.name}")
    if p.is_dir():
        raise ModuleError("write_failed", f"{p} 是一个文件夹")
    for parent in p.parents:
        if parent.exists():
            if not parent.is_dir():
                raise ModuleError("write_failed", f"{parent} 是文件，不能在它下面保存")
            break
    if p.exists() and mode == "error":
        raise ModuleError("exists", f"{p} 已经存在；要覆盖或追加请把 if_exists 设为 overwrite 或 append")
    return p.resolve()


def _rows(inputs: dict) -> list[dict]:
    rows = inputs["rows"]
    if not isinstance(rows, list):
        raise ModuleError("not_rows", "rows 必须是行列表（每一行是一个对象）")
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict):
            raise ModuleError("not_rows", f"第 {i} 行不是对象（{{表头: 值}} 的形式）：{to_text(r)[:80]}")
    return rows


def _existing_header(p: Path) -> tuple[list[str], str] | None:
    """追加时读已有文件的表头和编码；文件不存在或是空的返回 None。"""
    if not p.is_file() or p.stat().st_size == 0:
        return None
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ModuleError("write_failed", f"{p.name} 既不是 UTF-8 也不是 GBK 编码，不能往里追加")
    first = next(csv.reader(io.StringIO(text, newline="")), [])
    if not any(h.strip() for h in first):
        return None
    return first, ("utf-8" if enc == "utf-8-sig" else "gbk")


def _plan(inputs: dict):
    p = _target(inputs)
    rows = _rows(inputs)
    headers: list[str] = []
    for r in rows:  # 所有行的键的并集，按第一次出现的顺序
        for k in r:
            if k not in headers:
                headers.append(k)
    old = _existing_header(p) if inputs["if_exists"] == "append" else None
    if old:
        missing = [h for h in headers if h not in old[0]]
        if missing:
            raise ModuleError("header_mismatch", f"{p.name} 已有的表头是 {'、'.join(old[0])}，没有这些列：{'、'.join(missing)}；不能追加，请换个文件或改用 overwrite")
        headers = old[0]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    if not old and headers:
        w.writerow(headers)
    for r in rows:
        w.writerow([to_text(r.get(h)) for h in headers])
    text = buf.getvalue()
    enc = old[1] if old else "utf-8"
    try:
        data = text.encode(enc)
    except UnicodeEncodeError as e:
        raise ModuleError("write_failed", f"有无法用 {enc.upper()} 保存的字符：{e}") from None
    if not old and not (p.exists() and inputs["if_exists"] == "append" and p.stat().st_size > 0):
        data = b"\xef\xbb\xbf" + data  # UTF-8 带 BOM：Excel 双击打开中文不乱码
    return p, data, len(rows)


def run(inputs: dict) -> dict:
    p, data, n = _plan(inputs)
    mode = inputs["if_exists"]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "error":
            with p.open("xb") as f:
                f.write(data)
        elif mode == "append":
            with p.open("ab") as f:
                f.write(data)
        else:
            fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".wf-", suffix=".tmp")
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
        raise ModuleError("write_failed", f"写不进 {p}（文件是不是正在 Excel 里打开着？）：{e}") from None
    return {"path": str(p), "count": n}


def dry_run(inputs: dict) -> dict:
    """演练：检查参数、生成要写的内容，但不创建文件夹、不写文件。"""
    p, _data, n = _plan(inputs)
    return {"path": str(p), "count": n}
