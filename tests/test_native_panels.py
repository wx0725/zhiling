"""右侧面板（native/panels）：运行表单收集输入、编辑器的提交时机与校验、“节点”页按选中的东西分流、页签。
用一个假的控制器（只有面板读的属性、方法和信号），不启动后端。"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from native.logic._js import UNSET  # noqa: E402
from native.testing import wait  # noqa: E402

app = QApplication.instance() or QApplication([])

from native.panels import editors  # noqa: E402
from native.panels.detail import NodeTab  # noqa: E402
from native.panels.genlog import GenerationLog  # noqa: E402
from native.panels.panel import RightPanel  # noqa: E402
from native.panels.run import RunPanel  # noqa: E402

WF = {"id": "w", "name": "名字", "intent": "",
      "inputs": {"s": {"type": "string", "description": "文字"}, "n": {"type": "integer"},
                 "opt": {"type": "boolean", "required": False}, "flag": {"type": "boolean"},
                 "xs": {"type": "list<string>"}, "o": {"type": "string", "required": False}},
      "outputs": {"out": {"type": "string", "description": "结果"}}, "nodes": [], "edges": []}


class FakeCtrl(QObject):
    current_changed = Signal()
    flow_changed = Signal()
    selection_changed = Signal()
    status_changed = Signal()
    gen_changed = Signal()
    tab_changed = Signal()
    modules_changed = Signal()
    leaving = Signal()
    commit_pending = Signal()
    run_requested = Signal()
    bugs_changed = Signal()
    ask_changed = Signal()

    def __init__(self, wf=WF, editable=True):
        super().__init__()
        self.current = {"wf": wf, "raw": wf, "source": "saved", "file": "w.json", "dirty": False} if wf else None
        self.tab = "run"
        self.running = False
        self.result = None
        self.generating = False
        self.gen_events = []
        self.drawn = None
        self.nodes = []
        self.selected = None
        self.selected_edge = None
        self.editable = editable
        self.runs = []
        self.calls = []
        self.bugs = None
        self.pending_ask = None
        self.run_id = None

    def answer_ask(self, answer):
        self.calls.append(("answer", answer))
        self.pending_ask["sending"] = True   # 像真控制器一样：先标记在送，收到确认（测试里由 confirm_answer）再清掉
        self.ask_changed.emit()

    def confirm_answer(self):
        self.pending_ask = None
        self.ask_changed.emit()

    def node_names(self):
        return {n["id"]: n["id"] for n in self.nodes}

    def bug_check(self):
        self.calls.append(("bug_check",))

    def focus_targets(self, targets):
        self.calls.append(("focus_targets", list(targets)))

    def run_state(self):
        return "running" if self.running else "idle"

    def set_tab(self, t):
        self.tab = t
        self.tab_changed.emit()

    def set_panel_open(self, on):
        self.calls.append(("panel_open", on))

    def run(self, inputs):
        self.runs.append(inputs)

    def statuses(self):
        return {}

    def records_by_id(self):
        return {}

    def selected_node(self):
        return next((n for n in self.nodes if n["id"] == self.selected), None)

    def node_edits(self):
        if not self.selected or not self.editable or self.selected.startswith("$"):
            return None
        n = self.selected_node()["data"]["node"]
        return {"node": n, "incoming": {}, "set_config": lambda p, v: self.calls.append(("config", p, v)),
                "set_label": lambda v: self.calls.append(("label", v)),
                "set_max_items": lambda v: self.calls.append(("max", v)), "remove": lambda: self.calls.append(("remove",))}

    def port_side(self):
        if not self.editable:
            return None
        return "inputs" if self.selected == "$in" else "outputs" if self.selected == "$out" else None

    def port_ops(self):
        return {"add": lambda: self.calls.append(("add",)), "rename": lambda a, b: self.calls.append(("rename", a, b)),
                "type": lambda n, t: self.calls.append(("type", n, t)), "fields": lambda n, f: self.calls.append(("fields", n, f)),
                "remove": lambda n: self.calls.append(("rm", n))}

    def set_meta(self, m):
        self.calls.append(("meta", m))

    def delete_selected(self):
        self.calls.append(("delete",))

    def targets_for(self, issue, rnd):
        return []

    def focus_node(self, nid):
        self.calls.append(("focus", nid))


class RunFormTest(unittest.TestCase):
    def test_collects_and_validates(self):
        c = FakeCtrl()
        p = RunPanel(c)
        p.sync()
        p.set_value("s", "你好")
        p.set_value("n", "abc")
        p.set_value("xs", '["a"]')
        p.submit()
        self.assertEqual(c.runs, [])
        self.assertEqual(p.errors, {"n": "需要整数"})
        self.assertTrue(p.fields["n"][0].error.isVisibleTo(p))
        p.set_value("n", "7")
        p.set_value("flag", True)
        p.submit()
        # 可选又没填的（opt 未选择、o 留空）不发送
        self.assertEqual(c.runs, [{"s": "你好", "n": 7, "flag": True, "xs": ["a"]}])
        p.set_value("opt", False)
        p.submit()
        self.assertEqual(c.runs[-1]["opt"], False)

    def test_values_kept_until_inputs_change(self):
        c = FakeCtrl()
        p = RunPanel(c)
        p.sync()
        p.set_value("s", "改过")
        c.current = {**c.current, "wf": {**WF, "nodes": [{"id": "x"}]}}
        p.sync()
        self.assertEqual(p.values["s"], "改过")
        c.current = {**c.current, "wf": {**WF, "inputs": {"s": {"type": "string"}}}}
        p.sync()
        self.assertEqual(p.values["s"], "")

    def test_running_disables(self):
        c = FakeCtrl()
        p = RunPanel(c)
        c.running = True
        p.sync()
        self.assertFalse(p.run_btn.isEnabled())
        self.assertEqual(p.run_btn.text(), "运行中…")
        c.running = False
        c.result = {"status": "completed", "seconds": 1.5, "outputs": {"out": "x" * 500}}
        p.sync()
        self.assertTrue(p.run_btn.isEnabled())


class EditorTest(unittest.TestCase):
    def test_param_commit(self):
        got = []
        decl = {"type": "integer", "default": 3}
        prm = editors.Param("k", decl)
        prm.sync(None, False, None, got.append)
        self.assertEqual(prm.ctl.placeholderText(), "默认：3")
        prm.commit("x")
        self.assertEqual(prm.error.text(), "需要整数")
        self.assertEqual(got, [])
        prm.commit(" 5 ")
        self.assertEqual(got, [5])
        prm.sync(5, True, None, got.append)
        self.assertEqual(prm.ctl.text(), "5")
        prm.commit("5")  # 没变不提交
        self.assertEqual(got, [5])
        prm.commit("")   # 留空：去掉固定值
        self.assertIs(got[-1], UNSET)

    def test_param_keeps_typed_text_on_same_props(self):
        prm = editors.Param("k", {"type": "string"})
        prm.sync("a", True, None, lambda v: None)
        prm.ctl.setText("正在打")
        prm.sync("a", True, None, lambda v: None)  # 重画但程序没变
        self.assertEqual(prm.ctl.text(), "正在打")
        prm.sync("b", True, None, lambda v: None)  # 撤销等：跟着程序走
        self.assertEqual(prm.ctl.text(), "b")

    def test_param_wired_and_bool(self):
        got = []
        prm = editors.Param("k", {"type": "boolean"})
        prm.sync(None, False, "$in.x", got.append)
        self.assertEqual(prm.mode, "wired")
        prm.sync(None, False, None, got.append)
        self.assertEqual(prm.mode, "bool")
        prm._pick(True)
        self.assertEqual(got, [True])

    def test_param_long_string_switches_to_multiline(self):
        prm = editors.Param("k", {"type": "string"})
        prm.sync(None, False, None, lambda v: None)
        self.assertEqual(prm.mode, "line")
        prm.ctl.setText("字" * 41)
        wait(10)
        self.assertEqual(prm.mode, "multi")
        self.assertEqual(prm.ctl.text(), "字" * 41)

    def test_label_and_max_items(self):
        got = []
        le = editors.LabelEditor("旧", "模块名", got.append)
        le.commit(" 旧 ")
        self.assertEqual(got, [])
        le.commit("新")
        self.assertEqual(got, ["新"])
        mi = editors.MaxItemsEditor(20, got.append)
        mi.commit("0")
        self.assertEqual(mi.error.text(), "需要大于 0 的整数")
        mi.commit("1.5")
        self.assertTrue(mi.error.isVisibleTo(mi))
        mi.commit("20")
        self.assertEqual(got, ["新"])
        mi.commit("30")
        self.assertEqual(got, ["新", 30])
        self.assertFalse(mi.error.isVisibleTo(mi))

    def test_port_card(self):
        c = FakeCtrl()
        ops = c.port_ops()
        ports = {"a": {"type": "string"}, "b": {"type": "integer", "default": 2}}
        pe = editors.PortsEditor("inputs", ports, ops)
        card = pe.cards["a"]
        card.rename("Bad")
        self.assertEqual(card.f_name.error.text(), "小写英文字母开头，后面跟小写字母、数字或下划线")
        card.rename("b")
        self.assertEqual(card.f_name.error.text(), "已经有叫 b 的了")
        card.rename("a")
        self.assertEqual(c.calls, [])
        card.rename("c_1")
        self.assertEqual(c.calls, [("rename", "a", "c_1")])
        cb = pe.cards["b"]
        self.assertTrue(cb.optional.isChecked())
        self.assertFalse(cb.optional.isEnabled())
        self.assertEqual(cb.def_ctl.text(), "2")
        cb._commit_default("x")
        self.assertEqual(cb.f_def.error.text(), "需要整数")
        cb._commit_default("")
        self.assertEqual(c.calls[-1], ("fields", "b", {"default": UNSET}))
        card._commit_desc("说明")
        self.assertEqual(c.calls[-1], ("fields", "a", {"description": "说明"}))
        # 改名后卡片按名字对应：旧的去掉、新的出现
        pe.sync({"c_1": {"type": "string"}, "b": ports["b"]}, ops)
        self.assertEqual(set(pe.cards), {"c_1", "b"})

    def test_meta(self):
        got = []
        me = editors.MetaEditor("名", "", got.append)
        me._commit_name("  ")
        me._commit_name("名")
        self.assertEqual(got, [])
        me._commit_name(" 新名 ")
        me._commit_intent("做点事")
        self.assertEqual(got, [{"name": "新名"}, {"intent": "做点事"}])


MOD = {"key": "text.format@0", "name": "转成文字", "description": "说明", "inputs": {"template": {"type": "string"}}, "outputs": {}}


def node(nid, kind="module"):
    n = {"id": nid, "kind": kind, "module": "text.format@0"} if kind == "module" else {"id": nid, "kind": kind}
    return {"id": nid, "type": "module", "data": {"node": n, "module": MOD if kind == "module" else None, "ins": [], "outs": []}}


class RoutingTest(unittest.TestCase):
    def test_node_tab_modes(self):
        c = FakeCtrl()
        c.nodes = [node("a"), node("l", "foreach")]
        t = NodeTab(c)
        t.sync()
        self.assertEqual(t.mode, ("meta",))
        c.selected = "$in"
        t.sync()
        self.assertEqual(t.mode, ("ports", "inputs"))
        c.selected = "a"
        t.sync()
        self.assertEqual(t.mode, ("node", "a"))
        self.assertIsNotNone(t.child.editing)
        self.assertIsNone(t.child.max_ed)
        c.selected = "l"
        t.sync()
        self.assertIsNotNone(t.child.max_ed)
        c.selected_edge = {"id": "e", "from": "a.text", "to": "$out.out", "scope": []}
        t.sync()
        self.assertEqual(t.mode[0], "edge")
        c.editable = False
        c.selected_edge = None
        c.selected = None
        t.sync()
        self.assertEqual(t.mode, ("node", ""))
        c.selected = "$in"  # 不能编辑时选中端点：节点详情
        t.sync()
        self.assertEqual(t.mode, ("node", "$in"))

    def test_panel_tabs_and_guard(self):
        c = FakeCtrl()
        c.nodes = [node("a")]
        p = RightPanel(c)
        self.assertIsNotNone(p.run_panel)
        self.assertEqual(list(p.tab_btns), ["run", "node", "generate"])   # 运行 / 节点 / 生成过程
        run = p.run_panel
        p.render()
        self.assertIs(p.run_panel, run)          # 运行页一直保留
        c.current = {**c.current, "file": "other.json"}
        p.render()
        self.assertIsNot(p.run_panel, run)       # 换了工作流：运行页重新建

    def test_selecting_node_opens_node_tab(self):
        c = FakeCtrl()
        c.nodes = [node("a")]
        p = RightPanel(c)
        self.assertEqual(c.tab, "run")
        c.selected = "a"
        c.selection_changed.emit()
        p.render()
        self.assertEqual(c.tab, "node")          # 点了节点：自动切到“节点”页，能看到修改入口
        self.assertIsInstance(p.page, NodeTab)
        self.assertEqual(p.page.mode, ("node", "a"))
        c.set_tab("run")
        c.selection_changed.emit()               # 同一个节点再点一次：不抢回页签
        p.render()
        self.assertEqual(c.tab, "run")
        c.selected = "$in"
        c.selection_changed.emit()
        p.render()
        self.assertEqual(c.tab, "node")          # 点“输入”端点：切到端点编辑
        self.assertEqual(p.page.mode, ("ports", "inputs"))

    def test_run_error_is_contained(self):
        c = FakeCtrl()
        c.result = 1  # 坏的运行结果：运行页画不出来
        p = RightPanel(c)
        from PySide6.QtWidgets import QLabel
        texts = [w.text() for w in p.findChildren(QLabel)]
        self.assertTrue(any(t.startswith("这个面板没法显示：") for t in texts))

    def test_bug_check_section(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        c = FakeCtrl()
        c.nodes = [node("a")]
        p = RightPanel(c)
        next(b for b in p.findChildren(QPushButton) if b.text() == "检查一遍").click()
        self.assertEqual(c.calls[-1], ("bug_check",))
        c.bugs = {"raw": c.current["raw"], "items": [{"text": "「a」还缺一个必填的输入", "raw": "x", "targets": ["a"]}]}
        c.bugs_changed.emit()
        p.render()
        texts = [w.text() for w in p.findChildren(QLabel)]
        self.assertIn("发现 1 个问题", texts)
        self.assertIn("· 「a」还缺一个必填的输入", texts)
        next(b for b in p.findChildren(QPushButton) if b.text() == "定位").click()
        self.assertEqual(c.calls[-1], ("focus_targets", ["a"]))
        c.bugs = {"raw": c.current["raw"], "items": []}
        p.render()
        self.assertIn("没有发现问题，可以运行。", [w.text() for w in p.findChildren(QLabel)])
        c.current = {**c.current, "raw": {**c.current["raw"]}}  # 程序改过了：旧结果不再显示
        p.render()
        self.assertNotIn("没有发现问题，可以运行。", [w.text() for w in p.findChildren(QLabel)])

    def test_run_error_explained(self):
        from PySide6.QtWidgets import QLabel
        c = FakeCtrl()
        p = RightPanel(c)
        c.result = {"status": "failed", "error": {"code": "timeout", "message": "超过 30 秒没有完成", "path": "w.a"}, "outputs": {}}
        c.nodes = [node("a")]
        c.current = {**c.current, "raw": {**WF, "nodes": [{"id": "a", "kind": "module", "module": "m"}]}}
        p.render()
        texts = [w.text() for w in p.findChildren(QLabel)]
        self.assertIn("「a」这一步出错了", texts)
        self.assertIn("原因：运行太久没完成，被自动停下了", texts)

    def test_genlog_links(self):
        c = FakeCtrl()
        c.targets_for = lambda issue, rnd: ["loop/fetch"]
        c.gen_events = [{"type": "validated", "round": 1, "seconds": 2, "errors": []}, {"type": "checking", "round": 1},
                        {"type": "checked", "round": 1, "status": "failed", "problems": ["节点 fetch：坏了"]},
                        {"type": "done", "ok": False, "valid": True, "rounds": 1}]
        c.drawn = {"round": 1}
        g = GenerationLog(c)
        g.sync()
        from PySide6.QtWidgets import QLabel, QPushButton
        btn = next(b for b in g.findChildren(QPushButton) if b.text() == "在图上看 fetch")
        btn.click()
        self.assertEqual(c.calls[-1], ("focus", "loop/fetch"))
        texts = [w.text() for w in g.findChildren(QLabel)]
        self.assertIn("1 轮之后仍有问题：最后一版没有通过实跑检查，画布上是第 1 轮那一版，可以运行看看，但不能保存。", texts)

if __name__ == "__main__":
    unittest.main()


class AskDialogTest(unittest.TestCase):
    """“提醒确认”节点等回答：面板弹出提示窗，点按钮把 {choice, done, note} 交给控制器；运行结束时关掉"""

    def test_dialog_round_trip(self):
        from PySide6.QtWidgets import QCheckBox, QPushButton
        c = FakeCtrl()
        p = RightPanel(c)
        self.assertIsNone(p.ask_host.dialog)
        c.pending_ask = {"run_id": "r1", "path": "w.ask",
                         "prompt": {"title": "今天的待办", "detail": "三件事", "items": ["回邮件", "整理发票"], "options": ["做完了", "先不做"]}}
        c.ask_changed.emit()
        d = p.ask_host.dialog
        self.assertIsNotNone(d)
        self.assertEqual([b.text() for b in d.buttons], ["做完了", "先不做"])
        boxes = d.findChildren(QCheckBox)
        self.assertEqual([b.text() for b in boxes], ["回邮件", "整理发票"])
        boxes[1].setChecked(True)
        d.note.setText("发票下周")
        d.reject()                                   # Esc 不关
        self.assertTrue(d.isVisible())
        d.close()                                    # Alt+F4 也不关
        self.assertTrue(d.isVisible())
        next(b for b in d.findChildren(QPushButton) if b.text() == "先不做").click()
        self.assertEqual(c.calls[-1], ("answer", {"choice": "先不做", "done": ["整理发票"], "note": "发票下周"}))
        self.assertIs(p.ask_host.dialog, d)          # 送到之前窗口留着、按钮变灰、不能重复提交
        self.assertFalse(d.buttons[0].isEnabled())
        d.buttons[0].click()
        self.assertEqual(len([x for x in c.calls if x[0] == "answer"]), 1)
        c.pending_ask["sending"] = False             # 没送到：恢复可点
        c.ask_changed.emit()
        self.assertTrue(d.buttons[0].isEnabled())
        c.confirm_answer()
        self.assertIsNone(p.ask_host.dialog)         # 服务端收到了才关掉

    def test_dialog_closed_when_run_ends(self):
        c = FakeCtrl()
        p = RightPanel(c)
        c.pending_ask = {"run_id": "r1", "path": "w.ask", "prompt": {"title": "提醒"}}
        c.ask_changed.emit()
        self.assertEqual([b.text() for b in p.ask_host.dialog.buttons], ["好的"])  # 没写按钮：默认一个“好的”
        c.pending_ask = None
        c.ask_changed.emit()
        self.assertIsNone(p.ask_host.dialog)
