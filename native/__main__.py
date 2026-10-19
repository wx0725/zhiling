"""启动原生桌面版：python -m native（打包后即 织灵.exe）。
先在后台启动接口服务（需要时也启动设置里要求由本程序拉起的 Ollama 服务），就绪后显示主窗口；启动失败时在窗口里说明原因。"""
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from .backend import Backend, project_root
from .theme import palette, qss, ui_font


class _Ready(QObject):
    done = Signal(bool)


INSTANCE = "local.workflow.native"  # 同一时间只开一个窗口：项目文件只有一个后端在写，不会互相覆盖


def _already_running() -> bool:
    """已经开着一个：让它把窗口拿到前面，本次直接退出"""
    s = QLocalSocket()
    s.connectToServer(INSTANCE)
    if not s.waitForConnected(500):
        return False
    s.write(b"show")
    s.waitForBytesWritten(500)
    s.disconnectFromServer()
    return True


def _listen(get_window) -> QLocalServer:
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)  # 上次异常退出留下的同名管道
    server.listen(INSTANCE)

    def show():
        while server.hasPendingConnections():
            server.nextPendingConnection().deleteLater()
        w = get_window()
        pet = getattr(w, "pet", None)
        if pet is not None:  # 窗口转到了后台（桌宠在显示）：拿回来、收起桌宠
            pet.open_main()
        elif w is not None:
            if w.isMinimized():
                w.showMaximized()
            w.raise_()
            w.activateWindow()
    server.newConnection.connect(show)
    return server


def main():
    # 任务栏按本程序自己的身份和图标分组，而不是算作 pythonw.exe
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.workflow.native")
    except (AttributeError, OSError):
        pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("织灵")
    if _already_running():
        return 0
    icon = project_root() / "native" / "assets" / "icon.ico"
    if icon.is_file():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setFont(ui_font())
    app.setStyleSheet(qss())

    # 启动中：先显示一个窗口，说明在做什么
    splash = QMainWindow()
    splash.setWindowTitle("织灵")
    msg = QLabel("正在启动…")
    msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
    msg.setProperty("role", "muted")
    msg.setWordWrap(True)
    splash.setCentralWidget(msg)
    splash.resize(1280, 800)
    splash.showMaximized()

    backend = Backend()
    ready = _Ready()
    holder = {}
    server = _listen(lambda: holder.get("win") or splash)  # noqa: F841  （留着引用，窗口开着时一直监听）

    def on_ready(ok):
        if not ok:
            msg.setText(backend.error)
            msg.setProperty("role", "bad")
            msg.style().polish(msg)
            return
        from .api import Api
        from .controller import Controller
        from .window import MainWindow
        api = Api(backend.base)
        ctrl = Controller(api)
        win = MainWindow(ctrl, icon if icon.is_file() else None)
        holder.update(api=api, ctrl=ctrl, win=win)
        try:  # 桌宠：关主窗口时还在运行就转到后台继续跑；载入失败不影响主程序
            from .pet.hook import PetHook
            win.pet = PetHook(ctrl, win)
        except Exception:
            import traceback
            traceback.print_exc()
        try:  # 还没完成初始化（第一次打开）：先进引导；读不到状态就直接进主程序，不挡住使用者
            if not api.request_sync("GET", "/api/setup/state").get("done", True):
                win.open_setup("A")
        except Exception:
            pass
        win.showMaximized()
        splash.close()
        ctrl.load()

    ready.done.connect(on_ready, Qt.ConnectionType.QueuedConnection)  # 在后台线程里发出：明确排队
    threading.Thread(target=lambda: ready.done.emit(backend.start()), daemon=True).start()
    code = app.exec()
    backend.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
