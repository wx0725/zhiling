"""把桌宠接进主程序（同一进程）：
- 关主窗口时还有工作流在运行：项目照常先存好，窗口只是隐藏，运行继续，桌宠出现在桌面右下角；
- 桌宠上：点一下看进度，出错时点一下 = 打开主窗口并定位到出错节点，双击 = 打开主窗口，右键“关闭” = 退出织灵；
- 主窗口重新打开（双击桌宠、点出错的桌宠、或再次启动织灵）时桌宠收起。
没在运行时关主窗口和以前一样直接退出。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .pet import Pet
from .source import ControllerRun


class PetHook:
    def __init__(self, ctrl, win):
        self.ctrl, self.win = ctrl, win
        self.pet = Pet(ControllerRun(ctrl), on_open=self.open_main, on_quit=self.quit)
        self.pet.on_settings = self.open_settings
        # 主窗口隐藏后，关掉桌宠的设置框等小窗口不能让程序跟着退出；真正退出走 quit / 主窗口的关闭
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def keep_running(self):
        """主窗口要关闭时问一句：有工作流在运行就转到后台（返回 True），否则照常关闭；设置里关掉了桌宠后台就不转"""
        return bool(self.ctrl.running) and bool(getattr(self.ctrl, "pet_background", True))

    def open_settings(self):
        """桌宠右键“设置”：打开主程序的设置面板（桌宠那一栏）"""
        w = self.win
        if hasattr(w, "open_settings"):
            w.open_settings("pet")

    def to_background(self):
        self.win.hide()
        self.pet.show()
        self.pet.raise_()

    def open_main(self, targets=None):
        """把主窗口拿回来；targets 非空时定位到那些节点（运行页的“定位”同一个做法）"""
        self.pet.hide()
        self.pet.bubble.hide()
        w = self.win
        if w.isHidden() or w.isMinimized():
            w.showMaximized()
        w.raise_()
        w.activateWindow()
        if targets:
            # 窗口刚显示出来，画布尺寸稳定后再移视野；选中节点会让右侧面板切到节点页，所以定位后再切回运行页（看出错说明）
            def locate():
                self.ctrl.focus_targets(targets)
                self.ctrl.set_tab("run")
            QTimer.singleShot(200, locate)

    def quit(self):
        """桌宠菜单“关闭”：退出织灵。还在运行就先问，确认后终止运行"""
        if self.ctrl.running and not self.ctrl.ask(title="工作流还在运行", message="现在关闭会终止正在运行的工作流。",
                                                   ok="关闭织灵", cancel="先不关", danger=True):
            return
        if self.ctrl.shutdown():
            self.pet.hide()
            QApplication.quit()
