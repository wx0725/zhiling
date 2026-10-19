"""流程小规则、运行表单、运行状态、初始视野、类型名称（移植自 web/tests 里 contracts、review_fixes、recheck_fixes、
audit3_fixes、efficiency_fixes、genlog、ui 的逻辑部分）"""
import math
import time
import unittest

from native.logic.flow import (canvas_ids, card_to_requirement, drawable, guide_steps, inspect_workflow, is_refusal,
                               issue_targets, issue_text, latest, merge_answers)
from native.logic.form import collect_inputs, initial_values, parse_value
from native.logic.graph import path_to_id, to_flow
from native.logic.labels import type_label
from native.logic.run_status import RunStatus
from native.logic.viewport import READABLE_ZOOM, free_spot, initial_viewport, whole_graph_visible

# 与 tests/test_contracts.py 的 DECL 相同
DECL = {
    "r": {"type": "integer"},
    "i": {"type": "integer", "default": 5},
    "b": {"type": "boolean", "required": False},
    "s": {"type": "string", "default": "默认"},
    "a": {"type": "any", "required": False},
}


class FormTest(unittest.TestCase):
    def test_contract_defaults(self):
        # 不改表单直接运行：必填给示例，有默认值的发默认值，可选的不发
        res = collect_inputs(DECL, initial_values(DECL))
        self.assertEqual(res["errors"], {})
        self.assertEqual(res["values"], {"r": 85, "i": 5, "s": "默认"})
        self.assertIs(type(res["values"]["r"]), int)

    def test_contract_falsy_values_sent(self):
        raw = {**initial_values(DECL), "r": "0", "i": "0", "b": False, "a": "null"}
        self.assertEqual(collect_inputs(DECL, raw)["values"], {"r": 0, "i": 0, "b": False, "s": "默认", "a": None})
        self.assertEqual(collect_inputs(DECL, {**raw, "a": "0"})["values"]["a"], 0)
        self.assertEqual(collect_inputs(DECL, {**raw, "a": '""'})["values"]["a"], "")

    def test_contract_empty_optional(self):
        raw = {**initial_values(DECL), "s": "", "a": ""}
        values = collect_inputs(DECL, raw)["values"]
        self.assertNotIn("s", values)
        self.assertNotIn("a", values)
        self.assertTrue(collect_inputs(DECL, {**raw, "r": ""})["errors"]["r"])
        self.assertTrue(collect_inputs(DECL, {**raw, "r": "1.5"})["errors"]["r"])

    def test_f07_defaults(self):
        inputs = {
            "score": {"type": "integer", "required": False, "default": 60},
            "flag": {"type": "boolean", "required": False},
            "note": {"type": "string", "required": False},
            "url": {"type": "string"},
        }
        init = initial_values(inputs)
        self.assertEqual(init["score"], "60")
        self.assertIsNone(init["flag"])
        res = collect_inputs(inputs, init)
        self.assertEqual(res["errors"], {})
        self.assertEqual(res["values"], {"score": 60, "url": "https://example.com"})

    def test_r03_any_default_round_trip(self):
        for d in ("hello", "123", "true", "null", 'say "hi"', 123, True, None, [1, "a"], {"k": "v"}):
            inputs = {"v": {"type": "any", "required": False, "default": d}}
            res = collect_inputs(inputs, initial_values(inputs))
            self.assertEqual(res["errors"], {}, repr(d))
            if d is None:
                self.assertNotIn("v", res["values"])  # 默认值为 null：不发送，由后端补
            else:
                self.assertEqual(res["values"], {"v": d}, repr(d))
                self.assertIs(type(res["values"]["v"]), type(d))
        s = {"t": {"type": "string", "default": "123"}}
        self.assertEqual(collect_inputs(s, initial_values(s))["values"], {"t": "123"})

    def test_parse_value_js_rules(self):
        self.assertEqual(parse_value("number", " 1e3 "), 1000)
        self.assertIs(type(parse_value("number", "85")), int)  # JS 不分整数小数：85 发出去还是 85
        self.assertEqual(parse_value("number", "0.5"), 0.5)
        self.assertEqual(parse_value("number", "0x10"), 16)
        for bad in ("", "abc", "1_000", "nan", "12px"):
            with self.assertRaises(ValueError, msg=bad):
                parse_value("number", bad)
        with self.assertRaises(ValueError):
            parse_value("any", "NaN")  # JSON 里没有 NaN
        with self.assertRaisesRegex(ValueError, "JSON 列表"):
            parse_value("list<string>", "[1,")
        self.assertEqual(parse_value("integer", " -12 "), -12)
        self.assertEqual(parse_value("boolean", ""), False)
        self.assertEqual(initial_values({"u": {"type": "list<string>"}, "x": {"type": "object"}}), {"u": "[]", "x": ""})
        self.assertEqual(initial_values({"urls": {"type": "list<string>"}})["urls"], '["https://example.com", "https://example.com/no-such-page"]')
        self.assertEqual(initial_values({"f": {"type": "number", "default": 2.0}})["f"], "2")


