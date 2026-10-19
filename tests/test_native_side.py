"""原生桌面版的顶栏、左侧栏、开始页、画布标题（native/side）。
不需要大模型：理清需求用替身接口；项目的新建、改名、删除走真实的本机后端（测试建的项目测试完删掉）。"""
import copy
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

app = QApplication.instance() or QApplication([])

from native import dialog, testing  # noqa: E402
from native.controller import Controller  # noqa: E402
from native.side.canvas_head import CanvasHead, add_label, menu_groups  # noqa: E402
from native.side.sidebar import Sidebar  # noqa: E402
from native.side.start import Question, StartPage  # noqa: E402
from native.side.modelpick import READY  # noqa: E402
from native.side.topbar import TopBar  # noqa: E402


class FakeApi:
    """记下每个请求，回调由测试自己调"""

    def __init__(self):
        self.calls = []

    def get(self, url, ok=None, err=None):
        self.calls.append(("GET", url, None, ok, err))

    def post(self, url, body, ok=None, err=None):
        self.calls.append(("POST", url, body, ok, err))

    def send(self, method, url, body=None, ok=None, err=None):
        self.calls.append((method, url, body, ok, err))

    def stream(self, url, body, on_event, on_done=None):
        self.calls.append(("STREAM", url, body, on_event, on_done))

        class H:
            def abort(self):
                pass
        return H()

    def request_sync(self, *a, **k):
        return {}

    def last(self, url):
        return next(c for c in reversed(self.calls) if c[1] == url)


MODULES = {
    "fetch": {"key": "fetch", "name": "抓取网页", "category": "获取", "description": "打开网址。返回正文", "inputs": {}, "outputs": {}},
    "llm": {"key": "llm", "name": "让模型回答", "category": "思考", "description": "问一个问题", "inputs": {}, "outputs": {}},
    "x": {"key": "x", "name": "没类别", "inputs": {}, "outputs": {}},
}


class MenuTest(unittest.TestCase):
    def test_groups_by_category_in_order(self):
        g = menu_groups(MODULES, None)
        self.assertEqual([c for c, _ in g], ["获取", "思考", "其他", "流程"])
        self.assertEqual(g[0][1], [("抓取网页", "打开网址", {"kind": "module", "module": "fetch"})])
        self.assertEqual(g[2][1][0][1], "")  # 没有说明
        self.assertEqual([s["kind"] for _, _, s in g[-1][1]], ["if", "merge", "foreach"])

    def test_no_foreach_inside_loop(self):
        g = menu_groups(MODULES, "each/inner")
        self.assertEqual([s["kind"] for _, _, s in g[-1][1]], ["if", "merge"])

    def test_label(self):
        self.assertEqual(add_label(None), "添加节点")
        self.assertEqual(add_label("outer/inner"), "添加节点到循环 inner")


class StartPageTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.ctrl = Controller(self.api)
        self.page = StartPage(self.ctrl)
        self.generated = []
        self.specs = []
        self.ctrl.generate = lambda text=None, spec=None: (self.generated.append(text), self.specs.append(spec))
        self.ctrl.set_requirement("总结网页")

    def reply(self, r):
        self.api.last("/api/refine")[3](r)

    RESULT = {"card": {"given": ["一个网址"], "wanted": ["一句总结"], "steps": [], "cases": []},
              "questions": [{"ask": "用什么语言？", "options": ["中文", "英文"]}],
              "unsupported": ["发邮件"], "requirement": "给一个网址，输出一句中文总结"}

    def test_no_example_list_here(self):
        """示例只在左侧栏列出，开始页不再重复一份"""
        texts = [w.text() for w in self.page.findChildren(QLabel)]
        self.assertNotIn("从示例开始", texts)
        self.assertIn("从空白开始", texts)

    def test_refine_flow(self):
        self.page.ask([])
        body = self.api.last("/api/refine")[2]
        self.assertEqual(body, {"idea": "总结网页", "answers": [], "card": None, "spec": None, "model": None})
        self.assertTrue(self.page.busy)
        self.assertTrue(self.page.stop_btn.isVisibleTo(self.page))
        self.assertFalse(self.page.idea.isEnabled())
        self.reply(copy.deepcopy(self.RESULT))
        self.assertFalse(self.page.busy)
        self.assertEqual(self.page.ask_btn.text(), "重新整理")
        self.assertEqual(self.page.final_text(), "给一个网址，输出一句中文总结")
        self.assertFalse(self.page.update_btn.isEnabled())
        # 回答问题后再整理：带上回答，卡片没改过就不交回去
        self.page._choose(0, "中文")
        self.assertTrue(self.page.update_btn.isEnabled())
        self.page._update_answers()
        body = self.api.last("/api/refine")[2]
        self.assertEqual(body["answers"], [{"ask": "用什么语言？", "answer": "中文"}])
        self.assertIsNone(body["card"])

    def test_spec_round_trip_other_and_checklist(self):
        """需求单原样送回、回答带栏目；“其他”点了才出现输入框；显示还缺什么；生成时带上需求单"""
        spec = {"inputs": None, "answered": {}}
        r = {**copy.deepcopy(self.RESULT), "spec": spec, "complete": False,
             "questions": [{"slot": "inputs", "ask": "给它什么？", "why": "还没有输入", "options": ["一个网址", "一组网址"]}],
             "checklist": [{"key": "inputs", "label": "输入", "state": "missing"},
                           {"key": "outputs", "label": "输出", "state": "ok"}]}
        self.page.ask([])
        self.reply(r)
        q = self.page.refine.findChildren(Question)[0]
        self.assertEqual([o for o, _ in q.chips], ["一个网址", "一组网址"])
        self.assertFalse(q.input.isVisibleTo(q))
        q.other_btn.click()
        self.assertTrue(q.input.isVisibleTo(q))
        q.input.setText("三个新闻网站")
        q.input.textEdited.emit("三个新闻网站")
        self.page._update_answers()
        body = self.api.last("/api/refine")[2]
        self.assertEqual(body["answers"], [{"slot": "inputs", "ask": "给它什么？", "answer": "三个新闻网站"}])
        self.assertEqual(body["spec"], spec)
        self.reply(r)
        labels = [w.text() for w in self.page.refine.findChildren(QLabel)]
        self.assertIn("需求单还缺什么", labels)
        self.assertIn("输入  缺", labels)
        self.page.gen_btn.click()
        self.assertEqual(self.specs, [spec])

    def test_card_edit_changes_final_text_and_is_sent(self):
        self.page.ask([])
        self.reply(copy.deepcopy(self.RESULT))
        self.page._card_change("steps", ["抓取正文"])
        self.assertTrue(self.page.card_edited())
        self.assertEqual(self.page.card_title.text(), "需求卡片（改过）")
        self.assertEqual(self.page.final_text(), "给它：一个网址。要它产出：一句总结。做法：抓取正文。")
        self.page.gen_btn.click()
        self.assertEqual(self.generated, ["给它：一个网址。要它产出：一句总结。做法：抓取正文。"])
        self.assertEqual(self.specs, [None])  # 卡片改过：需求单已对不上，不交给生成
        self.page.ask([])
        self.assertEqual(self.api.last("/api/refine")[2]["card"]["steps"], ["抓取正文"])

    def test_stale_reply_ignored_after_stop(self):
        self.page.ask([])
        ok = self.api.last("/api/refine")[3]
        self.page.stop()
        self.assertFalse(self.page.busy)
        ok(copy.deepcopy(self.RESULT))
        self.assertIsNone(self.page.result)
        # 第二次整理之后，第一次的结果晚到也不理
        self.page.ask([])
        first = self.api.last("/api/refine")[3]
        self.page.stop()
        self.page.ask([])
        first(copy.deepcopy(self.RESULT))
        self.assertIsNone(self.page.result)
        self.assertTrue(self.page.busy)

    def test_error_and_project_change_resets(self):
        self.page.ask([])
        self.api.last("/api/refine")[4](Exception("超时"))
        self.assertEqual(self.page.error, "没能整理：超时")
        self.page.ask([])
        self.reply(copy.deepcopy(self.RESULT))
        self.ctrl.project = "p_other"
        self.ctrl.projects_changed.emit()
        self.assertIsNone(self.page.result)
        self.assertEqual(self.page.error, "")
        self.assertFalse(self.page.refine.isVisibleTo(self.page))

    def test_idea_bound_to_requirement(self):
        self.page.idea.setPlainText("新的想法")
        self.assertEqual(self.ctrl.requirement, "新的想法")
        self.ctrl.set_requirement("从顶栏改的")
        self.assertEqual(self.page.idea.toPlainText(), "从顶栏改的")
        self.page.direct_btn.click()
        self.assertEqual(self.generated, ["从顶栏改的"])


