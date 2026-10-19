import copy
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.engine import Engine, WorkflowInvalid
from core.registry import ROOT, ModuleSpec, load_registry

# 这些测试抓取本机 fixtures（127.0.0.1）：放行本机地址，否则 web.fetch 的 SSRF 防护会拒绝。
# 在导入期设置，保证之后新起的模块子进程（独立进程运行 web.fetch）都继承到。
os.environ["WORKFLOW_ALLOW_LOCAL_FETCH"] = "1"

REG = load_registry()


def example(name):
    return json.loads((ROOT / "examples" / f"{name}.workflow.json").read_text(encoding="utf-8"))


def fake_think(inputs):
    """代替本地模型：回答 = 指令开头 + 材料开头，结果可预测。"""
    return {"answer": f"[{inputs['instruction'][:6]}]{inputs['text'][:30]}"}


class _Pages(BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/ok": (200, "<title>Example Domain</title> hello"), "/gone": (404, "not here")}.get(self.path, (500, "boom"))
        data = body[1].encode("utf-8")
        self.send_response(body[0]); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def engine(self, **kw):
        return Engine(REG, overrides={"llm.think@0": fake_think, **kw.pop("overrides", {})}, **kw)

    def status_of(self, res, path):
        return next(r.status for r in res.records if r.path == path)


class Linear(Base):
    def test_summarize_page(self):
        res = self.engine().run(example("summarize_page"), {"url": self.base + "/ok"})
        self.assertEqual(res.status, "completed", res.error)
        self.assertTrue(res.outputs["summary"].startswith("[用三句话中文"))
        self.assertIn("Example Domain", res.outputs["summary"])
        self.assertEqual([r.path for r in res.records], ["summarize_page.fetch", "summarize_page.think"])
        self.assertTrue(all(r.status == "completed" for r in res.records))

    def test_config_and_defaults_reach_module(self):
        seen = {}
        def spy(inputs):
            seen.update(inputs)
            return {"answer": "x"}
        self.engine(overrides={"llm.think@0": spy}).run(example("summarize_page"), {"url": self.base + "/ok"})
        self.assertEqual(seen["temperature"], 0.2)          # 默认值
        self.assertTrue(seen["instruction"].startswith("用三句话"))  # config
        self.assertIn("Example Domain", seen["text"])       # 连线


class Branching(Base):
    def test_if_with_value_and_merge_inside_foreach(self):
        urls = [self.base + "/ok", self.base + "/gone"]
        res = self.engine().run(example("summarize_many"), {"urls": urls})
        self.assertEqual(res.status, "completed", res.error)
        ok, gone = res.outputs["summaries"]
        self.assertTrue(ok.startswith("[用一句话中文") and "Example" in ok)
        self.assertTrue(gone.startswith("[这个网页没有") and "not here" in gone)
        p = "summarize_many.each"
        self.assertEqual(self.status_of(res, f"{p}[0].explain"), "skipped")
        self.assertEqual(self.status_of(res, f"{p}[1].summarize"), "skipped")

    def test_control_lines_route_without_value(self):
        wf = example("score_message")
        high = self.engine().run(wf, {"score": 85})
        self.assertEqual(high.status, "completed", high.error)
        self.assertEqual(high.outputs["message"], "[根据分数写一]分数：85")
        self.assertEqual(self.status_of(high, "score_message.congratulate"), "completed")
        self.assertEqual(self.status_of(high, "score_message.encourage"), "skipped")
        low = self.engine().run(wf, {"score": 30})
        self.assertEqual(self.status_of(low, "score_message.congratulate"), "skipped")
        self.assertEqual(self.status_of(low, "score_message.encourage"), "completed")

    def test_skip_propagates_downstream(self):
        wf = example("score_message")
        wf["nodes"].append({"id": "shout", "kind": "module", "module": "text.format@0", "config": {"template": "！{value}"}})
        wf["edges"].append({"from": "encourage.answer", "to": "shout.value"})
        res = self.engine().run(wf, {"score": 90})
        rec = next(r for r in res.records if r.path == "score_message.shout")
        self.assertEqual(rec.status, "skipped")
        self.assertIn("来自 encourage.answer", rec.reason)

    def test_join_then_think(self):
        res = self.engine().run(example("overall_summary"), {"urls": [self.base + "/ok", self.base + "/gone"]})
        self.assertEqual(res.status, "completed", res.error)
        join = next(r for r in res.records if r.path == "overall_summary.join")
        self.assertTrue(join.outputs["text"].startswith("1. [用一句中文总]"), join.outputs["text"])
        self.assertIn("\n2. ", join.outputs["text"])


class Failures(Base):
    def test_invalid_workflow_rejected_before_running(self):
        wf = example("summarize_page")
        wf["nodes"][0]["module"] = "web.nope@0"
        with self.assertRaises(WorkflowInvalid) as c:
            self.engine().run(wf, {"url": "http://x"})
        self.assertTrue(any("不存在的模块" in e for e in c.exception.errors))

    def test_input_type_and_missing(self):
        res = self.engine().run(example("score_message"), {"score": "八十"})
        self.assertEqual((res.status, res.error["code"]), ("failed", "input_type"))
        res = self.engine().run(example("score_message"), {})
        self.assertEqual(res.error["code"], "missing_input")

    def test_stops_at_first_error_and_keeps_records(self):
        wf = example("summarize_page")
        res = self.engine().run(wf, {"url": "ftp://nope"})
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.error, {"path": "summarize_page.fetch", "code": "invalid_url", "message": res.error["message"]})
        self.assertEqual([(r.path, r.status) for r in res.records], [("summarize_page.fetch", "failed")])

    def test_max_items(self):
        wf = example("summarize_many")
        wf["nodes"][0]["max_items"] = 1
        res = self.engine().run(wf, {"urls": [self.base + "/ok"] * 2})
        self.assertEqual(res.error["code"], "too_many_items")

    def test_timeout(self):
        def slow(inputs):
            time.sleep(2)
            return {"answer": "late"}
        t = time.time()
        res = self.engine(overrides={"llm.think@0": slow}, timeout_override=0.3).run(example("summarize_page"), {"url": self.base + "/ok"})
        self.assertEqual(res.error["code"], "timeout")
        self.assertLess(time.time() - t, 1.5)

    def test_module_crash_and_bad_output(self):
        res = self.engine(overrides={"llm.think@0": lambda i: 1 / 0}).run(example("summarize_page"), {"url": self.base + "/ok"})
        self.assertEqual(res.error["code"], "module_crash")
        res = self.engine(overrides={"llm.think@0": lambda i: {"reply": "x"}}).run(example("summarize_page"), {"url": self.base + "/ok"})
        self.assertEqual(res.error["code"], "bad_output")


