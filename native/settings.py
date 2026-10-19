"""设置面板：顶栏“设置”打开，左边三栏——桌宠、杂项、自定义。

桌宠：关主窗口时有工作流在运行要不要转到后台并显示桌宠（记在本机 QSettings 的 pet_background）；现在就看看桌宠。
杂项：生成后实跑检查（原来在顶栏的开关）；配置 AI（重新走引导）；默认模型的说明。
自定义：“思考”模块默认用的本地模型；自己添加 OpenAI 兼容的云端服务商（中转站等），存进 model_settings.json（不进 git）。
每一项改了立刻生效、立刻保存，没有“确定”按钮。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from .side.topbar import Switch
from .theme import C, FS, R

SECTIONS = [("pet", "桌宠"), ("misc", "杂项"), ("custom", "自定义")]

QSS = f"""
QDialog#settings {{ background: {C['canvas']}; }}
QListWidget#nav {{ background: {C['rail']}; border: 0; border-right: 1px solid {C['line']}; padding: 8px; outline: 0; font-size: {FS['md']}px; }}
QListWidget#nav::item {{ padding: 8px 12px; border-radius: {R['md']}px; }}
QListWidget#nav::item:selected {{ background: {C['surface']}; color: {C['ink']}; }}
QLabel#h2 {{ font-size: 18px; font-weight: 650; background: transparent; }}
QLabel#h3 {{ font-size: {FS['md']}px; font-weight: 600; background: transparent; }}
QLabel[role="muted"] {{ color: {C['muted']}; background: transparent; }}
QLabel[role="ok"] {{ color: {C['led_ok']}; background: transparent; }}
QLabel[role="bad"] {{ color: {C['bad_ink']}; background: transparent; }}
QFrame#card {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['lg']}px; }}
QFrame#card QLabel {{ background: transparent; }}
"""


def _label(text, name=None, role=None, wrap=True):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def _card(title=None):
    f = QFrame(objectName="card")
    col = QVBoxLayout(f)
    col.setContentsMargins(18, 16, 18, 16)
    col.setSpacing(8)
    if title:
        col.addWidget(_label(title, "h3"))
    return f, col


def _btn(text, kind=None, on=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on:
        b.clicked.connect(on)
    return b


class SettingsDialog(QDialog):
    def __init__(self, ctrl, parent=None, start="pet"):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setObjectName("settings")
        self.setWindowTitle("织灵 · 设置")
        self.setStyleSheet(QSS)
        self.setModal(False)
        self.resize(760, 560)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.nav = QListWidget(objectName="nav")
        self.nav.setFixedWidth(150)
        for key, text in SECTIONS:
            self.nav.addItem(text)
        row.addWidget(self.nav)
        self.pages = QStackedWidget()
        row.addWidget(self.pages, 1)
        self.builders = {"pet": self._pet, "misc": self._misc, "custom": self._custom}
        for key, _ in SECTIONS:
            self.pages.addWidget(self._page(key))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.nav.setCurrentRow(max(0, [k for k, _ in SECTIONS].index(start) if start in dict(SECTIONS) else 0))

    def _page(self, key):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(14)
        col.addWidget(_label(dict(SECTIONS)[key], "h2"))
        self.builders[key](col)
        col.addStretch(1)
        area.setWidget(body)
        return area

    # ---------- 桌宠 ----------
    def _pet(self, col):
        f, c = _card("关主窗口时")
        sw = Switch("有工作流还在运行就转到后台继续跑，并在桌面右下角显示桌宠", self.ctrl.pet_background)
        sw.setAccessibleName("关窗口后台运行并显示桌宠")
        sw.toggled.connect(self.ctrl.set_pet_background)
        c.addWidget(sw)
        c.addWidget(_label("关掉这一项：关主窗口就直接退出，正在运行的工作流会被终止（关之前会问一句）。", role="muted"))
        col.addWidget(f)
        f, c = _card("桌宠能做什么")
        for t in ("头顶小珠子的颜色就是运行状态：橙色在运行、绿色完成、红色出错、灰色没在运行。",
                  "点一下：弹出气泡，看现在跑到哪一步、进度多少。",
                  "出错时点一下：打开主窗口并定位到出错的节点。",
                  "双击：打开主窗口。按住可以拖到任何地方。",
                  "右键：这个设置面板，或者关闭织灵。"):
            c.addWidget(_label(f"·  {t}"))
        c.addSpacing(4)
        b = _btn("现在看看桌宠", on=self._show_pet)
        b.setToolTip("把桌宠放到桌面上看看效果；主窗口照常开着")
        rowb = QHBoxLayout()
        rowb.addWidget(b)
        rowb.addStretch(1)
        c.addLayout(rowb)
        col.addWidget(f)

    def _show_pet(self):
        w = self.parent().window() if self.parent() else None
        hook = getattr(w, "pet", None)
        if hook is None:
            self.ctrl.set_notice("桌宠还没准备好（这个窗口里没有桌宠）")
            return
        hook.pet.show()
        hook.pet.raise_()
        hook.pet.poke()

    # ---------- 杂项 ----------
    def _misc(self, col):
        f, c = _card("生成")
        sw = Switch("生成后实跑检查", self.ctrl.check)
        sw.setAccessibleName("生成后实跑检查")
        sw.toggled.connect(self.ctrl.set_check)
        c.addWidget(sw)
        c.addWidget(_label("开着：生成后用示例输入实际运行一遍，由审查模型检查结果，发现问题会自动重写，更可靠但更慢；"
                           "显存紧张（生成模型和思考模型挤在一块显卡上）时可以先关掉。", role="muted"))
        col.addWidget(f)
        f, c = _card("模型")
        c.addWidget(_label("顶栏的下拉框：没打开项目时改的是默认模型；打开着项目时改的只是这个项目用的模型，每个项目可以不同。"))
        c.addSpacing(4)
        b = _btn("重新配置 AI…", on=self._open_setup)
        b.setToolTip("重新选择用云端 AI、本地模型，或检测电脑并安装本地模型")
        rowb = QHBoxLayout()
        rowb.addWidget(b)
        rowb.addStretch(1)
        c.addLayout(rowb)
        col.addWidget(f)

    def _open_setup(self):
        w = self.parent().window() if self.parent() else None
        if hasattr(w, "open_setup"):
            self.close()
            w.open_setup("B")

    # ---------- 自定义 ----------
    def _custom(self, col):
        self.data = {}
        f, c = _card("“思考”模块默认用的本地模型")
        c.addWidget(_label("工作流里“思考”节点没有指定模型时用它。只能是本机 Ollama 上已装的模型；选“自动”就用已装的第一个。", role="muted"))
        self.think = QComboBox()
        self.think.setAccessibleName("思考模块默认模型")
        self.think.currentIndexChanged.connect(self._think_picked)
        c.addWidget(self.think)
        self.think_note = _label("", role="muted")
        c.addWidget(self.think_note)
        col.addWidget(f)

        f, c = _card("自己添加云端服务商（OpenAI 兼容接口）")
        c.addWidget(_label("中转站、公司内部的模型服务等，只要是 OpenAI 兼容的接口都能加。密钥本身不写在这里：填一个变量名，"
                           "然后在顶栏“配置 AI”里给这家填 Key（会存进系统凭据管理器）。", role="muted"))
        self.custom_list = QVBoxLayout()
        c.addLayout(self.custom_list)
        form = QWidget()
        g = QVBoxLayout(form)
        g.setContentsMargins(0, 8, 0, 0)
        g.setSpacing(6)
        self.fields = {}
        for key, ph, tip in (("key", "编号，例如 my-proxy", "只能小写字母、数字、下划线、短横线"),
                             ("name", "名称，例如 我的中转", "顶栏下拉框里显示的名字"),
                             ("base", "接口地址，例如 https://example.com/v1", "OpenAI 兼容接口的根地址"),
                             ("key_env", "密钥变量名，例如 MY_PROXY_KEY", "大写字母和下划线"),
                             ("models", "模型名，多个用逗号分开", "例如 gpt-4o-mini, deepseek-chat")):
            e = QLineEdit()
            e.setPlaceholderText(ph)
            e.setToolTip(tip)
            e.setAccessibleName(ph.split("，")[0])
            self.fields[key] = e
            g.addWidget(e)
        rowb = QHBoxLayout()
        self.add_btn = _btn("添加", "primary", on=self._add_custom)
        rowb.addWidget(self.add_btn)
        rowb.addStretch(1)
        g.addLayout(rowb)
        self.custom_msg = _label("", role="bad")
        g.addWidget(self.custom_msg)
        c.addWidget(form)
        self.file_note = _label("", role="muted")
        c.addWidget(self.file_note)
        col.addWidget(f)
        self._load()

    def _load(self):
        self.ctrl.api.get("/api/settings", ok=self._fill, err=lambda e: self.custom_msg.setText(f"没能读取设置：{e}"))

    def reload(self):
        """再次打开：重新读设置文件；开关也对一遍控制器里的当前值"""
        for w in self.findChildren(Switch):
            name = w.accessibleName()
            if name == "关窗口后台运行并显示桌宠":
                w.setChecked(self.ctrl.pet_background)
            elif name == "生成后实跑检查":
                w.setChecked(self.ctrl.check)
        self._load()

    def _fill(self, data):
        self.data = data if isinstance(data, dict) else {}
        self.think.blockSignals(True)
        self.think.clear()
        default = self.data.get("think_default") or ""
        self.think.addItem(f"自动（现在是 {default}）" if default else "自动（还没装任何模型）", "")
        for m in self.data.get("think_choices") or []:
            self.think.addItem(m, m)
        cur = self.data.get("think_model") or ""
        i = self.think.findData(cur)
        if cur and i < 0:
            self.think.addItem(f"{cur}（没找到这个模型）", cur)
            i = self.think.count() - 1
        self.think.setCurrentIndex(max(i, 0))
        self.think.blockSignals(False)
        while self.custom_list.count():
            it = self.custom_list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        apis = self.data.get("custom_apis") or []
        if not apis:
            self.custom_list.addWidget(_label("还没有添加过。", role="muted"))
        for a in apis:
            row = QHBoxLayout()
            text = f"{a.get('name')}（{a.get('key')}）  {a.get('base')}  密钥变量 {a.get('key_env')}  模型 {', '.join(a.get('models') or [])}"
            if a.get("overrides_builtin"):
                text += "  （覆盖了内置的同名服务商）"
            row.addWidget(_label(text), 1)
            row.addWidget(_btn("删除", on=lambda _=False, k=a.get("key"): self._remove_custom(k)))
            w = QWidget()
            w.setLayout(row)
            self.custom_list.addWidget(w)
        self.custom_msg.setText("")
        self.file_note.setText(f"这些都存在 {self.data.get('settings_file') or 'model_settings.json'}（不进 git），也可以直接改那个文件。")

    def _think_picked(self, i):
        m = self.think.itemData(i)
        if m is None:
            return
        self.ctrl.api.send("PUT", "/api/settings", {"think_model": m}, ok=self._fill,
                           err=lambda e: self.custom_msg.setText(f"没能保存：{e}"))

    def _add_custom(self):
        body = {k: e.text() for k, e in self.fields.items()}
        body["models"] = [m.strip() for m in body["models"].replace("，", ",").split(",") if m.strip()]

        def ok(data):
            for e in self.fields.values():
                e.clear()
            self._fill(data)
            self.ctrl.set_notice("已添加，去“配置 AI”里给它填密钥后就能在模型列表里选")
            self._refresh_picker()
        self.ctrl.api.post("/api/settings/custom-api", body, ok=ok, err=lambda e: self.custom_msg.setText(str(e)))

    def _refresh_picker(self):
        """顶栏的模型下拉框在主窗口的 topbar 上：服务商变了让它重新读一遍"""
        w = self.parent().window() if self.parent() else None
        picker = getattr(getattr(w, "topbar", None), "model", None)
        if picker is not None and hasattr(picker, "refresh"):
            picker.refresh()

    def _remove_custom(self, key):
        if not self.ctrl.ask(title=f"删除自定义服务商“{key}”？", message="只删这条配置，不会动系统里存的密钥。", ok="删除", danger=True):
            return

        def ok(data):
            self._fill(data)
            self._refresh_picker()
        self.ctrl.api.send("DELETE", f"/api/settings/custom-api/{key}", None, ok=ok,
                           err=lambda e: self.custom_msg.setText(str(e)))
