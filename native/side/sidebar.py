"""左侧栏（网页版 .side）：项目列表（新建、切换、重命名、删除）、没打开工作流时的提示、“我保存的”和“示例”两组工作流。"""
from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..theme import C, FS, R
from .widgets import ClickRow, IconButton, Popup, menu_button, more_icon, plus_icon, set_prop

EMPTY_TEXT = "还没有项目。点右上角的“+”新建一个，或者直接在上面输入需求、打开下面的示例。"

QSS = f"""
QWidget#side {{ background: {C['rail']}; }}
QLabel[role="h2"] {{ font-size: {FS['sm']}px; font-weight: 600; color: {C['muted']}; letter-spacing: 0; }}
QLabel#sideEmpty {{ font-size: {FS['sm']}px; color: {C['muted']}; }}
QLabel#sideNotice {{ font-size: {FS['md']}px; color: {C['muted']}; }}
QFrame[row="proj"] {{ border-radius: {R['md']}px; background: transparent; }}
QFrame[row="proj"]:hover {{ background: {C['hover']}; }}
QFrame[row="proj"][on="true"] {{ background: {C['surface']}; }}
QLabel#projName {{ color: {C['ink_2']}; background: transparent; }}
QLabel#projName[on="true"] {{ color: {C['ink']}; font-weight: 600; }}
QFrame[row="proj"] QToolButton[icon_btn="true"]:hover {{ background: {C['line']}; }}
QLineEdit#projRename {{ padding: 5px 8px; margin: 3px 4px; }}
QFrame[row="wf"] {{ border-radius: {R['md']}px; background: transparent; }}
QFrame[row="wf"]:hover {{ background: {C['hover']}; }}
QFrame[row="wf"][on="true"] {{ background: {C['surface']}; }}
QLabel#wfName {{ font-weight: 600; background: transparent; }}
QLabel#wfIntent {{ font-size: {FS['sm']}px; color: {C['muted']}; background: transparent; }}
"""


def clamp_lines(label, text, lines=2):
    """.wf-intent 的两行截断：超出两行用省略号收尾（按标签当前宽度计算）"""
    fm = label.fontMetrics()
    width = max(40, label.width())
    out, rest = [], text
    for i in range(lines):
        if not rest:
            break
        if i == lines - 1:
            out.append(fm.elidedText(rest, Qt.TextElideMode.ElideRight, width))
            break
        # 二分找放得下的最长前缀（逐字缩短在很长的说明上会卡住界面）
        lo, hi = 1, len(rest)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if fm.horizontalAdvance(rest[:mid]) <= width:
                lo = mid
            else:
                hi = mid - 1
        n = lo
        out.append(rest[:n])
        rest = rest[n:]
    return "\n".join(out)


class ClampLabel(QLabel):
    """最多显示两行、超出加省略号的说明文字；宽度变了重新截断"""

    def __init__(self, text, lines=2):
        super().__init__()
        self._full = text
        self._lines = lines
        self.setObjectName("wfIntent")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        t = clamp_lines(self, self._full, self._lines)
        if t != self.text():
            self.setText(t)

    def sizeHint(self):
        s = super().sizeHint()
        rows = min(self._lines, max(1, self.text().count("\n") + 1 if self.text() else 1))
        return QSize(s.width(), self.fontMetrics().lineSpacing() * rows)

    def minimumSizeHint(self):
        return self.sizeHint()


def paint_on_bar(w):
    """选中行左边 3px 的浅色竖条（网页版 box-shadow: inset 3px 0 0），随圆角裁掉"""
    if w.property("on") is not True:
        return
    p = QPainter(w)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(w.rect()), R["md"], R["md"])
    p.setClipPath(path)
    p.fillRect(QRectF(0, 0, 3, w.height()), QColor(C["ink"]))


class ElideLabel(QLabel):
    """一行放不下时末尾省略（.proj-name 的 text-overflow: ellipsis）"""

    def __init__(self):
        super().__init__(objectName="projName")
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_full(self, text):
        self._full = text
        self._elide()

    def _elide(self):
        t = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(20, self.width()))
        if t != self.text():
            self.setText(t)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()


class Dot(QWidget):
    """.proj-dot：正在生成的项目旁的橙色小灯"""

    def __init__(self):
        super().__init__()
        self.setFixedSize(13, 13)
        self.setToolTip("正在生成")
        self.setAccessibleName("正在生成")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(240, 169, 74, 46))
        p.drawEllipse(0, 0, 13, 13)
        p.setBrush(QColor(C["led_run"]))
        p.drawEllipse(3, 3, 7, 7)


