"""暂停、继续、终止一次运行：执行引擎的 should_pause，服务端的 run_id 和 /api/run/{run_id}/{pause|resume|stop}。
不需要大模型：引擎测试用替身，服务端测试在第一个节点（抓取一个故意慢的本机网页）跑着时暂停，停在“总结”之前再终止。"""
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 服务端控制测试抓取本机慢网页（127.0.0.1）：放行本机地址（导入期设置，模块子进程继承）
os.environ["WORKFLOW_ALLOW_LOCAL_FETCH"] = "1"

from fastapi.testclient import TestClient

import server.app as appmod
from core.engine import Engine
from core.registry import ROOT, load_registry

REG = load_registry()


def example(name):
    return json.loads((ROOT / "examples" / f"{name}.workflow.json").read_text(encoding="utf-8"))


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > end:
            return False
        time.sleep(0.02)
    return True


class _Slow(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.8)
        data = b"<title>slow</title> hello"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def fake_fetch(inputs):
    return {"text": "页面正文", "status": 200, "url": inputs["url"]}


def fake_think(inputs):
    return {"answer": "总结：" + inputs["text"]}


class EnginePause(unittest.TestCase):
    def engine(self, events, pause, stop=lambda: False):
        overrides = {"web.fetch@0": fake_fetch, "llm.think@0": fake_think}
        return Engine(REG, overrides=overrides, on_event=events.append, should_pause=pause, should_stop=stop)

    def test_pause_holds_before_next_node_then_resumes(self):
        events, flag = [], threading.Event()
        flag.set()
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("res", self.engine(events, flag.is_set).run(
            example("summarize_page"), {"url": "http://x"})))
        t.start()
        self.assertTrue(wait_for(lambda: any(e["type"] == "paused" for e in events)))
        time.sleep(0.2)
        self.assertFalse(any(e["type"] == "node_start" for e in events))  # 停在第一个节点之前
        self.assertEqual(events[0], {"type": "paused", "path": "summarize_page.fetch"})
        flag.clear()
        t.join(5)
        res = out["res"]
        self.assertEqual(res.status, "completed", res.error)
        self.assertEqual(res.outputs["summary"], "总结：页面正文")
        self.assertEqual(events[1], {"type": "resumed", "path": "summarize_page.fetch"})

    def test_stop_while_paused(self):
        events, stop = [], threading.Event()
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("res", self.engine(events, lambda: True, stop.is_set).run(
            example("summarize_page"), {"url": "http://x"})))
        t.start()
        self.assertTrue(wait_for(lambda: events))
        stop.set()
        t.join(5)
        res = out["res"]
        self.assertEqual(res.status, "failed")
        self.assertEqual(res.error["code"], "cancelled")
        self.assertEqual([e["type"] for e in events], ["paused"])  # 停止后不再发 resumed，也没有节点开始


class ServerControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Slow)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/"
        cls.client = TestClient(appmod.app)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_unknown_run_and_action(self):
        self.assertEqual(self.client.post("/api/run/nope/pause").status_code, 404)
        appmod.RUNS["known"] = {"pause": threading.Event(), "stop": threading.Event(), "seq": 0}
        try:
            self.assertEqual(self.client.post("/api/run/known/explode").status_code, 404)
            self.assertEqual(self.client.post("/api/run/known/pause").status_code, 200)
            self.assertTrue(appmod.RUNS["known"]["pause"].is_set())
            self.client.post("/api/run/known/resume")
            self.assertFalse(appmod.RUNS["known"]["pause"].is_set())
        finally:
            appmod.RUNS.pop("known", None)

    def test_late_older_command_ignored(self):
        """暂停（1）、继续（2）同时在路上、继续先到：晚到的暂停不再生效"""
        appmod.RUNS["order"] = ctl = {"pause": threading.Event(), "stop": threading.Event(), "seq": 0}
        try:
            self.client.post("/api/run/order/resume", json={"seq": 2})
            r = self.client.post("/api/run/order/pause", json={"seq": 1})
            self.assertTrue(r.json().get("stale"))
            self.assertFalse(ctl["pause"].is_set())
            self.client.post("/api/run/order/stop", json={"seq": 1})  # 终止不论先后都生效
            self.assertTrue(ctl["stop"].is_set())
        finally:
            appmod.RUNS.pop("order", None)

    def test_pause_waits_for_running_node_then_stop(self):
        rid = "test-pause-stop"
        got = {}

        def run():
            r = self.client.post("/api/run", json={"workflow": example("summarize_page"), "inputs": {"url": self.url},
                                                   "source": "examples", "run_id": rid})
            got["events"] = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(wait_for(lambda: rid in appmod.RUNS))
        self.assertEqual(self.client.post(f"/api/run/{rid}/pause").status_code, 200)
        time.sleep(1.5)  # 抓取（0.8 秒）跑完，停在“总结”之前
        self.assertIn(rid, appmod.RUNS)
        self.assertEqual(self.client.post(f"/api/run/{rid}/stop").status_code, 200)
        t.join(15)
        self.assertNotIn(rid, appmod.RUNS)  # 结束后不再登记
        kinds = [(e["type"], e.get("path", "").split(".")[-1]) for e in got["events"]]
        self.assertEqual(kinds[:3], [("node_start", "fetch"), ("node_end", "fetch"), ("paused", "think")])
        self.assertNotIn(("node_start", "think"), kinds)
        res = got["events"][-1]
        self.assertEqual(res["type"], "result")
        self.assertEqual(res["status"], "stopped")

    def test_stop_interrupts_running_node(self):
        rid = "test-stop-now"
        got = {}

        def run():
            r = self.client.post("/api/run", json={"workflow": example("summarize_page"), "inputs": {"url": self.url},
                                                   "source": "examples", "run_id": rid})
            got["events"] = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(wait_for(lambda: rid in appmod.RUNS))
        time.sleep(0.2)
        t0 = time.monotonic()
        self.client.post(f"/api/run/{rid}/stop")
        t.join(15)
        ev = got["events"]
        self.assertIn({"type": "node_failed", "path": "summarize_page.fetch", "code": "cancelled", "message": "已停止"}, ev)
        self.assertEqual(ev[-1]["status"], "stopped")
        self.assertLess(time.monotonic() - t0, 5)

    def test_stop_arriving_before_run(self):
        """终止比运行请求先到：随后到达的这次运行一个节点也不跑"""
        self.assertEqual(self.client.post("/api/run/early/stop", json={"seq": 1}).status_code, 404)
        r = self.client.post("/api/run", json={"workflow": example("summarize_page"), "inputs": {"url": self.url},
                                               "source": "examples", "run_id": "early"})
        ev = [json.loads(x) for x in r.text.splitlines() if x.strip()]
        self.assertFalse(any(e["type"] == "node_start" for e in ev))
        self.assertEqual(ev[-1]["status"], "stopped")
        self.assertNotIn("early", appmod.STOPPED_EARLY)

    def test_same_run_id_twice_rejected(self):
        appmod.RUNS["dup"] = {"pause": threading.Event(), "stop": threading.Event(), "seq": 0}
        try:
            r = self.client.post("/api/run", json={"workflow": example("summarize_page"), "inputs": {"url": self.url},
                                                   "source": "examples", "run_id": "dup"})
            self.assertEqual(r.status_code, 409)
        finally:
            appmod.RUNS.pop("dup", None)


if __name__ == "__main__":
    unittest.main()
