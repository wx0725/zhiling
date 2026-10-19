"""引导页用到的后端能力：都走本地接口 /api/setup/*（见 server/app.py、tools/setup_ai.py）。
请求在后台线程里发，回调回到界面线程（见 native/api.py），界面不会卡住。

每个方法的回调约定：
  providers(ok({"apis", "clis", "keystore"}), err(detail))
  test_api(pid, key, ok(models), err(code, detail))        code: key / quota / network / unavailable
  check_cli(cid, ok({"found", "logged_in"}), err(detail))  logged_in 为 None 表示没法自动确认
  save(model_id, key, ok(), err(detail))                   key 为 None 时只记下选择；保存后算完成初始化
  finish(ok(), err(detail))                                不配置 AI，直接手动搭建
  detect(ok({"runtimes", "models"}), err(detail))
  probe(model_id, ok({"result", "score", "detail"}), err(detail))
  scan(on_item(key, value), ok(hw), err(detail)) 之后 recommend(hw) -> {"fits", "reasons"}
  install(model, on_step(step, pct, text), ok(model_id), err(step, detail))；cancel()
  run_test(model_id, ok(), err(detail))
安装放在这个对象上而不是页面上：切换页面不会中断，回到检测页时 installing 说明它还在进行。"""
from PySide6.QtCore import QObject, QTimer

SCAN_KEYS = ("cpu", "gpus", "ram", "disk", "os", "runtime")


def _msg(e) -> str:
    return str(e) or type(e).__name__


class OnboardingService(QObject):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self.closed = False  # 引导收起后：还在路上的回调不再交给已经删掉的页面
        self._scan = None
        self._install = None  # 正在进行的安装：StreamHandle
        self._task = None  # 当前这次安装的标记：取消后马上重装时，上一次迟到的结束回调不能动这一次的状态
        self.installing = None  # 正在安装的模型（目录里的一项）

    def close(self):
        self.cancel()
        self.closed = True

    def _live(self, fn):
        def call(*args):
            if not self.closed:
                fn(*args)
        return call

    # ---------- 云端 ----------
    def providers(self, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.get("/api/setup/providers", ok=ok, err=lambda e: err(_msg(e)))

    def test_api(self, pid, key, ok, err):
        ok, err = self._live(ok), self._live(err)
        def got(r):
            if r.get("ok"):
                ok(r.get("models") or [])
            else:
                err(r.get("code") or "unavailable", r.get("detail") or "")
        self.api.post("/api/setup/test-api", {"provider": pid, "key": key}, ok=got,
                      err=lambda e: err("unavailable", _msg(e)))

    def check_cli(self, cid, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.post("/api/setup/check-cli", {"cli": cid}, ok=ok, err=lambda e: err(_msg(e)))

    def save(self, model_id, key, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.post("/api/setup/save", {"model": model_id, "key": key}, ok=lambda _: ok(), err=lambda e: err(_msg(e)))

    def finish(self, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.post("/api/setup/finish", {}, ok=lambda _: ok(), err=lambda e: err(_msg(e)))

    # ---------- 本地模型 ----------
    def detect(self, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.get("/api/setup/local", ok=ok, err=lambda e: err(_msg(e)))

    def probe(self, model_id, ok, err):
        ok, err = self._live(ok), self._live(err)
        self.api.post("/api/setup/probe", {"model": model_id}, ok=ok, err=lambda e: err(_msg(e)))

    # ---------- 检测电脑 ----------
    def scan(self, on_item, ok, err):
        on_item, ok, err = self._live(on_item), self._live(ok), self._live(err)
        def got(r):
            self._scan = r
            hw = r.get("hw") or {}
            keys = [k for k in SCAN_KEYS if k in hw]

            def step(i=0):  # 一项一项填上，让人看得清每一项
                if i < len(keys):
                    on_item(keys[i], hw[keys[i]])
                    QTimer.singleShot(120, self, lambda: step(i + 1))
                else:
                    ok(hw)
            step()
        self.api.get("/api/setup/scan", ok=got, err=lambda e: err(_msg(e)))

    def recommend(self, hw):
        r = self._scan or {}
        return {"fits": r.get("fits") or [], "reasons": r.get("reasons") or []}

    def install(self, model, on_step, ok, err):
        on_step, ok, err = self._live(on_step), self._live(ok), self._live(err)
        self.cancel()
        self.installing = model
        task = self._task = object()
        state = {"step": "runtime", "model": None, "error": None}

        def event(ev):
            if self._task is not task:
                return
            if ev.get("type") == "error":
                state["error"] = ev.get("message") or "安装失败"
                return
            if ev.get("type") == "done":
                return
            if ev.get("step"):
                state["step"] = ev["step"]
                if ev.get("model"):
                    state["model"] = ev["model"]
                on_step(ev["step"], int(ev.get("pct") or 0), ev.get("text") or "")

        def done(error):
            from ..api import Aborted
            if self._task is not task:
                return  # 已经取消或换成了新的安装
            self._task = self._install = self.installing = None
            if isinstance(error, Aborted):
                return
            if error is not None or state["error"]:
                err(state["step"], state["error"] or _msg(error))
            elif state["model"]:
                ok(state["model"])
            else:
                err(state["step"], "安装没有完成")
        self._install = self.api.stream("/api/setup/install", {"tag": model.get("tag")}, on_event=event, on_done=done)

    def cancel(self):
        self._task = None
        if self._install is not None:
            self._install.abort()
            self._install = None
        self.installing = None

    def run_test(self, model_id, ok, err):
        ok, err = self._live(ok), self._live(err)
        def got(r):
            if r.get("ok"):
                ok()
            else:
                err(r.get("detail") or "模型没能正常运行")
        self.api.post("/api/setup/run-test", {"model": model_id}, ok=got, err=lambda e: err(_msg(e)))
