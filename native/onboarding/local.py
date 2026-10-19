"""引导 D 页：使用已有的本地 AI。问 Ollama、LM Studio 的标准接口列出已装的模型（不扫描整个电脑），
每个模型先做能力测试：通过可以选；能运行但不稳定，警告后仍可选；无法启动，显示错误、不能选。
选好 → go("main")。没有模型时给出：检测电脑 → go("E")、改用云端 → go("C")、手动搭建 → go("main")。返回 → go("B")。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ..theme import C, R, S
from .common import alive, clear, label, restyle, scrolled

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QLabel[role="body"] {{ color: {C['ink_2']}; }}
QLabel[role="ok"] {{ color: {C['led_ok']}; font-weight: 600; }}
QLabel[role="run"] {{ color: {C['led_run']}; font-weight: 600; }}
QLabel[role="bad"] {{ font-weight: 600; }}
QLabel[role="dot_on"] {{ color: {C['led_ok']}; }}
QLabel[role="dot_off"] {{ color: {C['led_off']}; }}
QFrame[role="model"] {{ background: {C['surface']}; border: 1.5px solid {C['line']}; border-radius: {R['xl']}px; }}
QFrame[role="model"][on="true"] {{ border-color: {C['focus']}; }}
"""


class ModelCard(QFrame):
    """一个模型：基本信息、能力测试结果、选择按钮"""
    chosen = Signal(object)

    def __init__(self, page, m):
        super().__init__()
        self.page, self.m = page, m
        self.usable = False  # 测试通过或不稳定（可以选）
        self.setProperty("role", "model")
        col = QVBoxLayout(self)
        col.setContentsMargins(S[5], S[4], S[5], S[4])
        col.setSpacing(S[2])
        head = QHBoxLayout()
        head.addWidget(label(m["name"], "h3", wrap=False))
        head.addWidget(label(m.get("runtime", ""), "muted", wrap=False))
        head.addStretch(1)
        self.test = QPushButton("能力测试")
        self.test.setProperty("small", True)
        self.test.clicked.connect(self.run_probe)
        self.pick = QPushButton("用这个")
        self.pick.setProperty("small", True)
        self.pick.setProperty("kind", "primary")
        self.pick.setEnabled(False)
        self.pick.setToolTip("先做能力测试")
        self.pick.clicked.connect(lambda: self.chosen.emit(self))
        head.addWidget(self.test)
        head.addWidget(self.pick)
        col.addLayout(head)
        facts = [f"{k} {m[f]}" for k, f in (("参数", "params"), ("量化", "quant"), ("上下文", "ctx"), ("占用", "size"))
                 if m.get(f)]
        if facts:
            col.addWidget(label("　·　".join(facts), "muted"))
        self.state = label("还没测试：测试会让模型按几段需求从候选模块里挑选，并检查输出格式，可能要几十秒到几分钟。", "muted")
        col.addWidget(self.state)

    def set_state(self, text, role):
        self.state.setText(text)
        self.state.setProperty("role", role)
        restyle(self.state)

    def run_probe(self):
        self.test.setEnabled(False)
        self.pick.setEnabled(False)
        self.set_state("正在测试：加载模型并回答 5 道小题…", "run")
        self.page.svc.probe(self.m["id"], alive(self, self.done), alive(self, self.failed))

    def done(self, r):
        self.usable = r.get("result") in ("pass", "unstable")
        self.test.setEnabled(True)
        self.test.setText("重新测试")
        if r.get("result") == "pass":
            self.set_state(f"通过（{r.get('score')}）：可以承担需求理解和工作流生成。", "ok")
            self.pick.setToolTip("")
        elif r.get("result") == "unstable":
            self.set_state(f"能运行但不稳定（{r.get('score')}）：{r.get('detail')}。生成复杂工作流时可能理解偏差、选错模块，"
                           "或需要多次修正。", "warn")
            self.pick.setToolTip("仍然可以使用，但效果可能不稳定")
        else:
            self.set_state(f"无法启动：{r.get('detail') or '模型没能运行'}", "bad")
            self.pick.setToolTip("这个模型现在无法启动")
        self.pick.setEnabled(self.usable and not self.page.saving)

    def failed(self, detail):
        self.test.setEnabled(True)
        self.set_state(f"测试没能完成：{detail}", "bad")


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.cards = []
        self.saving = False
        self.batch = 0  # 第几次检测：重新检测后，上一次迟到的结果不再显示
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 2, S[6], S[6] * 2, S[6])
        col.setSpacing(S[4])
        top = QHBoxLayout()
        back = QPushButton("返回")
        back.setProperty("kind", "ghost")
        back.clicked.connect(lambda: self.go.emit("B"))
        top.addWidget(back)
        top.addStretch(1)
        self.again = QPushButton("重新检测")
        self.again.clicked.connect(self.detect)
        top.addWidget(self.again)
        col.addLayout(top)
        col.addWidget(label("使用已有的本地 AI", "h1"))
        col.addWidget(label("织灵会询问电脑上的本地模型工具（Ollama、LM Studio），列出已经装好的模型。"
                            "模型大小不等于能力，选用前请先做一次能力测试。", "body"))
        self.runtimes = QHBoxLayout()
        self.runtimes.setSpacing(S[5])
        col.addLayout(self.runtimes)
        inner = QWidget()
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, S[2], 0)
        self.body.setSpacing(S[3])
        col.addWidget(scrolled(inner), 1)
        self.started = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self.started and self.svc is not None:  # 进到这一页才去检测，不在打开引导时就问
            self.started = True
            self.detect()

    def detect(self):
        self.again.setEnabled(False)
        self.cards = []
        clear(self.runtimes)
        clear(self.body)
        self.body.addWidget(label("正在检测本地模型工具…", "muted"))
        self.body.addStretch(1)
        self.batch += 1
        batch = self.batch
        self.svc.detect(lambda d: batch == self.batch and self.show_result(d),
                        lambda e: batch == self.batch and self.show_error(e))

    def show_error(self, detail):
        self.again.setEnabled(True)
        clear(self.body)
        self.body.addWidget(label(f"检测没能完成：{detail}", "bad"))
        self.body.addStretch(1)

    def show_result(self, data):
        self.again.setEnabled(True)
        clear(self.body)
        for r in data.get("runtimes") or []:
            row = QHBoxLayout()
            row.setSpacing(S[1])
            row.addWidget(label("●", "dot_on" if r.get("found") else "dot_off", wrap=False))
            row.addWidget(label(f"{r['name']}：{'已找到' if r.get('found') else '未找到'}", wrap=False))
            row.addWidget(label(r.get("detail", ""), "muted", wrap=False))
            self.runtimes.addLayout(row)
        self.runtimes.addStretch(1)
        models = data.get("models") or []
        if not models:
            self.show_empty()
            return
        for m in models:
            c = ModelCard(self, m)
            c.chosen.connect(self.choose)
            self.cards.append(c)
            self.body.addWidget(c)
        self.body.addStretch(1)

    def show_empty(self):
        box = QFrame()
        box.setProperty("role", "card")
        b = QVBoxLayout(box)
        b.setContentsMargins(S[6], S[6], S[6], S[6])
        b.setSpacing(S[3])
        b.addWidget(label("没有检测到本地模型", "h3"))
        b.addWidget(label("电脑上没有找到可用的本地模型工具，或者工具里还没有下载对话模型。你可以：", "body"))
        grid = QGridLayout()
        grid.setSpacing(S[3])
        for i, (text, target, kind) in enumerate((("检测电脑并推荐模型", "E", "primary"),
                                                  ("改用云端 AI", "C", None), ("直接手动搭建", "main", None))):
            btn = QPushButton(text)
            if kind:
                btn.setProperty("kind", kind)
            btn.clicked.connect(lambda _=False, t=target: self.go.emit(t))
            grid.addWidget(btn, 0, i)
        grid.setColumnStretch(3, 1)
        b.addLayout(grid)
        self.body.addWidget(box)
        self.body.addStretch(1)

    def choose(self, card):
        self.saving = True
        self.again.setEnabled(False)  # 保存中不重新检测：卡片不会在保存途中被删掉
        for c in self.cards:
            c.setProperty("on", c is card)
            restyle(c)
            c.pick.setEnabled(False)
        card.pick.setText("正在保存…")

        def settle():  # 页面自己的状态：不管卡片还在不在都要恢复
            self.saving = False
            self.again.setEnabled(True)

        def ok():
            settle()
            alive(card, lambda: card.pick.setText("已选用"))()
            self.go.emit("main")

        def bad(detail):
            settle()
            for c in self.cards:
                alive(c, lambda c=c: c.pick.setEnabled(c.usable))()
            alive(card, lambda: (card.pick.setText("用这个"), card.set_state(f"没能保存：{detail}", "bad")))()
        self.svc.save(card.m["id"], None, ok, bad)
