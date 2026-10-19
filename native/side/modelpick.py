"""顶栏的“用哪个模型生成”：本地 Ollama、云端 API、订阅命令行（见 tools/llm.py）。
选项和默认选择从 /api/models 取。打开着项目时，这里显示并修改的是**这个项目**用的模型（记在项目里，换项目各用各的）；
没打开项目（首页）时改的是默认模型（存进 model_settings.json），新项目一开始都跟着默认。
能用的在前；还不能用的（缺密钥、没装命令行）隔一条分隔线放在后面，灰色、不能点，悬停说明还差什么。
本地的是电脑上 Ollama 已装的模型。生成中不能换。"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate

from ..theme import qc

KIND = {"local": "本地", "api": "API", "cli": "订阅"}
READY = Qt.ItemDataRole.UserRole + 1  # 这一项现在能不能用


class ModelPicker(QComboBox):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setAccessibleName("生成用的模型")
        self.setToolTip("生成工作流用哪个模型")
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setItemDelegate(QStyledItemDelegate(self))  # 默认的弹出列表不认每项的文字颜色，也不跟主题的高亮色
        self._chosen = None
        self._filling = False
        self.addItem("模型：读取中…")
        self.setEnabled(False)
        self.currentIndexChanged.connect(self._picked)
        ctrl.gen_changed.connect(self._sync)
        ctrl.projects_changed.connect(self._project_changed)
        self.refresh(report=True)
        for ms in (8000, 20000):  # 程序刚启动时本地 Ollama 可能还没起来：过一会儿再看一次装了哪些模型
            QTimer.singleShot(ms, self, self.refresh)

    def refresh(self, report=False):
        def ok(data):
            if not self.view().isVisible():  # 列表正开着时不换内容
                self._fill(data)

        def err(e):
            if report:
                self._failed(f"没能读取可选的模型：{e}")
        self.ctrl.api.get("/api/models", ok=ok, err=err)

    def _effective(self):
        """现在该显示哪个：项目自己选的，否则默认的。项目选的模型已经不在列表里（删了、服务没开）时也列出来并标明不可用，
        这样显示的和实际会用的始终是同一个，使用者看到了就会换"""
        pm = getattr(self.ctrl, "project_model", None)
        if not (self.ctrl.project and pm):
            return self._chosen
        if self.findData(pm) < 0:
            self.addItem(f"{pm}（这个项目选的，现在不可用，请换一个）", pm)
            i = self.count() - 1
            self.setItemData(i, "这个项目原来选的模型现在找不到了：可能被删了或服务没开。换一个，或者把它装回来", Qt.ItemDataRole.ToolTipRole)
            self.setItemData(i, False, READY)
            self.setItemData(i, qc("bad_ink"), Qt.ItemDataRole.ForegroundRole)
        return pm

    def _project_changed(self):
        """换了项目：显示它自己选的模型"""
        if self._filling or self.count() == 0 or self.itemData(0) is None:
            return
        self._filling = True
        self.setCurrentIndex(max(self.findData(self._effective()), 0))
        self._filling = False
        self._sync()

    def _fill(self, data):
        self._filling = True
        self.clear()
        self._chosen = data.get("chosen")
        items = data.get("choices") or []
        for group in ([c for c in items if c.get("ready")], [c for c in items if not c.get("ready")]):
            if not group:
                continue
            if self.count():
                self.insertSeparator(self.count())
            for c in group:
                kind = KIND.get(c.get("kind"))
                self.addItem(f"{kind} · {c.get('name')}" if kind else c.get("name"), c.get("id"))
                i = self.count() - 1
                self.setItemData(i, c.get("note") or "", Qt.ItemDataRole.ToolTipRole)
                self.setItemData(i, bool(c.get("ready")), READY)
                if not c.get("ready"):
                    self.setItemData(i, qc("muted"), Qt.ItemDataRole.ForegroundRole)
                    self.model().item(i).setEnabled(False)  # 灰色、不能点
        if not self.count():
            self.addItem("没有可用的模型")
            self.setItemData(0, "请安装 Ollama 并下载一个模型，或设置云端 API 的密钥，或登录订阅命令行", Qt.ItemDataRole.ToolTipRole)
        i = self.findData(self._effective())
        self.setCurrentIndex(max(i, 0))
        self._filling = False
        self._sync()

    def _failed(self, msg):
        self.ctrl.set_notice(msg)

    def _sync(self):
        self.setEnabled(self.count() > 0 and self.itemData(0) is not None and not self.ctrl.generating)
        note = self.itemData(self.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        where = "这个项目生成、理清需求用哪个模型（每个项目可以不同；首页上改的是默认模型）" if self.ctrl.project else "默认用哪个模型生成（新项目都跟着它；打开项目后可以单独改）"
        self.setToolTip(f"{where}。{note}" if note else where)

    def _picked(self, i):
        model = self.itemData(i)
        if self._filling or i < 0 or model == self._effective():
            return
        prev = self._effective()
        if model is None or not self.itemData(i, READY):  # 还不能用或不支持：不切换，说明原因
            note = self.itemData(i, Qt.ItemDataRole.ToolTipRole) or "这个模型现在还不能用"
            self._failed(f"{self.itemText(i)}：{note}")
            self._filling = True
            self.setCurrentIndex(max(self.findData(prev), 0))
            self._filling = False
            self._sync()
            return

        def bad(e):
            self._failed(f"没能换模型：{e}")
            self._chosen = prev
            self._filling = True
            self.setCurrentIndex(max(self.findData(prev), 0))  # 回到原来的选择
            self._filling = False
            self._sync()
        if self.ctrl.project:  # 打开着项目：只改这个项目的
            self.ctrl.set_project_model(model)
            self._sync()
            return
        self._chosen = model
        self._sync()
        self.ctrl.api.send("PUT", "/api/models", {"model": model}, ok=self._fill, err=bad)