class FlowTest(unittest.TestCase):
    def test_f01_drawable(self):
        wf = {"nodes": [{"id": "f"}]}
        self.assertFalse(drawable({"type": "validated", "errors": ["缺 edges"], "workflow": wf}))
        self.assertTrue(drawable({"type": "validated", "errors": [], "workflow": wf}))
        self.assertFalse(drawable({"type": "done", "ok": False, "valid": False, "workflow": wf}))
        self.assertTrue(drawable({"type": "done", "ok": False, "valid": True, "workflow": wf}))
        self.assertFalse(drawable(None))

    def test_f04_latest(self):
        seq = latest()
        a = seq.next()
        b = seq.next()
        self.assertFalse(seq.is_current(a))
        self.assertTrue(seq.is_current(b))

    def test_n02_inspect_workflow(self):
        cases = [
            ({"id": "a", "name": "a", "inputs": {"n": None}, "outputs": {}, "nodes": [], "edges": []}, "inputs.n"),
            ({"id": "a", "name": "a", "inputs": {"n": 3}, "outputs": {}, "nodes": [], "edges": []}, "inputs.n"),
            ({"id": "a", "name": "a", "inputs": {"n": {}}, "outputs": {}, "nodes": [], "edges": []}, "inputs.n"),
            ({"id": "a", "name": "a", "inputs": None, "outputs": {}, "nodes": [], "edges": []}, "inputs"),
            ({"id": "a", "name": "a", "inputs": [1], "outputs": {}, "nodes": [], "edges": []}, "inputs"),
            ({"id": "a", "name": "a", "outputs": {}, "nodes": [], "edges": []}, "inputs"),
            (None, "不是一个工作流"),
            ({"id": 1, "name": {"x": 1}, "inputs": {}, "outputs": {}, "nodes": "x", "edges": []}, "nodes"),
        ]
        for wf, hint in cases:
            res = inspect_workflow(wf)
            clean, problems = res["wf"], res["problems"]
            self.assertTrue(any(hint in p for p in problems), f"{wf} → {problems}")
            self.assertIsInstance(clean["name"], str)
            collect_inputs(clean["inputs"], initial_values(clean["inputs"]))
            to_flow(clean, {})
        self.assertIn("缺少 inputs", inspect_workflow(cases[5][0])["problems"])
        self.assertIn("inputs 应当是对象", inspect_workflow(cases[3][0])["problems"])
        ok = {"id": "a", "name": "a", "intent": "", "inputs": {"n": {"type": "integer", "default": 42}}, "outputs": {}, "nodes": [], "edges": []}
        self.assertEqual(inspect_workflow(ok), {"wf": ok, "problems": []})

    def test_n08_refusal(self):
        identity = {"id": "id", "name": "直通", "inputs": {"x": {"type": "integer"}}, "outputs": {"y": {"type": "integer"}},
                    "nodes": [], "edges": [{"from": "$in.x", "to": "$out.y"}]}
        self.assertTrue(drawable({"type": "validated", "errors": [], "workflow": identity}))
        self.assertTrue(drawable({"type": "done", "ok": True, "valid": True, "workflow": identity}))
        refusal = {"id": "not_feasible", "name": "无法实现：发邮件", "inputs": {}, "outputs": {}, "nodes": [], "edges": []}
        self.assertTrue(is_refusal(refusal))
        self.assertTrue(is_refusal({"id": "x", "name": "无法实现这个", "nodes": []}))
        self.assertFalse(drawable({"type": "done", "ok": True, "valid": True, "workflow": refusal}))
        self.assertFalse(drawable({"type": "done", "ok": False, "valid": False, "workflow": {}}))

    WF = {"id": "batch", "nodes": [
        {"id": "each", "kind": "foreach", "body": {"nodes": [{"id": "fetch"}, {"id": "summarize"}, {"id": "ok"}]}},
        {"id": "join", "kind": "module"},
        {"id": "ok", "kind": "module"},
    ]}

    def test_canvas_ids(self):
        self.assertEqual(canvas_ids(self.WF), ["each", "each/fetch", "each/summarize", "each/ok", "join", "ok"])

    def test_issue_targets(self):
        wf = self.WF
        self.assertEqual(issue_targets("节点 join：拼接后没有再总结；改法：加一个思考节点", wf), ["join"])
        self.assertEqual(issue_targets("节点 summarize：收到的是网址而不是网页正文", wf), ["each/summarize"])
        self.assertEqual(issue_targets('示例输入 {"urls": []} 运行出错：节点 batch.each[1].fetch：network：连不上', wf), ["each/fetch"])
        self.assertEqual(sorted(issue_targets("batch > each.body：连线 4（fetch.text → summarize.text）：类型不兼容", wf)), ["each/fetch", "each/summarize"])

    def test_issue_targets_ambiguous(self):
        wf = self.WF
        self.assertEqual(issue_targets("节点 ok：条件写反了", wf), ["ok"])
        self.assertEqual(issue_targets("节点 ghost：不存在", wf), [])
        self.assertEqual(issue_targets("工作流输出 y 没有来源", wf), [])
        self.assertEqual(issue_targets("连线 1（$in.x → join.items）", wf), ["join"])

    def test_issue_text(self):
        self.assertEqual(issue_text("fetch_and_summarize_urls > loop.body：连线 7：summarize.text 已经有来源"), "循环 loop 里：连线 7：summarize.text 已经有来源")
        self.assertEqual(issue_text("wf：工作流输出 y 没有来源"), "工作流输出 y 没有来源")
        self.assertEqual(issue_text("wf > a.body > b.body：节点 x：缺输入"), "循环 a 里、循环 b 里：节点 x：缺输入")
        self.assertEqual(issue_text("节点 fmt：句子里没有写单位"), "节点 fmt：句子里没有写单位")
        self.assertEqual(issue_text("结构错误 nodes/0：缺 id"), "结构错误 nodes/0：缺 id")
        self.assertEqual(issue_text("wf > sub（child.workflow.json）：无法读取"), "wf > sub（child.workflow.json）：无法读取")

    def test_guide_steps(self):
        blank = {"inputs": {"input": {"type": "string"}}, "outputs": {"output": {"type": "string"}}, "nodes": [], "edges": []}
        g = guide_steps(blank)
        self.assertEqual([s["done"] for s in g["steps"]], [True, False, False, False, False])
        self.assertEqual(g["current"], 1)
        with_node = {**blank, "nodes": [{"id": "format", "kind": "module", "module": "text.format@0"}]}
        self.assertEqual(guide_steps(with_node)["current"], 2)
        wired = {**with_node, "edges": [{"from": "$in.input", "to": "format.value"}, {"from": "format.text", "to": "$out.output"}]}
        self.assertEqual(guide_steps(wired, ["节点 format：必需输入 template 没有来源"])["current"], 3)
        self.assertEqual(guide_steps(wired, [])["current"], 4)
        self.assertEqual(guide_steps(wired, [], True)["current"], -1)
        self.assertFalse(guide_steps({**blank, "outputs": {}})["steps"][0]["done"])
        guide_steps(None)

    def test_card_and_answers(self):
        self.assertEqual(card_to_requirement({"given": ["一组网址"], "wanted": ["每个一句总结"], "steps": ["抓取", "总结"], "cases": [" ", "打不开说明原因"]}),
                         "给它：一组网址。要它产出：每个一句总结。做法：抓取，然后总结。特殊情况：打不开说明原因。")
        self.assertEqual(card_to_requirement({}), "")
        qs = [{"ask": "分条还是一段？", "options": ["分条", "一段"]}, {"ask": "打不开怎么办？", "options": ["跳过", "说明原因"]}]
        prev = [{"ask": "分条还是一段？", "answer": "一段"}, {"ask": "要英文吗？", "answer": "不要"}]
        want = [{"ask": "要英文吗？", "answer": "不要"}, {"ask": "分条还是一段？", "answer": "分条"}]
        self.assertEqual(merge_answers(prev, qs, {0: "分条", 1: " "}), want)
        self.assertEqual(merge_answers(prev, qs, {"0": "分条"}), want)  # 键是文字（来自 JSON）也认


