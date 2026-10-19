"""复检报告（2026-10-17）R02、R04、R05 的回归测试（R01、R03 与 R02 的画布部分在 web/tests）。"""
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
from tests.test_review_fixes import _ok_run, example_page
from tests.test_semantic import FakeChat


class R04_NoModelCallAfterStop(unittest.TestCase):
    def test_no_retry_for_samples_after_stop(self):
        """第一次编示例期间被停止、且回答无效：不能再发第二次请求。"""
        stop = {"flag": False}

        class Chat(FakeChat):
            def __call__(self, *a, **kw):
                out = super().__call__(*a, **kw)
                stop["flag"] = True  # 这次请求进行期间用户点了停止
                return out

        fake = Chat({"samples": []}, {"samples": [{"url": "ftp://x"}]})
        with mock.patch.object(sem, "chat", fake):
            problems, log = sem.make_checker("需求", "judge", should_stop=lambda: stop["flag"])(example_page())
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual((problems, log["status"], log["reason"]), ([], "inconclusive", "已停止"))

    def test_no_feasibility_call_when_already_stopped(self):
        fake = FakeChat({"feasible": True, "steps": []})
        with mock.patch.object(gen, "chat", fake):
            res = gen.generate("x", "m", should_stop=lambda: True)
        self.assertEqual(fake.calls, [])
        self.assertTrue(res["stopped"])
        self.assertFalse(res["ok"])


class R05_WrongFieldTypes(unittest.TestCase):
    def test_samples_not_a_list_retried_then_inconclusive(self):
        for bad in (None, 1, True, "x"):
            with mock.patch.object(sem, "chat", FakeChat({"samples": bad}, {"samples": bad})):
                problems, log = sem.make_checker("需求", "judge")(example_page())
            self.assertEqual((problems, log["status"]), ([], "inconclusive"), bad)

    def test_samples_bad_then_good(self):
        good = {"samples": [{"url": "ftp://x"}]}
        with mock.patch.object(Engine, "run", return_value=_ok_run()), \
             mock.patch.object(sem, "chat", FakeChat({"samples": None}, good, {"ok": True})):
            problems, log = sem.make_checker("需求", "judge")(example_page())
        self.assertEqual(log["status"], "passed")

    def test_problems_not_a_list_is_invalid_verdict(self):
        for bad in (1, True, "坏了", {"problem": "x"}):
            self.assertEqual(sem.parse_verdict(json.dumps({"ok": False, "problems": bad}, ensure_ascii=False)), (None, []), bad)
        s = {"samples": [{"url": "ftp://x"}]}
        bad = {"ok": False, "problems": 1}
        with mock.patch.object(Engine, "run", return_value=_ok_run()), \
             mock.patch.object(sem, "chat", FakeChat(s, bad, bad)):
            problems, log = sem.make_checker("需求", "judge")(example_page())
        self.assertEqual((problems, log["status"]), ([], "inconclusive"))


class R02_BadRefPorts(unittest.TestCase):
    def test_ports_endpoint_drops_bad_declarations(self):
        with tempfile.TemporaryDirectory() as d:
            child = {"id": "c", "inputs": {"x": None, "ok": {"type": "string"}, "t": {"type": "list<str>"}}, "outputs": []}
            (Path(d) / "child.workflow.json").write_text(json.dumps(child), encoding="utf-8")
            outer = {"nodes": [{"id": "s", "kind": "subflow", "ref": "child.workflow.json"}], "edges": []}
            with mock.patch.dict(appmod.BASE_DIRS, {"saved": Path(d)}):
                r = TestClient(appmod.app).post("/api/ports", json={"workflow": outer, "source": "saved"}).json()
        p = r["child.workflow.json"]
        self.assertEqual((p["inputs"], p["outputs"]), ({"ok": {"type": "string"}}, {}))
        self.assertIn("inputs.x", p["error"])


if __name__ == "__main__":
    unittest.main()
