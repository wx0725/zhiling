"""运行状态的增量汇总：每条运行事件 O(1) 更新，展示时只按画布节点数生成快照。
（以前每条事件都复制一遍全部历史路径，循环项多时是平方级开销。）（移植自 web/src/runStatus.js）"""
from .graph import path_to_id

STATES = ["failed", "running", "completed", "skipped"]  # 按显示优先级从高到低


class RunStatus:
    def __init__(self, wf_id):
        self.wf_id = wf_id
        self.paths = {}  # 记录路径 → {"id", "state", "reason"}
        # 画布节点 id → {"loop", "sets": {状态 → 路径集合}}；集合用字典按加入顺序保存（快照取最早的那条的原因）
        self.nodes = {}

    def apply(self, path, state, reason=None):
        """一条节点事件：state 为 running / completed / skipped / failed"""
        p = self.paths.get(path)
        if p is None:
            r = path_to_id(path, self.wf_id)
            n = self.nodes.get(r["id"])
            if n is None:
                n = {"loop": len(r["items"]) > 0, "sets": {s: {} for s in STATES}}
                self.nodes[r["id"]] = n
            p = {"id": r["id"], "state": None, "reason": None}
            self.paths[path] = p
        n = self.nodes[p["id"]]
        if p["state"]:
            n["sets"][p["state"]].pop(path, None)
        p["state"] = state
        p["reason"] = reason
        n["sets"][state][path] = True

    def snapshot(self):
        """画布用的状态表：{节点 id → {"state", "reason", "counts"}}；循环体里的节点汇总各项"""
        out = {}
        for nid, n in self.nodes.items():
            top = next((s for s in STATES if n["sets"][s]), None)
            if top is None:
                continue
            first = next(iter(n["sets"][top]))
            s = {"state": top, "reason": self.paths[first]["reason"], "counts": None}
            if n["loop"]:
                counts = {"total": 0}
                for st in STATES:
                    counts[st] = len(n["sets"][st])
                    counts["total"] += len(n["sets"][st])
                s["counts"] = counts
                if top not in ("failed", "running"):
                    s["state"] = "completed" if counts["completed"] else "skipped"
            out[nid] = s
        return out

    def settle_running(self, state, reason=None):
        """还标着运行中的节点一律改成 state（运行被中途断开、不会再有它们的结束事件时用）"""
        for path, p in list(self.paths.items()):
            if p["state"] == "running":
                self.apply(path, state, reason)