class SideEffects(Base):
    def setUp(self):
        data = copy.deepcopy(REG["text.format@0"].data)
        data.update(id="file.save", side_effects="write")
        self.reg = {**REG, "file.save@0": ModuleSpec("file.save", 0, REG["text.format@0"].path, data)}
        self.wf = {"spec": "0.2", "id": "save", "name": "保存", "intent": "", "inputs": {"v": {"type": "string"}},
                   "outputs": {"t": {"type": "string"}},
                   "nodes": [{"id": "save", "kind": "module", "module": "file.save@0"}],
                   "edges": [{"from": "$in.v", "to": "save.value"}, {"from": "save.text", "to": "$out.t"}]}
        self.calls = []

    def fake_save(self, inputs):
        self.calls.append(inputs)
        return {"text": "saved"}

    def test_write_needs_confirmation(self):
        res = Engine(self.reg, overrides={"file.save@0": self.fake_save}).run(self.wf, {"v": "x"})
        self.assertEqual(res.error["code"], "not_confirmed")
        self.assertEqual(self.calls, [])

    def test_confirmed_once_per_module(self):
        asked = []
        eng = Engine(self.reg, overrides={"file.save@0": self.fake_save},
                     confirm=lambda spec, path, target=None: asked.append(path) or True)
        eng.run(self.wf, {"v": "a"})
        eng.run(self.wf, {"v": "b"})
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(self.calls), 2)


