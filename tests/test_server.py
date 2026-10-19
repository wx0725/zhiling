import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
from core.registry import ROOT

client = TestClient(appmod.app)

FORMAT_ONLY = {"spec": "0.2", "id": "fmt_demo", "name": "转文字", "intent": "把数字写成一句话",
               "inputs": {"n": {"type": "integer"}}, "outputs": {"s": {"type": "string"}},
               "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "数字是 {value}"}}],
               "edges": [{"from": "$in.n", "to": "f.value"}, {"from": "f.text", "to": "$out.s"}]}


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(appmod, "SAVED", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_list_and_get_examples(self):
        items = client.get("/api/workflows").json()
        self.assertIn("score_message.workflow.json", [i["file"] for i in items if i["source"] == "examples"])
        wf = client.get("/api/workflows/examples/score_message.workflow.json").json()
        self.assertEqual(wf["id"], "score_message")

    def test_path_traversal_rejected(self):
        # 编码后的“/”会变成另一条路径，匹配不到任何接口；无论 400 还是 404，都不会读到文件
        self.assertIn(client.get("/api/workflows/examples/..%2F..%2Fsecret.workflow.json").status_code, (400, 404))
        self.assertEqual(client.get("/api/workflows/examples/..secret.workflow.json").status_code, 400)
        self.assertEqual(client.get("/api/workflows/other/x.workflow.json").status_code, 400)
        self.assertEqual(client.get("/api/workflows/examples/missing.workflow.json").status_code, 404)

    def test_save_valid_and_reject_invalid(self):
        r = client.post("/api/workflows", json={"workflow": FORMAT_ONLY})
        self.assertEqual(r.json(), {"source": "saved", "file": "fmt_demo.workflow.json", "id": "fmt_demo", "renamed": False})
        self.assertIn("fmt_demo.workflow.json", [i["file"] for i in client.get("/api/workflows").json()])
        bad = {**FORMAT_ONLY, "nodes": [{**FORMAT_ONLY["nodes"][0], "module": "nope.x@0"}]}
        r = client.post("/api/workflows", json={"workflow": bad})
        self.assertEqual(r.status_code, 422)

    def test_save_never_overwrites_a_different_workflow(self):
        first = client.post("/api/workflows", json={"workflow": FORMAT_ONLY}).json()
        self.assertEqual((first["file"], first["renamed"]), ("fmt_demo.workflow.json", False))
        again = client.post("/api/workflows", json={"workflow": FORMAT_ONLY}).json()
        self.assertEqual(again["file"], "fmt_demo.workflow.json")  # 内容相同：不重复存
        other = {**FORMAT_ONLY, "name": "另一个"}
        r = client.post("/api/workflows", json={"workflow": other}).json()
        self.assertEqual((r["file"], r["id"], r["renamed"]), ("fmt_demo_2.workflow.json", "fmt_demo_2", True))
        kept = client.get("/api/workflows/saved/fmt_demo.workflow.json").json()
        self.assertEqual(kept["name"], "转文字")  # 原来的没有被覆盖
        self.assertEqual(client.get("/api/workflows/saved/fmt_demo_2.workflow.json").json()["id"], "fmt_demo_2")

    def test_overwrite_own_file_when_asked(self):
        client.post("/api/workflows", json={"workflow": FORMAT_ONLY})
        edited = {**FORMAT_ONLY, "name": "改过的"}
        r = client.post("/api/workflows", json={"workflow": edited, "overwrite": True}).json()
        self.assertEqual((r["file"], r["renamed"]), ("fmt_demo.workflow.json", False))
        self.assertEqual(client.get("/api/workflows/saved/fmt_demo.workflow.json").json()["name"], "改过的")
        bad = {**edited, "edges": []}  # 覆盖也要先通过校验
        self.assertEqual(client.post("/api/workflows", json={"workflow": bad, "overwrite": True}).status_code, 422)

    def test_validate(self):
        self.assertEqual(client.post("/api/validate", json={"workflow": FORMAT_ONLY}).json(), {"errors": []})


class Streams(unittest.TestCase):
    def test_run_streams_node_events_then_result(self):
        evs = events(client.post("/api/run", json={"workflow": FORMAT_ONLY, "inputs": {"n": 7}}))
        self.assertEqual([e["type"] for e in evs], ["node_start", "node_end", "result"])
        self.assertEqual(evs[-1]["outputs"], {"s": "数字是 7"})
        self.assertEqual(evs[-1]["records"][0]["path"], "fmt_demo.f")

    def test_run_waits_for_answer(self):
        """提醒确认：运行流里先出 ask 事件，等 /api/run/{id}/answer 送来回答后才继续，回答进了模块的输出"""
        import threading
        wf = {"spec": "0.2", "id": "ask_demo", "name": "审核", "intent": "审核", "inputs": {}, "outputs": {"who": {"type": "string"}},
              "nodes": [{"id": "a", "kind": "module", "module": "user.confirm@0", "config": {"title": "请审核", "options": ["通过", "不通过"]}}],
              "edges": [{"from": "a.choice", "to": "$out.who"}]}
        got = {}

        def go():
            got["evs"] = events(client.post("/api/run", json={"workflow": wf, "inputs": {}, "run_id": "ask1"}))
        t = threading.Thread(target=go)
        t.start()
        deadline = time.time() + 10
        while "ask1" not in appmod.RUNS and time.time() < deadline:
            time.sleep(0.02)
        time.sleep(0.3)  # 等引擎走到 ask
        r = client.post("/api/run/ask1/answer", json={"path": "ask_demo.a", "answer": {"choice": "不通过", "note": "改"}})
        self.assertEqual(r.status_code, 200)
        t.join(10)
        types = [e["type"] for e in got["evs"]]
        self.assertEqual(types, ["node_start", "ask", "answered", "node_end", "result"])
        self.assertEqual(got["evs"][1]["prompt"]["title"], "请审核")
        self.assertEqual(got["evs"][-1]["outputs"], {"who": "不通过"})
        self.assertEqual(client.post("/api/run/ask1/answer", json={"path": "x", "answer": {}}).status_code, 404)  # 已结束

    def test_project_model_and_generate_override(self):
        """项目可以记下自己用的模型；生成、理清需求带 model 时用它，不带用默认"""
        p = client.post("/api/projects", json={}).json()
        try:
            r = client.put(f"/api/projects/{p['id']}", json={"model": "ollama://127.0.0.1:11434/qwen3:8b"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["model"], "ollama://127.0.0.1:11434/qwen3:8b")
            self.assertEqual(client.get(f"/api/projects/{p['id']}").json()["model"], "ollama://127.0.0.1:11434/qwen3:8b")
            self.assertEqual(client.put(f"/api/projects/{p['id']}", json={"model": "nonsense"}).status_code, 400)
            r = client.put(f"/api/projects/{p['id']}", json={"model": ""})  # 空：回到跟着默认
            self.assertIsNone(r.json()["model"])
            with mock.patch.object(appmod.llm, "chosen", return_value="ollama://127.0.0.1:11434/default"):
                self.assertEqual(appmod.model_config()["generator"], "ollama://127.0.0.1:11434/default")
                self.assertEqual(appmod.model_config("ollama://127.0.0.1:11434/mine")["generator"], "ollama://127.0.0.1:11434/mine")
                self.assertEqual(appmod.model_config("bad")["generator"], "ollama://127.0.0.1:11434/default")
        finally:
            client.delete(f"/api/projects/{p['id']}")

    def test_settings_endpoints(self):
        """设置面板的接口：思考模型、自定义服务商都存进 model_settings.json（测试里换成临时文件）"""
        with tempfile.TemporaryDirectory() as d, mock.patch.object(appmod.llm, "SETTINGS", Path(d) / "s.json"),                 mock.patch.object(appmod.llm, "ollama_models", return_value=["m1"]):
            v = client.get("/api/settings").json()
            self.assertEqual((v["think_model"], v["think_choices"], v["custom_apis"]), ("", ["m1"], []))
            v = client.put("/api/settings", json={"think_model": "m1"}).json()
            self.assertEqual(v["think_model"], "m1")
            self.assertEqual(appmod.llm.load_settings()["think_model"], "m1")
            self.assertEqual(client.put("/api/settings", json={"think_model": ""}).json()["think_model"], "")
            bad = client.post("/api/settings/custom-api", json={"key": "Bad Key", "name": "x", "base": "https://a/v1", "key_env": "A_KEY", "models": ["m"]})
            self.assertEqual(bad.status_code, 400)
            bad = client.post("/api/settings/custom-api", json={"key": "ok", "name": "x", "base": "ftp://a", "key_env": "A_KEY", "models": ["m"]})
            self.assertEqual(bad.status_code, 400)
            bad = client.post("/api/settings/custom-api", json={"key": "deepseek", "name": "x", "base": "https://a/v1", "key_env": "A_KEY", "models": ["m"]})
            self.assertEqual(bad.status_code, 400)  # 不能顶掉内置服务商
            self.assertIn("内置", bad.json()["detail"])
            v = client.post("/api/settings/custom-api", json={"key": "ok", "name": "我的", "base": "https://a/v1/", "key_env": "A_KEY", "models": ["m", " "]}).json()
            self.assertEqual(v["custom_apis"][0]["base"], "https://a/v1")
            self.assertEqual(v["custom_apis"][0]["models"], ["m"])
            self.assertIn("ok", appmod.llm.api_specs())
            self.assertEqual(client.delete("/api/settings/custom-api/nope").status_code, 404)
            v = client.delete("/api/settings/custom-api/ok").json()
            self.assertEqual(v["custom_apis"], [])
            self.assertNotIn("apis", appmod.llm.load_settings())

    def test_run_invalid_workflow(self):
        bad = {**FORMAT_ONLY, "edges": []}
        evs = events(client.post("/api/run", json={"workflow": bad, "inputs": {}}))
        self.assertEqual(evs[0]["type"], "invalid")

    def test_run_failure_reported(self):
        evs = events(client.post("/api/run", json={"workflow": FORMAT_ONLY, "inputs": {"n": "七"}}))
        self.assertEqual((evs[-1]["status"], evs[-1]["error"]["code"]), ("failed", "input_type"))

    def test_generate_streams_progress(self):
        def fake_generate(req, model, rounds, post_check, on_event, should_stop=None, spec=None):
            on_event({"type": "drafting", "round": 1})
            on_event({"type": "validated", "round": 1, "errors": [], "workflow": FORMAT_ONLY})
            return {"ok": True, "rounds": 1, "workflow": FORMAT_ONLY}
        cfg = {"generator": "g", "generator_url": "u", "judge": "j", "judge_url": "u"}
        with mock.patch.object(appmod, "model_config", return_value=cfg), \
             mock.patch.object(appmod.gen, "generate", side_effect=fake_generate):
            evs = events(client.post("/api/generate", json={"requirement": "写一句话", "check": False}))
        self.assertEqual([e["type"] for e in evs], ["start", "drafting", "validated", "done"])
        self.assertEqual(evs[-1]["workflow"]["id"], "fmt_demo")

    def test_generate_error_is_streamed(self):
        cfg = {"generator": "g", "generator_url": "u", "judge": "j", "judge_url": "u"}
        with mock.patch.object(appmod, "model_config", return_value=cfg), \
             mock.patch.object(appmod.gen, "generate", side_effect=ConnectionError("模型服务没开")):
            evs = events(client.post("/api/generate", json={"requirement": "x", "check": False}))
        self.assertEqual(evs[-1]["type"], "error")
        self.assertIn("模型服务没开", evs[-1]["message"])

    def test_empty_requirement(self):
        self.assertEqual(client.post("/api/generate", json={"requirement": "  "}).status_code, 400)

    def test_done_reports_whether_last_draft_is_valid(self):
        """F01：前端据此决定能不能把草稿交给画布。"""
        broken = {k: v for k, v in FORMAT_ONLY.items() if k != "edges"}
        def fake_generate(req, model, rounds, post_check, on_event, should_stop=None, spec=None):
            return {"ok": False, "rounds": 3, "workflow": broken, "log": [{"round": 3, "errors": ["结构错误 (根)：缺 edges"]}]}
        cfg = {"generator": "g", "generator_url": "u", "judge": "j", "judge_url": "u"}
        with mock.patch.object(appmod, "model_config", return_value=cfg), \
             mock.patch.object(appmod.gen, "generate", side_effect=fake_generate):
            done = events(client.post("/api/generate", json={"requirement": "x", "check": False}))[-1]
        self.assertEqual((done["ok"], done["valid"]), (False, False))


class Cancellation(unittest.TestCase):
    """F06：前端停止（断开连接）后，后台工作收到停止信号。"""

    def test_disconnect_sets_stop_flag(self):
        seen = threading.Event()
        stopped = threading.Event()

        def work(emit, should_stop):
            emit({"type": "start"})
            seen.set()
            for _ in range(100):
                if should_stop():
                    stopped.set()
                    return
                time.sleep(0.05)

        class Gone:  # 模拟浏览器已断开（TestClient 会读完整个响应，无法真正中途断开）
            async def is_disconnected(self):
                return seen.is_set()

        async def consume():
            it = appmod._stream(Gone(), work).body_iterator
            async for _ in it:
                pass

        asyncio.run(consume())
        self.assertTrue(seen.wait(2))
        self.assertTrue(stopped.wait(5), "断开后后台工作没有收到停止信号")


class Subflows(unittest.TestCase):
    """F15/F16：网页上按工作流所在文件夹解析子流程，并提供被引用子流程的端口。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        sub = {"spec": "0.2", "id": "hello", "name": "打招呼", "intent": "", "inputs": {"x": {"type": "string"}},
               "outputs": {"y": {"type": "string"}},
               "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "hello {value}"}}],
               "edges": [{"from": "$in.x", "to": "f.value"}, {"from": "f.text", "to": "$out.y"}]}
        (d / "hello.workflow.json").write_text(json.dumps(sub, ensure_ascii=False), encoding="utf-8")
        self.outer = {"spec": "0.2", "id": "outer", "name": "外层", "intent": "", "inputs": {"x": {"type": "string"}},
                      "outputs": {"y": {"type": "string"}}, "nodes": [{"id": "sub", "kind": "subflow", "ref": "hello.workflow.json"}],
                      "edges": [{"from": "$in.x", "to": "sub.x"}, {"from": "sub.y", "to": "$out.y"}]}
        self.patch = mock.patch.dict(appmod.BASE_DIRS, {"saved": d})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_run_resolves_ref_from_workflow_folder(self):
        evs = events(client.post("/api/run", json={"workflow": self.outer, "inputs": {"x": "you"}, "source": "saved"}))
        self.assertEqual(evs[-1]["outputs"], {"y": "hello you"})

    def test_ports_endpoint(self):
        r = client.post("/api/ports", json={"workflow": self.outer, "source": "saved"}).json()
        self.assertEqual(set(r["hello.workflow.json"]["inputs"]), {"x"})
        self.assertEqual(set(r["hello.workflow.json"]["outputs"]), {"y"})

    def test_bad_source(self):
        self.assertEqual(client.post("/api/validate", json={"workflow": self.outer, "source": "../x"}).status_code, 400)



class Page(unittest.TestCase):
    @unittest.skipUnless(appmod.WEB_DIST.is_dir(), "需要先构建前端")
    def test_index_is_never_cached(self):
        """首页被缓存时，桌面窗口会一直加载旧版脚本，重新构建后也看不到改动。"""
        r = client.get("/?wf=examples/summarize_page.workflow.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("no-store", r.headers["cache-control"])


if __name__ == "__main__":
    unittest.main()
