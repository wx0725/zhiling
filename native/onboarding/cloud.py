"""引导 C 页：接入云端 AI。左边是织灵已经能接的服务商（来自 config/providers.json），分“API 接入”和“订阅或账号”。
API 接入：填 Key → 测试连接 → 成功后选模型 → 保存（Key 进系统凭据管理器）→ go("main")；失败时说明原因，可以改了再试。
订阅接入：检查官方命令行装没装，说明怎么登录，订阅不等于 API 额度。返回 → go("B")。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from ..theme import C, R, S
from .common import alive, clear, label, restyle

FAIL = {
    "key": ("API Key 不对", "检查是否复制完整、有没有多余的空格，或者这个 Key 已经被删除。"),
    "network": ("连不上服务商", "检查网络是否正常；如果用了代理，确认代理能访问这个服务商。"),
    "quota": ("账户额度不足", "到服务商的平台充值或领取额度后再试。"),
    "unavailable": ("服务商接口暂时不可用", "可能是服务商在维护，稍后再试，或先换一家。"),
}

QSS = f"""
QWidget#page {{ background: {C['canvas']}; }}
QWidget#list {{ background: {C['rail']}; border: 1px solid {C['line']}; border-radius: {R['xl']}px; }}
QWidget#detail {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['xl']}px; }}
QLabel[role="body"] {{ color: {C['ink_2']}; }}
QLabel[role="ok"] {{ color: {C['led_ok']}; font-weight: 600; }}
QLabel[role="bad"] {{ font-weight: 600; }}
QFrame[role="box"] {{ background: {C['rail']}; border: 1px solid {C['line']}; border-radius: {R['lg']}px; }}
QRadioButton {{ spacing: 8px; padding: 4px 0; }}
"""


class Page(QWidget):
    go = Signal(str)

    def __init__(self, svc=None):
        super().__init__()
        self.svc = svc
        self.data = {"apis": [], "clis": [], "keystore": True}
        self.items = []
        self.setObjectName("page")
        self.setStyleSheet(QSS)

        col = QVBoxLayout(self)
        col.setContentsMargins(S[6] * 2, S[6], S[6] * 2, S[6])
        col.setSpacing(S[4])
        back = QPushButton("返回")
        back.setProperty("kind", "ghost")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.go.emit("B"))
        col.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(label("接入云端 AI", "h1"))
        col.addWidget(label("选一个服务商。API 接入要填写从服务商平台获取的 API Key；订阅接入使用你电脑上已登录的官方命令行。", "body"))

        row = QHBoxLayout()
        row.setSpacing(S[4])
        self.list_box = QWidget()
        self.list_box.setObjectName("list")
        self.list_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.list_box.setFixedWidth(300)
        outer = QVBoxLayout(self.list_box)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        self.lst = QVBoxLayout(inner)
        self.lst.setContentsMargins(S[3], S[4], S[3], S[4])
        self.lst.setSpacing(2)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        row.addWidget(self.list_box, 0)

        self.detail = QStackedWidget()
        self.detail.setObjectName("detail")
        self.detail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.empty = label("正在读取可接入的服务商…", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.addWidget(self.empty)
        row.addWidget(self.detail, 1)
        col.addLayout(row, 1)
        if svc is not None:
            svc.providers(self._fill, lambda d: self.empty.setText(f"没能读取服务商列表：{d}"))

    # ---------- 左侧服务商列表 ----------
    def _fill(self, data):
        self.data = data
        clear(self.lst)
        self.items = []
        for kind, title, specs in (("api", "API 接入", data.get("apis") or []),
                                   ("cli", "订阅或账号", data.get("clis") or [])):
            if not specs:
                continue
            h = label(title, "h2", wrap=False)
            h.setContentsMargins(S[2], S[2], 0, S[1])
            self.lst.addWidget(h)
            for spec in specs:
                b = QPushButton(spec["name"])
                b.setProperty("kind", "item")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=kind, s=spec, btn=b: self._pick(k, s, btn))
                self.lst.addWidget(b)
                self.items.append(b)
            self.lst.addSpacing(S[3])
        self.lst.addStretch(1)
        if self.items:
            self.items[0].click()  # 默认选中第一家
        else:
            self.empty.setText("现在没有可接入的云端服务商")

    def _pick(self, kind, spec, btn):
        for b in self.items:
            b.setProperty("on", b is btn)
            restyle(b)
        while self.detail.count() > 1:
            old = self.detail.widget(1)
            self.detail.removeWidget(old)
            old.deleteLater()
        self.detail.addWidget(self._api_panel(spec) if kind == "api" else self._cli_panel(spec))
        self.detail.setCurrentIndex(1)

    # ---------- API 接入 ----------
    def _api_panel(self, spec):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(S[6], S[6], S[6], S[6])
        col.setSpacing(S[3])
        col.addWidget(label(spec["name"], "h3"))
        where = (f'到 <a href="{spec["console"]}" style="color:{C["focus"]}">{spec["console"]}</a> 注册登录，'
                 if spec.get("console") else "到这家服务商的官方平台注册登录，")
        intro = label(f"API Key 是服务商发给你的一串密码，织灵用它代你调用 AI。{where}新建一个 API Key，复制粘贴到下面。"
                      "按用量计费，具体价格以服务商平台为准。", "body")
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setOpenExternalLinks(True)
        col.addWidget(intro)

        key_row = QHBoxLayout()
        key_row.setSpacing(S[2])
        key = QLineEdit()
        key.setPlaceholderText("已保存过 Key，可以直接测试，也可以填新的" if spec.get("has_key") else "粘贴 API Key")
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setAccessibleName("API Key")
        show = QPushButton("显示")
        show.setProperty("small", True)
        show.setCheckable(True)
        show.toggled.connect(lambda on: (key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
                                         show.setText("隐藏" if on else "显示")))
        test = QPushButton("测试连接")
        test.setProperty("kind", "primary")
        saved = bool(spec.get("has_key"))
        test.setEnabled(saved)
        key_row.addWidget(key, 1)
        key_row.addWidget(show)
        key_row.addWidget(test)
        col.addLayout(key_row)
        if self.data.get("keystore", True):
            col.addWidget(label("Key 保存在系统的凭据管理器里，不会写进配置文件、日志或导出的工作流。", "muted"))
        else:
            col.addWidget(label("这台电脑上没有可用的凭据管理器，新填的 Key 没法安全保存。可以把 Key 设成环境变量，这里留空直接测试连接。", "warn"))

        result = QVBoxLayout()
        result.setSpacing(S[3])
        col.addLayout(result)
        col.addStretch(1)

        def running(on):
            key.setEnabled(not on)
            test.setEnabled(not on and (saved or bool(key.text().strip())))
            test.setText("正在测试…" if on else "测试连接")

        def ok(models):
            running(False)
            clear(result)
            if not models:
                result.addWidget(label("连接成功，但这家没有返回可用的对话模型。", "bad"))
                return
            result.addWidget(label("连接成功，选择要使用的模型：", "ok"))
            group = QButtonGroup(w)
            for i, m in enumerate(models):
                r = QRadioButton(m)
                r.setProperty("model", m)
                r.setChecked(i == 0)
                group.addButton(r)
                result.addWidget(r)
            save = QPushButton("保存并进入主程序")
            save.setProperty("kind", "primary")
            # 没有凭据管理器时，只有要存新填的 Key 才保存不了；用已有的（环境变量里的）Key 只需记下选的模型
            save.setEnabled(self.data.get("keystore", True) or not key.text().strip())
            note = label("", "bad")
            note.hide()
            save.clicked.connect(lambda: self._save(f"api://{spec['id']}/{group.checkedButton().property('model')}",
                                                    key.text().strip(), save, note))
            result.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
            result.addWidget(note)

        def err(code, detail):
            running(False)
            clear(result)
            title, tip = FAIL.get(code, ("连接失败", "请稍后重试。"))
            box = QFrame()
            box.setProperty("role", "box")
            b = QVBoxLayout(box)
            b.setContentsMargins(S[4], S[4], S[4], S[4])
            b.addWidget(label(title, "bad"))
            b.addWidget(label(tip, "body"))
            if detail:
                b.addWidget(label(f"服务商返回：{detail}", "muted"))
            b.addWidget(label("改好后再点一次“测试连接”。", "muted"))
            result.addWidget(box)

        def start():
            if not (saved or key.text().strip()):
                return
            clear(result)
            running(True)
            self.svc.test_api(spec["id"], key.text().strip(), alive(w, ok), alive(w, err))

        key.textChanged.connect(lambda t: (test.setEnabled(saved or bool(t.strip())), clear(result)))
        key.returnPressed.connect(start)
        test.clicked.connect(start)
        return w

    # ---------- 订阅接入 ----------
    def _cli_panel(self, spec):
        w = QWidget()
        col = QVBoxLayout(w)
        col.setContentsMargins(S[6], S[6], S[6], S[6])
        col.setSpacing(S[3])
        col.addWidget(label(spec["name"], "h3"))
        col.addWidget(label(
            "订阅本身不能直接当作 API 额度使用。织灵只通过这家服务商的官方命令行工作，用的是你在命令行里登录的账号，"
            "织灵拿不到你的账号密码。能否这样使用以该服务的条款为准。", "body"))
        state = QVBoxLayout()
        state.setSpacing(S[3])
        col.addLayout(state)
        col.addStretch(1)

        def check():
            clear(state)
            state.addWidget(label("正在检查电脑上的命令行…", "muted"))
            self.svc.check_cli(spec["id"], alive(w, show),
                               alive(w, lambda d: (clear(state), state.addWidget(label(f"检查失败：{d}", "bad")))))

        def show(info):
            clear(state)
            again = QPushButton("重新检查")
            again.clicked.connect(check)
            if not info.get("found"):
                state.addWidget(label(f"没有找到命令行 {spec['exe']}", "bad"))
                state.addWidget(label("先按这家服务商的官方说明安装它的命令行工具，装好后点“重新检查”。"
                                      "暂时不想装，可以返回改用 API 接入。", "body"))
                state.addWidget(again, 0, Qt.AlignmentFlag.AlignLeft)
                return
            if info.get("logged_in") is False:
                state.addWidget(label("已找到命令行，但还没有登录", "bad"))
                state.addWidget(label(f"{spec.get('login') or '按官方说明登录'}，登录后点“重新检查”。", "body"))
                state.addWidget(again, 0, Qt.AlignmentFlag.AlignLeft)
                return
            state.addWidget(label("已找到命令行", "ok"))
            if info.get("logged_in") is None and spec.get("login"):
                state.addWidget(label(f"织灵没法自动确认是否已经登录。还没登录的话，{spec['login']}。", "body"))
            use = QPushButton("使用它并进入主程序")
            use.setProperty("kind", "primary")
            note = label("", "bad")
            note.hide()
            use.clicked.connect(lambda: self._save(f"cli://{spec['id']}", None, use, note))
            state.addWidget(use, 0, Qt.AlignmentFlag.AlignLeft)
            state.addWidget(note)

        check()
        return w

    # ---------- 保存 ----------
    def _save(self, model_id, key, btn, note):
        btn.setEnabled(False)
        text = btn.text()
        btn.setText("正在保存…")
        note.hide()

        def ok():
            btn.setText("已保存")
            self.go.emit("main")

        def bad(detail):
            btn.setEnabled(True)
            btn.setText(text)
            note.setText(f"没能保存：{detail}")
            note.show()
        self.svc.save(model_id, key or None, alive(btn, ok), alive(btn, bad))
