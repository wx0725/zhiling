"""扩充模块库：写文件的确认与演练、模块描述校验，以及新模块在真实进程里拼成图后能跑通。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
import tools.semantic as sem
import core.engine as engine_mod
from core.engine import Engine
from core.registry import ModuleSpec, load_registry
from core.validate import validate_module
from tests.test_semantic import FakeChat

client = TestClient(appmod.app)


def save_wf(path: str) -> dict:
    return {"spec": "0.2", "id": "save_note", "name": "保存", "intent": "把一段文字存成文件",
            "inputs": {"text": {"type": "string"}}, "outputs": {"path": {"type": "string"}},
            "nodes": [{"id": "save", "kind": "module", "module": "file.write@0", "config": {"path": path}}],
            "edges": [{"from": "$in.text", "to": "save.text"}, {"from": "save.path", "to": "$out.path"}]}


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


class WriteConfirmation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "笔记.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def test_runtime_check_rehearses_writes_without_touching_disk(self):
        fake = FakeChat({"samples": [{"text": "你好"}]}, {"ok": True, "problems": []})
        with mock.patch.object(sem, "chat", fake):
            problems, log = sem.make_checker("存文件", "judge")(save_wf(str(self.target)))
        self.assertEqual((problems, log["status"]), ([], "passed"))
        self.assertEqual(log["runs"][0]["outputs"], {"path": str(self.target.resolve())})
        self.assertFalse(self.target.exists())                       # 只演练，没有真的写
        self.assertIn("只演练", fake.calls[1][0]["content"])          # 审查模型知道这一点，不会当成错误

    def test_rehearsal_still_reports_problems(self):
        # 写模块（输出类，不是“获取”类）演练报错 exists，是图里固定配置/路径的问题，能归因于图 → 交回修图
        self.target.write_text("旧内容", encoding="utf-8")
        fake = FakeChat({"samples": [{"text": "你好"}]})
        with mock.patch.object(sem, "chat", fake):
            problems, log = sem.make_checker("存文件", "judge")(save_wf(str(self.target)))
        self.assertEqual(log["status"], "failed")
        self.assertIn("exists", problems[0])
        self.assertEqual(len(fake.calls), 1)  # 发现图错，不必再调用审查模型
        self.assertEqual(self.target.read_text(encoding="utf-8"), "旧内容")

    def test_web_run_writes_only_after_confirmation(self):
        body = {"workflow": save_wf(str(self.target)), "inputs": {"text": "第一行"}}
        evs = events(client.post("/api/run", json=body))
        self.assertEqual(evs[-1]["error"]["code"], "not_confirmed")
        self.assertFalse(self.target.exists())
        evs = events(client.post("/api/run", json={**body, "allow_write": ["file.write@0"]}))
        self.assertEqual(evs[-1]["status"], "completed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "第一行")

    def _read_wf(self, path: str) -> dict:
        return {"spec": "0.2", "id": "read_note", "name": "读", "intent": "",
                "inputs": {}, "outputs": {"text": {"type": "string"}},
                "nodes": [{"id": "r", "kind": "module", "module": "file.read@0", "config": {"path": path}}],
                "edges": [{"from": "r.text", "to": "$out.text"}]}

    def test_web_run_reads_only_after_confirmation(self):
        """读类模块首次运行前也要确认；确认事件带上 side_effects 和目标路径（数据层）。"""
        self.target.write_text("正文", encoding="utf-8")
        wf = self._read_wf(str(self.target))
        evs = events(client.post("/api/run", json={"workflow": wf, "inputs": {}}))
        self.assertEqual(evs[-1]["error"]["code"], "not_confirmed")
        cev = next(e for e in evs if e["type"] == "confirm")
        self.assertEqual((cev["module"], cev["side_effects"], cev["ok"]), ("file.read@0", "read", False))
        self.assertEqual(cev["target"], str(self.target))
        evs = events(client.post("/api/run", json={"workflow": wf, "inputs": {}, "allow_write": ["file.read@0"]}))
        self.assertEqual(evs[-1]["status"], "completed")
        self.assertEqual(evs[-1]["outputs"]["text"], "正文")


class WriteDiscovery(unittest.TestCase):
    """运行前就找出要确认的写模块（含引用的子流程），网页先问完再运行，不会跑到一半被拦下再重跑。"""

    def test_lists_write_modules_inside_loops_and_refs(self):
        with tempfile.TemporaryDirectory() as d:
            inner = save_wf(str(Path(d) / "x.txt"))
            (Path(d) / "inner.workflow.json").write_text(json.dumps(inner, ensure_ascii=False), encoding="utf-8")
            (Path(d) / "loop.workflow.json").write_text(json.dumps(  # 互相引用也不会死循环
                {"nodes": [{"id": "s", "kind": "subflow", "ref": "loop.workflow.json"}]}), encoding="utf-8")
            outer = {"nodes": [{"id": "s", "kind": "subflow", "ref": "inner.workflow.json"},
                               {"id": "l", "kind": "subflow", "ref": "loop.workflow.json"},
                               {"id": "gone", "kind": "subflow", "ref": "missing.workflow.json"},
                               {"id": "j", "kind": "module", "module": "text.join@0"}]}
            with mock.patch.dict(appmod.BASE_DIRS, {"saved": Path(d)}), mock.patch.object(appmod, "SAVED", Path(d)):
                self.assertEqual(client.post("/api/writes", json={"workflow": outer, "source": "saved"}).json(),
                                 ["file.write@0"])
        each = {"nodes": [{"id": "e", "kind": "foreach", "body": {"nodes": [save_wf("a.txt")["nodes"][0]]}}]}
        self.assertEqual(client.post("/api/writes", json={"workflow": each}).json(), ["file.write@0"])
        self.assertEqual(client.post("/api/writes", json={"workflow": {"nodes": []}}).json(), [])

    def test_lists_read_modules_for_confirmation(self):
        """读类模块也要确认：/api/writes 一并列出（界面按自己的模块表查 side_effects 决定措辞）。"""
        wf = {"nodes": [{"id": "r", "kind": "module", "module": "file.read@0"},
                        {"id": "w", "kind": "module", "module": "file.write@0"},
                        {"id": "j", "kind": "module", "module": "text.join@0"}]}  # text.join 无副作用，不列
        self.assertEqual(client.post("/api/writes", json={"workflow": wf}).json(),
                         ["file.read@0", "file.write@0"])

    def test_rehearsal_runs_in_a_stoppable_worker_process(self):
        """演练和真实模块一样走独立进程（可停止、超时能真正结束），调用的是 dry_run 入口。"""
        calls = []
        real = engine_mod.worker.call

        def spy(mod, func, *a, **kw):
            calls.append((mod, func))
            return real(mod, func, *a, **kw)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(engine_mod.worker, "call", spy):
            target = Path(d) / "x.txt"
            res = Engine(rehearse={"file.write@0"}).run(save_wf(str(target)), {"text": "你好"})
            self.assertEqual(res.status, "completed", res.error)
            self.assertFalse(target.exists())
        self.assertEqual(calls, [("modules.file_write.module", "dry_run")])


class ModuleDescriptions(unittest.TestCase):
    def test_dry_run_only_for_write_modules(self):
        spec = load_registry()["text.join@0"]
        data = {**spec.data, "runtime": {**spec.data["runtime"], "dry_run": "module.py:run"}}
        errs = validate_module(ModuleSpec(spec.id, spec.major, spec.path, data))
        self.assertTrue(any("dry_run" in e for e in errs), errs)

    def test_dry_run_entry_must_exist(self):
        spec = load_registry()["file.write@0"]
        data = {**spec.data, "runtime": {**spec.data["runtime"], "dry_run": "missing.py:dry_run"}}
        errs = validate_module(ModuleSpec(spec.id, spec.major, spec.path, data))
        self.assertTrue(any("missing.py" in e for e in errs), errs)


class Pipelines(unittest.TestCase):
    """新模块在独立进程里运行、按图拼接后的结果。"""

    def run_wf(self, wf, inputs):
        # 读类模块（file.read 等）现在首次运行前也要确认；这里是单元测试，一律放行
        res = Engine(confirm=lambda *a: True).run(wf, inputs)
        self.assertEqual(res.status, "completed", res.error)
        return res.outputs

    def test_keep_positive_then_sum(self):
        wf = {"spec": "0.2", "id": "pos_sum", "name": "正数求和", "intent": "",
              "inputs": {"nums": {"type": "list<integer>"}}, "outputs": {"total": {"type": "number"}},
              "nodes": [{"id": "each", "kind": "foreach", "max_items": 20, "body": {
                            "inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "boolean"}},
                            "nodes": [{"id": "pos", "kind": "module", "module": "logic.compare@0", "config": {"op": "gt", "b": 0}}],
                            "edges": [{"from": "$in.item", "to": "pos.a"}, {"from": "pos.result", "to": "$out.result"}]}},
                        {"id": "keep", "kind": "module", "module": "list.filter@0"},
                        {"id": "sum", "kind": "module", "module": "list.stats@0", "config": {"op": "sum"}}],
              "edges": [{"from": "$in.nums", "to": "each.items"}, {"from": "$in.nums", "to": "keep.items"},
                        {"from": "each.results", "to": "keep.keep"}, {"from": "keep.items", "to": "sum.items"},
                        {"from": "sum.value", "to": "$out.total"}]}
        self.assertEqual(self.run_wf(wf, {"nums": [3, -1, 0, 5]}), {"total": 8})

    def test_read_csv_split_count_and_get_field(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "表.csv"
            f.write_text("名字,分数\n小王,90\n小李,80\n", encoding="gbk")
            wf = {"spec": "0.2", "id": "rows", "name": "数行", "intent": "",
                  "inputs": {"path": {"type": "file"}, "json": {"type": "string"}},
                  "outputs": {"rows": {"type": "integer"}, "name": {"type": "any"}},
                  "nodes": [{"id": "read", "kind": "module", "module": "file.read@0"},
                            {"id": "lines", "kind": "module", "module": "text.split@0"},
                            {"id": "count", "kind": "module", "module": "list.stats@0", "config": {"op": "count"}},
                            {"id": "get", "kind": "module", "module": "data.get@0", "config": {"path": "user.name"}}],
                  "edges": [{"from": "$in.path", "to": "read.path"}, {"from": "read.text", "to": "lines.text"},
                            {"from": "lines.items", "to": "count.items"}, {"from": "count.count", "to": "$out.rows"},
                            {"from": "$in.json", "to": "get.data"}, {"from": "get.value", "to": "$out.name"}]}
            out = self.run_wf(wf, {"path": str(f), "json": '{"user": {"name": "小王"}}'})
        self.assertEqual(out, {"rows": 3, "name": "小王"})

    def test_html_text_then_find_emails(self):
        wf = {"spec": "0.2", "id": "mails", "name": "找邮箱", "intent": "",
              "inputs": {"html": {"type": "string"}}, "outputs": {"emails": {"type": "list<string>"}},
              "nodes": [{"id": "body", "kind": "module", "module": "html.text@0"},
                        {"id": "find", "kind": "module", "module": "text.find@0", "config": {"pattern": "email"}}],
              "edges": [{"from": "$in.html", "to": "body.html"}, {"from": "body.text", "to": "find.text"},
                        {"from": "find.matches", "to": "$out.emails"}]}
        html = "<p>写信给 <a href='mailto:x@a.com'>x@a.com</a></p><script>var y='hide@b.com'</script>"
        self.assertEqual(self.run_wf(wf, {"html": html}), {"emails": ["x@a.com"]})


if __name__ == "__main__":
    unittest.main()
