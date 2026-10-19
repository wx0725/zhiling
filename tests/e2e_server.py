"""端到端测试用的隔离服务：工作流放在临时目录，生成用确定性替身，可人为让某个文件读取变慢。
用法：python -B -m tests.e2e_server <端口>。只给 web/tests/e2e.mjs 使用，不会碰真实的 workflows 目录。"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import uvicorn

import server.app as appmod


def wf(wid, name, inputs, edges=None):
    return {"spec": "0.2", "id": wid, "name": name, "intent": "", "inputs": inputs,
            "outputs": {"y": {"type": "integer"}}, "nodes": [],
            "edges": edges if edges is not None else [{"from": "$in.n", "to": "$out.y"}]}


FIXTURES = {
    "form42": wf("form42", "表单保持", {"n": {"type": "integer", "default": 42}}),
    "slow_a": wf("slow_a", "慢的A", {"n": {"type": "integer", "default": 7}}),
    "fast_b": wf("fast_b", "快的B", {"n": {"type": "integer", "default": 42}}),
    "badroot": wf("badroot", "输入声明损坏", {"n": None}, edges=[]),
    "edit_me": {**wf("edit_me", "拿来改的", {"x": {"type": "string", "default": "世界"}},
                     edges=[{"from": "$in.x", "to": "$out.y"}]), "outputs": {"y": {"type": "string"}}},
    "badmerge": {**wf("badmerge", "合并端口写错", {"n": {"type": "integer", "default": 1}}, edges=[
        {"from": "$in.n", "to": "m.a"}, {"from": "m.value", "to": "$out.y"}]), "nodes": [{"id": "m", "kind": "merge", "ports": 1}]},
    # 真实模块：循环里比较、分支、各自写一句话、合并，最后连成一段文字
    "grade": {
        "spec": "0.2", "id": "grade", "name": "逐个打分", "intent": "",
        "inputs": {"scores": {"type": "list<integer>", "default": [90, 30]}},
        "outputs": {"text": {"type": "string"}},
        "nodes": [
            {"id": "each", "kind": "foreach", "max_items": 20, "body": {
                "inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "string"}},
                "nodes": [
                    {"id": "cmp", "kind": "module", "module": "logic.compare@0", "config": {"op": "ge", "b": 60}},
                    {"id": "br", "kind": "if"},
                    {"id": "pass", "kind": "module", "module": "text.format@0", "config": {"template": "及格 {value}"}},
                    {"id": "fail", "kind": "module", "module": "text.format@0", "config": {"template": "不及格 {value}"}},
                    {"id": "m", "kind": "merge"},
                ],
                "edges": [
                    {"from": "$in.item", "to": "cmp.a"}, {"from": "cmp.result", "to": "br.condition"},
                    {"from": "$in.item", "to": "br.value"},
                    {"from": "br.then", "to": "pass.value"}, {"from": "br.else", "to": "fail.value"},
                    {"from": "pass.text", "to": "m.a"}, {"from": "fail.text", "to": "m.b"},
                    {"from": "m.value", "to": "$out.result"},
                ]}},
            {"id": "join", "kind": "module", "module": "text.join@0", "config": {"separator": "；"}},
        ],
        "edges": [{"from": "$in.scores", "to": "each.items"}, {"from": "each.results", "to": "join.items"},
                  {"from": "join.text", "to": "$out.text"}],
    },
}
IDENTITY = wf("identity", "直通", {"x": {"type": "integer"}}, edges=[{"from": "$in.x", "to": "$out.y"}])


FMT = {**wf("fmt_demo", "数字写成一句话", {"x": {"type": "integer"}}, edges=[
    {"from": "$in.x", "to": "fmt.value"}, {"from": "fmt.text", "to": "$out.y"}]),
    "outputs": {"y": {"type": "string"}},
    "nodes": [{"id": "fmt", "kind": "module", "module": "text.format@0", "config": {"template": "数是 {value}"}}]}


def fake_generate(requirement, model, rounds, post_check, on_event, should_stop=None):
    if "检查不过" in requirement:  # 第 1 轮实跑检查指出 fmt 的问题，第 2 轮结构校验没过
        on_event({"type": "drafting", "round": 1})
        on_event({"type": "validated", "round": 1, "errors": [], "seconds": 1.2, "workflow": FMT})
        on_event({"type": "checking", "round": 1})
        on_event({"type": "checked", "round": 1, "status": "failed", "seconds": 3.4, "samples": [{"x": 7}],
                  "problems": ["节点 fmt：句子里没有写单位；改法：模板改成“数是 {value} 个”"]})
        on_event({"type": "drafting", "round": 2})
        on_event({"type": "validated", "round": 2, "errors": ["连线 1（fmt.text → $out.z）：工作流没有输出 z"], "seconds": 1.0,
                  "workflow": {**FMT, "edges": []}})
        return {"ok": False, "rounds": 2, "workflow": {**FMT, "edges": []}, "log": [{"round": 2, "errors": ["x"]}]}
    on_event({"type": "drafting", "round": 1})
    on_event({"type": "validated", "round": 1, "errors": [], "seconds": 0.1, "workflow": IDENTITY})
    return {"ok": True, "rounds": 1, "workflow": IDENTITY, "log": [{"round": 1, "errors": []}]}


def fake_refine(idea, answers=None, card=None, model=None, url=None):
    """第一轮问一个问题；回答后不再问，需求描述里带上回答。"""
    if not answers:
        return {"card": {"given": ["一个整数"], "wanted": ["一句话"], "steps": ["把整数写进句子"], "cases": []},
                "questions": [{"ask": "句子里要不要带单位？", "options": ["要带“个”", "不用"]}],
                "unsupported": ["发邮件通知"], "requirement": "给我一个整数，写成一句话。"}
    ans = answers[-1]["answer"]
    return {"card": {"given": ["一个整数"], "wanted": [f"一句话（{ans}）"], "steps": ["把整数写进句子"], "cases": []},
            "questions": [], "unsupported": [], "requirement": f"给我一个整数，写成一句话，{ans}。"}


def main(port: int):
    tmp = Path(tempfile.mkdtemp(prefix="wf-e2e-"))
    # 写文件的真实模块：写到临时目录里，运行前网页要先确认
    save = {**wf("save_note", "保存笔记", {"x": {"type": "string", "default": "第一行"}}, edges=[
        {"from": "$in.x", "to": "save.text"}, {"from": "save.path", "to": "$out.y"}]),
        "outputs": {"y": {"type": "string"}},
        "nodes": [{"id": "save", "kind": "module", "module": "file.write@0",
                   "config": {"path": str(tmp / "写出" / "笔记.txt")}}]}
    # 写模块藏在引用的子流程里：网页事先看不到，要等服务端拦下后再确认
    inner = {**save, "id": "save_inner", "name": "子流程里保存",
             "nodes": [{**save["nodes"][0], "config": {"path": str(tmp / "写出" / "子流程.txt"), "if_exists": "overwrite"}}]}
    outer = {**wf("save_ref", "引用子流程保存", {"x": {"type": "string", "default": "来自子流程"}}, edges=[
        {"from": "$in.x", "to": "sub.x"}, {"from": "sub.y", "to": "$out.y"}]),
        "outputs": {"y": {"type": "string"}},
        "nodes": [{"id": "sub", "kind": "subflow", "ref": "save_inner.workflow.json"}]}
    drag = {**FIXTURES["grade"], "id": "grade_drag", "name": "拖来拖去"}  # 拖动碰撞测试专用，不影响别的测试
    box = {**FIXTURES["grade"], "id": "grade_box", "name": "拉伸框"}  # 拉伸循环框测试专用
    fmt_body = lambda: {"inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "string"}},  # noqa: E731
                        "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "第 {value} 项"}}],
                        "edges": [{"from": "$in.item", "to": "f.value"}, {"from": "f.text", "to": "$out.result"}]}
    nested = {"spec": "0.2", "id": "nested_box", "name": "框中框", "intent": "",
              "inputs": {"rows": {"type": "list<list<integer>>", "default": [[1, 2]]}},
              "outputs": {"y": {"type": "list<list<string>>"}},
              "nodes": [{"id": "outer", "kind": "foreach", "max_items": 5, "body": {
                  "inputs": {"item": {"type": "list<integer>"}}, "outputs": {"result": {"type": "list<string>"}},
                  "nodes": [{"id": "inner", "kind": "foreach", "max_items": 5, "body": fmt_body()}],
                  "edges": [{"from": "$in.item", "to": "inner.items"}, {"from": "inner.results", "to": "$out.result"}]}}],
              "edges": [{"from": "$in.rows", "to": "outer.items"}, {"from": "outer.results", "to": "$out.y"}]}
    for name, doc in {**FIXTURES, "save_note": save, "save_inner": inner, "save_ref": outer, "grade_drag": drag,
                      "grade_box": box, "nested_box": nested}.items():
        (tmp / f"{name}.workflow.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    @appmod.app.middleware("http")
    async def slow(request, call_next):  # 让“慢的A”晚 3 秒返回，制造响应乱序
        if request.url.path.endswith("/slow_a.workflow.json"):
            await asyncio.sleep(3)
        return await call_next(request)

    cfg = {"generator": "替身", "generator_url": "", "judge": "替身", "judge_url": ""}
    try:
        with mock.patch.object(appmod, "SAVED", tmp), \
             mock.patch.object(appmod, "PROJECTS", tmp / "projects"), \
             mock.patch.dict(appmod.BASE_DIRS, {"saved": tmp, "generated": tmp, "draft": tmp}), \
             mock.patch.object(appmod, "model_config", return_value=cfg), \
             mock.patch.object(appmod.gen, "generate", fake_generate),              mock.patch.object(appmod.refine_tool, "refine", fake_refine):
            uvicorn.run(appmod.app, host="127.0.0.1", port=port, log_level="warning")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main(int(sys.argv[1]))
