// 运行状态的增量汇总：每条运行事件 O(1) 更新，展示时只按画布节点数生成快照。
// （以前每条事件都复制一遍全部历史路径，循环项多时是平方级开销。）
import { dict, pathToId } from "./graph.js";

const STATES = ["failed", "running", "completed", "skipped"]; // 按显示优先级从高到低

export class RunStatus {
  constructor(wfId) {
    this.wfId = wfId;
    this.paths = new Map(); // 记录路径 → { id, state, reason }
    this.nodes = new Map(); // 画布节点 id → { loop, sets: { 状态 → 路径集合 } }
  }

  // 一条节点事件：state 为 running / completed / skipped / failed
  apply(path, state, reason) {
    let p = this.paths.get(path);
    if (!p) {
      const { id, items } = pathToId(path, this.wfId);
      let n = this.nodes.get(id);
      if (!n) {
        n = { loop: items.length > 0, sets: Object.fromEntries(STATES.map((s) => [s, new Set()])) };
        this.nodes.set(id, n);
      }
      p = { id, state: null, reason: undefined };
      this.paths.set(path, p);
    }
    const n = this.nodes.get(p.id);
    if (p.state) n.sets[p.state].delete(path);
    p.state = state;
    p.reason = reason;
    n.sets[state].add(path);
  }

  // 画布用的状态表：{ 节点 id → { state, reason, counts } }；循环体里的节点汇总各项
  snapshot() {
    const out = dict();
    for (const [id, n] of this.nodes) {
      const top = STATES.find((s) => n.sets[s].size > 0);
      if (!top) continue;
      const first = n.sets[top].values().next().value;
      const s = { state: top, reason: this.paths.get(first).reason, counts: null };
      if (n.loop) {
        s.counts = { total: 0 };
        for (const st of STATES) {
          s.counts[st] = n.sets[st].size;
          s.counts.total += n.sets[st].size;
        }
        if (top !== "failed" && top !== "running") s.state = s.counts.completed ? "completed" : "skipped";
      }
      out[id] = s;
    }
    return out;
  }
}
