"""原生桌面版审查修复的回归测试：编辑后旧运行作废、拉伸中撤销、关窗口前保存遇到 409 重试、小地图拖动取景固定、长说明截断不卡。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from native.api import ApiError  # noqa: E402
from native.controller import Controller  # noqa: E402

app = QApplication.instance() or QApplication([])


class _Api:
    """只记下请求的假接口；request_sync 按给定的顺序返回或抛错"""

    def __init__(self, sync=()):
        self.sync = list(sync)
        self.calls = []

    def get(self, *a, **k):
        pass

    def post(self, *a, **k):
        pass

    def send(self, *a, **k):
        pass

    def request_sync(self, method, url, body=None, timeout=10):
        self.calls.append((method, url, body))
        r = self.sync.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


MODULES = {"text.format@0": {"key": "text.format@0", "name": "转成文字", "category": "处理",
                             "inputs": {"value": {"type": "any"}}, "outputs": {"text": {"type": "string"}}}}
WF = {"spec": "0.2", "id": "w", "name": "w", "intent": "", "inputs": {"x": {"type": "string"}},
      "outputs": {"y": {"type": "string"}},
      "nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {
          "inputs": {"item": {"type": "string"}}, "outputs": {"result": {"type": "string"}},
          "nodes": [{"id": "f", "kind": "module", "module": "text.format@0"}],
          "edges": [{"from": "$in.item", "to": "f.value"}, {"from": "f.text", "to": "$out.result"}]}}],
      "edges": [], "layout": {"each": {"x": 200, "y": 0, "w": 600, "h": 300}}}


def make():
    c = Controller(_Api())
    c.modules = MODULES
    c.ask = lambda **k: True
    c._show(WF, "new", None)
    return c


class Fixes(unittest.TestCase):
    def test_edit_drops_running_run(self):
        c = make()

        class H:
            aborted = False

            def abort(self):
                H.aborted = True
        c._run_handle = H()
        c.running = True
        token = c.run_seq.n
        self.assertTrue(c.set_meta({"name": "改了"}))
        self.assertFalse(c.run_seq.is_current(token))  # 旧运行晚到的事件不再认
        self.assertFalse(c.running)
        self.assertTrue(H.aborted)

    def test_undo_during_resize_cancels_it(self):
        c = make()
        self.assertTrue(c.set_meta({"name": "先改一次"}))  # 留一步可以撤销
        box = next(n for n in c.nodes if n["type"] == "loop")
        bid = box["id"]
        c.box_resize_start(bid)
        p, d = box["position"], box["data"]
        big = {"x": p["x"], "y": p["y"], "width": d["width"] + 200, "height": d["height"]}
        self.assertTrue(c.box_resize_move(bid, big))
        c.travel("undo")
        undone = c.current["raw"]
        c.box_resize_end(bid, big)
        self.assertIs(c.current["raw"], undone)  # 撤销后的程序没有被拉伸结果盖掉
        self.assertIsNone(c._resize)

    def test_shutdown_save_retries_on_409(self):
        c = make()
        c.api = _Api([ApiError("旧", 409), {"rev": 10 ** 18}, {}])
        c._unsynced = {"p1": {"body": {"workflow": WF}, "rev": 5}}
        self.assertEqual(c.flush_store_sync(), [])
        self.assertEqual([m for m, _, _ in c.api.calls], ["PUT", "GET", "PUT"])
        self.assertGreater(c.api.calls[2][2]["rev"], 10 ** 18)
        self.assertEqual(c._unsynced, {})

    def test_shutdown_reports_failures(self):
        c = make()
        c.api = _Api([ApiError("磁盘满了", 500)])
        c._unsynced = {"p1": {"body": {}, "rev": 1}}
        c.projects = [{"id": "p1", "name": "我的项目"}]
        self.assertEqual(c.flush_store_sync(), [("我的项目", "磁盘满了")])
        asked = []
        c.api = _Api([ApiError("磁盘满了", 500)])
        c.ask = lambda **k: asked.append(k) or False
        self.assertFalse(c.shutdown())  # 使用者选了先不关
        self.assertIn("我的项目", asked[0]["message"])

    def test_shutdown_commits_focused_input_first(self):
        c = make()
        c.project = "p1"
        c.current["project"] = "p1"
        c.commit_pending.connect(lambda: c.set_meta({"name": "还在输入框里"}))  # 右侧面板会这样提交焦点输入框
        c.api = _Api([{}])
        self.assertTrue(c.shutdown())
        self.assertEqual(c.api.calls[0][2]["workflow"]["name"], "还在输入框里")

    def test_long_intent_clamp_is_fast(self):
        import time
        from native.side.sidebar import clamp_lines
        lab = QLabel()
        lab.resize(180, 40)
        t = time.perf_counter()
        out = clamp_lines(lab, "很长的说明" * 2000)
        self.assertLess(time.perf_counter() - t, 0.3)
        self.assertEqual(out.count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
