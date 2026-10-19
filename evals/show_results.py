"""把生成结果打印成便于人工复查的文字：python -m evals.show_results 结果文件 [题号 ...]"""
import json
import sys


def show(g: dict, ind: str = "  "):
    for n in g.get("nodes", []):
        cfg = json.dumps(n.get("config", {}), ensure_ascii=False) if n.get("config") else ""
        print(f"{ind}[{n.get('kind')}] {n.get('id')} {n.get('module', '')} {cfg}")
        if isinstance(n.get("body"), dict):
            show(n["body"], ind + "    ")
    for e in g.get("edges", []):
        print(f"{ind}  {e.get('from')} -> {e.get('to')}")


def main(path: str, ids: list[str]):
    data = json.load(open(path, encoding="utf-8"))
    for c in data["cases"]:
        if ids and c["id"] not in ids:
            continue
        wf = c.get("workflow") or {}
        ports = lambda d: ", ".join(f"{k}:{v.get('type')}" for k, v in (d or {}).items())
        print(f"===== {c['id']}  {'✓' if c['passed'] else '✗'} {c['note']}  | 输入 {ports(wf.get('inputs'))} | 输出 {ports(wf.get('outputs'))}")
        if not wf.get("nodes"):
            print(f"  {wf.get('name')}")
        show(wf)
        if not c["valid"]:
            for err in c["log"][-1].get("errors", [])[:4]:
                print(f"  ! {err}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
