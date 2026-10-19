"""代码审查报告（2026-10-16）所列问题的回归测试：每条都按报告里的触发条件复现。"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

# 本机慢网页 fixture（127.0.0.1）：放行本机地址（导入期设置，模块子进程继承）
os.environ["WORKFLOW_ALLOW_LOCAL_FETCH"] = "1"

import tools.generate as gen
import tools.semantic as sem
from core import types
from core.engine import Engine
from core.registry import ROOT, ModuleSpec, load_registry
from core.validate import validate_workflow, value_matches
from tests.test_semantic import FakeChat

REG = load_registry()


def wf(inputs, outputs, nodes, edges, wid="t"):
    return {"spec": "0.2", "id": wid, "name": "测试", "intent": "", "inputs": inputs, "outputs": outputs,
            "nodes": nodes, "edges": edges}


PASSTHROUGH = lambda t_in, t_out: wf({"x": {"type": t_in}}, {"y": {"type": t_out}}, [], [{"from": "$in.x", "to": "$out.y"}])  # noqa: E731


class F02_CheckWithoutConclusion(unittest.TestCase):
    """运行检查没有有效结论时，不能当作通过。"""

    def setUp(self):
        self.page = example_page()

    def check(self, *answers):
        fake = FakeChat(*answers)
        with mock.patch.object(sem, "chat", fake):
            return sem.make_checker("需求", "judge", engine_kwargs={"overrides": {"llm.think@0": lambda i: {"answer": "x"}}})(self.page)

    def test_no_valid_samples(self):
        problems, log = self.check({"samples": [{"wrong": 1}]}, {"samples": []})
        self.assertEqual((problems, log["status"]), ([], "inconclusive"))

    def test_judge_not_json_twice(self):
        s = {"samples": [{"url": "ftp://unused"}]}
        with mock.patch.object(Engine, "run", return_value=_ok_run()):
            for bad in ("不是 JSON", {}, {"ok": False, "problems": []}, {"ok": "yes"}):
                fake = FakeChat(s, bad, bad) if not isinstance(bad, str) else _RawChat(s, bad, bad)
                with mock.patch.object(sem, "chat", fake):
                    problems, log = sem.make_checker("需求", "judge")(self.page)
                self.assertEqual(log["status"], "inconclusive", bad)
                self.assertEqual(problems, [])

    def test_retry_then_valid(self):
        s = {"samples": [{"url": "ftp://unused"}]}
        with mock.patch.object(Engine, "run", return_value=_ok_run()), \
             mock.patch.object(sem, "chat", FakeChat(s, {}, {"ok": True})):
            problems, log = sem.make_checker("需求", "judge")(self.page)
        self.assertEqual((problems, log["status"]), ([], "passed"))

    def test_generate_reports_inconclusive_not_ok(self):
        with mock.patch.object(gen, "chat", FakeChat(self.page)):
            res = gen.generate("x", "m", rounds=3, feasibility=False,
                               post_check=lambda w: ([], {"status": "inconclusive", "reason": "没编出示例"}))
        self.assertFalse(res["ok"])
        self.assertTrue(res["inconclusive"])
        self.assertEqual(res["rounds"], 1)


class F03_FullOutputs(unittest.TestCase):
    def test_long_output_not_truncated(self):
        long_text = "字" * 2500 + "END"
        res = Engine(REG).run(PASSTHROUGH("string", "string"), {"x": long_text})
        out = res.to_json()["outputs"]["y"]
        self.assertEqual(len(out), 2503)
        self.assertTrue(out.endswith("END"))


class F08_MissingOptionalInput(unittest.TestCase):
    def test_optional_to_required_port_is_structured_error(self):
        g = wf({"x": {"type": "boolean", "required": False}}, {}, [{"id": "branch", "kind": "if"}],
               [{"from": "$in.x", "to": "branch.condition"}])
        self.assertEqual(validate_workflow(g, REG), [])
        res = Engine(REG).run(g, {})
        self.assertEqual((res.status, res.error["code"]), ("failed", "missing_input"))
        self.assertIn("$in.x", res.error["message"])

    def test_optional_passthrough_gives_none_with_warning(self):
        g = PASSTHROUGH("string", "string")
        g["inputs"]["x"]["required"] = False
        res = Engine(REG).run(g, {})
        self.assertEqual((res.status, res.outputs), ("completed", {"y": None}))
        self.assertIn("没有提供", res.warnings[0])


class F09_Null(unittest.TestCase):
    def test_null_only_for_any(self):
        self.assertFalse(value_matches("string", None))
        self.assertFalse(value_matches("integer", None))
        self.assertTrue(value_matches("any", None))

    def test_null_input_rejected(self):
        res = Engine(REG).run(PASSTHROUGH("integer", "integer"), {"x": None})
        self.assertEqual(res.error["code"], "input_type")


class F10_InvalidNestedType(unittest.TestCase):
    def test_list_str_rejected_by_validator(self):
        errs = validate_workflow(PASSTHROUGH("list<str>", "list<str>"), REG)
        self.assertTrue(any("list<str> 不合法" in e for e in errs), errs)

    def test_value_matches_unknown_type_does_not_crash(self):
        self.assertFalse(value_matches("list<str>", ["a"]))
        self.assertFalse(types.is_valid("list<str>"))


class F11_Defaults(unittest.TestCase):
    def test_bad_default_rejected(self):
        g = PASSTHROUGH("integer", "integer")
        g["inputs"]["x"]["default"] = "not_an_integer"
        errs = validate_workflow(g, REG)
        self.assertTrue(any("默认值" in e and "integer" in e for e in errs), errs)

    def test_default_applied_and_checked(self):
        g = PASSTHROUGH("integer", "integer")
        g["inputs"]["x"]["default"] = 7
        self.assertEqual(Engine(REG).run(g, {}).outputs, {"y": 7})


class F12_BoundaryTypes(unittest.TestCase):
    def test_any_to_integer_output_checked(self):
        res = Engine(REG).run(PASSTHROUGH("any", "integer"), {"x": "不是整数"})
        self.assertEqual((res.status, res.error["code"]), ("failed", "output_type"))

    def test_foreach_items_checked_against_body(self):
        g = wf({"xs": {"type": "any"}}, {"ys": {"type": "list<integer>"}},
               [{"id": "each", "kind": "foreach", "max_items": 5,
                 "body": {"inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "integer"}},
                          "nodes": [], "edges": [{"from": "$in.item", "to": "$out.result"}]}}],
               [{"from": "$in.xs", "to": "each.items"}, {"from": "each.results", "to": "$out.ys"}])
        res = Engine(REG).run(g, {"xs": ["a", "b"]})
        self.assertEqual((res.status, res.error["code"]), ("failed", "input_type"))
        self.assertIn("each[0]", res.error["path"])
        self.assertEqual(Engine(REG).run(g, {"xs": [1, 2]}).outputs, {"ys": [1, 2]})


class F13_F14_SubflowRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, doc):
        (self.dir / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def ref_wf(self, wid, ref):
        return wf({"x": {"type": "string"}}, {"y": {"type": "string"}}, [{"id": "sub", "kind": "subflow", "ref": ref}],
                  [{"from": "$in.x", "to": "sub.x"}, {"from": "sub.y", "to": "$out.y"}], wid)

    def test_self_reference(self):
        self.write("a.workflow.json", self.ref_wf("a", "a.workflow.json"))
        errs = validate_workflow(self.ref_wf("root", "a.workflow.json"), REG, self.dir)
        self.assertTrue(any("循环引用" in e for e in errs), errs)

    def test_mutual_reference(self):
        self.write("a.workflow.json", self.ref_wf("a", "b.workflow.json"))
        self.write("b.workflow.json", self.ref_wf("b", "a.workflow.json"))
        errs = validate_workflow(self.ref_wf("root", "a.workflow.json"), REG, self.dir)
        self.assertTrue(any("循环引用" in e for e in errs), errs)

    def test_invalid_document_reported_not_crash(self):
        for doc in ({}, [1, 2], {"spec": "0.2"}):
            self.write("bad.workflow.json", doc)
            errs = validate_workflow(self.ref_wf("root", "bad.workflow.json"), REG, self.dir)
            self.assertTrue(errs, doc)


class A01_TimeoutKillsModule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = {"id": "test.slow", "version": "0.1.0", "name": "慢", "description": "测试", "category": "处理",
                "side_effects": "none",
                "inputs": {"seconds": {"type": "number", "description": ""}, "marker": {"type": "string", "description": ""}},
                "outputs": {"text": {"type": "string", "description": ""}},
                "examples": [{"title": "t", "input": {}, "output": {}}], "runtime": {"entry": "module.py:run", "timeout_s": 1}}
        self.reg = {**REG, "test.slow@0": ModuleSpec("test.slow", 0, ROOT / "tests" / "fixtures" / "slow_module", data)}
        self.marker = Path(self.tmp.name) / "marker.txt"
        self.g = wf({}, {"t": {"type": "string"}},
                    [{"id": "slow", "kind": "module", "module": "test.slow@0", "config": {"seconds": 1.5, "marker": str(self.marker)}}],
                    [{"from": "slow.text", "to": "$out.t"}])

    def tearDown(self):
        self.tmp.cleanup()

    def test_module_really_stopped(self):
        res = Engine(self.reg, timeout_override=0.5).run(self.g, {})
        self.assertEqual(res.error["code"], "timeout")
        time.sleep(2)  # 如果模块还活着，此时已经写出标记文件
        self.assertFalse(self.marker.exists())

    def test_stop_kills_module(self):
        flag = threading.Event()
        threading.Timer(0.5, flag.set).start()
        res = Engine(self.reg, should_stop=flag.is_set).run(self.g, {})
        self.assertEqual(res.error["code"], "cancelled")
        time.sleep(2)
        self.assertFalse(self.marker.exists())

    def test_worker_still_works_after_kill(self):
        Engine(self.reg, timeout_override=0.3).run(self.g, {})
        self.g["nodes"][0]["config"]["seconds"] = 0
        self.assertEqual(Engine(self.reg).run(self.g, {}).outputs, {"t": "done"})


class _SlowBody(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "10"); self.end_headers()
        self.wfile.flush()
        time.sleep(1)
        try:
            self.wfile.write(b"0123456789")
        except OSError:
            pass

    def log_message(self, *a):
        pass


class A02_BodyTimeout(unittest.TestCase):
    def test_reported_as_fetch_timeout(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowBody)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            g = wf({}, {"t": {"type": "string"}},
                   [{"id": "f", "kind": "module", "module": "web.fetch@0",
                     "config": {"url": f"http://127.0.0.1:{server.server_port}/", "timeout_s": 0.2}}],
                   [{"from": "f.text", "to": "$out.t"}])
            t = time.time()
            res = Engine(REG).run(g, {})
            self.assertEqual(res.error["code"], "timeout")
            self.assertIn("没有读完网页内容", res.error["message"])
            self.assertLess(time.time() - t, 5)
        finally:
            server.shutdown()
            server.server_close()


class A03_RecordsActualArguments(unittest.TestCase):
    def test_config_and_defaults_recorded(self):
        g = wf({}, {"t": {"type": "string"}},
               [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "固定"}}],
               [{"from": "f.text", "to": "$out.t"}])
        rec = Engine(REG).run(g, {}).records[0]
        self.assertEqual(rec.inputs, {"template": "固定", "value": None})


# ---------- 小工具 ----------
def example_page():
    return json.loads((ROOT / "examples" / "summarize_page.workflow.json").read_text(encoding="utf-8"))


def _ok_run():
    from core.engine import RunResult
    return RunResult("completed", {"summary": "x"}, [])


class _RawChat(FakeChat):
    """按原样返回字符串（用来模拟不是 JSON 的回答）。"""

    def __call__(self, model, messages, fmt, temperature=0, url=None):
        self.calls.append(messages)
        a = self.answers.pop(0)
        return (a if isinstance(a, str) else json.dumps(a, ensure_ascii=False)), 0.1


if __name__ == "__main__":
    unittest.main()
