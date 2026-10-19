"""运行一个工作流：python -m tools.run 文件.workflow.json 名字=值 ...

值能按 JSON 解析就按 JSON（如 85、true、["a","b"]），否则当作文本；写成 名字=@文件.json 则从文件读取。
运行记录保存到 runs/（含每个节点的输入、输出、耗时、状态）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

from core.engine import Engine, WorkflowInvalid
from core.registry import ROOT

ICON = {"node_start": "…", "node_end": "✓", "node_skipped": "–", "node_failed": "✗"}


def parse_value(text: str):
    if text.startswith("@"):  # 名字=@文件.json：从文件读 JSON，避开 Windows 命令行吞引号的问题
        return json.loads(Path(text[1:]).read_text(encoding="utf-8"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def ask_confirm(spec, path, target=None) -> bool:
    verb = "读取你电脑上的文件" if spec.data["side_effects"] == "read" else "写文件或产生外部影响"
    where = f"（{target}）" if target else f"（{spec.data['description'][:60]}）"
    ans = input(f"节点 {path} 使用的模块 {spec.id} 会{verb}{where}。允许运行？[y/N] ")
    return ans.strip().lower() == "y"


def show(event: dict):
    if event["type"] == "node_start":
        return
    if event["type"] == "ask":  # 提醒确认：命令行里没人可问，按第一个按钮继续
        pr = event.get("prompt") or {}
        print(f"  ? {event['path']}  提醒：{pr.get('title')}（命令行里没法回答，按“{(pr.get('options') or ['好的'])[0]}”继续）", flush=True)
        return
    if event["type"] == "answered":
        return
    extra = f"{event['seconds']:.1f}s" if "seconds" in event else event.get("reason") or f"{event.get('code')}：{event.get('message')}"
    print(f"  {ICON[event['type']]} {event['path']}  {extra}", flush=True)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow")
    ap.add_argument("inputs", nargs="*", help="名字=值")
    a = ap.parse_args(argv)
    path = Path(a.workflow).resolve()
    wf = json.loads(path.read_text(encoding="utf-8"))
    inputs = {}
    for item in a.inputs:
        name, _, value = item.partition("=")
        inputs[name] = parse_value(value)
    print(f"运行 {wf['name']}（{path.name}）")
    try:
        res = Engine(confirm=ask_confirm, on_event=show).run(wf, inputs, path.parent)
    except WorkflowInvalid as e:
        print("工作流没有通过校验：")
        for err in e.errors:
            print(f"  {err}")
        return 2
    print(f"\n结果：{'完成' if res.status == 'completed' else '失败'}（{res.seconds:.1f}s）")
    if res.error:
        print(f"  出错节点 {res.error['path']}：{res.error['code']}：{res.error['message']}")
    for w in res.warnings:
        print(f"  注意：{w}")
    for k, v in res.outputs.items():
        print(f"  {k} = {json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}")
    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    out = runs / f"{time.strftime('%Y%m%d-%H%M%S')}-{wf['id']}.json"
    out.write_text(json.dumps({"workflow": str(path), "inputs": inputs, **res.to_json()}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"运行记录：{out}")
    return 0 if res.status == "completed" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
