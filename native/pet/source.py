"""桌宠的真实数据源：直接读同一进程里的 Controller（主窗口关掉后只是隐藏，运行照常进行）。
提供和 pet.FakeRun 一样的几样东西：state / headline / detail / progress / problem / tick。"""
from ..logic import bugcheck
from ..logic._js import js_str

# Controller.run_state() → 桌宠的状态（头顶小珠子的颜色）
STATE = {"running": "running", "pausing": "running", "paused": "running", "stopping": "running",
         "done": "done", "error": "error", "stopped": "idle", "idle": "idle"}


def _short(text, n=26):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


class ControllerRun:
    def __init__(self, ctrl):
        self.ctrl = ctrl

    def tick(self):
        pass

    @property
    def state(self):
        return STATE.get(self.ctrl.run_state(), "idle")

    def _steps(self):
        """画布上真正干活的节点（不算输入、输出端点）"""
        return [n["id"] for n in self.ctrl.nodes if n.get("type") != "terminal"]

    def _finished(self, steps):
        st = self.ctrl.node_status
        return [i for i in steps if (st.get(i) or {}).get("state") in ("completed", "skipped", "failed")]

    def _name(self):
        c = self.ctrl.current
        return js_str((c or {}).get("wf", {}).get("name") or "工作流")

    def _why(self):
        c = self.ctrl.current
        err = (self.ctrl.result or {}).get("error")
        return bugcheck.explain_error(err, c["raw"] if c else {}, self.ctrl.node_names())

    def progress(self):
        if self.state == "done":
            return 1.0
        steps = self._steps()
        return len(self._finished(steps)) / len(steps) if steps else 0.0

    def headline(self):
        rs = self.ctrl.run_state()
        if rs == "idle":
            return "现在没有在运行的工作流"
        if rs == "stopped":
            return "运行已终止"
        if rs == "done":
            secs = (self.ctrl.result or {}).get("seconds")
            return f"运行完成，用时 {js_str(secs)} 秒" if secs is not None else "运行完成"
        if rs == "error":
            title = self._why()["title"]  # “「X」这一步出错了” 或 “运行出错了”
            return _short("运行出错：" + title.replace("这一步出错了", ""), 22) if "这一步" in title else title
        if rs == "stopping":
            return "正在终止…"
        names = self.ctrl.node_names()
        running = [(i, s) for i, s in self.ctrl.node_status.items()
                   if s.get("state") == "running" and not i.split("/")[-1].startswith("$")]
        if not running:
            return "已暂停" if rs == "paused" else "正在运行：准备中"
        nid, s = running[-1]
        text = _short(names.get(nid, nid.split("/")[-1]), 12)
        cnt = s.get("counts")
        if cnt and cnt.get("total"):
            done = cnt.get("completed", 0) + cnt.get("skipped", 0) + cnt.get("failed", 0)
            text += f" {done}/{cnt['total']}"
        return ("暂停中：" if rs in ("pausing", "paused") else "正在运行：") + text

    def detail(self):
        rs = self.ctrl.run_state()
        if rs == "error":
            why = self._why()
            return _short(why["cause"] or why["detail"] or "没有拿到具体原因", 18) + " · 点我去看看"
        if rs in ("idle", "stopped"):
            return "双击我打开织灵"
        steps = self._steps()
        if rs == "done" or not steps:
            return self._name()
        k = min(len(self._finished(steps)) + 1, len(steps))
        return f"{_short(self._name(), 12)} · 第 {k} 步，共 {len(steps)} 步"

    def problem(self):
        """出错时：(要定位的节点 id 列表, 出错节点的名字)"""
        why = self._why()
        targets = why["targets"]
        names = self.ctrl.node_names()
        return targets, names.get(targets[0], targets[0].split("/")[-1]) if targets else ""
