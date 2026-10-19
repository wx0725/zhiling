"""引导各页共用的小工具。"""
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QScrollArea, QVBoxLayout

from ..theme import S


def label(text, role=None, wrap=True):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    return w


def button(text, kind=None, small=False):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if small:
        b.setProperty("small", True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card():
    f = QFrame()
    f.setProperty("role", "card")
    col = QVBoxLayout(f)
    col.setContentsMargins(S[5], S[5], S[5], S[5])
    col.setSpacing(S[3])
    return f, col


def restyle(w):
    w.style().unpolish(w)
    w.style().polish(w)


def clear(layout):
    """清空布局：控件先藏起来再删，删除排队期间也不会还显示着"""
    while layout.count():
        it = layout.takeAt(0)
        if it.widget():
            it.widget().hide()
            it.widget().deleteLater()
        elif it.layout():
            clear(it.layout())


def alive(widget, fn):
    """包一层回调：widget 已经被删掉（换了服务商、重新检测）时什么都不做。后台请求的结果可能晚于页面变化才回来"""
    def call(*args):
        if shiboken6.isValid(widget):
            fn(*args)
    return call


def scrolled(widget):
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    s.setWidget(widget)
    return s