# 修改前 App.jsx 的汇总逻辑，作为对照
RANK = {"failed": 4, "running": 3, "completed": 2, "skipped": 1, "idle": 0}


def summarize(raw, wf_id):
    by_id = {}
    for path, s in raw.items():
        r = path_to_id(path, wf_id)
        cur = by_id.get(r["id"]) or {"state": "idle", "counts": {"total": 0, "completed": 0, "skipped": 0, "failed": 0, "running": 0} if r["items"] else None}
        if RANK[s["state"]] > RANK[cur["state"]]:
            cur.update(state=s["state"], reason=s["reason"])
        if cur["counts"]:
            cur["counts"]["total"] += 1
            cur["counts"][s["state"]] = cur["counts"].get(s["state"], 0) + 1
        by_id[r["id"]] = cur
    for s in by_id.values():
        if s["counts"] and s["state"] not in ("failed", "running"):
            s["state"] = "completed" if s["counts"]["completed"] else "skipped"
    return by_id


class RunStatusTest(unittest.TestCase):
    def test_q02_matches_full_summary(self):
        seed = 11

        def rnd():
            nonlocal seed
            seed = (seed * 1103515245 + 12345) % 2 ** 31
            return seed / 2 ** 31
        for _ in range(200):
            raw = {}
            rs = RunStatus("w")
            paths = ["w.a", "w.b", *[f"w.each[{i}].f" for i in range(8)], "w.each"]
            for k in range(40):
                path = paths[math.floor(rnd() * len(paths))]
                state = ["running", "completed", "skipped", "failed"][math.floor(rnd() * 4)]
                reason = f"原因{k}" if state == "skipped" else None
                raw[path] = {"state": state, "reason": reason}
                rs.apply(path, state, reason)
            want = summarize(raw, "w")
            got = rs.snapshot()
            self.assertEqual(sorted(got), sorted(want))
            for id in want:
                self.assertEqual(got[id]["state"], want[id]["state"], id)
                if want[id]["counts"]:
                    for st in ("total", "completed", "skipped", "failed", "running"):
                        self.assertEqual(got[id]["counts"][st], want[id]["counts"][st], f"{id}.{st}")
                else:
                    self.assertIsNone(got[id]["counts"])
                    self.assertEqual(got[id]["reason"], want[id].get("reason"), id)

    def test_q02_fast(self):
        rs = RunStatus("w")
        t0 = time.perf_counter()
        for i in range(8000):
            rs.apply(f"w.each[{i}].f", "running")
            rs.apply(f"w.each[{i}].f", "completed")
        s = rs.snapshot()
        ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(len(rs.paths), 8000)
        self.assertEqual(s["each/f"]["counts"], {"total": 8000, "failed": 0, "running": 0, "completed": 8000, "skipped": 0})
        self.assertLess(ms, 2000, f"用时 {ms:.0f} ms")  # 宽松上限，只防止退回平方级


