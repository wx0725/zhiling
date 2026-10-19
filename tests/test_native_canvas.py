"""原生画布（native/canvas）：按节点建图元、连线条数、拖动交给 Controller、拖线连接的校验、视野计算。
运行：QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m unittest tests.test_native_canvas"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["WORKFLOW_ALLOW_LOCAL_FETCH"] = "1"  # 测试用本机慢网页：放行本机地址（抓取网页默认拒绝内网）

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from native.api import Api  # noqa: E402
from native.backend import Backend  # noqa: E402
from native.canvas import overlays  # noqa: E402
from native.canvas.items import EdgeItem, ModuleItem, TerminalItem, bezier  # noqa: E402
from native.canvas.view import CanvasView  # noqa: E402
from native.controller import Controller  # noqa: E402
from native.logic import collide, edit as ed  # noqa: E402
from native.testing import wait, wait_until  # noqa: E402


class CanvasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = Backend()
        assert cls.backend.start(), cls.backend.error
        cls.ctrl = Controller(Api(cls.backend.base))
        cls.ctrl.ask = lambda **kw: True  # 不弹确认框
        cls.ctrl.project = "__canvas_test__"  # 假装已在一个项目里：打开示例时不新建项目（自动保存到它会 404，被忽略）
        cls.view = CanvasView(cls.ctrl)
        cls.view.resize(1100, 700)
        cls.view.show()
        cls.ctrl.load()
        assert wait_until(lambda: cls.ctrl.modules and cls.ctrl.items, 20000)

    @classmethod
    def tearDownClass(cls):
        cls.view.close()
        cls.backend.stop()

    def open(self, file):
        c = self.ctrl
        c.open({"source": "examples", "file": file})
        self.assertTrue(wait_until(lambda: c.current and c.current.get("file") == file and self.view.node_items, 20000))
        wait(100)

    def screen(self, item, pt):
        return self.view.mapFromScene(item.mapToScene(pt))

    # ---------- 画图 ----------
    def test_items_and_edges(self):
        self.open("summarize_many.workflow.json")
        v, c = self.view, self.ctrl
        self.assertEqual(set(v.node_items), {n["id"] for n in c.nodes})
        self.assertEqual(len(v.edge_items), len(c.edges))
        for n in c.nodes:
            it = v.node_items[n["id"]]
            if n["type"] == "module":
                self.assertIsInstance(it, ModuleItem)
            elif n["type"] == "terminal":
                self.assertIsInstance(it, TerminalItem)
            if n.get("parentId"):  # 框里的节点是框的子图元，位置相对框
                self.assertIs(it.parentItem(), v.node_items[n["parentId"]])
            self.assertEqual((it.pos().x(), it.pos().y()), (n["position"]["x"], n["position"]["y"]))
        # 循环体里的线挂在框上
        inner = [e for e in c.edges if e["data"]["scope"]]
        self.assertTrue(inner)
        for e in inner:
            self.assertIs(v.edge_items[e["id"]].parentItem(), v.node_items["/".join(e["data"]["scope"])])
        # 每个连线端点都落在插孔上
        e = c.edges[0]
        ei = v.edge_items[e["id"]]
        start = ei.mapToScene(ei._path.pointAtPercent(0))
        want = v.node_items[e["source"]].handle_scene_pos(e["sourceHandle"])
        self.assertAlmostEqual(start.x(), want.x(), 3)
        self.assertAlmostEqual(start.y(), want.y(), 3)
        self.assertIsNotNone(v.issue_map)

    def test_no_smart_viewport_update(self):
        # SmartViewportUpdate 在快速拖动循环框时漏掉旧位置，留下残影（在真实窗口里截屏对比复现过；offscreen 下看不到，只能锁住设置）
        from PySide6.QtWidgets import QGraphicsView
        self.assertEqual(self.view.viewportUpdateMode(), QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)

    def test_rebuild_reuses_items_and_nodes_moved(self):
        self.open("summarize_page.workflow.json")
        v, c = self.view, self.ctrl
        before = dict(v.node_items)
        c.flow_changed.emit()
        self.assertTrue(all(v.node_items[k] is before[k] for k in before))
        nid = next(n["id"] for n in c.nodes if n["type"] == "module")
        c.nodes = [{**n, "position": {"x": n["position"]["x"] + 50, "y": n["position"]["y"]}} if n["id"] == nid else n for n in c.nodes]
        c.nodes_moved.emit([nid])
        n = next(n for n in c.nodes if n["id"] == nid)
        self.assertEqual(v.node_items[nid].pos().x(), n["position"]["x"])
        e = next(e for e in c.edges if e["target"] == nid)
        ei = v.edge_items[e["id"]]
        end = ei.mapToScene(ei._path.pointAtPercent(1))
        self.assertAlmostEqual(end.x(), v.node_items[nid].handle_scene_pos(e["targetHandle"]).x(), 3)
        c.flow_changed.emit()  # 位置还原

    # ---------- 点选、拖动 ----------
    def test_click_select_and_clear(self):
        self.open("summarize_page.workflow.json")
        v, c = self.view, self.ctrl
        nid = next(n["id"] for n in c.nodes if n["type"] == "module")
        it = v.node_items[nid]
        p = self.screen(it, QPointF(100, 30))
        QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        self.assertEqual(c.selected, nid)
        self.assertTrue(it.is_selected())
        far = v.mapFromScene(it.mapToScene(QPointF(100, -400)))
        QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, far)
        self.assertIsNone(c.selected)

    def test_drag_calls_controller(self):
        self.open("summarize_page.workflow.json")
        v, c = self.view, self.ctrl
        calls = []
        orig = (c.drag_start, c.drag_move, c.drag_stop)
        c.drag_start = lambda: calls.append(("start",))
        c.drag_move = lambda pos: calls.append(("move", pos))
        c.drag_stop = lambda ids: calls.append(("stop", ids))
        try:
            nid = next(n["id"] for n in c.nodes if n["type"] == "module")
            n = next(n for n in c.nodes if n["id"] == nid)
            it = v.node_items[nid]
            z = v.zoom()
            p = self.screen(it, QPointF(100, 30))
            vp = v.viewport()
            QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
            QTest.mouseMove(vp, p + QPoint(20, 0))
            QTest.mouseMove(vp, p + QPoint(40, 30))
            QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(40, 30))
            wait(50)
        finally:
            c.drag_start, c.drag_move, c.drag_stop = orig
        self.assertEqual(calls[0], ("start",))
        moves = [x[1] for x in calls if x[0] == "move"]
        self.assertEqual(len(moves), 2)
        last = moves[-1][nid]
        self.assertAlmostEqual(last["x"], n["position"]["x"] + 40 / z, 3)
        self.assertAlmostEqual(last["y"], n["position"]["y"] + 30 / z, 3)
        self.assertEqual(calls[-1], ("stop", [nid]))
        # 拖动中图元自己不动：位置只听 nodes_moved
        self.assertEqual(it.pos().x(), n["position"]["x"])

    def test_drag_node_into_loop_and_back_out(self):
        """框外的节点拖到循环框上：框亮起、松手放进框里（程序里挪进 body），可撤销；节点页“移出循环框”再挪出来"""
        self.open("summarize_many.workflow.json")
        c = self.ctrl
        self.assertTrue(c.edit(lambda wf: ed.add_node(wf, [], {"kind": "module", "module": "text.format@0"}, {"x": 0, "y": 900}),
                               None))
        wait_until(lambda: any(n["id"] == "format" for n in c.nodes), 5000)
        box = next(n for n in c.nodes if n["id"] == "each")
        br = collide.rect_of(box)
        node = next(n for n in c.nodes if n["id"] == "format")
        nr = collide.rect_of(node)
        target = {"x": br["x"] + br["w"] / 2 - nr["w"] / 2, "y": br["y"] + br["h"] / 2 - nr["h"] / 2}
        c.drag_start()
        c.drag_move({"format": target})
        self.assertEqual(c.drop_box, "each")
        self.assertEqual(next(n for n in c.nodes if n["id"] == "format")["position"], target)  # 框不挡路
        c.drag_stop(["format"])
        wait(100)
        self.assertIsNone(c.drop_box)
        raw = c.current["raw"]
        self.assertNotIn("format", [n["id"] for n in raw["nodes"]])
        body = next(n for n in raw["nodes"] if n["id"] == "each")["body"]
        self.assertIn("format", [n["id"] for n in body["nodes"]])
        self.assertEqual(c.selected, "each/format")
        self.assertIn("已放进循环框", c.notice)
        self.assertTrue(c.node_edits()["move_out"])
        c.node_edits()["move_out"]()
        wait(100)
        raw = c.current["raw"]
        self.assertIn("format", [n["id"] for n in raw["nodes"]])
        self.assertEqual(c.selected, "format")
        c.travel("undo")
        self.assertIn("format", [n["id"] for n in next(n for n in c.current["raw"]["nodes"] if n["id"] == "each")["body"]["nodes"]])
        # 拖过框但松手时中心不在框里：照常放在外面
        c.travel("undo")
        c.drag_start()
        c.drag_move({"format": {"x": br["x"] - nr["w"] - 200, "y": br["y"]}})
        self.assertIsNone(c.drop_box)
        c.drag_stop(["format"])
        self.assertIn("format", [n["id"] for n in c.current["raw"]["nodes"]])

    def test_drag_multi_group(self):
        self.open("summarize_many.workflow.json")
        v, c = self.view, self.ctrl
        c.box_select_kids("each")
        kids = sorted(c.multi)
        got = []
        orig = c.drag_move
        c.drag_move = lambda pos: got.append(pos)
        try:
            kid = next(k for k in kids if isinstance(v.node_items[k], ModuleItem))
            p = self.screen(v.node_items[kid], QPointF(100, 30))
            vp = v.viewport()
            QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
            QTest.mouseMove(vp, p + QPoint(10, 10))
            QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + QPoint(10, 10))
            wait(200)
        finally:
            c.drag_move = orig
        self.assertEqual(sorted(got[-1]), kids)

    # ---------- 拖线 ----------
    def test_connect_validation_and_drag(self):
        self.open("summarize_many.workflow.json")
        v, c = self.view, self.ctrl
        src = v.node_items["$in"]  # 外层的“输入”
        h = next(h for h in src.handles() if h[2] == "out")
        pr = {"nid": "$in", "handle": h[0], "jack": "out"}
        # 框里节点的插孔不能连（不同层）
        inner = next(it for k, it in v.node_items.items() if k.startswith("each/") and isinstance(it, ModuleItem))
        ih = next(x for x in inner.handles() if x[2] == "in")
        self.assertIsNone(v.candidate(pr, inner.mapToScene(ih[1])))
        # 循环框的 in:items 在同一层：能连
        loop = v.node_items["each"]
        lp = loop.handle_scene_pos("in:items")
        got = v.candidate(pr, lp + QPointF(10, 5))  # 半径内吸附
        self.assertIsNotNone(got)
        self.assertEqual(got[0], {"source": "$in", "sourceHandle": h[0], "target": "each", "targetHandle": "in:items"})
        self.assertIsNone(v.candidate(pr, lp + QPointF(60, 60)))  # 太远
        self.assertFalse(Controller.can_connect({"source": "a", "sourceHandle": "out:x", "target": "a", "targetHandle": "in:y"}))

        # 用鼠标从插孔拖到插孔：调 ctrl.connect
        sent = []
        orig = c.connect
        c.connect = lambda conn: sent.append(conn)
        try:
            vp = v.viewport()
            a = v.mapFromScene(src.handle_scene_pos(h[0]))
            b = v.mapFromScene(lp)
            QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
            QTest.mouseMove(vp, (a + b) / 2)
            self.assertTrue(v._draft.isVisible())
            QTest.mouseMove(vp, b + QPoint(3, 2))
            QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b + QPoint(3, 2))
            wait(50)
        finally:
            c.connect = orig
        self.assertFalse(v._draft.isVisible())
        self.assertEqual(sent, [{"source": "$in", "sourceHandle": h[0], "target": "each", "targetHandle": "in:items"}])

    def test_edge_click_selects_edge(self):
        self.open("summarize_page.workflow.json")
        v, c = self.view, self.ctrl
        def mid(e):
            ei = v.edge_items[e["id"]]
            return ei.mapToScene(ei._path.pointAtPercent(0.5))
        e = next(e for e in c.edges if v._hit(mid(e))[0] == "edge")  # 中点没被节点盖住的线
        ei = v.edge_items[e["id"]]
        p = v.mapFromScene(mid(e) + QPointF(0, 6))  # 偏开一点也算点中（可点宽度 20）
        QTest.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        self.assertEqual((c.selected_edge or {}).get("id"), e["id"])
        self.assertTrue(ei.selected)
        c.clear_selection()

    def test_pause_then_stop_real_run(self):
        """真实后端：抓取一个慢网页时点暂停，停在“总结”之前（不会去调大模型），再点终止"""
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Slow(BaseHTTPRequestHandler):
            def do_GET(self):
                time.sleep(0.8)
                data = b"<title>slow</title> hi"
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.open("summarize_page.workflow.json")
            v, c, bar = self.view, self.ctrl, self.view.runbar
            c.run({"url": f"http://127.0.0.1:{srv.server_port}/"}, approved=[])
            self.assertTrue(wait_until(lambda: c.status_of("fetch")["state"] == "running", 10000))
            bar.pause.click()
            self.assertEqual(c.run_state(), "pausing")
            self.assertTrue(wait_until(lambda: c.run_state() == "paused", 10000))
            self.assertEqual(c.status_of("fetch")["state"], "completed")
            self.assertEqual(c.status_of("think")["state"], "idle")
            self.assertFalse(v._tick.isActive())
            bar.stop.click()
            self.assertTrue(wait_until(lambda: c.run_state() == "stopped", 10000))
            self.assertEqual(c.status_of("think")["state"], "idle")
            self.assertTrue(bar.start.isEnabled())
        finally:
            srv.shutdown()
            srv.server_close()
            c.result = None
            c.status_changed.emit()

    def test_run_state_styles(self):
        self.open("summarize_page.workflow.json")
        v, c = self.view, self.ctrl
        mod = next(n["id"] for n in c.nodes if n["type"] == "module")
        c.node_status = {mod: {"state": "running"}}
        c.running = True
        c.status_changed.emit()
        self.assertTrue(v._tick.isActive())
        out = next(e for e in c.edges if e["source"] == "$in")
        self.assertTrue(v.edge_items[out["id"]].state["animated"])
        c.running = False
        c.node_status = {}
        c.result = {"status": "completed", "records": [], "outputs": {}}
        c.status_changed.emit()
        self.assertFalse(v._tick.isActive())
        dead = next(e for e in c.edges if e["source"] == mod)
        self.assertTrue(v.edge_items[dead["id"]].state["dead"])
        c.result = None
        c.status_changed.emit()

    # ---------- 视野 ----------
    def test_viewport_math(self):
        v = self.view
        v.set_viewport(0.5, 100, 40)
        z, x, y = v.get_viewport()
        self.assertAlmostEqual(z, 0.5)
        self.assertAlmostEqual(x, 100, delta=1)
        self.assertAlmostEqual(y, 40, delta=1)
        v.set_viewport(10, 0, 0)
        self.assertAlmostEqual(v.zoom(), 2.0)  # 最大 2 倍
        z, x, y = overlays.fit_viewport(QRectF(0, 0, 1000, 500), 1100, 700)
        self.assertAlmostEqual(z, min(1100 / 1080, 700 / 540, 1.1))
        # 太宽放不下：看得清字的 0.75 倍，从左边开始
        z, x, y = overlays.initial_view(QRectF(0, 0, 4000, 300), 1100, 700)
        self.assertEqual((z, x), (0.75, 32))
        self.open("summarize_page.workflow.json")
        v.fit_items(None)
        b = v.graph_bounds()
        self.assertTrue(v.visible_scene_rect().contains(b))
        c = v.view_center_scene()
        self.assertAlmostEqual(c["x"], b.center().x(), delta=2)

    def test_bezier_like_react_flow(self):
        p = bezier(QPointF(0, 0), QPointF(100, 50))
        self.assertEqual(p.elementAt(1).x, 50)  # 控制点各离开 0.5 × 距离
        self.assertEqual(p.elementAt(2).x, 50)
        q = bezier(QPointF(100, 0), QPointF(0, 50))  # 往回连：curvature 0.25 × 25 × √距离
        self.assertAlmostEqual(q.elementAt(1).x, 100 + 0.25 * 25 * 10)
        self.assertIsInstance(EdgeItem.HIT, int)


if __name__ == "__main__":
    unittest.main()
