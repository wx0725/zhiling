"""画布上方一行（网页版 .canvas-head）：左边工作流名称、说明、问题；右边编辑工具（撤销、重做、添加节点）、
草稿/改动标记、保存按钮、提示文字和布局开关。"""
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..logic.flow import issue_text
from ..theme import C, FS, R
from .widgets import ClickRow, FlowLayout, LayoutToggles, Popup

__all__ = ["CanvasHead", "AddMenu", "LayoutToggles", "menu_groups", "add_label"]

QSS = f"""
QWidget#canvasHead {{ background: {C['canvas']}; border-bottom: 1px solid {C['line']}; }}
QLabel#h1 {{ font-size: {FS['xl']}px; font-weight: 650; }}
QLabel#intent {{ color: {C['muted']}; }}
QLabel#issue {{ color: {C['issue_ink']}; font-size: {FS['md']}px; }}
QLabel#summary {{ color: {C['bad_ink']}; font-size: {FS['md']}px; font-weight: 600; background: transparent; }}
QWidget#canvasHead QPushButton[small="true"] {{ padding: 5px 10px; font-size: {FS['md']}px; border-radius: {R['sm']}px; }}
QLabel#draftTag {{ font-size: {FS['md']}px; color: {C['warn_ink']}; background: {C['warn_bg']}; padding: 4px 10px; border-radius: {R['sm']}px; }}
QLabel#notice {{ color: {C['muted']}; font-size: {FS['md']}px; }}
QFrame#editTools {{ border: 0; border-right: 1px solid {C['line']}; }}
QFrame#editTools QPushButton[kind="ghost"] {{ border-color: transparent; }}
QFrame#editTools QPushButton[kind="ghost"]:disabled {{ background: transparent; color: rgba(155,155,164,0.55); }}
"""

MENU_QSS = f"""
QLabel#menuTitle {{ font-size: {FS['sm']}px; color: {C['muted']}; padding: 4px 8px; background: transparent; }}
QFrame#menuItem {{ background: transparent; border-radius: {R['sm']}px; }}
QFrame#menuItem:hover, QFrame#menuItem:focus {{ background: {C['hover']}; }}
QLabel#menuName {{ background: transparent; }}
QLabel#menuSub {{ font-size: {FS['sm']}px; color: {C['muted']}; background: transparent; }}
QFrame#menuSep {{ background: {C['line']}; max-height: 1px; min-height: 1px; border: 0; }}
"""


def add_label(loop):
    """添加节点按钮的文字：选中循环（或循环里的节点）时新节点加进那个循环"""
    return f"添加节点{f'到循环 {loop.split(chr(47))[-1]}' if loop else ''}"


def menu_groups(modules, loop):
    """添加节点菜单的内容：[(组名, [(名称, 一句说明, spec)])]。模块按类别分组（按模块列表的顺序），最后是“流程”；
    已经在循环里时不能再加逐项循环"""
    groups = {}
    for key, m in (modules or {}).items():
        sub = m.get("description").split("。")[0] if isinstance(m.get("description"), str) else ""
        groups.setdefault(m.get("category") or "其他", []).append((m.get("name") or key, sub, {"kind": "module", "module": key}))
    out = list(groups.items())
    flow = [("条件分支", "按真假走 then 或 else", {"kind": "if"}), ("合并", "把两条支路合成一个值", {"kind": "merge"})]
    if not loop:
        flow.append(("逐项循环", "对列表的每一项做同样的事", {"kind": "foreach"}))
    out.append(("流程", flow))
    return out