class ViewTest(unittest.TestCase):
    def test_initial_viewport(self):
        self.assertIsNone(initial_viewport({"x": 0, "y": 0, "width": 600, "height": 300}, 1000, 700))
        v = initial_viewport({"x": 100, "y": 50, "width": 3000, "height": 400}, 1000, 700)
        self.assertEqual(v["zoom"], READABLE_ZOOM)
        self.assertEqual(v["x"], 32 - 100 * READABLE_ZOOM)
        self.assertEqual(v["y"], 350 - (50 + 200) * READABLE_ZOOM)
        self.assertIsNone(initial_viewport({"x": 0, "y": 0, "width": 0, "height": 0}, 1000, 700))  # 空图不抛错

    def test_type_label(self):
        self.assertEqual(type_label("string"), "文本")
        self.assertEqual(type_label("integer"), "整数")
        self.assertEqual(type_label("list<string>"), "文本列表")
        self.assertEqual(type_label("list<list<integer>>"), "整数列表列表")
        self.assertEqual(type_label("any"), "任意")
        self.assertEqual(type_label(None), "任意")
        self.assertEqual(type_label("custom"), "custom")

    def test_whole_graph_visible(self):
        b = {"x": 0, "y": 0, "width": 500, "height": 200}
        self.assertTrue(whole_graph_visible(b, (10, 10, 1), 800, 600))
        self.assertFalse(whole_graph_visible(b, (10, 10, 2), 800, 600))
        self.assertFalse(whole_graph_visible(b, (-50, 10, 1), 800, 600))
        self.assertTrue(whole_graph_visible(None, (0, 0, 1), 800, 600))

    def test_free_spot(self):
        nodes = [{"position": {"x": 0, "y": 0}, "data": {"width": 196, "height": 100}}]
        self.assertEqual(free_spot({"x": 0, "y": 0}, nodes), {"x": 0, "y": 180})
        self.assertEqual(free_spot({"x": 1000, "y": 0}, nodes), {"x": 1000, "y": 0})