class RenameField(QLineEdit):
    """就地改名：回车或点别处确认，Esc 放弃。on_done(新名字或 None) 只调用一次"""

    def __init__(self, name, on_done):
        super().__init__(name)
        self.setObjectName("projRename")
        self.setMaxLength(80)
        self.setAccessibleName("项目名")
        self._on_done = on_done
        self._done = False
        QTimer.singleShot(0, self._grab)

    def _grab(self):
        self.setFocus()
        self.selectAll()

    def finish(self, value):
        if self._done:
            return
        self._done = True
        QTimer.singleShot(0, lambda: self._on_done(value))  # 等这次按键、失焦事件处理完再改界面

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(self.text().strip())
        elif e.key() == Qt.Key.Key_Escape:
            self.finish(None)
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() != Qt.FocusReason.PopupFocusReason:
            self.finish(self.text().strip())


class ProjectRow(QFrame):
    """一行项目：点名字切换，双击名字改名；右边“⋯”（悬停时才显示）打开重命名、删除菜单"""

    def __init__(self, side, p):
        super().__init__()
        self.side = side
        self.p = p
        self.setProperty("row", "proj")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)

        self.item = ClickRow()
        self.item.setStyleSheet("background: transparent;")
        it = QHBoxLayout(self.item)
        it.setContentsMargins(10, 8, 4, 8)
        it.setSpacing(8)
        self.name = ElideLabel()
        it.addWidget(self.name, 1)
        self.dot = Dot()
        it.addWidget(self.dot)
        self.item.clicked.connect(lambda: side.ctrl.open_project(self.p["id"]))
        self.item.double_clicked.connect(lambda: side.start_rename(self.p["id"]))
        self._row.addWidget(self.item, 1)

        self.more = IconButton(lambda c: more_icon(c), size=26, tip="更多操作")
        self.more.clicked.connect(self._menu)
        self._row.addWidget(self.more)
        self._row.addSpacing(4)
        self.rename = None
        self._menu_open = False
        self.update_from(p)

    def update_from(self, p):
        self.p = p
        ctrl = self.side.ctrl
        name = p.get("name") or ""
        on = p.get("id") == ctrl.project
        set_prop(self, "on", on)
        set_prop(self.name, "on", on)
        self.name.set_full(name)
        self.item.setToolTip(name)
        self.item.setAccessibleName(name)
        self.more.setAccessibleName(f"“{name}”的更多操作")
        self.dot.setVisible(ctrl.gen_project == p.get("id"))
        self._show_more()

    def paintEvent(self, e):
        super().paintEvent(e)
        paint_on_bar(self)


    def _show_more(self):
        # .proj-more 平时透明，悬停、键盘聚焦或菜单打开时才显示
        visible = self.rename is None and (self.underMouse() or self._menu_open or self.more.hasFocus())
        self.more.setVisible(self.rename is None)
        self.more.set_maker(more_icon if visible else (lambda c: more_icon("transparent")))

    def event(self, e):
        if e.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave, QEvent.Type.Enter, QEvent.Type.Leave):
            QTimer.singleShot(0, self._show_more)
        return super().event(e)

    def _menu(self):
        pop = Popup(self, width=140)
        col = QVBoxLayout(pop)
        col.setContentsMargins(4, 4, 4, 4)
        col.setSpacing(0)
        rn = menu_button("重命名")
        de = menu_button("删除", danger=True)
        col.addWidget(rn)
        col.addWidget(de)

        def do_rename():
            pop.close()
            self.side.start_rename(self.p["id"])

        def do_delete():
            pop.close()
            QTimer.singleShot(0, lambda: self.side.ctrl.delete_project(self.p))
        rn.clicked.connect(do_rename)
        de.clicked.connect(do_delete)
        self._menu_open = True
        pop.closed.connect(self._menu_closed)
        pop.show_under(self.more, align="right", gap=2)
        self._show_more()

    def _menu_closed(self):
        self._menu_open = False
        self._show_more()

    def set_editing(self, on):
        if on and self.rename is None:
            self.item.hide()
            pid, name = self.p["id"], self.p.get("name") or ""
            self.rename = RenameField(name, lambda v: self.side.rename_done(pid, name, v))
            self._row.insertWidget(0, self.rename, 1)
        elif not on and self.rename is not None:
            self.rename.deleteLater()
            self.rename = None
            self.item.show()
        self._show_more()


class WorkflowRow(ClickRow):
    """.wf-item：名称一行，说明最多两行"""

    def __init__(self, item, on):
        super().__init__()
        self.setProperty("row", "wf")
        self.setProperty("on", on)
        col = QVBoxLayout(self)
        col.setContentsMargins(10, 9, 10, 9)
        col.setSpacing(2)
        name = QLabel(item.get("name") or "", objectName="wfName")
        name.setWordWrap(True)
        col.addWidget(name)
        if item.get("intent"):
            col.addWidget(ClampLabel(item["intent"]))

    def paintEvent(self, e):
        super().paintEvent(e)
        paint_on_bar(self)