class GenWaitHintTest(unittest.TestCase):
    def test_hint_after_silence_and_cleared_on_event(self):
        """生成中模型 45 秒没回音：提示条说明并指向“停止生成”；来了事件就撤掉；生成结束停表"""
        import time as _t
        api = FakeApi()
        ctrl = Controller(api)
        ctrl.generating = True
        ctrl._gen_last = _t.monotonic() - 50
        ctrl._gen_watch()
        self.assertIn("模型还没回音", ctrl.notice)
        self.assertIn("停止生成", ctrl.notice)
        ctrl._gen_tick()
        self.assertEqual(ctrl.notice, "")
        ctrl._gen_last = _t.monotonic() - 10
        ctrl._gen_watch()
        self.assertEqual(ctrl.notice, "")      # 没到 45 秒不提示
        ctrl.generating = False
        ctrl._gen_watch()
        self.assertFalse(ctrl._gen_wait_timer.isActive())
        # 提示条上有别的通知（改名失败）：等待提示不抢，来事件也不清掉别人的
        ctrl.generating = True
        ctrl.set_notice("没能改名：x")
        ctrl._gen_last = _t.monotonic() - 50
        ctrl._gen_watch()
        self.assertEqual(ctrl.notice, "没能改名：x")
        ctrl._gen_tick()
        self.assertEqual(ctrl.notice, "没能改名：x")
        # 等待提示显示着时生成失败：失败原因留下，不被清理盖掉
        ctrl.set_notice("")
        ctrl._gen_last = _t.monotonic() - 50
        ctrl._gen_watch()
        self.assertIn("模型还没回音", ctrl.notice)
        ctrl._clear_gen_wait()
        ctrl.set_notice("3 轮之后仍有问题")
        self.assertEqual(ctrl.notice, "3 轮之后仍有问题")


class NewProjectGuardTest(unittest.TestCase):
    def test_rapid_clicks_create_one(self):
        """连续点“+”：请求还没回来时再点不重复建"""
        api = FakeApi()
        ctrl = Controller(api)
        ctrl.set_requirement("有内容")
        ctrl.new_project()
        ctrl.new_project()
        ctrl.new_project()
        self.assertEqual(len([c for c in api.calls if c[0] == "POST" and c[1] == "/api/projects"]), 1)
        api.last("/api/projects")[3]({"id": "p_1"})
        self.assertEqual(ctrl.project, "p_1")
        ctrl.new_project()  # 刚建好的空项目：再点也不建
        self.assertEqual(len([c for c in api.calls if c[0] == "POST" and c[1] == "/api/projects"]), 1)


