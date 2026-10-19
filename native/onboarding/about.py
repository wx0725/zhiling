"""引导 F 页：产品详情与功能说明。从 A 页“了解详情”进入，“返回”发出 go("A")；左侧目录点了滚到对应部分。"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from ..theme import C, S

TEXT = {
    "sections": [
        {"title": "织灵是做什么的",
         "lead": "织灵帮你把想做的事变成电脑能自动执行的工作流。你用平常说话的方式描述需求，织灵把它拆成一步步的模块，"
                 "连成一条完整的流程，然后替你运行。整个过程不需要写代码，每一步做了什么都能在画布上看到。",
         "terms": [("工作流", "按顺序执行的一串步骤，例如“每天读取表格 → 筛选出逾期项 → 生成提醒文字 → 保存成文件”。")],
         "points": []},
        {"title": "可视化工作流",
         "lead": "工作流画在一块画布上。每个方块是一个模块，方块之间的连线表示数据从哪里来、送到哪里去。",
         "terms": [("模块", "完成一件小事的积木，比如读取文件、让 AI 总结一段文字、判断条件、写出结果。"),
                   ("端口", "模块左右两侧的小圆点，左边接收数据，右边送出数据。颜色表示数据类型，只有类型对得上的端口才能连在一起。"),
                   ("循环框", "把一组模块框起来，让它们对一批数据逐个重复执行，比如逐个处理文件夹里的每份文档。")],
         "points": ["从模块库里把模块拖到画布上，拖动端口就能连线；点开模块可以修改它的参数。",
                    "数据沿着连线从左往右流动：上一个模块的输出，成为下一个模块的输入。",
                    "点“开始”后，织灵按连线顺序逐个运行模块，每个模块的状态灯会显示等待、运行中、完成或出错，"
                    "点开模块能看到它这一步的结果。",
                    "运行中可以随时暂停，暂停会在当前模块跑完后生效，之后可以继续；也可以直接终止。",
                    "运行前织灵会自动检查连线和参数是否完整，有问题的地方会在图上标出来。",
                    "可以建多个项目，每个项目各自自动保存。"]},
        {"title": "AI 辅助",
         "lead": "配置好 AI 后，你只需要说出想做什么，AI 会帮你把需求理清楚，并自动搭出工作流。",
         "terms": [("需求澄清", "需求说得不够具体时，AI 会追问。每次只问一个关键问题，并给出几个简单的选项，也可以自己写答案。")],
         "points": ["用日常语言描述需求，例如“把下载文件夹里的发票按月份整理好”。",
                    "AI 先理解你的目标；如果有会影响流程设计的地方没说清，就一个一个地问你，直到需求明确。",
                    "需求明确后，织灵从模块库中找出候选模块，由 AI 判断该用哪些、怎么组合。",
                    "生成的工作流会经过程序检查：模块之间的接口、参数、连线和运行条件都要对得上，才会交给你。",
                    "生成后你可以照常查看、修改、测试和运行，和手动搭建的工作流没有区别。",
                    "AI 还可以帮你修改工作流里的某个模块，或在运行出错时帮你分析原因。"]},
        {"title": "模型接入方式",
         "lead": "织灵的 AI 能力可以来自你自己的电脑，也可以来自云端的服务商，选哪种取决于你的电脑配置和使用习惯。",
         "terms": [("本地 AI", "模型下载到你的电脑上运行，需求和数据都不出电脑，也不按次收费，但需要电脑有足够的内存和显卡。"),
                   ("云端 API", "向服务商购买的调用额度。织灵用你提供的 API Key 证明身份，按实际用量计费，对电脑配置几乎没有要求。"),
                   ("订阅", "你在服务商那里买的会员。会员本身不等于 API 额度，织灵只能通过服务商的官方命令行，用你已登录的账号来调用。"),
                   ("API Key", "服务商发给你的一串密码，用来证明调用是你本人发起的。")],
         "points": ["本地 AI：织灵会找出电脑上 Ollama、LM Studio 里已经装好的模型。不知道该装哪个，可以让织灵检测电脑配置，"
                    "推荐合适的模型并自动下载安装。",
                    "云端 API：支持 DeepSeek、Kimi、智谱、通义千问、豆包、小米 MiMo、OpenAI、Claude、Gemini、Grok、OpenRouter 等。"
                    "在服务商平台创建 API Key，填进织灵并测试连接，就可以选择要用的模型。",
                    "订阅：支持 Kimi Code、Codex、Claude Code、Gemini CLI 这几家的官方命令行。装好命令行并登录后，织灵就能使用。",
                    "模型大小不等于能力。织灵会给本地模型做一组简短的测试，看它能否正确理解需求、挑对模块，再决定是否适合使用。",
                    "随时可以在主程序里换用别的模型，不需要重新走一遍引导。"]},
        {"title": "桌面助手",
         "lead": "一个停在桌面上的小助手。工作流在后台运行时，你不用一直开着主窗口，助手会告诉你进展。",
         "terms": [],
         "points": ["显示正在运行的任务和进度。",
                    "任务完成或出错时提醒你。",
                    "AI 需要你回答问题时，可以直接在助手上作答。"]},
        {"title": "安全与隐私",
         "lead": "织灵尽量把数据留在你的电脑上；凡是涉及外部服务或可能改动文件的操作，都会让你知情并确认。",
         "terms": [],
         "points": ["使用本地 AI 时，需求和数据都在你的电脑上处理，不会发到网上。",
                    "使用云端 AI 时，只把需求和完成任务所需的数据发给你选择的服务商。",
                    "API Key 保存在系统的凭据管理器里，不会写进普通配置文件、日志或导出的工作流。",
                    "工作流要写入或修改文件时，运行前会先列出来请你确认。",
                    "下载程序、修改系统、发送本地数据这类操作，都要先经过你同意。",
                    "运行中可以随时暂停或终止。"]},
    ],
}

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QLabel[role="body"] {{ color: {C['ink_2']}; }}
QLabel[role="term"] {{ color: {C['ink']}; font-weight: 600; }}
QLabel[role="dot"] {{ color: {C['muted']}; }}
"""


