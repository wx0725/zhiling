"""原生桌面版的暂停、继续、终止：Controller.run_state() 的各个阶段、发给服务端的请求、终止后的状态灯和结果，
画布右上角 RunBar 按钮的启用和文字，画布上点“开始”交给运行页。用替身接口，不需要后端。"""
import os
import unittest

import shiboken6

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from native.api import Aborted, ApiError  # noqa: E402
from native.canvas.view import CanvasView  # noqa: E402
from native.controller import Controller  # noqa: E402
from native.panels.panel import RightPanel  # noqa: E402

MODULES = {"text.format@0": {"key": "text.format@0", "name": "转成文字", "category": "处理",
                             "inputs": {"value": {"type": "any"}}, "outputs": {"text": {"type": "string"}}}}
WF = {"spec": "0.2", "id": "w", "name": "w", "intent": "", "inputs": {"x": {"type": "string"}},
      "outputs": {"y": {"type": "string"}},
      "nodes": [{"id": "a", "kind": "module", "module": "text.format@0"},
                {"id": "b", "kind": "module", "module": "text.format@0"}],
      "edges": [{"from": "$in.x", "to": "a.value"}, {"from": "a.text", "to": "b.value"}, {"from": "b.text", "to": "$out.y"}],
      "layout": {"a": {"x": 0, "y": 0}, "b": {"x": 300, "y": 0}}}


class FakeApi:
    def __init__(self):
        self.calls = []
        self.aborted = 0

    def get(self, url, ok=None, err=None):
        self.calls.append(("GET", url, None, ok, err))

    def post(self, url, body, ok=None, err=None):
        self.calls.append(("POST", url, body, ok, err))

    def send(self, method, url, body=None, ok=None, err=None):
        self.calls.append((method, url, body, ok, err))

    def stream(self, url, body, on_event, on_done=None):
        self.calls.append(("STREAM", url, body, on_event, on_done))
        api = self

        class H:
            def abort(self):
                api.aborted += 1
        return H()

    def request_sync(self, *a, **k):
        return {}

    def last(self, url):
        return next(c for c in reversed(self.calls) if c[1] == url)


_KEEP = []  # 测试里建的 Controller 一直留着：交给垃圾回收时可能正赶上它自己的定时器在触发，Qt 会崩


def make():
    api = FakeApi()
    c = Controller(api)
    _KEEP.append(c)
    c.modules = MODULES
    c.ask = lambda **k: True
    c._show(WF, "new", None)
    return c, api


def start(c, api):
    c.run({"x": "hi"}, approved=[])
    _, url, body, on_event, on_done = api.last("/api/run")
    return body, on_event, on_done