class Subflow(Base):
    def test_ref_subflow(self):
        sub = ROOT / "tests" / "_tmp_engine_sub.workflow.json"
        sub.write_text(json.dumps(example("summarize_page"), ensure_ascii=False), encoding="utf-8")
        try:
            wf = {"spec": "0.2", "id": "outer", "name": "外层", "intent": "",
                  "inputs": {"u": {"type": "string"}}, "outputs": {"s": {"type": "string"}},
                  "nodes": [{"id": "sub", "kind": "subflow", "ref": "_tmp_engine_sub.workflow.json"}],
                  "edges": [{"from": "$in.u", "to": "sub.url"}, {"from": "sub.summary", "to": "$out.s"}]}
            res = self.engine().run(wf, {"u": self.base + "/ok"}, base_dir=ROOT / "tests")
            self.assertEqual(res.status, "completed", res.error)
            self.assertIn("Example", res.outputs["s"])
            self.assertIn("outer.sub.fetch", [r.path for r in res.records])
        finally:
            sub.unlink()


class Events(Base):
    def test_ask_module_waits_for_answer(self):
        """提醒确认：引擎先发 ask 事件、等 ask 回调给回答，再把回答交给模块；没人回答（命令行）按第一个按钮继续"""
        wf = {"spec": "0.2", "id": "w", "name": "审核", "intent": "审核一段文字", "inputs": {"t": {"type": "string"}},
              "outputs": {"ok": {"type": "boolean"}, "who": {"type": "string"}},
              "nodes": [{"id": "ask", "kind": "module", "module": "user.confirm@0",
                         "config": {"title": "请审核", "options": ["通过", "不通过"]}}],
              "edges": [{"from": "$in.t", "to": "ask.detail"}, {"from": "ask.approved", "to": "$out.ok"},
                        {"from": "ask.choice", "to": "$out.who"}]}
        events = []
        seen = {}

        def ask(path, prompt):
            seen["path"], seen["prompt"] = path, prompt
            return {"choice": "不通过", "note": "再改改"}
        res = Engine(REG, on_event=events.append, ask=ask).run(wf, {"t": "一段总结"})
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.outputs, {"ok": False, "who": "不通过"})
        self.assertEqual(seen["path"], "w.ask")
        self.assertEqual(seen["prompt"]["title"], "请审核")
        self.assertEqual(seen["prompt"]["detail"], "一段总结")
        self.assertNotIn("answer", seen["prompt"])
        self.assertEqual([e["type"] for e in events], ["node_start", "ask", "answered", "node_end"])
        self.assertEqual(res.records[0].inputs["answer"], {"choice": "不通过", "note": "再改改"})
        # 没人回答：按第一个按钮
        res = Engine(REG).run(wf, {"t": "x"})
        self.assertEqual(res.outputs, {"ok": True, "who": "通过"})
        # 演练（运行检查）时不问
        asked = []
        res = Engine(REG, rehearse={"user.confirm@0"}, ask=lambda p, q: asked.append(p) or {}).run(wf, {"t": "x"})
        self.assertEqual((res.status, asked), ("completed", []))
        # 等回答期间被停止：算已停止
        res = Engine(REG, ask=lambda p, q: None).run(wf, {"t": "x"})
        self.assertEqual((res.status, res.error["code"]), ("failed", "cancelled"))

    def test_events_in_order(self):
        events = []
        self.engine(on_event=events.append).run(example("score_message"), {"score": 70})
        kinds = [(e["type"], e["path"].split(".")[-1]) for e in events]
        self.assertIn(("node_skipped", "encourage"), kinds)
        self.assertLess(kinds.index(("node_start", "passed")), kinds.index(("node_start", "branch")))


if __name__ == "__main__":
    unittest.main()
