"""桌宠接进主程序：真实数据源 ControllerRun 的各状态文字和进度；关主窗口时在运行就转到后台（运行不中断、桌宠出现），
没在运行就照常退出；出错时点桌宠打开主窗口并定位到出错节点；桌宠菜单“关闭”。用替身接口，不需要后端。"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from native.controller import Controller  # noqa: E402
from native.pet.hook import PetHook  # noqa: E402
from native.pet.source import ControllerRun  # noqa: E402
from native.testing import wait  # noqa: E402
from native.window import MainWindow  # noqa: E402
from tests.test_native_run_control import MODULES, WF, FakeApi  # noqa: E402

LOOP_WF = {**WF, "name": "每日新闻摘要"}

_KEEP = []


def make():
    api = FakeApi()
    c = Controller(api)
    c.modules = MODULES
    c.ask = lambda **k: True
    c._show(LOOP_WF, "new", None)
    _KEEP.append(c)
    return c, api


def start(c, api):
    c.run({"x": "hi"}, approved=[])
    _, _, body, on_event, on_done = api.last("/api/run")
    return on_event, on_done


class SourceTest(unittest.TestCase):
    def test_states_text_and_progress(self):
        c, api = make()
        src = ControllerRun(c)
        self.assertEqual(src.state, "idle")
        self.assertEqual(src.headline(), "现在没有在运行的工作流")
        ev, done = start(c, api)
        self.assertEqual(src.state, "running")
        self.assertEqual(src.headline(), "正在运行：准备中")
        ev({"type": "node_start", "path": "w.a"})
        c._flush_status()
        self.assertTrue(src.headline().startswith("正在运行："))
        self.assertIn("第 1 步，共 2 步", src.detail())
        self.assertIn("每日新闻摘要", src.detail())
        ev({"type": "node_end", "path": "w.a"})
        ev({"type": "node_start", "path": "w.b"})
        c._flush_status()
        self.assertIn("第 2 步，共 2 步", src.detail())
        self.assertAlmostEqual(src.progress(), 0.5)
        ev({"type": "result", "status": "completed", "outputs": {"y": "hi"}, "records": [], "seconds": 1.5})
        done(None)
        self.assertEqual(src.state, "done")
        self.assertEqual(src.headline(), "运行完成，用时 1.5 秒")
        self.assertEqual(src.progress(), 1.0)

    def test_loop_counts(self):
        c, api = make()
        src = ControllerRun(c)
        ev, _ = start(c, api)
        for i in range(3):
            ev({"type": "node_start", "path": f"w.a[{i}]"})
        ev({"type": "node_end", "path": "w.a[0]"})
        c._flush_status()
        self.assertTrue(src.headline().endswith(" 1/3"), src.headline())

    def test_error_points_at_node(self):
        c, api = make()
        src = ControllerRun(c)
        ev, done = start(c, api)
        ev({"type": "node_end", "path": "w.a"})
        ev({"type": "node_failed", "path": "w.b", "code": "timeout", "message": "接口超时"})
        ev({"type": "result", "status": "failed", "outputs": {}, "records": [],
            "error": {"code": "timeout", "message": "接口超时", "path": "w.b"}})
        done(None)
        self.assertEqual(src.state, "error")
        self.assertTrue(src.headline().startswith("运行出错：「"), src.headline())
        self.assertIn("点我去看看", src.detail())
        targets, name = src.problem()
        self.assertEqual(targets, ["b"])
        self.assertTrue(name)


class BubbleEdgeTest(unittest.TestCase):
    def test_bubble_stays_on_screen_at_left_edge(self):
        from PySide6.QtGui import QGuiApplication
        from native.pet.pet import Pet
        area = QGuiApplication.primaryScreen().availableGeometry()
        pet = Pet()
        _KEEP.append(pet)
        pet.move(area.left(), area.top())
        pet.show()
        pet.poke()
        b = pet.bubble
        b.refresh()
        self.assertGreaterEqual(b.x(), area.left() - b.SHADOW)
        self.assertGreaterEqual(b.y(), area.top() - b.SHADOW)
        self.assertLessEqual(b.x() + b.tail_x, pet.mapToGlobal(pet.rect().center()).x())  # 尾巴还在桌宠这边
        b.hide()
        pet.hide()


class HookTest(unittest.TestCase):
    def setUp(self):
        self.c, self.api = make()
        self.c.pet_background = True  # 不受这台电脑设置里“关窗后台”开关的影响
        self.win = MainWindow(self.c)
        _KEEP.append(self.win)
        self.hook = PetHook(self.c, self.win)
        self.win.pet = self.hook
        self.win.show()

    def tearDown(self):
        self.hook.pet.hide()
        self.win.hide()

    def test_close_while_running_goes_to_background(self):
        start(self.c, self.api)
        with mock.patch.object(QApplication, "quit") as quit_:
            self.win.close()
        quit_.assert_not_called()
        self.assertTrue(self.win.isHidden())
        self.assertTrue(self.hook.pet.isVisible())
        self.assertEqual(self.api.aborted, 0)       # 运行没被中断
        self.assertTrue(self.c.running)

    def test_close_when_idle_quits(self):
        with mock.patch.object(QApplication, "quit") as quit_:
            self.win.close()
        quit_.assert_called_once()
        self.assertFalse(self.hook.pet.isVisible())

    def test_click_on_error_opens_and_locates(self):
        ev, done = start(self.c, self.api)
        with mock.patch.object(QApplication, "quit"):
            self.win.close()
        ev({"type": "node_failed", "path": "w.b", "code": "timeout", "message": "接口超时"})
        ev({"type": "result", "status": "failed", "outputs": {}, "records": [],
            "error": {"code": "timeout", "message": "接口超时", "path": "w.b"}})
        done(None)
        self.hook.pet.poke()                        # 出错时点一下
        self.assertFalse(self.win.isHidden())
        self.assertFalse(self.hook.pet.isVisible())
        wait(300)
        self.assertEqual(self.c.selected, "b")
        self.assertEqual(self.c.tab, "run")

    def test_stopped_in_background_still_paints(self):
        ev, done = start(self.c, self.api)
        self.c.stop_run()
        with mock.patch.object(QApplication, "quit"):
            self.win.close()
        ev({"type": "node_failed", "path": "w.a", "code": "cancelled", "message": "已停止"})
        done(None)
        self.assertEqual(ControllerRun(self.c).state, "idle")
        self.hook.pet.grab()                        # 画一帧：没有在运行时（灰色小灯）也能画

    def test_alt_f4_on_pet_goes_through_quit(self):
        start(self.c, self.api)
        with mock.patch.object(QApplication, "quit"):
            self.win.close()
        self.c.ask = lambda **k: False
        with mock.patch.object(QApplication, "quit") as quit_:
            self.hook.pet.close()
        quit_.assert_not_called()
        self.assertTrue(self.hook.pet.isVisible())  # 取消：桌宠留着
        self.c.ask = lambda **k: True
        with mock.patch.object(QApplication, "quit") as quit_:
            self.hook.pet.close()
        quit_.assert_called_once()

    def test_menu_close_asks_while_running(self):
        start(self.c, self.api)
        self.c.ask = lambda **k: False
        with mock.patch.object(QApplication, "quit") as quit_:
            self.hook.quit()
        quit_.assert_not_called()
        self.c.ask = lambda **k: True
        with mock.patch.object(QApplication, "quit") as quit_:
            self.hook.quit()
        quit_.assert_called_once()
        self.assertEqual(self.api.aborted, 1)       # 确认后才终止运行


if __name__ == "__main__":
    unittest.main()
