"""测试、截图脚本用的小工具。

注意：不要用 QTest.qWait 等后台请求——它等待时不放开 Python 的全局锁，后台线程里的接口请求和后端服务都会停住，
回调迟迟不来。用这里的 wait(毫秒)（真正跑一个事件循环）或 wait_until(条件, 最长毫秒)。"""
from PySide6.QtCore import QEventLoop, QTimer


def wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_until(cond, timeout_ms: int = 5000, step_ms: int = 30) -> bool:
    waited = 0
    while not cond():
        if waited >= timeout_ms:
            return False
        wait(step_ms)
        waited += step_ms
    return True