class ProjectModelTest(unittest.TestCase):
    def test_picker_edits_project_model_when_project_open(self):
        """打开着项目时，顶栏换模型只改这个项目的；首页上改的是默认"""
        api = FakeApi()
        ctrl = Controller(api)
        bar = TopBar(ctrl)
        pick = bar.model
        data = {"chosen": "ollama://h/a", "choices": [
            {"id": "ollama://h/a", "name": "a", "kind": "local", "ready": True, "note": ""},
            {"id": "ollama://h/b", "name": "b", "kind": "local", "ready": True, "note": ""}]}
        api.last("/api/models")[3](data)
        self.assertEqual(pick.currentData(), "ollama://h/a")
        ctrl._enter("p_1", "ollama://h/b")           # 打开一个自己选了 b 的项目
        self.assertEqual(pick.currentData(), "ollama://h/b")
        self.assertEqual(ctrl.generation_model(), "ollama://h/b")
        pick.setCurrentIndex(pick.findData("ollama://h/a"))
        self.assertEqual(ctrl.project_model, "ollama://h/a")
        self.assertEqual(ctrl._unsynced["p_1"]["body"]["model"], "ollama://h/a")  # 走自动保存队列（带 rev）
        ctrl.flush_store()
        put = api.last("/api/projects/p_1")
        self.assertEqual((put[0], put[2]["model"]), ("PUT", "ollama://h/a"))
        self.assertIn("rev", put[2])
        self.assertFalse(any(c[1] == "/api/models" and c[0] == "PUT" for c in api.calls))  # 没动默认
        # 项目选的模型已经不在列表里：显式列出并标为不可用，显示的和实际要用的一致
        ctrl._enter("p_2", "ollama://h/gone")
        self.assertEqual(pick.currentData(), "ollama://h/gone")
        self.assertFalse(pick.itemData(pick.currentIndex(), READY))
        self.assertEqual(ctrl.generation_model(), "ollama://h/gone")
        ctrl._enter(None)                             # 回首页：显示默认
        self.assertEqual(pick.currentData(), "ollama://h/a")
        self.assertIsNone(ctrl.generation_model())
        pick.setCurrentIndex(pick.findData("ollama://h/b"))
        self.assertEqual(api.last("/api/models")[0], "PUT")  # 首页：改默认


class TopBarTest(unittest.TestCase):
    def test_no_requirement_field_and_setup_locked_while_generating(self):
        """顶栏不再有一句话输入框（和首页重复）；生成中“配置 AI”不可用；实跑检查开关直通控制器"""
        from PySide6.QtWidgets import QLineEdit
        ctrl = Controller(FakeApi())
        bar = TopBar(ctrl)
        self.assertEqual(bar.findChildren(QLineEdit), [])
        self.assertTrue(bar.setup.isEnabled())
        ctrl.generating = True
        ctrl.gen_changed.emit()
        self.assertFalse(bar.setup.isEnabled())
        self.assertEqual(bar.settings_btn.text(), "设置")


class SettingsDialogTest(unittest.TestCase):
    def test_sections_and_switches(self):
        """设置面板三栏：桌宠开关记进本机设置、实跑检查开关直通控制器、自定义栏从 /api/settings 读、添加服务商发请求"""
        from PySide6.QtWidgets import QLineEdit
        from native.settings import SettingsDialog
        import tempfile
        from PySide6.QtCore import QSettings
        api = FakeApi()
        ctrl = Controller(api)
        tmp = tempfile.NamedTemporaryFile(suffix=".ini", delete=False)
        tmp.close()
        ctrl.settings = QSettings(tmp.name, QSettings.Format.IniFormat)  # 不碰这台电脑真正的设置
        ctrl.pet_background = True
        ctrl.check = True
        d = SettingsDialog(ctrl, None, "misc")
        self.assertEqual([d.nav.item(i).text() for i in range(d.nav.count())], ["桌宠", "杂项", "自定义"])
        self.assertEqual(d.pages.currentIndex(), 1)
        from native.side.topbar import Switch
        switches = {w.accessibleName(): w for w in d.findChildren(Switch)}
        switches["生成后实跑检查"].setChecked(False)
        self.assertFalse(ctrl.check)
        switches["关窗口后台运行并显示桌宠"].setChecked(False)
        self.assertFalse(ctrl.pet_background)
        self.assertEqual(ctrl.settings.value("pet_background"), "0")
        api.last("/api/settings")[3]({"think_model": "", "think_default": "qwen3:8b", "think_choices": ["qwen3:8b", "other"],
                                      "custom_apis": [{"key": "mine", "name": "我的中转", "base": "https://x/v1", "key_env": "MY_KEY",
                                                       "models": ["m1"], "overrides_builtin": False}],
                                      "settings_file": "E:/x/model_settings.json"})
        self.assertEqual(d.think.count(), 3)
        self.assertEqual(d.think.currentData(), "")
        d.think.setCurrentIndex(2)
        put = api.last("/api/settings")
        self.assertEqual((put[0], put[2]), ("PUT", {"think_model": "other"}))
        d.fields["key"].setText("proxy2")
        d.fields["name"].setText("另一个")
        d.fields["base"].setText("https://y/v1")
        d.fields["key_env"].setText("Y_KEY")
        d.fields["models"].setText("a，b")
        d.add_btn.click()
        post = api.last("/api/settings/custom-api")
        self.assertEqual(post[2]["models"], ["a", "b"])
        self.assertEqual(post[2]["key"], "proxy2")

    def test_model_picker(self):
        api = FakeApi()
        ctrl = Controller(api)
        bar = TopBar(ctrl)
        pick = bar.model
        self.assertFalse(pick.isEnabled())  # 还没读到选项
        data = {"chosen": "api://d/x", "choices": [
            {"id": "local", "name": "本地", "kind": "local", "ready": True, "note": ""},
            {"id": "api://d/x", "name": "D · x", "kind": "api", "ready": True, "note": "按用量计费"},
            {"id": "cli://k", "name": "K", "kind": "cli", "ready": False, "note": "没有找到命令行 k"}]}
        api.last("/api/models")[3](data)
        self.assertTrue(pick.isEnabled())
        self.assertEqual(pick.currentData(), "api://d/x")
        k = pick.findData("cli://k")
        self.assertEqual(k, 3)  # 能用的在前，还不能用的隔一条分隔线放在后面
        self.assertFalse(pick.model().item(k).isEnabled())  # 灰色、不能点
        self.assertEqual(pick.itemText(k), "订阅 · K")
        pick.setCurrentIndex(0)
        method, _, body, ok, err = api.calls[-1]
        self.assertEqual((method, body), ("PUT", {"model": "local"}))
        err(Exception("断了"))  # 没存上：回到原来的选择，并提示
        self.assertEqual(pick.currentData(), "api://d/x")
        self.assertIn("没能换模型", ctrl.notice)
        ctrl.generating = True
        ctrl.gen_changed.emit()
        self.assertFalse(pick.isEnabled())  # 生成中不能换


