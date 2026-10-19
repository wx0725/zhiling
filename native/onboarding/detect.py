"""引导 E 页：检测电脑并推荐本地模型。先说明会读哪些信息（只读）→ 检测 → 显示这台电脑的全部显卡等信息和三档推荐
（各写明原因）→ 安装前确认来源、大小、位置 → 安装（可取消、失败可重试）→ 实际推理测试 → 保存 → go("main")。
没有合适的模型时说明原因，介绍云端 AI：尝试云端 → go("C")，直接手动搭建 → go("main")。返回 → go("B")。
安装由 OnboardingService 在后台进行，切换页面不会中断。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QProgressBar, QStackedWidget, QVBoxLayout, QWidget

from ..theme import C, FS, R, S
from .common import alive, button, card, label, restyle, scrolled

SCAN_ITEMS = [("cpu", "处理器"), ("gpus", "显卡"), ("ram", "内存"), ("disk", "可用磁盘"),
              ("os", "操作系统"), ("runtime", "驱动与运行环境")]

STEPS = [("runtime", "安装运行环境"), ("download", "下载模型"), ("verify", "校验完整性"), ("config", "确认安装")]

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QLabel[role="body"] {{ color: {C['ink_2']}; }}
QLabel[role="ok"] {{ color: {C['led_ok']}; font-weight: 600; }}
QLabel[role="run"] {{ color: {C['led_run']}; }}
QLabel[role="bad"] {{ font-weight: 600; }}
QLabel[role="tier"] {{ color: {C['muted']}; font-size: {FS['sm']}px; font-weight: 600; letter-spacing: 1px; }}
QProgressBar {{ background: {C['rail']}; border: 1px solid {C['line']}; border-radius: {R['sm']}px; height: 8px;
                text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {C['focus']}; border-radius: {R['sm']}px; }}
"""


KIND = {"external": "外接", "discrete": "独立", "integrated": "集成", "virtual": "虚拟", "unknown": "类型未确定"}


def gpu_line(g):
    """一张显卡一行：名称（种类）　显存　驱动　状态"""
    if g.get("shared"):
        mem = "与内存共用"
    elif g.get("vram"):
        mem = f"{g['vram'] / (1 << 30):.0f} GB 显存"
    else:
        mem = "显存未知"
    parts = [f"{g['name']}（{KIND.get(g.get('kind'), '未知')}）", mem]
    if g.get("driver"):
        parts.append(f"驱动 {g['driver']}")
    if not g.get("ok", True):
        parts.append(f"设备异常（代码 {g.get('problem')}）")
    return "　".join(parts)


