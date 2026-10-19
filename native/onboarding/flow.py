"""首次启动引导：A 欢迎、B 选择方式、C 云端 AI、D 本地模型、E 检测电脑、F 了解详情，装在一个控件里按各页的 go 信号切换。

跳转规则（ROUTES）：
  A：开始使用 → B，了解详情 → F            F：返回 → A
  B：返回 → A，云端 → C，本地 → D，检测电脑 → E，手动搭建 → 主程序
  C：返回 → B，保存成功 → 主程序
  D：返回 → B；没有模型时 检测电脑 → E、改用云端 → C、手动搭建 → 主程序；选好模型 → 主程序
  E：返回 → B，尝试云端 → C，手动搭建或安装测试成功 → 主程序
六页各建一次、一直留着，切换只是换显示的那一页：E 页正在进行的安装不会因为切页而中断。
进主程序时先记下已完成初始化（下次启动直接进主程序），再发出 finished。"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from . import about, choose, cloud, detect, local, welcome

PAGES = {"A": welcome, "B": choose, "C": cloud, "D": local, "E": detect, "F": about}
ROUTES = {
    "A": {"B", "F"},
    "B": {"A", "C", "D", "E", "main"},
    "C": {"B", "main"},
    "D": {"B", "C", "E", "main"},
    "E": {"B", "C", "main"},
    "F": {"A"},
}


class Onboarding(QWidget):
    finished = Signal()

    def __init__(self, svc, start="A"):
        super().__init__()
        self.svc = svc
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.stack = QStackedWidget()
        col.addWidget(self.stack)
        self.pages = {}
        for name, module in PAGES.items():
            page = module.Page(svc)
            page.go.connect(lambda target, src=name: self.route(src, target))
            self.stack.addWidget(page)
            self.pages[name] = page
        self.current = start
        self.show_page(start)

    def route(self, src, target):
        if src != self.current or target not in ROUTES.get(src, ()):
            return  # 不是当前页发出的、或不在规则里的跳转不理会
        if target != "main":
            self.show_page(target)
            return
        # 进主程序：记下已完成初始化（保存模型时已记过一次，手动搭建时这里记；重复记没有坏处），记不下也照样进
        self.svc.finish(self.finished.emit, lambda _detail: self.finished.emit())

    def show_page(self, name):
        self.current = name
        self.stack.setCurrentWidget(self.pages[name])
