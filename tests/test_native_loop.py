"""原生界面的循环框（native/canvas/loop.py）：拉伸时的停住算法、框顶菜单的文字和操作、拉伸点、按住拖动整个框。
用一个最小画布（只把节点摆成图元）配真实的 Controller 和进程内后端，打开示例 summarize_many（里面有逐项循环）。"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView  # noqa: E402

from native.canvas.base import NodeItem  # noqa: E402
from native.canvas.loop import BoxMenu, LoopItem, clamp_resize, resized_rect  # noqa: E402
from native.logic.graph import NODE_W, PAD  # noqa: E402
from native.testing import wait, wait_until  # noqa: E402


class Pure(unittest.TestCase):
    def test_resized_rect(self):
        st = (10, 20, 300, 200)
        self.assertEqual(resized_rect(st, 1, 1, 5, 7), {"x": 10, "y": 20, "width": 305, "height": 207})
        self.assertEqual(resized_rect(st, -1, -1, 5, 7), {"x": 15, "y": 27, "width": 295, "height": 193})
        self.assertEqual(resized_rect(st, 0, 1, 50, 7), {"x": 10, "y": 20, "width": 300, "height": 207})

    def test_clamp_stops_at_limit(self):
        st = (0, 0, 300, 200)
        allow = lambda r: r["width"] >= 250 and r["height"] >= 120  # noqa: E731
        # 往里拉太多：每个方向各自停在刚好允许处
        self.assertEqual(clamp_resize(allow, st, 1, 1, (0, 0), (-200, -10)), (-50, -10))
        self.assertEqual(clamp_resize(allow, st, -1, -1, (0, 0), (90, 300)), (50, 80))
        self.assertEqual(clamp_resize(allow, st, 1, 1, (0, 0), (40, 40)), (40, 40))


class _Plain(NodeItem):
    def paint(self, p, o, w=None):
        pass


class _View(QGraphicsView):
    """最小画布：按 ctrl.nodes 摆图元，nodes_moved 时更新"""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.issue_map = {}
        self.setScene(QGraphicsScene(-5000, -5000, 10000, 10000))
        self.by = {}
        self.links = [(ctrl.flow_changed, self.rebuild), (ctrl.nodes_moved, self.moved), (ctrl.selection_changed, self.sel)]
        for sig, fn in self.links:
            sig.connect(fn)

    def sel(self):
        for i in self.by.values():
            i.refresh()

    def drop(self):
        for sig, fn in self.links:
            sig.disconnect(fn)

    def rebuild(self):
        self.scene().clear()
        self.by = {}
        for n in self.ctrl.nodes:
            par = self.by.get(n.get("parentId"))
            it = (LoopItem if n["type"] == "loop" else _Plain)(self, n, par)
            self.by[n["id"]] = it
            if par is None:
                self.scene().addItem(it)

    def moved(self, ids):
        nodes = {n["id"]: n for n in self.ctrl.nodes}
        for i in ids:
            if i in self.by and i in nodes:
                self.by[i].set_node(nodes[i])


class Box(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from native.api import Api
        from native.backend import Backend
        from native.controller import Controller
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.be = Backend()
        assert cls.be.start(), cls.be.error
        cls.ctrl = Controller(Api(cls.be.base))
        cls.ctrl.project = "__loop_test__"  # 假装已在一个项目里：打开示例时不新建项目（自动保存到它会 404，被忽略）
        cls.ctrl.load()
        assert wait_until(lambda: cls.ctrl.modules, 20000)

    @classmethod
    def tearDownClass(cls):
        cls.be.stop()

    def setUp(self):
        c = self.ctrl
        self.view = _View(c)
        self.view.resize(1300, 800)
        self.view.show()
        c.nodes = []
        if c.current:
            c.current["dirty"] = False  # 上一个测试改过：不要弹“改动还没保存”
        c.open({"source": "examples", "file": "summarize_many.workflow.json"})
        self.assertTrue(wait_until(lambda: any(n["type"] == "loop" for n in c.nodes), 20000))
        self.bid = next(n for n in c.nodes if n["type"] == "loop")["id"]
        it = self.view.by[self.bid]
        self.view.fitInView(it.sceneBoundingRect().adjusted(-60, -120, 160, 60), Qt.AspectRatioMode.KeepAspectRatio)
        c.select_node(self.bid)
        wait(50)
        self.menu = BoxMenu.for_view(self.view)

    def tearDown(self):
        self.view.drop()
        self.view.close()
        self.view.deleteLater()
        wait(20)

    def box(self):
        return next(n for n in self.ctrl.nodes if n["id"] == self.bid)

    def item(self):
        return self.view.by[self.bid]

    def drag_handle(self, name, delta):
        vp = self.view.viewport()
        p = self.view.mapFromScene(self.item()._handles[name].scenePos())
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        QTest.mouseMove(vp, p + delta)
        wait(20)
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p + delta)
        wait(80)

    def test_menu_texts_and_auto(self):
        m = self.menu
        self.assertTrue(m.isVisible())
        texts = [m.auto.text()] + [b.text() for b in m.findChildren(type(m.grip))]
        self.assertEqual(texts, ["收缩", "拖动", "收紧", "整理", "全选", "总览"])
        self.assertTrue(m.auto.isChecked())
        self.assertFalse(m.fit.isEnabled())
        self.assertFalse(any(h.isVisible() for h in self.item()._handles.values()))
        # 菜单在框顶边上方 14px、水平居中
        r = self.item().frame_rect()
        top = self.view.mapFromScene(self.item().mapToScene(r.center().x(), r.top()))
        self.assertEqual(m.geometry().bottom() + 1, top.y() - 14)
        self.assertLessEqual(abs(m.geometry().center().x() - top.x()), 1)
        m.auto.click()
        wait(50)
        self.assertTrue(self.box()["data"]["manual"])
        self.assertIn("w", self.ctrl.current["raw"]["layout"][self.bid])
        self.assertTrue(all(h.isVisible() for h in self.item()._handles.values()))
        self.assertTrue(m.fit.isEnabled())
        m.auto.click()
        wait(50)
        self.assertFalse(self.box()["data"]["manual"])
        self.assertNotIn("w", self.ctrl.current["raw"]["layout"][self.bid])

    def test_menu_click_keeps_selection_and_ctrl_keys_pass(self):
        QTest.mouseClick(self.menu, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(2, 2))
        wait(20)
        self.assertEqual(self.ctrl.selected, self.bid)
        z = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.menu.keyPressEvent(z)
        self.assertFalse(z.isAccepted())  # 撤销交给上层
        d = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        self.menu.keyPressEvent(d)
        self.assertTrue(d.isAccepted())

    def test_menu_hidden_when_not_selected(self):
        self.ctrl.clear_selection()
        wait(30)
        self.assertFalse(self.menu.isVisible())

    def test_resize(self):
        self.menu.auto.click()
        wait(50)
        w0 = self.box()["data"]["width"]
        self.drag_handle("right", QPoint(60, 0))
        w1 = self.ctrl.current["raw"]["layout"][self.bid]["w"]
        self.assertGreater(w1, w0)
        # 左边往外拉，再往里拉很多：停在框里节点左边正好留 PAD 处
        self.drag_handle("left", QPoint(-40, 0))
        kids = lambda: [n for n in self.ctrl.nodes if n.get("parentId") == self.bid]  # noqa: E731
        self.assertGreater(min(k["position"]["x"] for k in kids()), PAD)
        self.drag_handle("left", QPoint(500, 0))
        self.assertEqual(min(k["position"]["x"] for k in kids()), PAD)
        self.assertGreaterEqual(self.box()["data"]["width"], NODE_W)
        # 往上压扁：不能压到框里节点
        self.drag_handle("bottom", QPoint(0, -400))
        lay = self.ctrl.current["raw"]["layout"][self.bid]
        self.assertTrue(all(k["position"]["y"] + k["data"]["height"] + PAD <= lay["h"] for k in kids()))

    def test_grip_drag(self):
        before = dict(self.box()["position"])
        steps = len(self.ctrl.past)
        g = self.menu.grip
        vp = self.view.viewport()
        c = g.rect().center()
        QTest.mousePress(g, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c)
        gc = g.mapTo(vp, c)
        for i in range(1, 6):
            QTest.mouseMove(vp, gc + QPoint(0, -15 * i))
            wait(10)
        self.assertLess(self.box()["position"]["y"], before["y"])
        self.assertTrue(self.menu.isVisible())
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, gc + QPoint(0, -75))
        wait(80)
        after = self.ctrl.current["raw"]["layout"][self.bid]
        self.assertLess(after["y"], before["y"])
        self.assertEqual(len(self.ctrl.past), steps + 1)  # 一步撤销
        self.assertIsNone(self.menu._drag)
        self.ctrl.travel("undo")
        wait(30)
        self.assertEqual(self.box()["position"]["y"], before["y"])


if __name__ == "__main__":
    unittest.main()