def label(text, role=None, wrap=True):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    return w


def restyle(w):
    w.style().unpolish(w)
    w.style().polish(w)


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 2, S[6], S[6] * 2, 0)
        col.setSpacing(S[4])
        back = QPushButton("返回")
        back.setProperty("kind", "ghost")
        back.clicked.connect(lambda: self.go.emit("A"))
        col.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)

        row = QHBoxLayout()
        row.setSpacing(S[6])
        nav = QVBoxLayout()
        nav.setSpacing(2)
        nav.addWidget(label("了解织灵", "h1", wrap=False))
        nav.addSpacing(S[4])

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, S[3], S[6])
        body.setSpacing(S[4])

        self.navs, self.anchors = [], []
        for i, sec in enumerate(TEXT["sections"]):
            b = QPushButton(sec["title"])
            b.setProperty("kind", "item")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=i: self.jump(n))
            nav.addWidget(b)
            self.navs.append(b)
            card = self.section(sec)
            body.addWidget(card)
            self.anchors.append(card)
        body.addStretch(1)
        nav.addStretch(1)
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().valueChanged.connect(self.sync_nav)

        side = QWidget()
        side.setFixedWidth(220)
        side.setLayout(nav)
        row.addWidget(side)
        row.addWidget(self.scroll, 1)
        col.addLayout(row, 1)
        QTimer.singleShot(0, self.sync_nav)

    def section(self, sec):
        card = QFrame()
        card.setProperty("role", "card")
        c = QVBoxLayout(card)
        c.setContentsMargins(S[6], S[5], S[6], S[5])
        c.setSpacing(S[3])
        c.addWidget(label(sec["title"], "h3"))
        c.addWidget(label(sec["lead"], "body"))
        for text in sec["points"]:
            r = QHBoxLayout()
            r.setSpacing(S[2])
            r.addWidget(label("·", "dot", wrap=False), 0, Qt.AlignmentFlag.AlignTop)
            r.addWidget(label(text, "body"), 1)
            c.addLayout(r)
        if sec["terms"]:
            line = QFrame()
            line.setProperty("role", "hline")
            c.addWidget(line)
            for term, text in sec["terms"]:
                r = QHBoxLayout()
                r.setSpacing(S[3])
                t = label(term, "term", wrap=False)
                t.setMinimumWidth(72)
                r.addWidget(t, 0, Qt.AlignmentFlag.AlignTop)
                r.addWidget(label(text, "muted"), 1)
                c.addLayout(r)
        return card

    def jump(self, n):
        self.scroll.verticalScrollBar().setValue(self.anchors[n].y())

    def sync_nav(self, *_):
        bar = self.scroll.verticalScrollBar()
        y = bar.value() + 24
        cur = 0
        for i, a in enumerate(self.anchors):
            if a.y() <= y:
                cur = i
        if bar.value() >= bar.maximum() and bar.maximum() > 0:
            cur = len(self.anchors) - 1
        for i, b in enumerate(self.navs):
            on = i == cur
            if b.property("on") != on:
                b.setProperty("on", on)
                restyle(b)