def is_current(ctrl, item):
    c = ctrl.current
    return bool(c) and c.get("file") == item.get("file") and c.get("source") == item.get("source")


class Sidebar(QScrollArea):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"QScrollArea {{ background: {C['rail']}; border: 0; border-right: 1px solid {C['line']}; }}")
        body = QWidget(objectName="side")
        body.setStyleSheet(QSS)
        self.setWidget(body)
        col = QVBoxLayout(body)
        col.setContentsMargins(12, 16, 12, 16)
        col.setSpacing(0)

        # 项目
        head = QHBoxLayout()
        head.setContentsMargins(10, 0, 2, 6)
        h2 = QLabel("项目")
        h2.setProperty("role", "h2")
        head.addWidget(h2)
        head.addStretch(1)
        self.add_btn = IconButton(lambda c: plus_icon(c), size=26, tip="新建项目")
        self.add_btn.clicked.connect(ctrl.new_project)
        head.addWidget(self.add_btn)
        col.addLayout(head)
        self.empty = QLabel(EMPTY_TEXT, objectName="sideEmpty")
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(10, 0, 10, 0)
        col.addWidget(self.empty)
        self.proj_box = QVBoxLayout()
        self.proj_box.setSpacing(0)
        col.addLayout(self.proj_box)
        self.rows = {}        # 项目编号 → ProjectRow：按编号保留，刷新列表时正在改的名字不丢
        self.editing = None

        self.notice = QLabel(objectName="sideNotice")
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(10, 8, 10, 0)
        col.addWidget(self.notice)

        self.groups = QVBoxLayout()
        self.groups.setSpacing(0)
        col.addLayout(self.groups)
        col.addStretch(1)

        ctrl.projects_changed.connect(self.render_projects)
        ctrl.items_changed.connect(self.render_items)
        ctrl.current_changed.connect(self.render_items)
        ctrl.current_changed.connect(self.render_notice)
        ctrl.notice_changed.connect(self.render_notice)
        self.render_projects()
        self.render_items()
        self.render_notice()

    # ---------- 项目 ----------
    def render_projects(self):
        ps = [p for p in self.ctrl.projects if isinstance(p, dict) and "id" in p]
        self.empty.setVisible(not ps)
        ids = [p["id"] for p in ps]
        for pid in list(self.rows):
            if pid not in ids:
                self.rows.pop(pid).deleteLater()
        while self.proj_box.count():
            self.proj_box.takeAt(0)  # 只拿出来重新排序，不删控件（焦点不丢）
        for p in ps:
            row = self.rows.get(p["id"])
            if row is None:
                row = self.rows[p["id"]] = ProjectRow(self, p)
                row.set_editing(self.editing == p["id"])
            else:
                row.update_from(p)
            self.proj_box.addWidget(row)
        if self.editing not in self.rows:
            self.editing = None

    def start_rename(self, pid):
        if self.editing and self.editing != pid and self.editing in self.rows:
            self.rows[self.editing].set_editing(False)
        self.editing = pid
        if pid in self.rows:
            self.rows[pid].set_editing(True)

    def rename_done(self, pid, old, value):
        if self.editing == pid:
            self.editing = None
        if pid in self.rows:
            self.rows[pid].set_editing(False)
        if value and value != old:
            self.ctrl.rename_project(pid, value)

    # ---------- 提示、工作流 ----------
    def render_notice(self):
        text = self.ctrl.notice
        self.notice.setText(text)
        self.notice.setVisible(bool(text) and not self.ctrl.current)

    def render_items(self):
        while self.groups.count():
            it = self.groups.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        saved = self.ctrl.saved_items()
        if saved:
            self.groups.addWidget(self._group("我保存的", saved))
        self.groups.addWidget(self._group("示例", self.ctrl.example_items()))

    def _group(self, title, items):
        g = QWidget()
        col = QVBoxLayout(g)
        col.setContentsMargins(0, 20, 0, 0)  # .group + .group 的间距
        col.setSpacing(0)
        h2 = QLabel(title)
        h2.setProperty("role", "h2")
        h2.setContentsMargins(10, 0, 10, 6)
        col.addWidget(h2)
        for i in items:
            row = WorkflowRow(i, is_current(self.ctrl, i))
            row.clicked.connect(lambda i=i: self.ctrl.open(i))
            col.addWidget(row)
        return g
