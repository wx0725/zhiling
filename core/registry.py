"""读取 modules/*/module.json，按 id 和主版本索引。"""
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES_DIR = ROOT / "modules"


@dataclass(frozen=True)
class ModuleSpec:
    id: str
    major: int
    path: Path
    data: dict

    @property
    def inputs(self) -> dict:
        return self.data["inputs"]

    @property
    def outputs(self) -> dict:
        return self.data["outputs"]


def load_registry(modules_dir: Path = MODULES_DIR) -> dict[str, ModuleSpec]:
    """返回 {"web.fetch@0": ModuleSpec, ...}。"""
    reg = {}
    for f in sorted(modules_dir.glob("*/module.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        major = int(data["version"].split(".")[0])
        key = f"{data['id']}@{major}"
        if key in reg:
            raise ValueError(f"模块重复：{key}（{f} 与 {reg[key].path}）")
        reg[key] = ModuleSpec(data["id"], major, f.parent, data)
    return reg
