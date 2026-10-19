"""第三轮复检报告（2026-10-17）N03–N06 的回归测试（N01、N02、N07、N08 见 web/tests 与 web/tests/e2e.mjs）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
import tools.generate as gen
import tools.semantic as sem
from core.engine import Engine
from core.registry import load_registry
from core.validate import validate_workflow
from tests.test_review_fixes import _ok_run
from tests.test_semantic import FakeChat

REG = load_registry()
client = TestClient(appmod.app)
CFG = {"generator": "g", "generator_url": "u", "judge": "j", "judge_url": "u"}
GOOD = {"spec": "0.2", "id": "good", "name": "好的", "intent": "", "inputs": {"x": {"type": "integer"}},
        "outputs": {"y": {"type": "integer"}}, "nodes": [], "edges": [{"from": "$in.x", "to": "$out.y"}]}


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


class N03_OneBadFileDoesNotBreakList(unittest.TestCase):
    def test_list_skips_non_object_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "good.workflow.json").write_text(json.dumps(GOOD), encoding="utf-8")
            for name, body in {"a": "null", "b": "[1]", "c": "3", "d": '"x"'}.items():
                (d / f"{name}.workflow.json").write_text(body, encoding="utf-8")
            (d / "meta.workflow.json").write_text(json.dumps({"id": 5, "name": ["x"], "intent": None}), encoding="utf-8")
            with mock.patch.object(appmod, "SAVED", d):
                r = client.get("/api/workflows")
        self.assertEqual(r.status_code, 200)
        saved = {i["file"]: i for i in r.json() if i["source"] == "saved"}
        self.assertEqual(set(saved), {"good.workflow.json", "meta.workflow.json"})
        self.assertEqual((saved["meta.workflow.json"]["name"], saved["meta.workflow.json"]["id"]), ("meta.workflow.json", ""))


class N04_FeasibilityAnswerShape(unittest.TestCase):
    BAD = ["null", "[]", "3", '{"feasible": true, "steps": null}', '{"feasible": true, "steps": [1]}',
           '{"feasible": "false", "steps": []}', '{"steps": []}', "不是 JSON"]

    def test_bad_answers_do_not_crash_or_block(self):
        for bad in self.BAD:
            fake = mock.Mock(return_value=(bad, 0.1))
            with mock.patch.object(gen, "chat", fake):
                ok, reason, info = gen.check_feasible("x", "m", "")
            self.assertEqual((ok, reason), (True, ""), bad)  # 不合格回答不拦截，交给生成与校验

    def test_real_false_still_blocks(self):
        with mock.patch.object(gen, "chat", mock.Mock(return_value=('{"feasible": false, "steps": [{"need": "发邮件", "module": null}]}', 0.1))):
            ok, reason, _ = gen.check_feasible("x", "m", "")
        self.assertEqual((ok, reason), (False, "缺少能力：发邮件"))

    def test_generate_route_continues_to_drafting(self):
        answers = iter([('{"feasible": true, "steps": null}', 0.1), (json.dumps(GOOD), 0.1)])
        with mock.patch.object(appmod, "model_config", return_value=CFG), \
             mock.patch.object(gen, "chat", side_effect=lambda *a, **k: next(answers)):
            evs = events(client.post("/api/generate", json={"requirement": "x", "check": False}))
        self.assertEqual([e["type"] for e in evs], ["start", "feasibility", "drafting", "validated", "done"])
        self.assertTrue(evs[-1]["ok"])


class N05_NonModuleConfig(unittest.TestCase):
    def graph(self, kind, cfg):
        n = {"id": "branch", "kind": kind, "config": cfg}
        if kind == "foreach":
            n.update(max_items=3, body={"inputs": {"item": {"type": "any"}}, "outputs": {"result": {"type": "any"}},
                                        "nodes": [], "edges": [{"from": "$in.item", "to": "$out.result"}]})
        if kind == "subflow":
            n["body"] = {"inputs": {}, "outputs": {}, "nodes": [], "edges": []}
        return {**GOOD, "nodes": [n]}

    def test_returns_errors_not_exception(self):
        for kind in ("if", "merge", "foreach", "subflow"):
            for cfg in (None, 1, True, [1], {"a": 1}, "x"):
                errors = validate_workflow(self.graph(kind, cfg), REG)
                self.assertTrue(any("只有模块节点可以使用 config" in e for e in errors), (kind, cfg, errors))

    def test_module_config_not_object(self):
        wf = {**GOOD, "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": None}]}
        self.assertIsInstance(validate_workflow(wf, REG), list)

    def test_validate_route_and_generator_retry(self):
        bad = self.graph("if", None)
        self.assertEqual(client.post("/api/validate", json={"workflow": bad}).status_code, 200)
        answers = iter([(json.dumps(bad), 0.1), (json.dumps(GOOD), 0.1)])
        with mock.patch.object(gen, "chat", side_effect=lambda *a, **k: next(answers)):
            res = gen.generate("x", "m", rounds=3, feasibility=False)
        self.assertEqual((res["ok"], res["rounds"]), (True, 2))
        self.assertTrue(res["log"][0]["errors"])


class N06_OmittedOptionalSamples(unittest.TestCase):
    DECL = {"x": {"type": "integer", "default": 7}, "o": {"type": "string", "required": False}, "r": {"type": "integer"}}

    def test_sample_contract(self):
        ok = sem.sample_ok
        self.assertTrue(ok(self.DECL, {"r": 1}))
        self.assertTrue(ok(self.DECL, {"r": 1, "x": 2, "o": "a"}))
        self.assertFalse(ok(self.DECL, {}))  # 必填 r 缺失
        self.assertFalse(ok(self.DECL, {"r": 1, "zzz": 1}))  # 未声明
        self.assertFalse(ok(self.DECL, {"r": "1"}))  # 类型不对
        self.assertFalse(ok(self.DECL, {"r": 1, "x": "7"}))

    def test_omitted_default_sample_is_run_and_judged(self):
        wf = {**GOOD, "inputs": {"x": {"type": "integer", "default": 7}}}
        self.assertEqual(Engine().run(wf, {}).outputs, {"y": 7})
        fake = FakeChat({"samples": [{}]}, {"ok": True})
        with mock.patch.object(sem, "chat", fake):
            problems, log = sem.make_checker("需求", "judge")(wf)
        self.assertEqual((problems, log["status"], log["samples"]), ([], "passed", [{}]))
        self.assertEqual(log["runs"][0]["outputs"], {"y": 7})


if __name__ == "__main__":
    unittest.main()
