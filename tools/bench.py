"""规模基准（不参与测试是否通过，只看增长趋势）：python -m tools.bench
每项在 1,000 / 2,000 / 4,000 规模下各测 3 次取中位数。规模翻倍、耗时也大约翻倍是线性；翻四倍说明是平方级。
前端运行状态汇总的基准见 web/tests/efficiency_fixes.test.mjs。"""
import json
import statistics
import tempfile
import time
from pathlib import Path

from core.engine import Engine
from core.registry import load_registry
from core.validate import validate_workflow

REG = load_registry()
SIZES = (1000, 2000, 4000)


def merge_chain(n):
    nodes = [{"id": f"m{i}", "kind": "merge"} for i in range(n)]
    edges = [{"from": "$in.x", "to": "m0.a"}] + [{"from": f"m{i - 1}.value", "to": f"m{i}.a"} for i in range(1, n)]
    edges.append({"from": f"m{n - 1}.value", "to": "$out.y"})
    return {"spec": "0.2", "id": "chain", "name": "长链", "intent": "", "inputs": {"x": {"type": "integer"}},
            "outputs": {"y": {"type": "integer"}}, "nodes": nodes, "edges": edges}


def loop_over_ref(n):
    body = {"inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "integer"}},
            "nodes": [{"id": "s", "kind": "subflow", "ref": "child.workflow.json"}],
            "edges": [{"from": "$in.item", "to": "s.x"}, {"from": "s.y", "to": "$out.result"}]}
    return {"spec": "0.2", "id": "loop", "name": "循环", "intent": "", "inputs": {"xs": {"type": "list<integer>"}},
            "outputs": {"ys": {"type": "list<integer>"}},
            "nodes": [{"id": "each", "kind": "foreach", "max_items": n, "body": body}],
            "edges": [{"from": "$in.xs", "to": "each.items"}, {"from": "each.results", "to": "$out.ys"}]}


def timed(fn):
    runs = []
    for _ in range(3):
        t = time.perf_counter()
        fn()
        runs.append((time.perf_counter() - t) * 1000)
    return statistics.median(runs)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wf-bench-"))
    (tmp / "child.workflow.json").write_text(json.dumps({
        "spec": "0.2", "id": "child", "name": "直通", "intent": "", "inputs": {"x": {"type": "integer"}},
        "outputs": {"y": {"type": "integer"}}, "nodes": [], "edges": [{"from": "$in.x", "to": "$out.y"}]}), encoding="utf-8")
    cases = {
        "校验长链": lambda n: validate_workflow(merge_chain(n), REG),
        "运行长链": lambda n: Engine(REG).run(merge_chain(n), {"x": 1}),
        "循环引用子流程": lambda n: Engine(REG).run(loop_over_ref(n), {"xs": list(range(n))}, tmp),
    }
    print(f"{'项目':<10}" + "".join(f"{n:>12,}" for n in SIZES) + "   增长（最大/最小规模）")
    for name, fn in cases.items():
        ms = [timed(lambda n=n: fn(n)) for n in SIZES]
        ratio = ms[-1] / ms[0] if ms[0] else float("nan")
        print(f"{name:<10}" + "".join(f"{m:>10.1f}ms" for m in ms) + f"   ×{ratio:.1f}（线性约 ×4，平方约 ×16）")


if __name__ == "__main__":
    main()
