"""与后端接口通信。请求都在后台线程里发，结果回到界面线程再交给回调，界面不会卡住。
生成和运行是逐行 JSON 的流，边到边处理；abort() 断开连接，后端随之停下。

用法：
    api.get("/api/modules", ok=lambda data: ..., err=lambda e: ...)
    api.send("PUT", f"/api/projects/{pid}", body, ok=..., err=...)
    h = api.stream("/api/run", body, on_event=..., on_done=lambda error: ...)  # error 为 None、ApiError 或 Aborted
    h.abort()
回调只在界面线程里调用。"""
import json
import threading

import httpx
from PySide6.QtCore import QObject, Qt, Signal, Slot


class ApiError(Exception):
    def __init__(self, message, status=0, data=None):
        super().__init__(message)
        self.status = status
        self.data = data or {}


class Aborted(Exception):
    """调用方主动停止（点了停止、换了工作流）"""


class _Main(QObject):
    """把后台线程里的函数调用转到界面线程：信号跨线程时自动排队"""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        # 明确排队：自动判断在后台线程里发信号时不一定能认出要排队，回调就丢了
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()


def _detail(r: httpx.Response) -> tuple[str, dict]:
    try:
        data = r.json()
    except ValueError:
        data = {}
    d = data.get("detail") if isinstance(data, dict) else None
    if isinstance(d, dict) and isinstance(d.get("errors"), list) and d["errors"]:
        msg = str(d["errors"][0])
    elif isinstance(d, str) and d:
        msg = d
    else:
        msg = f"请求失败（{r.status_code}）"
    return msg, data if isinstance(data, dict) else {}


class StreamHandle:
    def __init__(self):
        self._stop = threading.Event()
        self._resp = None

    def abort(self):
        self._stop.set()
        r = self._resp
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    @property
    def aborted(self):
        return self._stop.is_set()


class Api:
    def __init__(self, base: str):
        self.base = base
        self._main = _Main()
        self._client = httpx.Client(base_url=base, timeout=httpx.Timeout(30, read=None))

    def _later(self, fn, *args):
        self._main.call.emit(lambda: fn(*args))

    def _bg(self, work):
        threading.Thread(target=work, daemon=True).start()

    # ---------- 普通请求 ----------
    def request_sync(self, method, url, body=None, timeout=10):
        """同步请求（只在关窗口前把没存好的内容发出去时用）"""
        r = self._client.request(method, url, json=body, timeout=timeout)
        if r.status_code >= 400:
            msg, data = _detail(r)
            raise ApiError(msg, r.status_code, data)
        return r.json()

    def send(self, method, url, body=None, ok=None, err=None):
        def work():
            try:
                r = self._client.request(method, url, json=body)
                if r.status_code >= 400:
                    msg, data = _detail(r)
                    raise ApiError(msg, r.status_code, data)
                try:
                    data = r.json()
                except ValueError:
                    data = {}
                if ok:
                    self._later(ok, data)
            except ApiError as e:
                if err:
                    self._later(err, e)
            except Exception as e:  # 连不上等
                if err:
                    self._later(err, ApiError(f"{type(e).__name__}: {e}"))
        self._bg(work)

    def get(self, url, ok=None, err=None):
        self.send("GET", url, None, ok, err)

    def post(self, url, body, ok=None, err=None):
        self.send("POST", url, body, ok, err)

    # ---------- 流 ----------
    def stream(self, url, body, on_event, on_done=None) -> StreamHandle:
        h = StreamHandle()

        def work():
            error = None
            try:
                if h.aborted:  # 还没发出去就停了（例如刚开始运行就点了终止）：不再发送
                    raise Aborted()
                with self._client.stream("POST", url, json=body) as r:
                    h._resp = r
                    if h.aborted:
                        raise Aborted()
                    if r.status_code >= 400:
                        r.read()
                        msg, data = _detail(r)
                        raise ApiError(msg, r.status_code, data)
                    for line in r.iter_lines():
                        if h.aborted:
                            raise Aborted()
                        if line.strip():
                            ev = json.loads(line)
                            self._later(lambda e=ev: None if h.aborted else on_event(e))
            except Aborted as e:
                error = e
            except ApiError as e:
                error = Aborted() if h.aborted else e
            except Exception as e:
                error = Aborted() if h.aborted else ApiError(f"{type(e).__name__}: {e}")
            if on_done:
                self._later(on_done, error)
        self._bg(work)
        return h

    def close(self):
        self._client.close()
