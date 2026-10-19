"""跑生成测试题：python -m evals.run_generation [--model 名称] [--format schema|json] [--rounds 3] [--spec]

--spec：先把题目当作使用者的第一句话理清成标准需求单（tools/spec.py），不回答任何追问（缺项按常规处理），再按需求单生成。
每道题记下模型调用次数（统一协议 tools/protocol.py 统计）。

除了校验器，还按每道题的 expect 检查：用到了哪些模块、哪些流程控制；不可实现的需求是否如实拒绝。
结果写入 evals/results/。
"""
import argparse
import functools
import json
import os
import re
import threading
import time
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# 评测用本机 fixtures 网页（127.0.0.1:8799）：放行本机地址，否则 web.fetch 的 SSRF 防护会拒绝
os.environ.setdefault("WORKFLOW_ALLOW_LOCAL_FETCH", "1")

from tools import protocol
from tools.generate import DEFAULT_MODEL, generate, resolve
from tools.refine import refine
from tools.semantic import make_checker

HERE = Path(__file__).parent
SITE_PORT = 8799  # 题目里的本地示例网页（evals/fixtures/site），不依赖外网，内容固定


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve_fixtures(port: int = SITE_PORT) -> ThreadingHTTPServer:
    handler = functools.partial(_Quiet, directory=str(HERE / "fixtures" / "site"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def collect(graph: dict, counts: Counter, kinds: set):
    for n in graph.get("nodes", []):
        kinds.add(n.get("kind"))
        if n.get("kind") == "module":
            counts[n.get("module")] += 1
        if isinstance(n.get("body"), dict):
            collect(n["body"], counts, kinds)


def judge(case: dict, res: dict) -> tuple[bool, str]:
    wf, exp = res.get("workflow") or {}, case["expect"]
    counts, kinds = Counter(), set()
    collect(wf, counts, kinds)
    if exp.get("impossible"):
        refused = res["ok"] and not wf.get("nodes") and "无法实现" in wf.get("name", "")
        return refused, "如实拒绝" if refused else f"没有拒绝（模块 {sorted(counts)}）"
    if res["ok"] and not wf.get("nodes") and "无法实现" in wf.get("name", ""):
        return False, f"误拒：{wf['name']}"
    if not res["ok"]:
        last = res["log"][-1] if res["log"] else {}
        if not last.get("errors") and last.get("check"):
            return False, "校验通过但运行检查未过"
        return False, "未通过校验"
    problems = [f"缺模块 {m}" for m in exp.get("modules", []) if not counts[m]]
    problems += [f"缺 {k}" for k in exp.get("kinds", []) if k not in kinds]
    problems += [f"{m} 应至少 {n} 个，实际 {counts[m]}" for m, n in exp.get("module_counts", {}).items() if counts[m] < n]
    out_types = [p.get("type") for p in wf.get("outputs", {}).values()]
    problems += [f"输出缺少 {t} 类型（实际 {out_types}）" for t in exp.get("output_types", []) if t not in out_types]
    if len(wf.get("inputs", {})) < exp.get("min_inputs", 0):
        problems.append(f"输入应至少 {exp['min_inputs']} 个，实际 {len(wf.get('inputs', {}))}")
    return not problems, "符合" if not problems else "；".join(problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--format", choices=["schema", "flat", "json"], default="json")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--no-feasibility", action="store_true", help="跳过生成前的可行性判断")
    ap.add_argument("--check", action="store_true", help="校验通过后用示例输入实际运行，再由审查模型检查")
    ap.add_argument("--judge-model", default=None, help="审查用的模型，默认与生成相同")
    ap.add_argument("--judge-url", default=None, help="审查模型所在的 Ollama 地址")
    ap.add_argument("--only", nargs="*", help="只跑这些题号")
    ap.add_argument("--spec", action="store_true", help="先理清成标准需求单（不回答追问），再按需求单生成")
    a = ap.parse_args()
    a.model = resolve(a.model)
    a.judge_model = a.judge_model or a.model
    cases = json.loads((HERE / "generation_cases.json").read_text(encoding="utf-8"))
    if a.only:
        cases = [c for c in cases if c["id"] in a.only]
    site = serve_fixtures() if a.check else None  # 运行检查要真的抓取题目里的本地网址
    out, passed = [], 0
    for c in cases:
        t = time.time()
        protocol.reset_stats()
        requirement, spec, asked = c["requirement"], None, 0
        if a.spec:
            r = refine(c["requirement"], model=a.model)
            requirement, spec, asked = r["requirement"], r["spec"], len(r["questions"])
        checker = make_checker(requirement, a.judge_model, a.judge_url, c.get("sample_hint", "")) if a.check else None
        res = generate(requirement, a.model, a.rounds, a.format, feasibility=not a.no_feasibility, post_check=checker,
                       spec=spec)
        used = protocol.stats()
        calls = used.get("calls", 0)
        ok, note = judge(c, res)
        passed += ok
        checks = [l["check"] for l in res["log"] if l.get("check")]
        flagged = sum(1 for ch in checks if ch.get("verdict", {}).get("ok") is False or any(r["status"] == "failed" for r in ch.get("runs", [])))
        row = {"id": c["id"], "valid": res["ok"], "rounds": res["rounds"], "passed": ok, "note": note,
               "checks": len(checks), "checks_flagged": flagged, "calls": calls, "call_tasks": {k[5:]: v for k, v in used.items() if k.startswith("task:")}, "questions": asked,
               "requirement": requirement, "spec": spec,
               "seconds": round(time.time() - t, 1), "log": res["log"], "workflow": res["workflow"]}
        out.append(row)
        extra = f"  运行检查 {len(checks)} 次，发现问题 {flagged} 次" if a.check and checks else ""
        print(f"{c['id']}  {'✓' if ok else '✗'}  校验{'通过' if res['ok'] else '未过'}  {res['rounds']} 轮  {row['seconds']}s  "
              f"调用 {calls} 次  {note}{extra}", flush=True)
    for g in dict.fromkeys(c.get("group", "") for c in cases):
        rows = [r for r, c in zip(out, cases) if c.get("group", "") == g]
        print(f"{g or '全部'}：{sum(r['passed'] for r in rows)}/{len(rows)}")
    print(f"合计 {passed}/{len(cases)}  模型调用 {sum(r['calls'] for r in out)} 次  用时 {round(sum(r['seconds'] for r in out))}s")
    d = HERE / "results"
    d.mkdir(exist_ok=True)
    safe = lambda m: re.sub(r"[^\w.-]+", "_", m).strip("_")  # noqa: E731  云端模型名带 :// 和 /
    tag = (f"-check-{safe(a.judge_model)}" if a.check else "") + ("-spec" if a.spec else "")
    f = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe(a.model)}-{a.format}{tag}.json"
    f.write_text(json.dumps({"model": a.model, "format": a.format, "rounds": a.rounds,
                             "feasibility": not a.no_feasibility, "check": a.check, "spec": a.spec,
                             "judge_model": a.judge_model if a.check else None, "passed": passed,
                             "total": len(cases), "cases": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"结果：{f}")
    if site:
        site.shutdown()


if __name__ == "__main__":
    main()