class Arrow(QWidget):
    """<details> 标题前的小三角：收起时朝右，展开时朝下"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(10, 18)
        self._open = False

    def set_open(self, on):
        self._open = on
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["bad_ink"]))
        pts = [QPointF(1, 6), QPointF(9, 6), QPointF(5, 12)] if self._open else [QPointF(2, 5), QPointF(8, 9), QPointF(2, 13)]
        p.drawPolygon(QPolygonF(pts))


class _MenuItem(ClickRow):
    def __init__(self, name, sub):
        super().__init__()
        self.setObjectName("menuItem")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(name)
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        col.setSpacing(0)
        n = QLabel(name, objectName="menuName")
        col.addWidget(n)
        if sub:
            s = QLabel(sub, objectName="menuSub")
            s.setWordWrap(True)
            col.addWidget(s)


class AddMenu(QWidget):
    """添加节点菜单：模块按类别分组，另有条件分支、合并、逐项循环。on_add(spec)"""

    def __init__(self, ctrl, on_add):
        super().__init__()
        self.ctrl = ctrl
        self.on_add = on_add
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.btn = QPushButton(add_label(None))
        self.btn.setProperty("small", True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.toggle)
        row.addWidget(self.btn)
        self.popup = None

    def set_loop(self, loop):
        self.btn.setText(add_label(loop))

    def toggle(self):
        if self.popup is not None and self.popup.isVisible():
            self.popup.close()
            return
        self.open_menu()

    def open_menu(self):
        loop = self.ctrl.loop_scope()
        pop = Popup(self.window(), width=300)
        pop.setStyleSheet(pop.styleSheet() + MENU_QSS)
        outer = QVBoxLayout(pop)
        outer.setContentsMargins(1, 1, 1, 1)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(3, 3, 3, 3)
        col.setSpacing(0)
        first = None
        for gi, (cat, items) in enumerate(menu_groups(self.ctrl.modules, loop)):
            if gi:
                col.addSpacing(4)
                sep = QFrame(objectName="menuSep")
                col.addWidget(sep)
                col.addSpacing(4)
            col.addWidget(QLabel(cat, objectName="menuTitle"))
            for name, sub, spec in items:
                it = _MenuItem(name, sub)
                it.clicked.connect(lambda spec=spec: self._pick(spec))
                col.addWidget(it)
                first = first or it
        area.setWidget(body)
        outer.addWidget(area)
        body.adjustSize()
        limit = int(self.window().height() * 0.6)  # .menu 的 max-height: 60vh
        area.setFixedHeight(min(limit, body.sizeHint().height() + 2))
        self.popup = pop
        pop.closed.connect(lambda p=pop: self._forget(p))  # 关掉后它会被删掉：不再引用
        pop.show_under(self.btn, align="right", gap=6)
        if first:
            first.setFocus()

    def _forget(self, pop):
        if self.popup is pop:
            self.popup = None

    def _pick(self, spec):
        if self.popup is not None:
            self.popup.close()
        self.on_add(spec)


class CanvasHead(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("canvasHead")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(QSS)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 14, 20, 10)
        row.setSpacing(16)

        titles = QWidget()
        titles.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tc = QVBoxLayout(titles)
        tc.setContentsMargins(0, 0, 0, 0)
        tc.setSpacing(0)
        self.title = QLabel(objectName="h1")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tc.addWidget(self.title)
        self.intent = QLabel(objectName="intent")
        self.intent.setWordWrap(True)
        self.intent.setMaximumWidth(620)  # 网页版 max-width: 70ch
        self.intent.setContentsMargins(0, 2, 0, 0)
        self.intent.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tc.addWidget(self.intent)
        self.problems = QVBoxLayout()
        self.problems.setContentsMargins(0, 6, 0, 0)
        self.problems.setSpacing(4)
        tc.addLayout(self.problems)
        # 编辑后校验出的问题：默认收起，点标题展开（网页版 <details>）
        self.checks = QWidget()
        cc = QVBoxLayout(self.checks)
        cc.setContentsMargins(0, 8, 0, 0)
        cc.setSpacing(0)
        self.summary_row = ClickRow()
        self.summary_row.setStyleSheet("background: transparent;")
        sr = QHBoxLayout(self.summary_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(6)
        self.arrow = Arrow()
        sr.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignTop)
        self.summary = QLabel(objectName="summary")
        self.summary.setWordWrap(True)
        self.summary.setMaximumWidth(620)
        sr.addWidget(self.summary, 1)
        self.summary_row.clicked.connect(self._toggle_checks)
        cc.addWidget(self.summary_row)
        self.check_list_w = QWidget()
        self.check_list_w.setMaximumWidth(620)
        self.check_list = QVBoxLayout(self.check_list_w)
        self.check_list.setContentsMargins(0, 6, 0, 0)
        self.check_list.setSpacing(4)
        cc.addWidget(self.check_list_w)
        self.checks_open = False
        tc.addWidget(self.checks)
        row.addWidget(titles, 1, Qt.AlignmentFlag.AlignTop)

        acts = self.acts_w = QWidget()
        acts.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.acts = FlowLayout(acts, spacing=12, align="right")
        self.tools = QFrame(objectName="editTools")
        tl = QHBoxLayout(self.tools)
        tl.setContentsMargins(0, 0, 8, 0)
        tl.setSpacing(4)
        self.undo = self._small("撤销", "撤销（Ctrl+Z）", lambda: ctrl.travel("undo"))
        self.redo = self._small("重做", "重做（Ctrl+Shift+Z）", lambda: ctrl.travel("redo"))
        self.add_menu = AddMenu(ctrl, self._add)
        for w in (self.undo, self.redo, self.add_menu):
            tl.addWidget(w)
        self.acts.addWidget(self.tools)
        self.dirty_tag = QLabel("有改动，还没保存", objectName="draftTag")
        self.draft_tag = QLabel(objectName="draftTag")
        self.stop_gen = self._small("停止生成", "停止这次生成，画布上留着已经画出的版本", ctrl.stop_generate)
        self.save_btn = QPushButton()
        self.save_btn.setProperty("kind", "primary")
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(ctrl.save)
        self.notice = QLabel(objectName="notice")
        self.toggles = LayoutToggles(ctrl)
        for w in (self.dirty_tag, self.draft_tag, self.stop_gen, self.save_btn, self.notice, self.toggles):
            self.acts.addWidget(w)
        row.addWidget(acts, 0, Qt.AlignmentFlag.AlignTop)

        for sig in (ctrl.current_changed, ctrl.history_changed, ctrl.notice_changed, ctrl.checks_changed,
                    ctrl.gen_changed, ctrl.selection_changed, ctrl.modules_changed):
            sig.connect(self.render)
        self._last_checks = None
        self.render()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 网页版两边都可以缩：右边的按钮最多占六成宽，放不下就换行，名称不会被挤成一条
        self.acts_w.setMaximumWidth(max(self.acts.minimumSize().width(), int(self.width() * 0.6)))

    def _small(self, text, tip, fn):
        b = QPushButton(text)
        b.setProperty("kind", "ghost")
        b.setProperty("small", True)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    def _add(self, spec):
        """新节点放在画布当前视野中间（画布还没有这个方法时由控制器自己找位置）"""
        canvas = getattr(self.window(), "canvas", None)
        fn = getattr(canvas, "view_center_scene", None)
        center = None
        if callable(fn):
            try:
                c = fn()
            except Exception:
                c = None
            if isinstance(c, QPointF):
                center = {"x": c.x(), "y": c.y()}
            elif isinstance(c, dict) and "x" in c and "y" in c:
                center = {"x": c["x"], "y": c["y"]}
        self.ctrl.add_at(spec, center)

    def _toggle_checks(self):
        self.checks_open = not self.checks_open
        self.render()

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    @staticmethod
    def _issue(text, tip=None):
        lab = QLabel(f"•  {text}", objectName="issue")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if tip:
            lab.setToolTip(tip)
        return lab

    def render(self):
        ctrl, c = self.ctrl, self.ctrl.current
        if not c:
            return
        wf = c["wf"]
        self.title.setText(wf.get("name") or "")
        self.intent.setText(wf.get("intent") or "")
        self.intent.setVisible(bool(wf.get("intent")))
        self._clear(self.problems)
        for x in c.get("problems") or []:
            self.problems.addWidget(self._issue(str(x)))

        errs = ctrl.check_errors if c["dirty"] else []
        self.checks.setVisible(bool(errs))
        if errs:
            self.arrow.set_open(self.checks_open)
            self.summary.setText(f"这张图现在有 {len(errs)} 个问题，改好之前不能保存，运行也会被拒绝")
            if errs != self._last_checks:
                self._last_checks = list(errs)
                self._clear(self.check_list)
                for x in errs:
                    self.check_list.addWidget(self._issue(issue_text(x), str(x)))
            self.check_list_w.setVisible(self.checks_open)

        editable = ctrl.editable
        self.tools.setVisible(editable)
        self.undo.setEnabled(ctrl.can_undo())
        self.redo.setEnabled(ctrl.can_redo())
        self.add_menu.set_loop(ctrl.loop_scope())
        self.dirty_tag.setVisible(bool(c["dirty"]))
        self.draft_tag.setVisible(c["source"] == "draft" and not c["dirty"])
        self.draft_tag.setText("草稿，正在检查" if ctrl.generating else "草稿：没有通过检查，不能保存")
        self.stop_gen.setVisible(bool(ctrl.generating))
        show_save = c["source"] == "generated" or bool(c["dirty"])
        self.save_btn.setVisible(show_save)
        blocked = bool(c["dirty"]) and bool(ctrl.check_errors)
        self.save_btn.setEnabled(not ctrl.generating and not blocked)
        self.save_btn.setToolTip("先改好图上标出的问题再保存" if blocked else "")
        self.save_btn.setText("保存" if c["source"] == "saved" else "保存到我的")
        self.notice.setText(ctrl.notice)
        self.notice.setVisible(bool(ctrl.notice))
        self.acts.invalidate()
        self.updateGeometry()
