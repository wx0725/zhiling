"""右侧面板：三个页签——运行（运行表单、运行前检查、运行结果）、节点（选中的节点、连线、端点或整个工作流的设置与运行记录）、
生成过程（每一轮的起草、校验、实跑检查）。“程序”页（JSON）暂不提供。

在画布上选中节点、连线或“输入”“输出”端点时自动切到“节点”页，这样新手点节点就能看到它的参数和修改入口。
运行页一直保留（离开“运行”再回来，填好的参数还在），换了打开的工作流（wf.id + file + source）才重新建一个；
其余页签只在显示时建、离开就拆掉。某一页出错时只在那一页显示原因，不影响其他部分。
控制器的信号可能一次连发好几个，这里合并到下一轮事件里只重画一次。"""
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QScrollArea, QWidget

from ..askdialog import AskHost
from .common import button, clear, commit_focused_now, flush_focused, hbox, label, qss, vbox
from .detail import NodeTab
from .genlog import GenerationLog
from .run import RunPanel

TABS = [("run", "运行"), ("node", "节点"), ("generate", "生成过程")]


def _error_box(e):
    w = label(f"这个面板没法显示：{e}", cls="error-text", select=True)
    w.setToolTip("".join(traceback.format_exception(e))[-2000:])
    return w


class RightPanel(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        # 马上要换工作流：正在输入的框先把改动交给还没换掉的那一份
        ctrl.leaving.connect(lambda: commit_focused_now(self))
        ctrl.commit_pending.connect(lambda: commit_focused_now(self, keep=True))
        self.setObjectName("rightPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(qss())
        lay = vbox(self)

        # 页签：一组分段按钮
        tabs = QFrame()
        tabs.setProperty("cls", "tabs")
        tl = hbox(tabs, spacing=2, margins=(3, 3, 3, 3))
        self.tab_btns = {}
        for k, text in TABS:
            b = button(text, cls="tab", wide=True, on=lambda k=k: self.ctrl.set_tab(k))
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.tab_btns[k] = b
            tl.addWidget(b)
        wrap = hbox(margins=(16, 12, 16, 0))
        wrap.addWidget(tabs)
        lay.addLayout(wrap)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("panelBody")
        self.body = vbox(body, margins=(18, 16, 18, 24))
        self.run_host = vbox()
        self.page_host = vbox()
        self.body.addLayout(self.run_host)
        self.body.addLayout(self.page_host)
        self.body.addStretch(1)
        self.scroll.setWidget(body)
        lay.addWidget(self.scroll, 1)

        self.ask_host = AskHost(ctrl, self)  # “提醒确认”节点等回答时弹出的提示窗

        self.run_panel = None
        self.run_error = None
        self.page = None        # 当前显示的页签部件（运行页之外）
        self.page_kind = None
        self._key = object()
        self._sel_key = None

        self._timer = QTimer(self, singleShot=True, interval=0, timeout=self.render)
        for sig in (ctrl.current_changed, ctrl.flow_changed, ctrl.selection_changed, ctrl.status_changed,
                    ctrl.gen_changed, ctrl.tab_changed, ctrl.modules_changed, ctrl.bugs_changed):
            sig.connect(self.schedule)
        ctrl.selection_changed.connect(self._selection_changed)
        ctrl.run_requested.connect(self._run_requested)
        self.render()

    def _selection_changed(self):
        """在画布上点了节点、连线或端点：切到“节点”页。只在选中的东西变了时切（同一个东西重复点不抢页签）"""
        c = self.ctrl
        key = (c.selected, bool(c.selected_edge))
        if key == self._sel_key:
            return
        self._sel_key = key
        if c.selected or c.selected_edge:
            c.set_tab("node")
            c.set_panel_open(True)

    def _run_requested(self):
        """画布上点了“开始”：按运行页填好的输入运行；输入有误（或运行页出错）就打开面板让使用者看到"""
        self.render()
        if self.run_panel is None or not self.run_panel.submit():
            self.ctrl.set_tab("run")
            self.ctrl.set_panel_open(True)

    def schedule(self, *_):
        if not self._timer.isActive():
            self._timer.start()

    def identity(self):
        c = self.ctrl.current
        return (c["wf"].get("id"), c.get("file") or "", c.get("source") or "") if c else "none"

    def render(self):
        self._timer.stop()
        c = self.ctrl
        tab = c.tab if c.tab in dict(TABS) else "run"
        for k, b in self.tab_btns.items():
            text = dict(TABS)[k]
            if k == "node" and c.selected_edge:
                text = "连线"
            if k == "generate" and c.generating:
                text += "…"
            b.setText(text)
            on = "true" if k == tab else "false"
            if b.property("on") != on:
                b.setProperty("on", on)
                b.style().unpolish(b)
                b.style().polish(b)

        key = self.identity()
        if key != self._key:  # 换了工作流：所有页签重新建
            self._key = key
            self._drop_page()
            if self.run_panel is not None:
                flush_focused(self.run_panel, self.ctrl)
            clear(self.run_host)
            self.run_panel = self.run_error = None
            try:
                self.run_panel = RunPanel(c)
                self.run_host.addWidget(self.run_panel)
            except Exception as e:
                self.run_error = _error_box(e)
                self.run_host.addWidget(self.run_error)

        # 运行页：一直在，只切换显示
        if self.run_panel is not None:
            self.run_panel.setVisible(tab == "run")
            try:
                self.run_panel.sync()
            except Exception as e:
                clear(self.run_host)
                self.run_panel = None
                self.run_error = _error_box(e)
                self.run_host.addWidget(self.run_error)
        if self.run_error is not None:
            self.run_error.setVisible(tab == "run")
        self._render_page(tab)

    def _drop_page(self):
        if self.page is not None:
            flush_focused(self.page, self.ctrl)
        clear(self.page_host)
        self.page = None
        self.page_kind = None

    def _render_page(self, tab):
        if tab not in ("generate", "node"):
            return self._drop_page()
        before = getattr(self.page, "mode", None) if self.page_kind == tab else object()
        if self.page_kind != tab:
            self._drop_page()
            self.page_kind = tab
            try:
                self.page = GenerationLog(self.ctrl) if tab == "generate" else NodeTab(self.ctrl)
            except Exception as e:
                self.page = _error_box(e)
            self.page_host.addWidget(self.page)
        if not hasattr(self.page, "sync"):
            return
        try:
            changed = self.page.sync()
        except Exception as e:
            # 出错的那一页换成说明；下次重画时重新建一个再试
            clear(self.page_host)
            self.page = None
            self.page_kind = None
            self.page_host.addWidget(_error_box(e))
            return
        if tab == "node" and getattr(self.page, "mode", None) != before:
            self.scroll.verticalScrollBar().setValue(0)  # 换了一个节点（或连线）：从头看起
        if tab == "generate" and changed and self.ctrl.generating:
            QTimer.singleShot(0, self._to_end)  # 生成中自动滚到最新

    def _to_end(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
