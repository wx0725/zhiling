"""屏幕中央的确认框（对应网页版的 dialog.jsx）：标题、说明、取消和确定两个按钮。
打开时按 Esc 或点框外的遮罩算取消，按 Enter 算确定；焦点先落在“确定”上。确认框开着时整个窗口不可操作。

用法：if ask(window, title="…", message="…", ok="删除", cancel="留下", danger=True): ...
ask 会等使用者回答再返回；回答期间界面照常刷新，所以返回后调用方要先核对状态有没有变（例如工作流已经换了）。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .theme import FS, R, C

_open = None  # 同一时间只有一个确认框：新的会把旧的当作取消


class _Backdrop(QWidget):
    """盖住整个窗口的半透明遮罩；点它算取消"""

    def __init__(self, parent, on_click):
        super().__init__(parent)
        self._on_click = on_click
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))

    def mousePressEvent(self, e):
        self._on_click()


class ConfirmDialog(QDialog):
    def __init__(self, parent, title, message, ok, cancel, danger):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setObjectName("confirmBox")
        box.setStyleSheet(f"QFrame#confirmBox {{ background: {C['surface']}; border: 1px solid {C['line_strong']}; border-radius: {R['xl']}px; }}")
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setWordWrap(True)
        t.setStyleSheet(f"font-size: {FS['lg'] + 1}px; font-weight: 650; background: transparent;")
        m = QLabel(message)
        m.setWordWrap(True)
        m.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        m.setStyleSheet(f"color: {C['ink_2']}; background: transparent;")
        lay.addWidget(t)
        lay.addWidget(m)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton(cancel)
        no.setProperty("kind", "ghost")
        yes = QPushButton(ok)
        yes.setProperty("kind", "danger" if danger else "primary")
        no.clicked.connect(self.reject)
        yes.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addSpacing(8)
        lay.addLayout(row)
        yes.setDefault(True)
        yes.setFocus()
        self.setFixedWidth(440)


def ask(parent, title="请确认", message="", ok="确定", cancel="取消", danger=False) -> bool:
    global _open
    if _open is not None:
        _open.reject()
    win = parent.window() if parent else None
    dlg = ConfirmDialog(win, title, message, ok, cancel, danger)
    backdrop = _Backdrop(win, dlg.reject) if win else None
    dlg.adjustSize()
    if win:
        g = win.geometry()
        dlg.move(g.center().x() - dlg.width() // 2, g.center().y() - dlg.height() // 2)
    _open = dlg
    try:
        answer = dlg.exec() == QDialog.DialogCode.Accepted
    finally:
        if _open is dlg:
            _open = None
        if backdrop:
            backdrop.deleteLater()
    return answer