class StateTest(unittest.TestCase):
    def test_pause_resume_flow(self):
        c, api = make()
        self.assertEqual(c.run_state(), "idle")
        body, ev, done = start(c, api)
        rid = body["run_id"]
        self.assertTrue(rid)
        self.assertEqual(c.run_state(), "running")
        ev({"type": "node_start", "path": "w.a"})
        c.pause_run()
        self.assertEqual(api.calls[-1][1], f"/api/run/{rid}/pause")
        self.assertEqual(c.run_state(), "pausing")  # 当前节点还在跑
        ev({"type": "node_end", "path": "w.a"})
        ev({"type": "paused", "path": "w.b"})
        self.assertEqual(c.run_state(), "paused")
        self.assertFalse(c.edge_state(next(e for e in c.edges if e["source"] == "$in"))["animated"])  # 暂停时连线不流动
        c.resume_run()
        self.assertEqual(api.calls[-1][1], f"/api/run/{rid}/resume")
        self.assertEqual(c.run_state(), "running")
        ev({"type": "resumed", "path": "w.b"})
        self.assertFalse(c.paused)
        ev({"type": "result", "status": "completed", "outputs": {"y": "hi"}, "records": [], "seconds": 0.1})
        done(None)
        self.assertEqual(c.run_state(), "done")
        self.assertIsNone(c.run_id)

    def test_pause_request_failed_reverts(self):
        c, api = make()
        start(c, api)
        c.pause_run()
        api.calls[-1][4](ApiError("这次运行已经结束", 404))
        self.assertEqual(c.run_state(), "running")
        self.assertIn("没能暂停", c.notice)

    def test_stop_with_server_result(self):
        c, api = make()
        body, ev, done = start(c, api)
        ev({"type": "node_end", "path": "w.a"})
        ev({"type": "node_start", "path": "w.b"})
        c.stop_run()
        self.assertEqual(api.calls[-1][1], f"/api/run/{body['run_id']}/stop")
        self.assertEqual(c.run_state(), "stopping")
        ev({"type": "node_failed", "path": "w.b", "code": "cancelled", "message": "已停止"})
        ev({"type": "result", "status": "stopped", "error": {"path": "w.b", "code": "cancelled", "message": "已停止"},
            "outputs": {}, "records": []})
        done(None)
        self.assertEqual(c.run_state(), "stopped")
        self.assertEqual(c.status_of("b"), {"state": "skipped", "reason": "已终止", "counts": None})  # 不算出错
        self.assertEqual(c.status_of("a")["state"], "completed")

    def test_old_failure_does_not_undo_newer_action(self):
        c, api = make()
        body, ev, _ = start(c, api)
        c.pause_run()
        first = api.calls[-1]
        c.resume_run()
        c.pause_run()
        self.assertEqual([x[2]["seq"] for x in api.calls[-3:]], [1, 2, 3])
        ev({"type": "paused", "path": "w.a"})
        first[4](ApiError("超时"))  # 第一次暂停的失败晚到
        self.assertEqual(c.run_state(), "paused")

    def test_two_failures_return_to_confirmed_state(self):
        """服务端已暂停时点继续、再点暂停，两个请求都失败（回调先后不定）：界面回到“已暂停”"""
        for order in ((0, 1), (1, 0)):
            c, api = make()
            _, ev, _ = start(c, api)
            c.pause_run()
            ev({"type": "paused", "path": "w.a"})
            c.resume_run()
            c.pause_run()
            calls = api.calls[-2:]
            for i in order:
                calls[i][4](ApiError("连不上"))
            self.assertEqual(c.run_state(), "paused")

    def test_events_are_authoritative(self):
        c, api = make()
        _, ev, _ = start(c, api)
        ev({"type": "paused", "path": "w.a"})  # 例如之前的暂停请求被服务端执行了
        self.assertEqual(c.run_state(), "paused")
        ev({"type": "resumed", "path": "w.a"})
        self.assertEqual(c.run_state(), "running")

    def test_stop_falls_back_to_disconnect(self):
        c, api = make()
        _, ev, done = start(c, api)
        ev({"type": "node_start", "path": "w.a"})
        c.stop_run()
        api.calls[-1][4](ApiError("连不上"))  # 终止请求没成功：直接断开，不等流的结束回调（连接可能卡着）
        self.assertEqual(api.aborted, 1)
        self.assertEqual(c.run_state(), "stopped")
        done(Aborted())  # 晚到的结束回调不再改动
        ev({"type": "node_end", "path": "w.a"})
        self.assertEqual(c.run_state(), "stopped")
        self.assertEqual(c.result["status"], "stopped")
        self.assertEqual(c.status_of("a")["state"], "skipped")  # 还亮着的灯熄掉

    def test_buttons_ignored_when_not_running(self):
        c, api = make()
        n = len(api.calls)
        c.pause_run()
        c.resume_run()
        c.stop_run()
        self.assertEqual(len(api.calls), n)


