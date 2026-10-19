"""原生桌面版的首次启动引导（native/onboarding）：页面跳转、各页的各种状态、在不同窗口大小下文字不被截断、不横向溢出，
以及装进主窗口后的打开和回到主程序。后端用替身（FakeService），回调立即执行；主窗口用真实的本机后端，但不写设置文件。"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QRadioButton, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from native.theme import palette, qss  # noqa: E402
from native.onboarding import flow  # noqa: E402
from native.onboarding.detect import gpu_line  # noqa: E402
from native.testing import wait  # noqa: E402

app.setPalette(palette())
app.setStyleSheet(qss())
GIB = 1 << 30

GPUS = [
    {"name": "外接显卡 A", "vendor": "NVIDIA", "kind": "external", "vram": 24 * GIB, "shared": False, "driver": "1.0",
     "ok": True, "problem": 0},
    {"name": "内置独显 B", "vendor": "NVIDIA", "kind": "discrete", "vram": 8 * GIB, "shared": False, "driver": "1.0",
     "ok": True, "problem": 0},
    {"name": "集成显卡 C", "vendor": "Intel", "kind": "integrated", "vram": None, "shared": True, "driver": "2.0",
     "ok": True, "problem": 0},
    {"name": "停用的显卡 D", "vendor": "AMD", "kind": "unknown", "vram": None, "shared": False, "driver": "",
     "ok": False, "problem": 22},
]
FIT = {"tier": "综合平衡", "name": "模型 X", "tag": "x:1b", "params": "1B", "quant": "Q4", "download_text": "1 GB",
       "disk_text": "约 1.1 GB", "install_dir": "某个文件夹", "source": "https://example.invalid", "needs_ollama": True,
       "why": "原因写在这里。", "vram": 2, "ram": 4, "download": 1}


class FakeService:
    """和 OnboardingService 同样的方法；结果由测试设置，回调立即执行，调用都记下来"""

    def __init__(self):
        self.calls = []
        self.providers_data = {"apis": [{"id": "p1", "name": "服务商一", "console": "https://example.invalid/keys",
                                         "models": ["m1"], "has_key": False}],
                               "clis": [{"id": "c1", "name": "订阅一", "exe": "c1", "login": "运行 c1 login"}],
                               "keystore": True}
        self.test_result = ("ok", ["m1", "m2"])
        self.cli_info = {"found": True, "logged_in": None}
        self.local = {"runtimes": [{"name": "Ollama", "found": True, "detail": "本机"}],
                      "models": [{"id": "ollama://h/a", "name": "a", "runtime": "Ollama", "params": "8B", "quant": "Q4",
                                  "ctx": "32K", "size": "5 GB"},
                                 {"id": "api://lmstudio/b", "name": "b", "runtime": "LM Studio"}]}
        self.probes = {"ollama://h/a": {"result": "pass", "score": "5/5", "detail": ""},
                       "api://lmstudio/b": {"result": "fail", "score": "", "detail": "内存不够"}}
        self.scan_result = {"hw": {"cpu": "处理器", "gpus": GPUS, "ram": "32 GB", "disk": "500 GB", "os": "系统",
                                   "runtime": "未安装 Ollama"}, "fits": [FIT], "reasons": []}
        self.install_result = ("ok", "ollama://h/x:1b")
        self.test_ok = True
        self.installing = None

    def providers(self, ok, err):
        ok(self.providers_data)

    def test_api(self, pid, key, ok, err):
        self.calls.append(("test_api", pid, key))
        kind, val = self.test_result
        ok(val) if kind == "ok" else err(kind, val)

    def check_cli(self, cid, ok, err):
        ok(self.cli_info)

    def save(self, model_id, key, ok, err):
        self.calls.append(("save", model_id, key))
        ok()

    def finish(self, ok, err):
        self.calls.append(("finish",))
        ok()

    def detect(self, ok, err):
        ok(self.local)

    def probe(self, model_id, ok, err):
        ok(self.probes[model_id])

    def scan(self, on_item, ok, err):
        for k, v in self.scan_result["hw"].items():
            on_item(k, v)
        ok(self.scan_result["hw"])

    def recommend(self, hw):
        return {"fits": self.scan_result["fits"], "reasons": self.scan_result["reasons"]}

    def install(self, model, on_step, ok, err):
        self.calls.append(("install", model["tag"]))
        kind, val = self.install_result
        if kind == "pending":
            self.installing = model
            return
        for step in ("runtime", "download", "verify", "config"):
            on_step(step, 100, "")
        ok(val) if kind == "ok" else err("download", val)

    def cancel(self):
        self.calls.append(("cancel",))
        self.installing = None

    def run_test(self, model_id, ok, err):
        ok() if self.test_ok else err("跑不起来")


def click(widget, text):
    for b in widget.findChildren(QPushButton):
        if b.text() == text and b.isVisibleTo(widget) and b.isEnabled():
            b.click()
            wait(10)
            return b
    raise AssertionError(f"找不到可点的按钮：{text}")


def texts(widget):
    return " ".join(lab.text() for lab in widget.findChildren(QLabel) if lab.isVisibleTo(widget))


class FlowBase(unittest.TestCase):
    def make(self, start="A", svc=None):
        self.svc = svc or FakeService()
        self.f = flow.Onboarding(self.svc, start)
        self.done = []
        self.f.finished.connect(lambda: self.done.append(1))
        self.f.resize(1280, 800)
        self.f.show()
        wait(20)
        return self.f

    def tearDown(self):
        self.f.close()
        self.f.deleteLater()

    def page(self):
        return self.f.pages[self.f.current]


class Flow(FlowBase):
    def test_routes(self):
        f = self.make()
        click(self.page(), "了解详情")
        self.assertEqual(f.current, "F")
        click(self.page(), "返回")
        self.assertEqual(f.current, "A")
        click(self.page(), "开始使用")
        self.assertEqual(f.current, "B")
        for title, target in (("使用云端 AI", "C"), ("使用已有的本地 AI", "D"), ("检测我的电脑", "E")):
            card = next(w for w in self.page().findChildren(QWidget) if w.accessibleName() == title)
            card.clicked.emit()
            self.assertEqual(f.current, target)
            click(self.page(), "返回")
            self.assertEqual(f.current, "B")
        click(self.page(), "返回")
        self.assertEqual(f.current, "A")

    def test_manual_build_finishes(self):
        f = self.make("B")
        next(w for w in self.page().findChildren(QWidget) if w.accessibleName() == "直接手动搭建").clicked.emit()
        self.assertEqual(self.done, [1])
        self.assertIn(("finish",), self.svc.calls)
        self.assertEqual(f.current, "B")

    def test_routes_outside_rules_are_ignored(self):
        f = self.make("A")
        f.pages["C"].go.emit("main")  # 不是当前页发的
        f.pages["A"].go.emit("E")  # A 页没有这条路
        self.assertEqual((f.current, self.done), ("A", []))

    def test_cloud_failure_then_success_saves_key(self):
        svc = FakeService()
        svc.test_result = ("key", "401 bad key")
        self.make("C", svc)
        page = self.page()
        key = page.findChildren(QLineEdit)[0]
        self.assertFalse(click_able(page, "测试连接"))
        key.setText("sk-123456")
        click(page, "测试连接")
        self.assertIn("API Key 不对", texts(page))
        self.assertIn("401 bad key", texts(page))
        svc.test_result = ("ok", ["m1", "m2"])
        click(page, "测试连接")
        radios = [r for r in page.findChildren(QRadioButton) if r.isVisibleTo(page)]
        self.assertEqual([r.text() for r in radios], ["m1", "m2"])
        radios[1].setChecked(True)
        click(page, "保存并进入主程序")
        self.assertIn(("save", "api://p1/m2", "sk-123456"), svc.calls)
        self.assertEqual(self.done, [1])

    def test_cloud_saved_key_can_be_tested_and_keystore_missing(self):
        svc = FakeService()
        svc.providers_data["apis"][0]["has_key"] = True
        svc.providers_data["keystore"] = False
        self.make("C", svc)
        page = self.page()
        click(page, "测试连接")  # 没填新的也能测
        self.assertIn(("test_api", "p1", ""), svc.calls)
        self.assertIn("没有可用的凭据管理器", texts(page))
        self.assertTrue(click_able(page, "保存并进入主程序"))  # 用已有（环境变量里）的 Key：只记下模型
        page.findChildren(QLineEdit)[0].setText("sk-new")
        click(page, "测试连接")
        self.assertFalse(click_able(page, "保存并进入主程序"))  # 新填的 Key 存不了

    def test_cloud_subscription(self):
        svc = FakeService()
        self.make("C", svc)
        page = self.page()
        click(page, "订阅一")
        self.assertIn("没法自动确认是否已经登录", texts(page))
        click(page, "使用它并进入主程序")
        self.assertIn(("save", "cli://c1", None), svc.calls)
        svc.cli_info = {"found": False, "logged_in": None}
        self.f.show_page("C")
        click(page, "服务商一")
        click(page, "订阅一")
        self.assertIn("没有找到命令行 c1", texts(page))

    def test_local_probe_states_and_choose(self):
        self.make("D")
        page = self.page()
        self.assertEqual(len(page.cards), 2)
        a, b = page.cards
        self.assertNotIn("参数", texts(b))  # LM Studio 没有的信息不显示
        for c in page.cards:
            c.run_probe()
        self.assertTrue(a.pick.isEnabled())
        self.assertFalse(b.pick.isEnabled())
        self.assertIn("无法启动：内存不够", texts(b))
        a.pick.click()
        self.assertIn(("save", "ollama://h/a", None), self.svc.calls)
        self.assertEqual(self.done, [1])

    def test_local_unstable_still_choosable(self):
        svc = FakeService()
        svc.probes["ollama://h/a"] = {"result": "unstable", "score": "3/5", "detail": "有 2 题选错了模块"}
        self.make("D", svc)
        a = self.page().cards[0]
        a.run_probe()
        self.assertTrue(a.pick.isEnabled())
        self.assertIn("不稳定", a.state.text())

    def test_local_empty(self):
        svc = FakeService()
        svc.local = {"runtimes": [{"name": "Ollama", "found": False, "detail": "没有安装"}], "models": []}
        f = self.make("D", svc)
        click(self.page(), "检测电脑并推荐模型")
        self.assertEqual(f.current, "E")

    def test_detect_full_path(self):
        self.make("E")
        page = self.page()
        click(page, "开始检测")
        shown = texts(page)
        for g in GPUS:  # 每张显卡都列出来
            self.assertIn(gpu_line(g), shown)
        self.assertIn("外接", shown)
        self.assertIn("设备异常（代码 22）", shown)
        click(page, "安装这个")
        self.assertIn("还会安装", texts(page))
        self.assertIn("某个文件夹", texts(page))
        click(page, "确认安装")
        self.assertIn("已安装", texts(page))
        click(page, "保存并进入主程序")
        self.assertIn(("save", "ollama://h/x:1b", None), self.svc.calls)
        self.assertEqual(self.done, [1])

    def test_detect_cancel_retry_and_failures(self):
        svc = FakeService()
        svc.install_result = ("pending", None)
        f = self.make("E", svc)
        page = self.page()
        click(page, "开始检测")
        click(page, "安装这个")
        click(page, "确认安装")
        f.show_page("B")  # 切走再回来，安装还在
        self.assertIsNotNone(svc.installing)
        f.show_page("E")
        click(page, "取消安装")
        self.assertIn(("cancel",), svc.calls)
        self.assertIn("已取消", texts(page))
        svc.install_result = ("err", "网断了")
        click(page, "重新安装")
        self.assertIn("下载模型失败：网断了", texts(page))
        svc.install_result = ("ok", "ollama://h/x:1b")
        svc.test_ok = False
        click(page, "重试")
        self.assertIn("没能正常运行：跑不起来", texts(page))
        click(page, "改用云端 AI")
        self.assertEqual(f.current, "C")

    def test_detect_no_fit(self):
        svc = FakeService()
        svc.scan_result["fits"] = []
        svc.scan_result["reasons"] = ["内存只有 4 GB"]
        f = self.make("E", svc)
        click(self.page(), "开始检测")
        self.assertIn("内存只有 4 GB", texts(self.page()))
        click(self.page(), "尝试云端 AI")
        self.assertEqual(f.current, "C")


class LateResults(FlowBase):
    """后台结果晚于页面变化才回来：换了服务商、重新检测之后，旧结果不能碰已经删掉的控件"""

    def test_cloud_switch_provider_while_testing(self):
        svc = FakeService()
        svc.providers_data["apis"].append({"id": "p2", "name": "服务商二", "console": "", "models": [], "has_key": False})
        late = []
        svc.test_api = lambda pid, key, ok, err: late.append((ok, err))
        self.make("C", svc)
        page = self.page()
        page.findChildren(QLineEdit)[0].setText("sk-1")
        click(page, "测试连接")
        click(page, "服务商二")  # 原来的面板被删掉
        wait(50)
        ok, err = late[0]
        ok(["m1"])  # 不应抛 “already deleted”
        err("key", "")

    def test_local_redetect_while_probing(self):
        svc = FakeService()
        late = []
        svc.probe = lambda mid, ok, err: late.append((ok, err))
        self.make("D", svc)
        page = self.page()
        page.cards[0].run_probe()
        click(page, "重新检测")
        wait(50)
        ok, err = late[0]
        ok({"result": "pass", "score": "5/5", "detail": ""})
        err("x")
        self.assertEqual(len(page.cards), 2)

    def test_old_detect_result_ignored(self):
        svc = FakeService()
        late = []
        svc.detect = lambda ok, err: late.append(ok)
        self.make("D", svc)
        page = self.page()
        page.detect()  # 界面上检测中“重新检测”是灰的；这里直接再发一次，模拟两次结果先后到达
        late[0]({"runtimes": [], "models": []})  # 第一次的结果晚到：不显示
        self.assertFalse(page.cards)
        self.assertNotIn("没有检测到本地模型", texts(page))
        late[1](FakeService().local)
        self.assertEqual(len(page.cards), 2)


class SavingState(FlowBase):
    def test_saving_blocks_redetect_and_recovers(self):
        svc = FakeService()
        late = []
        svc.save = lambda mid, key, ok, err: late.append((ok, err))
        self.make("D", svc)
        page = self.page()
        page.cards[0].run_probe()
        page.cards[0].pick.click()
        self.assertFalse(page.again.isEnabled())  # 保存中不能重新检测
        late[0][1]("磁盘写不进去")
        self.assertTrue(page.again.isEnabled())
        self.assertFalse(page.saving)
        self.assertTrue(page.cards[0].pick.isEnabled())


class InstallTask(unittest.TestCase):
    """OnboardingService：取消后马上重装，上一次迟到的结束回调不能清掉这一次"""

    def test_stale_done_ignored(self):
        from native.api import Aborted
        from native.onboarding.service import OnboardingService
        streams = []

        class Handle:
            def __init__(self):
                self.aborted = False

            def abort(self):
                self.aborted = True

        class Api:
            def stream(self, url, body, on_event, on_done):
                h = Handle()
                streams.append((h, on_event, on_done))
                return h
        svc = OnboardingService(Api())
        got = []
        svc.install({"tag": "a"}, lambda *a: None, got.append, lambda *a: got.append(a))
        svc.cancel()
        svc.install({"tag": "b"}, lambda *a: None, got.append, lambda *a: got.append(a))
        streams[0][2](Aborted())  # A 迟到的结束
        self.assertEqual(svc.installing, {"tag": "b"})
        svc.cancel()
        self.assertTrue(streams[1][0].aborted)  # 还能取消 B
        streams[1][1]({"step": "config", "pct": 100, "model": "x"})  # 取消后的事件不再交给页面
        streams[1][2](None)
        self.assertEqual(got, [])


def click_able(widget, text):
    return any(b.text() == text and b.isVisibleTo(widget) and b.isEnabled() for b in widget.findChildren(QPushButton))


class Layout(unittest.TestCase):
    """兼容测试：从最小窗口（主窗口的最小尺寸）到 2K 屏，每一页、每一个状态的文字都完整显示、不横向溢出"""
    SIZES = [(960, 620), (1280, 800), (1366, 768), (1920, 1080), (2560, 1440)]

    def states(self, f):
        """把每页带到要检查的状态，逐个产出 (名字, 页面)"""
        for name in "ABCDF":
            f.show_page(name)
            if name == "D":
                for c in f.pages["D"].cards:
                    c.run_probe()
            yield name, f.pages[name]
        f.show_page("E")
        e = f.pages["E"]
        yield "E 说明", e
        click(e, "开始检测")
        yield "E 推荐", e
        click(e, "安装这个")
        yield "E 确认", e

    def check(self, page, where):
        wait(30)
        pw = page.width()
        for lab in page.findChildren(QLabel):
            if not lab.isVisibleTo(page) or not lab.text() or lab.visibleRegion().isEmpty():
                continue
            right = lab.mapTo(page, lab.rect().topRight()).x()
            self.assertLessEqual(right, pw + 1, f"{where}：文字超出右边 {lab.text()[:20]}")
            if lab.wordWrap():
                need = lab.heightForWidth(lab.width())
                self.assertGreaterEqual(lab.height() + 2, need, f"{where}：文字被截断 {lab.text()[:20]}")
            else:
                self.assertGreaterEqual(lab.width() + 2, lab.sizeHint().width(), f"{where}：文字被截断 {lab.text()[:20]}")

    def test_all_sizes(self):
        for w, h in self.SIZES:
            with self.subTest(size=f"{w}x{h}"):
                f = flow.Onboarding(FakeService(), "A")
                f.resize(w, h)
                f.show()
                wait(30)
                try:
                    for name, page in self.states(f):
                        self.check(page, f"{w}x{h} {name}")
                finally:
                    f.close()
                    f.deleteLater()


class InMainWindow(unittest.TestCase):
    """装进主窗口：第一次打开进引导，完成后回到主程序；顶栏“配置 AI”从 B 页打开；引导开着时快捷键不动画布"""

    @classmethod
    def setUpClass(cls):
        from native.api import Api
        from native.backend import Backend
        from native.controller import Controller
        from native.window import MainWindow
        cls.backend = Backend()
        assert cls.backend.start(), cls.backend.error
        cls.ctrl = Controller(Api(cls.backend.base))
        cls.win = MainWindow(cls.ctrl)
        cls.win.resize(1280, 800)
        cls.win.show()

    @classmethod
    def tearDownClass(cls):
        cls.win.hide()
        cls.backend.stop()

    def test_open_and_finish(self):
        win = self.win
        win.open_setup("A")
        self.assertIs(win.views.currentWidget(), win.setup)
        self.assertEqual(win.setup.current, "A")
        with mock.patch.object(self.ctrl, "delete_selected") as delete:
            from PySide6.QtGui import QShortcut
            for sc in win.findChildren(QShortcut):
                if sc.key().toString() == "Del":
                    sc.activated.emit()
            delete.assert_not_called()
        with mock.patch.object(win.setup.svc, "finish", side_effect=lambda ok, err: ok()):
            win.setup.pages["B"].go.emit("main")  # 不是当前页：不理会
            self.assertIs(win.views.currentWidget(), win.setup)
            win.setup.show_page("B")
            win.setup.pages["B"].go.emit("main")
        self.assertIs(win.views.currentWidget(), win.root)
        self.assertIsNone(win.setup)  # 没有在安装：用完就收起

    def test_topbar_opens_at_b(self):
        win = self.win
        click(win.topbar, "配置 AI")
        self.assertIs(win.views.currentWidget(), win.setup)
        self.assertEqual(win.setup.current, "B")
        win._setup_done()


if __name__ == "__main__":
    unittest.main()
