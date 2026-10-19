"""“提醒确认”模块的提示窗：工作流运行到这个节点时弹出，显示标题、内容、可勾选的待办、备注框和几个按钮；
点了按钮就把回答 {choice, done, note} 交回控制器（Controller.answer_ask），工作流接着往下跑。

不是模态的：弹着的时候画布照常能看、能点“终止”；按 Esc 不会关掉它（关掉了工作流也还在等），要停就点画布上的“终止”。
运行结束（完成、出错、终止）时由右侧面板关掉它。同一时间最多一个。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .theme import C, FS, R


class AskDialog(QDialog):
    def __init__(self, parent, prompt: dict, on_answer):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.on_answer = on_answer
        self.prompt = prompt or {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame(objectName="askBox")
        box.setStyleSheet(f"QFrame#askBox {{ background: {C['surface']}; border: 1px solid {C['line_strong']}; border-radius: {R['xl']}px; }}"
                          f"QLabel {{ background: transparent; }}")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)

        tag = QLabel("工作流在等你")
        tag.setStyleSheet(f"font-size: {FS['sm']}px; color: {C['warn_ink']}; font-weight: 600;")
        lay.addWidget(tag)
        title = QLabel(str(self.prompt.get("title") or "请确认"))
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size: {FS['lg'] + 1}px; font-weight: 650;")
        title.setAccessibleName("提醒标题")
        lay.addWidget(title)

        detail = str(self.prompt.get("detail") or "")
        if detail:
            d = QLabel(detail)
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            d.setStyleSheet(f"color: {C['ink_2']};")
            d.setAccessibleName("提醒内容")
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setWidget(d)
            area.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
            area.setMaximumHeight(260)
            lay.addWidget(area)

        self.checks = []
        items = [str(x) for x in (self.prompt.get("items") or []) if isinstance(x, (str, int, float))]
        if items:
            h = QLabel("待办")
            h.setStyleSheet(f"font-size: {FS['sm']}px; color: {C['muted']}; font-weight: 600;")
            lay.addWidget(h)
            for x in items:
                cb = QCheckBox(x)
                cb.setAccessibleName(f"待办：{x}")
                lay.addWidget(cb)
                self.checks.append((x, cb))

        self.note = QLineEdit()
        self.note.setPlaceholderText("备注（可不填）")
        self.note.setAccessibleName("备注")
        lay.addWidget(self.note)

        row = QHBoxLayout()
        row.addStretch(1)
        options = [str(o) for o in (self.prompt.get("options") or []) if isinstance(o, (str, int, float))] or ["好的"]
        self.buttons = []
        for i, o in enumerate(options):
            b = QPushButton(o)
            b.setProperty("kind", "primary" if i == 0 else "ghost")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=o: self.answer(o))
            row.addWidget(b)
            self.buttons.append(b)
        lay.addSpacing(8)
        lay.addLayout(row)
        self.buttons[0].setDefault(True)
        self.buttons[0].setFocus()
        self.setFixedWidth(480)

    def answer(self, choice):
        """点了按钮：把回答交出去，按钮变灰等服务端确认收到；窗口由 AskHost 在回答确认后关掉（送不出去时恢复可点）"""
        if not self.on_answer:
            return
        done = [x for x, cb in self.checks if cb.isChecked()]
        self.set_sending(True)
        self.on_answer({"choice": choice, "done": done, "note": self.note.text().strip()})

    def set_sending(self, on):
        for b in self.buttons:
            b.setEnabled(not on)

    def reject(self):
        pass  # Esc 不关：工作流还在等；要停请点画布上的“终止”

    def dismiss(self):
        """程序主动关掉（回答已送到、运行结束、换了工作流）"""
        self.on_answer = None
        self.close()

    def closeEvent(self, e):
        if self.on_answer is not None:  # 使用者按 Alt+F4 之类：不关，工作流还在等
            e.ignore()
            return
        super().closeEvent(e)


def place(dlg, win):
    """放在主窗口中央偏上；没有窗口（关着主窗口在后台跑）就放屏幕中央"""
    dlg.adjustSize()
    if win is not None and win.isVisible():
        g = win.geometry()
        dlg.move(g.center().x() - dlg.width() // 2, g.y() + max(80, g.height() // 4))
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


class AskHost(QWidget):
    """把控制器的 pending_ask 变成提示窗：有要回答的就开一个，回答了或运行结束就关掉"""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.dialog = None
        self._key = None
        ctrl.ask_changed.connect(self.sync)
        self.hide()

    def sync(self):
        p = self.ctrl.pending_ask
        key = (p["run_id"], p["path"]) if p else None
        if key == self._key:
            if self.dialog is not None and p:
                self.dialog.set_sending(bool(p.get("sending")))  # 送出去了等确认 / 没送到恢复可点
            return
        self._key = key
        if self.dialog is not None:
            d, self.dialog = self.dialog, None
            d.dismiss()
            d.deleteLater()
        if p:
            self.dialog = AskDialog(self.window(), p["prompt"], self.ctrl.answer_ask)
            self.dialog.set_sending(bool(p.get("sending")))
            place(self.dialog, self.window())