WF = {"spec": "0.2", "id": "t", "name": "测试图", "intent": "做测试",
      "inputs": {"input": {"type": "string"}}, "outputs": {"output": {"type": "string"}},
      "nodes": [], "edges": [], "layout": {"$in": {"x": 0, "y": 0}, "$out": {"x": 620, "y": 0}}}


class CanvasHeadTest(unittest.TestCase):
    def test_head_states(self):
        api = FakeApi()
        ctrl = Controller(api)
        ctrl.modules = MODULES
        ctrl.project = "p_x"
        head = CanvasHead(ctrl)
        ctrl._show(copy.deepcopy(WF), "examples", "t.workflow.json")
        self.assertEqual(head.title.text(), "测试图")
        self.assertEqual(head.intent.text(), "做测试")
        self.assertFalse(head.save_btn.isVisibleTo(head))
        self.assertFalse(head.undo.isEnabled())
        ctrl.add_at({"kind": "merge"}, None)
        self.assertTrue(ctrl.current["dirty"])
        self.assertTrue(head.undo.isEnabled())
        self.assertTrue(head.dirty_tag.isVisibleTo(head))
        self.assertTrue(head.save_btn.isVisibleTo(head))
        self.assertEqual(head.save_btn.text(), "保存到我的")
        self.assertTrue(head.save_btn.isEnabled())
        # 校验出问题：保存按钮不能点，标题下出现可展开的问题列表
        ctrl.check_errors = ["merge：缺少输入 a"]
        ctrl.checks_changed.emit()
        self.assertFalse(head.save_btn.isEnabled())
        self.assertEqual(head.save_btn.toolTip(), "先改好图上标出的问题再保存")
        self.assertIn("这张图现在有 1 个问题", head.summary.text())
        self.assertFalse(head.check_list_w.isVisibleTo(head))
        head._toggle_checks()
        self.assertTrue(head.check_list_w.isVisibleTo(head))
        ctrl.travel("undo")
        self.assertTrue(head.redo.isEnabled())


