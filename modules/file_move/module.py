import errno
import os
import re
import shutil
import tempfile
from pathlib import Path

from core.errors import ModuleError
from core.paths import is_device_name, safe_path
from core.registry import ROOT

MODES = ("error", "overwrite", "rename")
_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _bad_part(part: str) -> bool:
    return (bool(_BAD_NAME.search(part)) or part.rstrip(" .") != part
            or part in ("", ".", "..") or is_device_name(part))


def _plan(inputs: dict) -> tuple[Path, Path, bool, bool]:
    """检查一切并算出 (源文件, 目标路径, 是否要新建文件夹, 目标是不是源文件本身)。真正移动和演练共用。"""
    mode = inputs["if_exists"]
    if mode not in MODES:
        raise ModuleError("invalid_option", f"if_exists 只能是 {' / '.join(MODES)}，收到 {mode!r}")
    src = safe_path(inputs["source"])
    if not src.is_absolute():
        src = ROOT / src
    if not src.is_file():
        raise ModuleError("not_found", f"文件不存在：{src}" if not src.exists() else f"这是文件夹，不是文件：{src}")
    src = src.resolve()

    folder_text = inputs["to_folder"].strip().strip('"')
    folder = src.parent
    if folder_text:
        folder = Path(folder_text)
        if not folder.is_absolute():  # 相对路径：相对源文件所在的文件夹，例如 已归档 → 源文件旁边的“已归档”子文件夹
            folder = src.parent / folder
        bad = [p for p in folder.parts[1:] if _bad_part(p)]
        if bad:
            raise ModuleError("bad_name", f"文件夹名不合法：{bad[0]}")

    name = inputs["new_name"].strip()
    if name:
        if _bad_part(name):
            raise ModuleError("bad_name", f"新文件名不合法（不能含有 \\ / : * ? \" < > | 等字符，也不能以点或空格结尾）：{name}")
        if "." not in name:  # 没写扩展名：沿用原来的
            name += src.suffix
    else:
        name = src.name

    for parent in [folder, *folder.parents]:
        if parent.exists():
            if not parent.is_dir():
                raise ModuleError("move_failed", f"{parent} 是文件，不能在它下面放文件")
            break
    make = not folder.is_dir()
    if make and not inputs["make_folder"]:
        raise ModuleError("no_folder", f"目标文件夹不存在：{folder}；要自动创建请把 make_folder 设为 true")

    dst = folder / name
    same = dst.exists() and dst.resolve() == src  # 原地不动，或只改英文大小写
    if dst.exists() and not same:
        if dst.is_dir():
            raise ModuleError("move_failed", f"目标位置已经有一个同名文件夹：{dst}")
        if mode == "error":
            raise ModuleError("exists", f"目标位置已经有同名文件：{dst}；要覆盖请把 if_exists 设为 overwrite，要自动加序号请设为 rename")
        if mode == "rename":
            stem, suffix = Path(name).stem, Path(name).suffix
            for k in range(1, 10000):
                dst = folder / f"{stem} ({k}){suffix}"
                if not dst.exists():
                    break
            else:
                raise ModuleError("move_failed", f"{folder} 里同名文件太多，找不到可用的序号")
    return src, Path(os.path.abspath(dst)), make, same


def _out(dst: Path) -> dict:
    return {"path": str(dst), "name": dst.name}


def run(inputs: dict) -> dict:
    src, dst, make, same = _plan(inputs)
    if same:
        if str(dst) != str(src):  # 只改大小写，例如 a.txt → A.txt
            try:
                os.rename(src, dst)
            except OSError as e:
                raise ModuleError("move_failed", f"改不了名 {src} → {dst}：{e}") from None
        return _out(dst)
    overwrite = inputs["if_exists"] == "overwrite"
    try:
        if make:
            dst.parent.mkdir(parents=True, exist_ok=True)
        if overwrite:
            try:
                os.replace(src, dst)
            except OSError as e:
                if not _cross_device(e):
                    raise
                fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".wf-", suffix=".tmp")
                os.close(fd)
                try:
                    shutil.copy2(src, tmp)
                    os.replace(tmp, dst)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
                os.unlink(src)
        else:
            if dst.exists():  # 检查之后被别人抢先放了同名文件：不覆盖
                raise FileExistsError(dst)
            try:
                os.rename(src, dst)  # Windows 上目标已存在会报错，不会覆盖
            except OSError as e:
                if not _cross_device(e):
                    raise
                with open(src, "rb") as fi, open(dst, "xb") as fo:  # 跨盘：复制（独占创建）后删原文件
                    shutil.copyfileobj(fi, fo)
                shutil.copystat(src, dst)
                os.unlink(src)
    except FileExistsError:
        raise ModuleError("exists", f"目标位置已经有同名文件：{dst}") from None
    except OSError as e:
        raise ModuleError("move_failed", f"移动不了 {src} → {dst}：{e}") from None
    return _out(dst)


def _cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17  # 17 = 不在同一个磁盘上


def dry_run(inputs: dict) -> dict:
    """演练：做全部检查、算出目标位置，但不建文件夹、不移动文件。"""
    dst = _plan(inputs)[1]
    return _out(dst)
