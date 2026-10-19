// 效率与严谨性审查（2026-10-17）Q01、Q02 的前端测试；Q01 的浏览器恢复见 e2e.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { toFlow, pathToId, dict } from "../src/graph.js";
import { RunStatus } from "../src/runStatus.js";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));

// 去掉所有类体（class 组件和普通类），剩下的代码（函数组件、普通函数）里不应出现 this.
function stripClassBodies(code) {
  let out = "";
  let i = 0;
  const re = /class\s+\w+(\s+extends\s+[\w.]+)?\s*\{/g;
  let m;
  while ((m = re.exec(code))) {
    out += code.slice(i, m.index);
    let depth = 1;
    let j = m.index + m[0].length;
    while (depth && j < code.length) {
      if (code[j] === "{") depth++;
      else if (code[j] === "}") depth--;
      j++;
    }
    i = re.lastIndex = j;
  }
  return out + code.slice(i);
}

test("Q01：函数组件里没有 this（类组件之外不许出现 this.）", () => {
  for (const f of readdirSync(SRC).filter((f) => /\.(jsx?|mjs)$/.test(f))) {
    const rest = stripClassBodies(readFileSync(join(SRC, f), "utf8"));
    const hits = rest.split("\n").map((l, n) => [n + 1, l]).filter(([, l]) => /\bthis\./.test(l));
    assert.deepEqual(hits, [], `${f} 在类组件之外用了 this`);
  }
});

test("Q01：merge.ports 写错时画图不抛错", () => {
  for (const ports of [1, "ab", [1, 2], null, {}]) {
    const wf = { id: "m", inputs: { x: { type: "any" } }, outputs: { y: { type: "any" } },
      nodes: [{ id: "m", kind: "merge", ports }], edges: [{ from: "$in.x", to: "m.a" }, { from: "m.value", to: "$out.y" }] };
    assert.doesNotThrow(() => toFlow(wf, {}), JSON.stringify(ports));
  }
});

// 修改前 App.jsx 的汇总逻辑，作为对照
const RANK = { failed: 4, running: 3, completed: 2, skipped: 1, idle: 0 };
function summarize(raw, wfId) {
  const byId = dict();
  for (const [path, s] of Object.entries(raw)) {
    const { id, items } = pathToId(path, wfId);
    const cur = byId[id] || { state: "idle", counts: items.length ? { total: 0, completed: 0, skipped: 0, failed: 0, running: 0 } : null };
    if (RANK[s.state] > RANK[cur.state]) Object.assign(cur, { state: s.state, reason: s.reason });
    if (cur.counts) {
      cur.counts.total += 1;
      cur.counts[s.state] = (cur.counts[s.state] || 0) + 1;
    }
    byId[id] = cur;
  }
  for (const s of Object.values(byId)) {
    if (s.counts && s.state !== "failed" && s.state !== "running") s.state = s.counts.completed ? "completed" : "skipped";
  }
  return byId;
}

test("Q02：增量汇总与原来的全量汇总结果一致", () => {
  let seed = 11;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  for (let round = 0; round < 200; round++) {
    const raw = {};
    const rs = new RunStatus("w");
    const paths = ["w.a", "w.b", ...Array.from({ length: 8 }, (_, i) => `w.each[${i}].f`), "w.each"];
    for (let k = 0; k < 40; k++) {
      const path = paths[Math.floor(rnd() * paths.length)];
      const state = ["running", "completed", "skipped", "failed"][Math.floor(rnd() * 4)];
      const reason = state === "skipped" ? `原因${k}` : undefined;
      raw[path] = { state, reason };
      rs.apply(path, state, reason);
    }
    const want = summarize(raw, "w");
    const got = rs.snapshot();
    assert.deepEqual(Object.keys(got).sort(), Object.keys(want).sort());
    for (const id of Object.keys(want)) {
      assert.equal(got[id].state, want[id].state, id);
      if (want[id].counts) {
        for (const st of ["total", "completed", "skipped", "failed", "running"]) assert.equal(got[id].counts[st], want[id].counts[st], `${id}.${st}`);
      } else {
        assert.equal(got[id].counts, null);
        assert.equal(got[id].reason, want[id].reason, id);
      }
    }
  }
});

test("Q02：每条事件只更新自己的那条记录，8,000 项循环的 16,000 条事件远低于原来的十几秒", () => {
  const rs = new RunStatus("w");
  const t0 = performance.now();
  for (let i = 0; i < 8000; i++) {
    rs.apply(`w.each[${i}].f`, "running");
    rs.apply(`w.each[${i}].f`, "completed");
  }
  const s = rs.snapshot();
  const ms = performance.now() - t0;
  assert.equal(rs.paths.size, 8000);
  assert.deepEqual(s["each/f"].counts, { total: 8000, failed: 0, running: 0, completed: 8000, skipped: 0 });
  assert.ok(ms < 2000, `用时 ${ms.toFixed(0)} ms`); // 宽松上限，只防止退回平方级
});