if __name__ == "__main__":
    unittest.main()


class FriendlyTextTest(unittest.TestCase):
    """端口的中文短词、bug 检查与出错说明的通俗中文"""
    WF = {"id": "w", "inputs": {"url": {"type": "string"}}, "outputs": {"out": {"type": "string"}},
          "nodes": [{"id": "fetch", "kind": "module", "module": "web.fetch@0"},
                    {"id": "think", "kind": "module", "module": "llm.think@0"}], "edges": []}
    NAMES = {"fetch": "抓取网页", "think": "思考"}

    def test_port_labels(self):
        from native.logic.labels import humanize_types, port_labels
        self.assertEqual(port_labels([{"type": "string"}, {"type": "file"}]), ["文本", "文件"])
        self.assertEqual(port_labels([{"type": "string"}, {"type": "integer"}, {"type": "string"}]), ["文本1", "整数", "文本2"])
        self.assertEqual(port_labels([{"type": "list<string>"}]), ["文本列表"])
        # 模块说明里起了中文短名的端口用短名；没起名的仍用类型词（序号只在同名的里面排）
        self.assertEqual(port_labels([{"type": "string", "label": "网址"}, {"type": "string"}, {"type": "integer", "label": "状态码"}]),
                         ["网址", "文本", "状态码"])
        self.assertEqual(port_labels([{"type": "string", "label": "文字"}, {"type": "string", "label": "文字"}]), ["文字1", "文字2"])
        self.assertEqual(humanize_types("应为 list<integer>，收到 string"), "应为 整数列表，收到 文本")

    def test_friendly(self):
        from native.logic.bugcheck import check_items, friendly
        f = lambda t: friendly(t, self.WF, self.NAMES)  # noqa: E731
        self.assertEqual(f("节点 fetch：必需输入 url（string）没有来源"),
                         "「抓取网页」还缺一个必填的输入（文本）：要么接一条线进来，要么填一个固定值")
        self.assertEqual(f("工作流输出 out 没有来源"), "工作流输出「out」还没有接上任何节点，运行完会拿不到结果")
        self.assertIn("「抓取网页」 → 「思考」 → 「抓取网页」", f("连线形成环：fetch → think → fetch。重复执行只能用 foreach"))
        self.assertEqual(f("连线 0（fetch.text → think.text）：类型不兼容，list<string> 不能接到 string，需要先经过转换模块"),
                         "「抓取网页」送出的是文本列表，但「思考」要的是文本，接不上；中间需要加一个转换的节点")
        self.assertEqual(f("节点 think：config.temperature 的值 \"x\" 不符合类型 number"),
                         "「思考」：固定值temperature 的值 \"x\" 不符合类型 数字")
        items = check_items(["节点 fetch：必需输入 url（string）没有来源"], ["inputs.x 的声明不合法"], self.WF, self.NAMES)
        self.assertEqual([i["targets"] for i in items], [[], ["fetch"]])

    def test_explain_error(self):
        from native.logic.bugcheck import explain_error
        r = explain_error({"code": "module_crash", "message": "ValueError: bad", "path": "w.fetch"}, self.WF, self.NAMES)
        self.assertEqual((r["title"], r["cause"], r["targets"]), ("「抓取网页」这一步出错了", "节点内部的程序出错了", ["fetch"]))
        r = explain_error({"code": "invalid", "message": "节点 think：必需输入 text（string）没有来源\n工作流输出 out 没有来源"},
                          self.WF, self.NAMES)
        self.assertEqual(r["title"], "运行出错了")
        self.assertEqual(r["targets"], ["think"])
        self.assertEqual(r["detail"].count("· "), 2)
        # 子流程里的同名节点：不会指到顶层的 fetch，而是指向所属的子流程
        wf = {**self.WF, "nodes": self.WF["nodes"] + [{"id": "sub", "kind": "subflow", "ref": "x.json"}]}
        self.assertEqual(explain_error({"code": "timeout", "path": "w.sub.fetch"}, wf)["targets"], ["sub"])
        r = explain_error({"code": "weird"}, self.WF)
        self.assertEqual(r["cause"], "没有拿到具体原因")


class GenOutcomeTest(unittest.TestCase):
    def test_gen_outcome(self):
        from native.logic.flow import gen_outcome
        self.assertEqual(gen_outcome([{"type": "done", "ok": True}]), "")
        self.assertIn("没有通过实跑检查", gen_outcome([{"type": "done", "ok": False, "valid": True}]))
        self.assertIn("做不到：无法实现：要联网", gen_outcome([{"type": "done", "ok": True, "workflow": {"id": "not_feasible", "name": "无法实现：要联网", "nodes": []}}]))
        self.assertIn("没能得出结论（审查模型没回答）", gen_outcome([{"type": "done", "ok": False, "valid": True, "inconclusive": True, "reason": "审查模型没回答"}]))
        self.assertEqual(gen_outcome([{"type": "done", "ok": False, "stopped": True}]), "")
        self.assertEqual(gen_outcome([{"type": "error", "message": "模型服务不可用"}]), "没能生成：模型服务不可用")
        self.assertEqual(gen_outcome([]), "")