class ProjectsTest(unittest.TestCase):
    """真实后端：新建两个项目、就地改名、删除"""

    @classmethod
    def setUpClass(cls):
        from native.api import Api
        from native.backend import Backend
        cls.backend = Backend()
        assert cls.backend.start(), cls.backend.error
        cls.api = Api(cls.backend.base)

    @classmethod
    def tearDownClass(cls):
        cls.api.close()
        cls.backend.stop()

    def setUp(self):
        self.ctrl = Controller(self.api)
        self.side = Sidebar(self.ctrl)
        self.made = []
        self._ask = dialog.ask
        dialog.ask = lambda *a, **k: True

    def tearDown(self):
        dialog.ask = self._ask
        for pid in self.made:
            try:
                self.api.request_sync("DELETE", f"/api/projects/{pid}")
            except Exception:
                pass

    def test_create_rename_delete(self):
        ctrl = self.ctrl
        ctrl.load()
        self.assertTrue(testing.wait_until(lambda: ctrl.items, 8000))
        self.assertTrue(self.side.groups.count() >= 1)
        ctrl.new_project()
        self.assertTrue(testing.wait_until(lambda: ctrl.project, 8000))
        first = ctrl.project
        self.made.append(first)
        self.assertTrue(testing.wait_until(lambda: first in self.side.rows, 8000))
        self.assertTrue(self.side.rows[first].property("on"))
        # 当前项目还是空的：再点“+”不会多建一个
        before = len(ctrl.projects)
        ctrl.new_project()
        ctrl.new_project()
        testing.wait(300)
        self.assertEqual(ctrl.project, first)
        self.assertEqual(len(ctrl.projects), before)
        ctrl.set_requirement("写了点东西")  # 不再是空项目：可以新建
        ctrl.new_project()
        self.assertTrue(testing.wait_until(lambda: ctrl.project and ctrl.project != first, 8000))
        second = ctrl.project
        self.made.append(second)
        self.assertTrue(testing.wait_until(lambda: second in self.side.rows and first in self.side.rows, 8000))
        self.assertFalse(self.side.rows[first].property("on"))

        # 就地改名：回车确认
        self.side.start_rename(first)
        field = self.side.rows[first].rename
        self.assertIsNotNone(field)
        field.setText("  侧栏测试项目  ")
        field.finish(field.text().strip())
        self.assertTrue(testing.wait_until(lambda: self.side.rows[first].rename is None, 2000))
        self.assertTrue(testing.wait_until(
            lambda: any(p["id"] == first and p["name"] == "侧栏测试项目" for p in ctrl.projects), 8000))
        doc = self.api.request_sync("GET", f"/api/projects/{first}")
        self.assertEqual(doc["name"], "侧栏测试项目")
        # Esc 放弃：名字不变
        self.side.start_rename(first)
        f2 = self.side.rows[first].rename
        f2.setText("不要")
        f2.finish(None)
        testing.wait(100)
        self.assertEqual(next(p for p in ctrl.projects if p["id"] == first)["name"], "侧栏测试项目")

        # 删除正打开的项目：回到首页，列表里没有了
        ctrl.delete_project(next(p for p in ctrl.projects if p["id"] == second))
        self.assertIsNone(ctrl.project)
        self.assertTrue(testing.wait_until(lambda: second not in self.side.rows, 8000))
        self.assertTrue(testing.wait_until(lambda: all(p["id"] != second for p in ctrl.projects), 8000))

    def test_open_example_highlights(self):
        ctrl = self.ctrl
        ctrl.load()
        self.assertTrue(testing.wait_until(lambda: ctrl.items and ctrl.modules, 8000))
        before = {p["id"] for p in ctrl.projects}
        item = next(i for i in ctrl.example_items() if i["file"] == "summarize_many.workflow.json")
        ctrl.open(item)
        ok = testing.wait_until(lambda: ctrl.current and ctrl.project, 8000)
        if ctrl.project and ctrl.project not in before:
            self.made.append(ctrl.project)  # 在首页打开示例会先建一个项目
        self.assertTrue(ok)
        testing.wait(100)
        rows = [self.side.groups.itemAt(i).widget() for i in range(self.side.groups.count())]
        on = [w for g in rows for w in g.findChildren(type(g)) if w.property("on") is True]
        self.assertEqual(len(on), 1)
        self.assertEqual(json.dumps(ctrl.current["source"]), '"examples"')


if __name__ == "__main__":
    unittest.main()