class RunBarTest(unittest.TestCase):
    def setUp(self):
        self.c, self.api = make()
        self.view = CanvasView(self.c)
        self.view.resize(900, 600)
        self.view.show()
        self.bar = self.view.runbar

    def tearDown(self):
        self.view.close()
        shiboken6.delete(self.view)  # 同上：趁 Controller 还在时删掉

    def test_buttons_follow_state(self):
        b, c = self.bar, self.c
        self.assertTrue(b.isVisible())
        self.assertTrue(b.start.isEnabled())
        self.assertFalse(b.pause.isEnabled())
        self.assertFalse(b.stop.isEnabled())
        self.assertFalse(b.state.isVisible())
        _, ev, done = start(c, self.api)
        self.assertFalse(b.start.isEnabled())
        self.assertTrue(b.pause.isEnabled())
        self.assertTrue(b.stop.isEnabled())
        self.assertIn("运行中", b.state.text())
        b.pause.click()
        self.assertIn("继续", b.pause.text())  # 等当前节点跑完时也能撤回暂停
        self.assertIn("等当前节点跑完", b.state.text())
        ev({"type": "paused", "path": "w.a"})
        self.assertIn("已暂停", b.state.text())
        self.assertFalse(self.view._tick.isActive())
        b.pause.click()
        self.assertTrue(self.api.calls[-1][1].endswith("/resume"))
        self.assertIn("暂停", b.pause.text())
        b.stop.click()
        self.assertFalse(b.pause.isEnabled())
        self.assertFalse(b.stop.isEnabled())
        self.assertIn("正在终止", b.state.text())
        done(Aborted())
        self.assertTrue(b.start.isEnabled())
        self.assertIn("已终止", b.state.text())

    def test_guide_moves_below_bar(self):
        self.view._place_overlays()
        self.assertGreater(self.view.guide.y(), self.bar.y() + self.bar.height() - 1)

    def test_hidden_without_workflow(self):
        self.c._clear_canvas()
        self.c.current_changed.emit()
        self.assertFalse(self.bar.isVisible())


class StartFromCanvasTest(unittest.TestCase):
    def test_start_uses_run_panel_inputs(self):
        c, api = make()
        panel = RightPanel(c)
        self.addCleanup(shiboken6.delete, panel)  # 趁 Controller 还在时删掉面板：交给垃圾回收时先后不定，后面的测试里会崩
        self.addCleanup(c.set_panel_open, c.panel_open)  # 面板展开与否记在本机设置里：测完恢复
        c.set_panel_open(False)
        panel.run_panel.set_value("x", "你好")
        c.request_run()
        api.last("/api/writes")[3]([])  # 服务端：没有要确认的写文件模块
        self.assertEqual(api.last("/api/run")[2]["inputs"], {"x": "你好"})
        self.assertFalse(c.panel_open)

    def test_bad_inputs_open_panel(self):
        c, api = make()
        c._show({**WF, "inputs": {"x": {"type": "integer"}}}, "new", None)
        panel = RightPanel(c)
        self.addCleanup(shiboken6.delete, panel)  # 趁 Controller 还在时删掉面板：交给垃圾回收时先后不定，后面的测试里会崩
        self.addCleanup(c.set_panel_open, c.panel_open)  # 面板展开与否记在本机设置里：测完恢复
        c.set_panel_open(False)
        panel.run_panel.set_value("x", "不是数")
        c.request_run()
        self.assertFalse(any(call[1] == "/api/run" for call in api.calls))
        self.assertTrue(c.panel_open)
        self.assertEqual(c.tab, "run")
        self.assertTrue(panel.run_panel.errors)



class BugCheckTest(unittest.TestCase):
    """运行页的 bug 检查、右键插孔断开连线"""

    def test_check_and_stale_reply(self):
        c, api = make()
        c.bug_check()
        self.assertTrue(c.bugs["checking"])
        ok = api.last("/api/validate")[3]
        ok({"errors": ["节点 a：必需输入 value（any）没有来源"]})
        self.assertEqual(c.bugs["items"][0]["targets"], ["a"])
        self.assertIn("「转成文字」还缺一个必填的输入", c.bugs["items"][0]["text"])
        # 检查中改了程序、旧回复晚到、再撤销回原样：不会一直卡在“检查中”
        c.bug_check()
        ok = api.last("/api/validate")[3]
        self.assertTrue(c.disconnect_port("a", "out:text"))
        ok({"errors": []})
        c.travel("undo")
        self.assertIsNone(c.bugs)

    def test_disconnect_port(self):
        c, api = make()
        self.assertEqual(c.disconnect_port("a", "out:text"), 1)
        self.assertNotIn({"from": "a.text", "to": "b.value"}, c.current["raw"]["edges"])
        self.assertEqual(len(c.current["raw"]["edges"]), 2)
        self.assertEqual(c.disconnect_port("a", "out:text"), 0)  # 已经没有线了
        c.travel("undo")
        self.assertIn({"from": "a.text", "to": "b.value"}, c.current["raw"]["edges"])

if __name__ == "__main__":
    unittest.main()
