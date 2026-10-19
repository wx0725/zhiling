import os
import re
from pathlib import Path

from core.errors import ModuleError
from core.paths import safe_path
from core.registry import ROOT


def _exts(text: str) -> set[str]:
    out = set()
    for part in re.split(r"[,，;；、\s]+", text.strip()):
        part = part.strip().lstrip("*").lower()
        if part:
            out.add(part if part.startswith(".") else "." + part)
    return out


def run(inputs: dict) -> dict:
    p = safe_path(inputs["folder"])
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        raise ModuleError("not_found", f"文件夹不存在：{p}")
    if not p.is_dir():
        raise ModuleError("not_folder", f"这是一个文件，不是文件夹：{p}")
    exts = _exts(inputs["extensions"])
    words = [w.casefold() for w in re.split(r"\s+", inputs["keyword"].strip()) if w]
    limit = max(inputs["max_items"], 0)

    def wanted(name: str) -> bool:
        if exts and os.path.splitext(name)[1].lower() not in exts:
            return False
        return all(w in name.casefold() for w in words)

    found = []
    if inputs["recursive"]:
        for d, subdirs, files in os.walk(p, onerror=lambda e: None):
            subdirs.sort()
            found += [Path(d) / f for f in files if wanted(f)]
    else:
        try:
            with os.scandir(p) as it:
                found = [Path(e.path) for e in it if e.is_file() and wanted(e.name)]
        except OSError as e:
            raise ModuleError("not_found", f"打不开文件夹 {p}：{e}") from None
    found.sort(key=lambda f: str(f).casefold())
    found = found[:limit]
    return {"paths": [str(f) for f in found], "names": [f.name for f in found], "count": len(found)}
