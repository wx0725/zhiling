"""引导 A 页：欢迎。产品名、两段介绍；“开始使用”发出 go("B")，“了解详情”发出 go("F")。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..theme import C, FS, S

TEXT = {
    "name": "织灵 Weavling",
    "intro": [
        "织灵是一款面向所有人的可视化自动化工作流工具。无需编程基础，只需用日常语言描述你想完成的事情，"
        "AI 就会逐步引导你明确需求，并自动选择、组合和配置模块，将你的想法转化为可以实际运行的工作流。",
        "你可以自由查看、调整和组合工作流中的每一个模块，也可以完全不依赖 AI，亲手搭建自己的自动化程序。"
        "软件支持接入本地或云端 AI，让电脑按照设定的流程持续执行任务，并通过桌面助手反馈运行状态，"
        "让复杂的自动化变得简单、直观且可控。",
    ],
}

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QLabel#name {{ font-size: 40px; font-weight: 700; }}
QLabel[role="intro"] {{ color: {C['ink_2']}; font-size: {FS['lg']}px; }}
"""


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 4, S[6], S[6] * 4, S[6])
        col.addStretch(1)

        body = QWidget()
        body.setMaximumWidth(720)
        b = QVBoxLayout(body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(S[4])

        name = QLabel(TEXT["name"])
        name.setObjectName("name")
        b.addWidget(name)
        b.addSpacing(S[2])
        for text in TEXT["intro"]:
            p = QLabel(text)
            p.setProperty("role", "intro")
            p.setWordWrap(True)
            b.addWidget(p)
        b.addSpacing(S[4])

        row = QHBoxLayout()
        row.setSpacing(S[3])
        start = QPushButton("开始使用")
        start.setProperty("kind", "primary")
        start.clicked.connect(lambda: self.go.emit("B"))
        more = QPushButton("了解详情")
        more.clicked.connect(lambda: self.go.emit("F"))
        for btn in (start, more):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(btn)
        row.addStretch(1)
        b.addLayout(row)

        mid = QHBoxLayout()
        mid.addStretch(1)
        mid.addWidget(body, 100)
        mid.addStretch(1)
        col.addLayout(mid)
        col.addStretch(1)
