"""在独立进程里运行模块，超时或停止时能真正结束它（线程无法被强制结束）。

进程可复用：一个进程跑完一次调用后放回池里；超时、停止或崩溃的进程直接结束并丢弃。
每次调用只占用一个进程，多个运行（例如网页上同时两个请求）各自取用。
边界：同时运行的模块进程最多 MAX_WORKERS 个（多出的调用排队）；池里最多留 MAX_IDLE 个空闲进程，
空闲超过 IDLE_SECONDS 秒的会被结束；主进程退出时全部结束。

子进程用 `python -m core.worker` 直接启动，通过本机端口加随机密钥连回来。
不用 multiprocessing 的 spawn：spawn 会在子进程里重新导入调用方的主脚本，
没有 `if __name__ == "__main__"` 保护的脚本（例如临时写的复现脚本）会让子进程起不来，模块一直等到超时。
"""
import atexit
import importlib
import os
import socket
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Client, Connection, answer_challenge, deliver_challenge
from pathlib import Path

from core.errors import ModuleError

MAX_WORKERS = int(os.environ.get("WORKFLOW_MAX_WORKERS", "4"))
MAX_IDLE = 2
IDLE_SECONDS = 300.0

ROOT = Path(__file__).resolve().parent.parent
START_SECONDS = 20.0  # 子进程启动并连回来的最长等待
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows 上不弹命令行窗口（桌面程序里也一样）
_POOL: list["_Worker"] = []  # 空闲进程，末尾是最近用过的
_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(MAX_WORKERS)
_reaper: threading.Thread | None = None


class WorkerTimeout(Exception):
    pass


class WorkerStopped(Exception):
    pass


class WorkerCrash(Exception):
    pass


def _serve(conn):
    while True:
        try:
            mod_name, func, args = conn.recv()
        except (EOFError, OSError):
            return
        try:
            fn = getattr(importlib.import_module(mod_name), func)
            conn.send(("ok", fn(args)))
        except ModuleError as e:
            conn.send(("module_error", e.code, e.message))
        except BaseException as e:  # noqa: BLE001 —— 模块的任何异常都要传回主进程
            conn.send(("crash", f"{type(e).__name__}: {e}"))


class _Worker:
    def __init__(self):
        key = os.urandom(32)
        srv = socket.create_server(("127.0.0.1", 0))
        srv.settimeout(0.2)
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-m", "core.worker", str(srv.getsockname()[1])],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        self.proc.stdin.write(key.hex().encode() + b"\n")
        self.proc.stdin.close()
        deadline = time.monotonic() + START_SECONDS
        try:
            while True:  # 等子进程连回来；它先退出了就立刻报告原因，不干等
                try:
                    sock, _ = srv.accept()
                    break
                except (socket.timeout, TimeoutError):
                    if self.proc.poll() is not None or time.monotonic() > deadline:
                        err = (self.proc.stderr.read() if self.proc.poll() is not None else b"").decode("utf-8", "replace")
                        self.proc.kill()
                        last = err.strip().splitlines()[-1] if err.strip() else "启动超时"
                        raise WorkerCrash(f"模块进程没能启动：{last}") from None
        finally:
            srv.close()
        sock.settimeout(None)
        self.conn = Connection(sock.detach())
        deliver_challenge(self.conn, key)  # 只接受拿着同一把密钥的子进程
        answer_challenge(self.conn, key)
        self.idle_since = time.monotonic()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def kill(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            self.conn.close()
            if self.proc.stderr:
                self.proc.stderr.close()


def _reap(now: float | None = None):
    """结束空闲太久的进程。"""
    now = time.monotonic() if now is None else now
    with _LOCK:
        old = [w for w in _POOL if now - w.idle_since > IDLE_SECONDS or not w.alive()]
        _POOL[:] = [w for w in _POOL if w not in old]
    for w in old:
        w.kill()


def _reaper_loop():
    while True:
        time.sleep(min(30.0, IDLE_SECONDS))
        _reap()


def _release(w: "_Worker"):
    """用完的进程放回池里；池满了就结束它。"""
    global _reaper
    w.idle_since = time.monotonic()
    with _LOCK:
        keep = len(_POOL) < MAX_IDLE
        if keep:
            _POOL.append(w)
            if _reaper is None:
                _reaper = threading.Thread(target=_reaper_loop, daemon=True, name="worker-reaper")
                _reaper.start()
    if not keep:
        w.kill()


def call(mod_name: str, func: str, args: dict, timeout: float, should_stop=None):
    """在工作进程里调用 mod_name.func(args)。返回模块输出；模块报错抛 ModuleError；
    超时抛 WorkerTimeout，停止抛 WorkerStopped（两者都会结束该进程）；进程异常退出抛 WorkerCrash。
    同时运行的进程已满时先排队；排队期间也响应停止。超时从模块真正开始运行时计算。"""
    while not _SLOTS.acquire(timeout=0.2):
        if should_stop and should_stop():
            raise WorkerStopped()
    try:
        return _call(mod_name, func, args, timeout, should_stop)
    finally:
        _SLOTS.release()


def _call(mod_name: str, func: str, args: dict, timeout: float, should_stop):
    _reap()
    with _LOCK:
        w = _POOL.pop() if _POOL else None
    if w is None or not w.alive():
        if w is not None:
            w.kill()
        w = _Worker()
    w.conn.send((mod_name, func, args))
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            w.kill()
            raise WorkerTimeout()
        if should_stop and should_stop():
            w.kill()
            raise WorkerStopped()
        if not w.alive():  # 子进程已经退出：马上报告，不等到超时
            w.kill()
            raise WorkerCrash("模块进程意外退出")
        try:
            ready = w.conn.poll(min(remaining, 0.2))
        except (EOFError, OSError):
            ready = True
        if ready:
            break
    try:
        msg = w.conn.recv()
    except (EOFError, OSError):
        w.kill()
        raise WorkerCrash("模块进程意外退出") from None
    _release(w)
    if msg[0] == "ok":
        return msg[1]
    if msg[0] == "module_error":
        raise ModuleError(msg[1], msg[2])
    raise WorkerCrash(msg[1])


def idle_count() -> int:
    with _LOCK:
        return len(_POOL)


def shutdown():
    with _LOCK:
        idle = list(_POOL)
        _POOL.clear()
    for w in idle:
        w.kill()


atexit.register(shutdown)


def _child_main():
    """子进程入口：python -m core.worker 端口；从标准输入读密钥，连回主进程后开始服务。"""
    port = int(sys.argv[1])
    key = bytes.fromhex(sys.stdin.readline().strip())
    conn = Client(("127.0.0.1", port), authkey=key)
    _serve(conn)


if __name__ == "__main__":
    _child_main()
