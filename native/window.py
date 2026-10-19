"""主窗口：顶栏（需求输入、生成）、左侧栏（项目、我保存的、示例）、中间（开始页或画布）、右侧面板（运行、生成过程、节点、程序）。
各区域由各自的模块实现，这里只负责摆放、展开收起和全局快捷键。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                               QPlainTextEdit, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from .theme import C


def _placeholder(text):
    w = QLabel(text)
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.setProperty("role", "muted")
    return w


def _part(module, name, what):
    """载入某一块界面；还没写好或载入出错时放一块说明，其余部分照常可用"""
    import importlib
    import traceback
    try:
        return getattr(importlib.import_module(f"native.{module}"), name)
    except Exception:
        err = traceback.format_exc(limit=2)
        return lambda ctrl: _placeholder(f"{what}没法显示：\n{err}")


class MainWindow(QMainWindow):
    def __init__(self, ctrl, icon_path=None):
        super().__init__()
        self.ctrl = ctrl
        ctrl.window = self
        self.setWindowTitle("织灵")
        if icon_path:
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1280, 800)
        self.setMinimumSize(960, 620)

        # 窗口里两套界面：主程序（root）和首次启动引导（需要时才建，见 open_setup）
        self.views = QStackedWidget()
        self.setCentralWidget(self.views)
        self.setup = None
        root = QWidget(objectName="root")
        self.root = root
        col = QVBoxLayout(root)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.views.addWidget(root)

        TopBar = _part("side.topbar", "TopBar", "顶栏")
        Sidebar = _part("side.sidebar", "Sidebar", "左侧栏")
        StartPage = _part("side.start", "StartPage", "开始页")
        CanvasHead = _part("side.canvas_head", "CanvasHead", "画布标题")
        CanvasView = _part("canvas.view", "CanvasView", "画布")
        RightPanel = _part("panels.panel", "RightPanel", "右侧面板")

        self.topbar = TopBar(ctrl)
        col.addWidget(self.topbar)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {C['line']};")
        col.addWidget(line)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        col.addLayout(body, 1)

        self.sidebar = Sidebar(ctrl)
        self.sidebar.setFixedWidth(236)
        body.addWidget(self.sidebar)

        # 中间：没打开工作流时是开始页，否则是画布（上面一行标题和编辑按钮）
        self.center = QStackedWidget()
        self.start = StartPage(ctrl)
        canvas_wrap = QWidget()
        cv = QVBoxLayout(canvas_wrap)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        self.head = CanvasHead(ctrl)
        self.canvas = CanvasView(ctrl)
        cv.addWidget(self.head)
        cv.addWidget(self.canvas, 1)
        self.center.addWidget(self.start)
        self.center.addWidget(canvas_wrap)
        body.addWidget(self.center, 1)

        self.panel = RightPanel(ctrl)
        self.panel.setFixedWidth(344)
        body.addWidget(self.panel)

        ctrl.current_changed.connect(self._sync_center)
        ctrl.layout_changed.connect(self._sync_layout)
        self._sync_center()
        self._sync_layout()

        # 全局快捷键：正在打字的地方不抢按键；Ctrl+Z 撤销，Ctrl+Shift+Z 或 Ctrl+Y 重做，Delete / Backspace 删除选中
        for seq, fn in (("Ctrl+Z", lambda: ctrl.travel("undo")), ("Ctrl+Shift+Z", lambda: ctrl.travel("redo")),
                        ("Ctrl+Y", lambda: ctrl.travel("redo")), ("Delete", ctrl.delete_selected),
                        ("Backspace", ctrl.delete_selected)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(lambda fn=fn: None if self._typing() or self.views.currentWidget() is not self.root
                                 else fn())  # 引导页开着时不动画布

    def open_setup(self, start="A"):
        """显示首次启动引导（顶栏“配置 AI”从 B 页开始）；完成后回到主程序"""
        if self.setup is None:
            from .onboarding.flow import Onboarding
            from .onboarding.service import OnboardingService
            self.setup = Onboarding(OnboardingService(self.ctrl.api, self), start)
            self.setup.finished.connect(self._setup_done)
            self.views.addWidget(self.setup)
        else:
            self.setup.show_page(start)
        self.views.setCurrentWidget(self.setup)

    def open_settings(self, start="pet"):
        """顶栏“设置”或桌宠右键“设置”：打开设置面板（非模态，同一时间一个）"""
        from .settings import SECTIONS, SettingsDialog
        d = getattr(self, "settings_dialog", None)
        row = [k for k, _ in SECTIONS].index(start) if start in dict(SECTIONS) else 0
        if d is None:  # 只建一个，关了只是藏起来，下次直接再用
            d = self.settings_dialog = SettingsDialog(self.ctrl, self, start)
            if self.isVisible():
                g = self.geometry()
                d.move(g.center().x() - 380, g.center().y() - 280)
        else:
            d.reload()  # 设置文件可能在别处改过：重新读
        d.nav.setCurrentRow(row)
        d.show()
        d.raise_()
        d.activateWindow()
        return d

    def _setup_done(self):
        self.views.setCurrentWidget(self.root)
        picker = getattr(self.topbar, "model", None)
        if picker is not None and hasattr(picker, "refresh"):
            picker.refresh(report=True)  # 刚选的模型显示到顶栏
        if self.setup is not None and self.setup.svc.installing is None:  # 没有在后台安装：下次打开重新读最新状态
            self.setup.svc.close()
            self.views.removeWidget(self.setup)
            self.setup.deleteLater()
            self.setup = None

    @staticmethod
    def _typing():
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)) or QApplication.activeModalWidget() is not None

    def _sync_center(self):
        self.center.setCurrentIndex(1 if self.ctrl.current else 0)

    def _sync_layout(self):
        self.sidebar.setVisible(self.ctrl.side_open)
        self.panel.setVisible(self.ctrl.panel_open)

    def closeEvent(self, e):
        pet = getattr(self, "pet", None)  # 桌宠（native/pet/hook.py）：有工作流在运行时关窗口只是转到后台
        keep = bool(pet and pet.keep_running())
        if not self.ctrl.shutdown(keep_running=keep):  # 有项目没能存好，使用者选了先不关
            e.ignore()
            return
        if keep:
            e.ignore()
            pet.to_background()
            return
        super().closeEvent(e)
        QApplication.quit()  # 接了桌宠后关最后一个窗口不会自动退出，这里明确退出