def fmt(key, value):
    """检测结果 → 显示的文字；显卡一张一行。"""
    if key == "gpus":
        if not value:
            return "没有检测到显卡"
        return "\n".join(gpu_line(g) for g in value)
    return value or ""


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.model = None
        self.hw = {}
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 2, S[6], S[6] * 2, S[6])
        col.setSpacing(S[4])
        self.back = button("返回", "ghost")
        self.back.clicked.connect(lambda: self.go.emit("B"))
        col.addWidget(self.back, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(label("检测电脑并推荐本地模型", "h1"))
        self.stack = QStackedWidget()
        col.addWidget(self.stack, 1)
        self.show_intro()

    def _swap(self, w):
        old = self.stack.currentWidget()
        self.stack.addWidget(w)
        self.stack.setCurrentWidget(w)
        if old:
            self.stack.removeWidget(old)
            old.deleteLater()

    # ---------- 1 说明 ----------
    def show_intro(self):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S[4])
        col.addWidget(label("织灵将读取这台电脑的部分硬件信息，用来判断适合运行哪些本地 AI 模型：", "body"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(S[6])
        grid.setVerticalSpacing(S[2])
        for i, (key, name) in enumerate(SCAN_ITEMS):
            text = "全部显卡（含集成显卡）和显存" if key == "gpus" else name
            grid.addWidget(label(f"· {text}", wrap=False), i // 3, i % 3)
        col.addLayout(grid)
        col.addWidget(label("检测只读取信息，不会修改任何系统设置，也不会把这些信息发到网上。", "muted"))
        start = button("开始检测", "primary")
        start.clicked.connect(self.show_scan)
        col.addWidget(start, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        self._swap(w)

    # ---------- 2 检测中 ----------
    def show_scan(self):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S[3])
        col.addWidget(label("正在检测…", "body"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(S[5])
        grid.setVerticalSpacing(S[2])
        values = {}
        for i, (key, name) in enumerate(SCAN_ITEMS):
            grid.addWidget(label(name, "muted", wrap=False), i, 0, Qt.AlignmentFlag.AlignTop)
            v = label("等待", "muted")
            grid.addWidget(v, i, 1)
            values[key] = v
        grid.setColumnStretch(1, 1)
        col.addLayout(grid)
        col.addStretch(1)
        self._swap(w)

        def item(key, value):
            values[key].setText(fmt(key, value))
            values[key].setProperty("role", None)
            restyle(values[key])

        def bad(detail):
            col.insertWidget(0, label(f"检测没能完成：{detail}", "bad"))
            again = button("重新检测")
            again.clicked.connect(self.show_scan)
            col.insertWidget(1, again, 0, Qt.AlignmentFlag.AlignLeft)
        self.svc.scan(alive(w, item), alive(w, self.show_result), alive(w, bad))

    # ---------- 3 结果与推荐 ----------
    def show_result(self, hw):
        self.hw = hw
        rec = self.svc.recommend(hw)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, S[2], 0)
        col.setSpacing(S[4])

        box, b = card()
        b.addWidget(label("这台电脑", "h3"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(S[5])
        grid.setVerticalSpacing(S[1])
        for i, (key, name) in enumerate(SCAN_ITEMS):
            grid.addWidget(label(name, "muted", wrap=False), i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label(fmt(key, hw.get(key)) or "未知"), i, 1)
        grid.setColumnStretch(1, 1)
        b.addLayout(grid)
        col.addWidget(box)

        if rec["fits"]:
            col.addWidget(label("推荐的模型", "h3"))
            row = QHBoxLayout()
            row.setSpacing(S[4])
            for m in rec["fits"]:
                f, c = card()
                c.addWidget(label(m["tier"], "tier", wrap=False))
                c.addWidget(label(m["name"], "h3", wrap=False))
                c.addWidget(label(f"下载 {m['download_text']}　·　参数 {m['params']}　·　量化 {m['quant']}", "muted"))
                c.addWidget(label(m["why"], "body"))
                c.addStretch(1)
                pick = button("安装这个", "primary" if m["tier"] == "综合平衡" else None)
                pick.clicked.connect(lambda _=False, mm=m: self.show_confirm(mm))
                c.addWidget(pick, 0, Qt.AlignmentFlag.AlignLeft)
                row.addWidget(f, 1)
            col.addLayout(row)
        else:
            self._no_fit(col, rec["reasons"])
        col.addStretch(1)
        self._swap(scrolled(inner))

    def _no_fit(self, col, reasons):
        box, b = card()
        b.addWidget(label("这台电脑暂时不适合运行本地模型", "h3"))
        for r in reasons:
            b.addWidget(label(f"· {r}", "body"))
        b.addSpacing(S[2])
        b.addWidget(label("可以改用云端 AI", "h3"))
        b.addWidget(label("云端 AI 运行在服务商的服务器上，你的电脑只负责把需求发过去、把结果拿回来，所以对电脑配置几乎没有要求。"
                          "例如 DeepSeek 提供 API 服务：在它的平台注册、充值少量金额、创建 API Key，填进织灵就能用，"
                          "按实际用量计费，价格以服务商平台为准。", "body"))
        b.addWidget(label("织灵的需求理解和自动生成工作流主要靠 AI；不配置 AI 也能手动添加、连接模块来搭建工作流。", "muted"))
        row = QHBoxLayout()
        cloud = button("尝试云端 AI", "primary")
        cloud.clicked.connect(lambda: self.go.emit("C"))
        manual = button("直接手动搭建")
        manual.clicked.connect(lambda: self.go.emit("main"))
        row.addWidget(cloud)
        row.addWidget(manual)
        row.addStretch(1)
        b.addLayout(row)
        col.addWidget(box)

    # ---------- 4 安装前确认 ----------
    def show_confirm(self, m):
        self.model = m
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S[4])
        box, b = card()
        b.addWidget(label(f"安装 {m['name']}（{m['tier']}）", "h3"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(S[5])
        grid.setVerticalSpacing(S[2])
        facts = [("来源", m["source"]), ("下载大小", m["download_text"]), ("占用磁盘", m["disk_text"]),
                 ("安装位置", m["install_dir"])]
        if m.get("needs_ollama"):
            facts.append(("还会安装", "Ollama（运行本地模型的免费开源工具），电脑上还没有"))
        for i, (k, v) in enumerate(facts):
            grid.addWidget(label(k, "muted", wrap=False), i, 0)
            grid.addWidget(label(v), i, 1)
        grid.setColumnStretch(1, 1)
        b.addLayout(grid)
        b.addWidget(label("确认后织灵会下载并安装，过程中可以随时取消；下载完成后会校验文件完整性。"
                          + ("安装 Ollama 前会检查安装程序的数字签名。" if m.get("needs_ollama") else ""), "muted"))
        row = QHBoxLayout()
        ok = button("确认安装", "primary")
        ok.clicked.connect(self.show_install)
        back = button("返回推荐")
        back.clicked.connect(lambda: self.show_result(self._hw()))
        row.addWidget(ok)
        row.addWidget(back)
        row.addStretch(1)
        b.addLayout(row)
        col.addWidget(box)
        col.addStretch(1)
        self._swap(w)

    # ---------- 5 安装中 ----------
    def show_install(self):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S[4])
        box, b = card()
        b.addWidget(label(f"正在安装 {self.model['name']}", "h3"))
        rows = {}
        for key, name in STEPS:
            line = QHBoxLayout()
            line.addWidget(label(name, wrap=False), 0)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFixedHeight(8)
            line.addWidget(bar, 1)
            st = label("等待", "muted", wrap=False)
            st.setMinimumWidth(64)
            line.addWidget(st)
            b.addLayout(line)
            rows[key] = (bar, st)
        msg = label("", "muted")
        b.addWidget(msg)
        actions = QHBoxLayout()
        cancel = button("取消安装")
        actions.addWidget(cancel)
        actions.addStretch(1)
        b.addLayout(actions)
        col.addWidget(box)
        col.addStretch(1)
        self._swap(w)

        def on_step(key, pct, text):
            if text:
                msg.setText(text)  # 正在做什么，例如“安装 Ollama（这一步开始后不能中途取消）”
            bar, st = rows[key]
            bar.setValue(pct)
            st.setText("完成" if pct >= 100 else f"{pct}%")
            st.setProperty("role", "ok" if pct >= 100 else "run")
            restyle(st)

        def stop():
            self.svc.cancel()
            cancel.setEnabled(False)
            msg.setText("已取消。已下载的部分会保留，重新安装时从断点继续。")
            retry = button("重新安装", "primary")
            retry.clicked.connect(self.show_install)
            other = button("选择其他模型")
            other.clicked.connect(lambda: self.show_result(self._hw()))
            actions.insertWidget(0, retry)
            actions.insertWidget(1, other)
            cancel.hide()

        def failed(key, detail):
            _, st = rows[key]
            st.setText("失败")
            st.setProperty("role", "bad")
            restyle(st)
            msg.setText(f"{dict(STEPS)[key]}失败：{detail}")
            cancel.hide()
            retry = button("重试", "primary")
            retry.clicked.connect(self.show_install)
            other = button("选择其他模型")
            other.clicked.connect(lambda: self.show_result(self._hw()))
            actions.insertWidget(0, retry)
            actions.insertWidget(1, other)

        cancel.clicked.connect(stop)
        self.svc.install(self.model, alive(w, on_step), alive(w, self.show_test), alive(w, failed))

    def _hw(self):
        return self.hw

    # ---------- 6 实际推理测试 ----------
    def show_test(self, model_id):
        self.model_id = model_id
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(S[4])
        box, b = card()
        b.addWidget(label(f"{self.model['name']} 已安装", "h3"))
        state = label("正在启动模型并做一次实际推理测试…", "run")
        b.addWidget(state)
        actions = QHBoxLayout()
        b.addLayout(actions)
        col.addWidget(box)
        col.addStretch(1)
        self._swap(w)

        def ok():
            state.setText("测试通过，模型可以正常工作。")
            state.setProperty("role", "ok")
            restyle(state)
            go = button("保存并进入主程序", "primary")
            go.clicked.connect(lambda: (go.setEnabled(False),
                                        self.svc.save(model_id, None, alive(w, lambda: self.go.emit("main")),
                                                      alive(w, lambda d: (go.setEnabled(True),
                                                                          state.setText(f"没能保存：{d}"))))))
            actions.addWidget(go)
            actions.addStretch(1)

        def bad(detail):
            state.setText(f"模型装好了，但没能正常运行：{detail}")
            state.setProperty("role", "bad")
            restyle(state)
            for text, fn, kind in (("重新测试", lambda: self.show_test(model_id), "primary"),
                                   ("选择其他模型", lambda: self.show_result(self._hw()), None),
                                   ("改用云端 AI", lambda: self.go.emit("C"), None)):
                bt = button(text, kind)
                bt.clicked.connect(fn)
                actions.addWidget(bt)
            actions.addStretch(1)
        self.svc.run_test(model_id, alive(w, ok), alive(w, bad))
