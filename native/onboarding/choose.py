"""引导 B 页：选择 AI 使用方式。云端 → go("C")，已有的本地 AI → go("D")，检测电脑（新手推荐）→ go("E")，
直接手动搭建 → go("main")，返回 → go("A")。四个选项互不限制：进主程序后随时可以从顶栏“配置 AI”重新选。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import C, FS, R, S

TEXT = {
    "lead": "织灵可以用本地 AI 或云端 AI 来理解你的需求、生成工作流；也可以完全不用 AI，直接手动搭建。"
            "现在选哪一种都可以，之后随时能在主程序里改。",
    "options": [
        {"go": "C", "title": "使用云端 AI",
         "text": "通过互联网使用 AI，对电脑性能要求不高，但可能需要账号、API Key，或支付一定的服务费用。"},
        {"go": "D", "title": "使用已有的本地 AI",
         "text": "AI 运行在你自己的电脑上，处理的内容通常不用发到云端，但需要电脑有一定的计算能力。"},
        {"go": "E", "title": "检测我的电脑", "tag": "新手推荐",
         "text": "不确定电脑能不能运行 AI？织灵会检测硬件配置，推荐适合的本地模型。"},
        {"go": "main", "title": "直接手动搭建",
         "text": "跳过 AI 配置，直接进入可视化编辑器，自己添加和连接模块来搭建自动化程序。"},
    ],
}

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QLabel#lead {{ color: {C['ink_2']}; font-size: {FS['lg']}px; }}
QFrame[role="option"] {{ background: {C['surface']}; border: 1.5px solid {C['line']}; border-radius: {R['xl']}px; }}
QFrame[role="option"]:hover {{ border-color: {C['line_strong']}; background: {C['hover']}; }}
QFrame[role="option"]:focus {{ border-color: {C['focus']}; }}
QLabel[role="opt_title"] {{ font-size: {FS['lg']}px; font-weight: 600; background: transparent; }}
QLabel[role="opt_text"] {{ color: {C['muted']}; background: transparent; }}
QLabel[role="tag"] {{ color: {C['on_ink']}; background: {C['led_ok']}; border-radius: {R['sm']}px;
                      padding: 2px 8px; font-size: {FS['sm']}px; font-weight: 600; }}
"""


class Option(QFrame):
    """可点的选项卡片：鼠标点击或键盘空格、回车都会触发。"""
    clicked = Signal()

    def __init__(self, title, text, tag=None):
        super().__init__()
        self.setProperty("role", "option")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)
        col = QVBoxLayout(self)
        col.setContentsMargins(S[5], S[5], S[5], S[5])
        col.setSpacing(S[2])
        head = QHBoxLayout()
        head.setSpacing(S[2])
        t = QLabel(title)
        t.setProperty("role", "opt_title")
        head.addWidget(t)
        if tag:
            g = QLabel(tag)
            g.setProperty("role", "tag")
            head.addWidget(g)
        head.addStretch(1)
        col.addLayout(head)
        d = QLabel(text)
        d.setProperty("role", "opt_text")
        d.setWordWrap(True)
        col.addWidget(d)
        col.addStretch(1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(e)


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 3, S[6], S[6] * 3, S[6])
        col.setSpacing(S[4])

        back = QPushButton("返回")
        back.setProperty("kind", "ghost")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.go.emit("A"))
        col.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)

        body = QWidget()
        body.setMaximumWidth(880)
        b = QVBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(S[4])
        title = QLabel("选择 AI 使用方式")
        title.setProperty("role", "h1")
        b.addWidget(title)
        lead = QLabel(TEXT["lead"])
        lead.setObjectName("lead")
        lead.setWordWrap(True)
        b.addWidget(lead)
        b.addSpacing(S[2])

        grid = QGridLayout()
        grid.setSpacing(S[4])
        for i, o in enumerate(TEXT["options"]):
            card = Option(o["title"], o["text"], o.get("tag"))
            card.setMinimumHeight(132)
            card.clicked.connect(lambda target=o["go"]: self.go.emit(target))
            grid.addWidget(card, i // 2, i % 2)
        b.addLayout(grid)

        mid = QHBoxLayout()
        mid.addStretch(1)
        mid.addWidget(body, 100)
        mid.addStretch(1)
        col.addLayout(mid)
        col.addStretch(2)
