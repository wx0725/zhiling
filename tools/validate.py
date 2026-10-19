"""命令行校验：python -m tools.validate [工作流文件 ...]

不带参数时校验全部模块描述和 examples/ 下的全部工作流。
"""
import json
import sys
from pathlib import Path

from core.registry import ROOT, load_registry
from core.validate import validate_module, validate_workflow


def main(argv: list[str]) -> int:
    reg = load_registry()
    failed = 0
    for spec in reg.values():
        errs = validate_module(spec)
        failed += bool(errs)
        print(f"{'✗' if errs else '✓'} 模块 {spec.id}@{spec.major}")
        for e in errs:
            print(f"    {e}")
    files = [Path(a) for a in argv] or sorted((ROOT / "examples").glob("*.workflow.json"))
    for f in files:
        wf = json.loads(f.read_text(encoding="utf-8"))
        errs = validate_workflow(wf, reg, f.resolve().parent)
        failed += bool(errs)
        print(f"{'✗' if errs else '✓'} 工作流 {f.name}")
        for e in errs:
            print(f"    {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
