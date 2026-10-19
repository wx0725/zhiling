// 画布碰撞：节点不重叠；循环框装不下就扩大，扩大后推开邻居
import test from "node:test";
import assert from "node:assert/strict";
import { GAP, overlaps, rectOf, slide, slideGroup, nearestFree, settle, fitWhileDragging, guardChanges, canResize, fitSize } from "../src/collide.js";
import { moveNode, freezeLayout, setBoxSize } from "../src/edit.js";
import { toFlow, flowToLayout, layoutMoves, PAD, TOP } from "../src/graph.js";

const box = (id, x, y, w = 100, h = 60, extra = {}) => ({ id, position: { x, y }, data: { width: w, height: h }, ...extra });
const noOverlap = (nodes, moves) => {
  const placed = nodes.map((n) => ({ ...n, position: moves[n.id] || n.position }));
  for (const a of placed) for (const b of placed) {
    if (a !== b && a.parentId === b.parentId) assert.ok(!overlaps(rectOf(a), rectOf(b), 0), `${a.id} 与 ${b.id} 重叠`);
  }
  return placed;
};

test("拖动中：前方有节点就贴着边滑，空地照常走", () => {
  const size = { w: 100, h: 60 };
  const wall = [{ x: 200, y: 0, w: 100, h: 60 }];
  assert.deepEqual(slide({ x: 0, y: 0 }, { x: 50, y: 0 }, size, wall), { x: 50, y: 0 });
  assert.deepEqual(slide({ x: 80, y: 0 }, { x: 120, y: 30 }, size, wall), { x: 80, y: 30 }); // 横向被挡，只走纵向
  assert.deepEqual(slide({ x: 80, y: 0 }, { x: 120, y: 0 }, size, wall), { x: 80, y: 0 }); // 正面撞上：停住
});

test("拖动中：原来就压着别的节点（旧数据），可以把它拖出来", () => {
  const wall = [{ x: 0, y: 0, w: 100, h: 60 }];
  assert.deepEqual(slide({ x: 20, y: 0 }, { x: 30, y: 0 }, { w: 100, h: 60 }, wall), { x: 30, y: 0 });
});

test("放下时压着别人：挪到最近的空位；不压着就不动", () => {
  const others = [{ x: 0, y: 0, w: 100, h: 60 }];
  assert.deepEqual(nearestFree({ x: 300, y: 0, w: 100, h: 60 }, others), { x: 300, y: 0 });
  const p = nearestFree({ x: 70, y: 10, w: 100, h: 60 }, others);
  assert.deepEqual(p, { x: 100 + GAP, y: 10 });
});

test("放到别的节点上：拖动的那个让开，别人不动", () => {
  const nodes = [box("a", 0, 0), box("b", 40, 20)];
  const moves = settle(nodes, ["b"]);
  assert.deepEqual(Object.keys(moves), ["b"]);
  noOverlap(nodes, moves);
});

test("框里的节点拖到右边框外：框向右扩大，右边的邻居被推开", () => {
  const loop = box("each", 0, 0, 300, 200, { type: "loop" });
  const child = box("each/f", 400, TOP, 100, 60, { parentId: "each" }); // 拖出右边框
  const right = box("next", 320, 0);
  const stay = box("each/s", PAD, TOP, 100, 60, { parentId: "each" }); // 框里还有别的节点，框左边收不回来
  const nodes = [loop, child, right, stay];
  const moves = settle(nodes, ["each/f"]);
  assert.equal(moves["each/f"], undefined); // 框里的节点留在放下的位置
  assert.ok(moves.next.x >= 400 + 100 + PAD + GAP, "邻居推到扩大后的框右边");
  noOverlap(nodes, moves);
});

test("节点接线后变高、压到下面的节点：下面的被推开", () => {
  const nodes = [box("a", 0, 0, 100, 160), box("b", 0, 100)];
  const moves = settle(nodes, []);
  assert.ok(moves.b.y >= 160 + GAP || moves.b.x >= 100 + GAP);
  noOverlap(nodes, moves);
});

test("没有重叠时什么都不改", () => {
  assert.deepEqual(Object.keys(settle([box("a", 0, 0), box("b", 300, 0)], ["a"])), []);
});

test("拖动中：拉伸过的框里的节点碰到右边、下边，框立刻变大", () => {
  const loop = box("each", 0, 0, 300, 200, { type: "loop", style: { width: 300, height: 200 }, data: { width: 300, height: 200, manual: { w: 300, h: 200 } } });
  const child = box("each/f", 260, 180, 100, 60, { parentId: "each" });
  const [grown] = fitWhileDragging([loop, child]);
  assert.deepEqual([grown.data.width, grown.data.height], [260 + 100 + PAD, 180 + 60 + PAD]);
  assert.deepEqual(grown.position, loop.position); // 拖动中不挪框的位置
  const still = [loop, box("each/f", 10, TOP, 100, 60, { parentId: "each" })];
  assert.equal(fitWhileDragging(still), still); // 没变就返回原数组，不触发重画
});

test("拖动中：没拉伸过的框时时刻刻正好包住框里节点，四边都收；不挪框的位置、不动框里节点，只改画出来的范围", () => {
  const loop = box("each", 100, 50, 600, 400, { type: "loop", data: { width: 600, height: 400 } });
  const a = box("each/a", 200, 150, 100, 60, { parentId: "each" });
  const b = box("each/b", 350, 220, 100, 60, { parentId: "each" });
  const [l, a2, b2] = fitWhileDragging([loop, a, b]);
  assert.deepEqual(l.position, loop.position);
  assert.equal(a2, a);
  assert.equal(b2, b);
  assert.deepEqual(l.data.visual, { x: 200 - PAD, y: 150 - TOP });
  assert.deepEqual([l.data.width, l.data.height], [Math.max(196, 450 - 200 + PAD * 2), 280 - 150 + PAD + TOP]);
});

test("拖动中：里层框画出来的范围变了，外层框按它画出来的范围包住", () => {
  const outer = box("o", 0, 0, 800, 600, { type: "loop", data: { width: 800, height: 600 } });
  const inner = box("o/i", PAD, TOP, 300, 200, { type: "loop", parentId: "o", data: { width: 300, height: 200 } });
  const x = box("o/i/x", PAD - 100, TOP, 100, 60, { parentId: "o/i" }); // 拖出里层框左边
  const out = fitWhileDragging([outer, inner, x]);
  const i2 = out.find((n) => n.id === "o/i");
  const o2 = out.find((n) => n.id === "o");
  assert.equal(i2.data.visual.x, -100);
  assert.equal(o2.data.visual.x, PAD + -100 - PAD);
});

test("画图：框里的节点不再限制在框内；旧数据里的负坐标整体移进框里", () => {
  const wf = {
    nodes: [{ id: "each", kind: "foreach", max_items: 5, body: {
      inputs: { item: { type: "string" } }, outputs: { result: { type: "string" } },
      nodes: [{ id: "f", kind: "module", module: "x" }], edges: [],
      layout: { f: { x: -80, y: -30 }, $in: { x: 100, y: 0 }, $out: { x: 300, y: 0 } },
    } }],
    edges: [],
  };
  const { nodes } = toFlow(wf, {});
  const kids = nodes.filter((n) => n.parentId === "each");
  assert.ok(kids.every((n) => n.extent === undefined));
  for (const k of kids) assert.ok(k.position.x >= PAD && k.position.y >= TOP, `${k.id} 在框内`);
  const loop = nodes.find((n) => n.id === "each");
  for (const k of kids) assert.ok(k.position.x + rectOf(k).w <= loop.data.width, `${k.id} 没超出右边`);
});

test("同时拖动多个节点：其中一个被挡住，整组一起停或一起滑，队形不散", () => {
  const wall = [{ x: 200, y: 0, w: 100, h: 60 }];
  const size = { w: 100, h: 60 };
  const out = { ...slideGroup([
    { id: "a", prev: { x: 80, y: 0 }, next: { x: 120, y: 30 }, size, obstacles: wall },  // 横向会撞墙
    { id: "b", prev: { x: 80, y: 300 }, next: { x: 120, y: 330 }, size, obstacles: wall }, // 自己不会撞
  ]) };
  assert.deepEqual(out, { a: { x: 80, y: 30 }, b: { x: 80, y: 330 } }); // 都只走纵向
});

test("尺寸优先用排版时算的，拖动时和重新画图后一致", () => {
  assert.deepEqual(rectOf({ position: { x: 1, y: 2 }, data: { width: 120, height: 80 }, measured: { width: 120, height: 70 } }),
    { x: 1, y: 2, w: 120, h: 80 });
  assert.deepEqual(rectOf({ position: { x: 0, y: 0 }, data: {}, measured: { width: 50, height: 40 } }), { x: 0, y: 0, w: 50, h: 40 });
});

// ---------- GPT 审查第 1 轮的回归测试 ----------

test("松手时 React Flow 发来的原始位置换成碰撞修正后的位置（被挡住后松手不会压上别人）", () => {
  const nodes = [box("a", 0, 0, 196, 60), box("b", 206, 0, 196, 60)];
  const memo = new Map();
  const during = guardChanges([{ type: "position", id: "a", dragging: true, position: { x: 30, y: 0 } }], nodes, memo);
  assert.deepEqual(during[0].position, { x: 0, y: 0 }); // 拖动中被挡住
  const end = guardChanges([{ type: "position", id: "a", dragging: false, position: { x: 30, y: 0 } }], nodes, memo);
  assert.deepEqual(end[0].position, { x: 0, y: 0 }); // 松手的原始位置也被换掉
  const other = guardChanges([{ type: "select", id: "a", selected: true }], nodes, memo);
  assert.deepEqual(other, [{ type: "select", id: "a", selected: true }]); // 其他变化原样放行
});

test("框向左扩大时，左边的邻居被推开，框和框里其他节点在屏幕上不动", () => {
  const loop = box("each", 200, 0, 400, 200, { type: "loop" });
  const leftNb = box("nb", -40, 0, 196, 60);
  const a = box("each/a", -50, TOP, 100, 60, { parentId: "each" });
  const b = box("each/b", 250, TOP, 100, 60, { parentId: "each" });
  const nodes = [loop, leftNb, a, b];
  const moves = settle(nodes, ["each/a"]);
  const abs = (id, own) => ((moves.each || loop.position).x + (moves[id] || own.position).x);
  assert.equal(abs("each/b", b), 200 + 250, "框里没拖动的节点在屏幕上不动");
  assert.ok(moves.nb && moves.nb.x < -40, "左边的邻居被往左推");
  noOverlap(nodes, moves);
});

test("放下时能找到斜着挪开的更近空位", () => {
  const p = nearestFree({ x: 150, y: 100, w: 100, h: 60 }, [{ x: 100, y: 0, w: 100, h: 300 }, { x: 210, y: 100, w: 300, h: 60 }]);
  const d = Math.hypot(p.x - 150, p.y - 100);
  assert.ok(d < 120, `挪了 ${d.toFixed(0)}，应找到约 92 的斜向空位：${JSON.stringify(p)}`);
});

test("画布位置换算回 layout 与画图互为逆运算（含旧数据里的负坐标）", () => {
  const wf = { nodes: [{ id: "each", kind: "foreach", max_items: 5, body: {
    inputs: {}, outputs: {}, nodes: [{ id: "a", kind: "module", module: "x" }, { id: "b", kind: "module", module: "x" }], edges: [],
    layout: { a: { x: -80, y: 0 }, b: { x: 200, y: 40 } } } }], edges: [] };
  const { nodes } = toFlow(wf, {});
  const conv = flowToLayout(nodes);
  for (const id of ["a", "b"]) assert.deepEqual(conv(nodes.find((n) => n.id === `each/${id}`)), wf.nodes[0].body.layout[id]);
  const b = nodes.find((n) => n.id === "each/b");
  assert.deepEqual(conv(b, { x: b.position.x + 30, y: b.position.y }), { x: 230, y: 40 }); // 拖动 30，写回也只多 30
});

// ---------- GPT 审查第 2 轮的回归测试 ----------

test("节点 id 叫 constructor 也不会被当成坐标（以 id 为键的表不带原型）", () => {
  const nodes = [box("constructor", 0, 0), box("a", 300, 0)];
  const memo = new Map();
  guardChanges([{ type: "position", id: "a", dragging: true, position: { x: 310, y: 0 } }], nodes, memo);
  const end = guardChanges([{ type: "position", id: "constructor", dragging: false, position: { x: 5, y: 5 } }], nodes, memo);
  assert.deepEqual(end[0].position, { x: 5, y: 5 });
  const moves = settle(nodes, ["a"]);
  assert.equal(moves.constructor, undefined);
  assert.equal(slideGroup([{ id: "a", prev: { x: 0, y: 0 }, next: { x: 1, y: 0 }, size: { w: 10, h: 10 }, obstacles: [] }]).constructor, undefined);
});

test("拖动中框已经变大：松手整理时仍知道框是往右扩大的，右边略高的邻居被往右推，框不被推走", () => {
  const loop = box("each", 0, 0, 528, 200, { type: "loop" }); // 拖动中已从 300 变宽到 528
  const child = box("each/f", 400, TOP, 100, 60, { parentId: "each" });
  const nb = box("nb", 320, -40, 100, 60); // 右边、位置略高
  const stay = box("each/s", PAD, TOP, 100, 140, { parentId: "each" });
  const moves = settle([loop, child, nb, stay], ["each/f"], { base: new Map([["each", { w: 300, h: 200 }]]) });
  assert.equal(moves.each, undefined, "框不动");
  assert.ok(moves.nb && moves.nb.x >= 528 + GAP, `邻居被往右推：${JSON.stringify(moves.nb)}`);
});

// ---------- 循环框回缩与手动拉伸 ----------

test("拉伸过的框不回缩；框里节点拖出左边时框向左扩大，记下的大小跟着变，右边不动", () => {
  const loop = box("each", 100, 0, 600, 300, { type: "loop", data: { width: 600, height: 300, manual: { w: 600, h: 300 } } });
  const a = box("each/a", 200, TOP, 100, 60, { parentId: "each" });
  assert.deepEqual(Object.keys(settle([loop, a], [])), []); // 左边空着也不回缩
  const out = box("each/a", -40, TOP, 100, 60, { parentId: "each" });
  const sizes = new Map();
  const moves = settle([loop, out], ["each/a"], { sizesOut: sizes });
  const dx = PAD + 40;
  assert.equal(moves.each.x, 100 - dx);
  assert.deepEqual(sizes.get("each"), { w: 600 + dx, h: 300 });
  assert.equal(moves.each.x + sizes.get("each").w, 100 + 600, "右边不动");
});

test("被拉伸的框压到邻居：推开邻居，框不动", () => {
  const loop = box("each", 0, 0, 500, 300, { type: "loop", data: { width: 500, height: 300, manual: { w: 500, h: 300 } } });
  const a = box("each/a", PAD, TOP, 100, 60, { parentId: "each" });
  const nb = box("nb", 450, 100, 100, 60);
  const moves = settle([loop, a, nb], [], { pinned: ["each"] });
  assert.equal(moves.each, undefined);
  assert.ok(moves.nb.x >= 500 + GAP || moves.nb.y >= 300 + GAP);
});

test("拉伸时不能压到框里的节点；拉左边、上边时按框里节点不动来算", () => {
  const start = { x: 100, y: 100, kids: [{ x: 50, y: 90, w: 100, h: 60 }] }; // 相对框
  assert.equal(canResize(start, { x: 100, y: 100, width: 400, height: 300 }), true);
  assert.equal(canResize(start, { x: 100, y: 100, width: 150 + PAD - 1, height: 300 }), false); // 右边压到
  assert.equal(canResize(start, { x: 100, y: 100, width: 400, height: 90 + 60 + PAD - 1 }), false); // 下边压到
  assert.equal(canResize(start, { x: 100 + 50 - PAD, y: 100, width: 400, height: 300 }), true); // 左边刚好贴住
  assert.equal(canResize(start, { x: 100 + 50 - PAD + 1, y: 100, width: 400, height: 300 }), false);
  assert.equal(canResize(start, { x: 100, y: 100 + 90 - TOP + 1, width: 400, height: 300 }), false); // 上边压到
  assert.equal(canResize({ x: 0, y: 0, kids: [] }, { x: 0, y: 0, width: 50, height: 50 }), false); // 空框也有最小尺寸
});

test("正好装下框里节点的大小", () => {
  const f = fitSize([{ x: PAD, y: TOP, w: 300, h: 60 }, { x: 400, y: 200, w: 100, h: 60 }]);
  assert.deepEqual(f, { w: 500 - PAD + PAD * 2, h: 260 - TOP + PAD + TOP });
});

test("画图：拉伸过的框按记下的大小画，装不下时照样变大", () => {
  const wf = (w, h) => ({ nodes: [{ id: "each", kind: "foreach", max_items: 5, body: {
    inputs: {}, outputs: {}, nodes: [{ id: "a", kind: "module", module: "x" }], edges: [], layout: { a: { x: 0, y: 0 } } } }],
    edges: [], layout: { each: { x: 0, y: 0, w, h } } });
  const big = toFlow(wf(900, 500), {}).nodes.find((n) => n.id === "each");
  assert.deepEqual([big.data.width, big.data.height, big.data.manual], [900, 500, { w: 900, h: 500 }]);
  const small = toFlow(wf(50, 50), {}).nodes.find((n) => n.id === "each");
  assert.ok(small.data.width >= 196 && small.data.height > 50);
});

test("只改位置时留着框记下的大小；setBoxSize(null) 回到自动伸缩", () => {
  const wf = { nodes: [{ id: "each", kind: "foreach", max_items: 5, body: { inputs: {}, outputs: {}, nodes: [], edges: [] } }], edges: [],
    layout: { each: { x: 0, y: 0, w: 800, h: 400 } } };
  assert.deepEqual(moveNode(wf, "each", { x: 10, y: 20 }).layout.each, { x: 10, y: 20, w: 800, h: 400 });
  assert.deepEqual(freezeLayout(wf, { each: { x: 5, y: 5 } }).layout.each, { x: 5, y: 5, w: 800, h: 400 });
  assert.deepEqual(setBoxSize(wf, "each", null).layout.each, { x: 0, y: 0 });
  assert.deepEqual(setBoxSize(wf, "each", { w: 300.4, h: 200.6 }).layout.each, { x: 0, y: 0, w: 300, h: 201 });
});

test("拉伸过的框被框里节点撑大后，记下新的大小，节点移回来也不回缩", () => {
  const loop = box("each", 0, 0, 600, 300, { type: "loop", data: { width: 600, height: 300, manual: { w: 600, h: 300 } } });
  const a = box("each/a", PAD, TOP, 100, 60, { parentId: "each" });
  const far = box("each/b", 900, TOP, 100, 60, { parentId: "each" }); // 撑到右边 1000 + PAD
  const sizes = new Map();
  settle([loop, a, far], ["each/b"], { sizesOut: sizes });
  assert.deepEqual(sizes.get("each"), { w: 1000 + PAD, h: 300 });
});

// ---------- 没拉伸过的框始终正好包住框里节点：按“拖动 → 整理 → 写回 → 重新画图”整条路验证 ----------

// 画出来的每个节点在画布上的绝对位置
const absolute = (nodes) => {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const abs = (n) => (n.parentId ? { x: abs(byId.get(n.parentId)).x + n.position.x, y: abs(byId.get(n.parentId)).y + n.position.y } : n.position);
  return Object.fromEntries(nodes.map((n) => [n.id, { ...abs(n), w: rectOf(n).w, h: rectOf(n).h }]));
};
// 把画布节点 ids 拖到 to（相对所在层的位置），整理后写回，再重新画
function dragRoundTrip(wf, to) {
  const { nodes } = toFlow(wf, {});
  const live = nodes.map((n) => (to[n.id] ? { ...n, position: to[n.id] } : n));
  const moves = { ...to, ...settle(live, Object.keys(to)) };
  const next = freezeLayout(wf, layoutMoves(live, moves));
  return { before: absolute(nodes), after: absolute(toFlow(next, {}).nodes), next };
}
const loopWf = (layout, outer = {}) => ({ nodes: [{ id: "each", kind: "foreach", max_items: 5, body: {
  inputs: {}, outputs: {}, nodes: Object.keys(layout).map((id) => ({ id, kind: "module", module: "x" })), edges: [], layout } }],
  edges: [], layout: { each: { x: 100, y: 100 }, ...outer } });
const contains = (b, k) => k.x >= b.x + PAD - 1 && k.y >= b.y + TOP - 1 && k.x + k.w <= b.x + b.w - PAD + 1 && k.y + k.h <= b.y + b.h - PAD + 1;

test("框里最左边的节点往右拖：框左边跟着收回，其他节点不动，框正好包住", () => {
  const wf = loopWf({ a: { x: 0, y: 0 }, b: { x: 600, y: 0 } });
  const { nodes } = toFlow(wf, {});
  const a = nodes.find((n) => n.id === "each/a");
  const { before, after } = dragRoundTrip(wf, { "each/a": { x: a.position.x + 200, y: a.position.y } });
  assert.equal(after["each/a"].x, before["each/a"].x + 200);
  assert.equal(after["each/b"].x, before["each/b"].x);
  assert.equal(after.each.x, after["each/a"].x - PAD, "框左边收到 a 的左边");
  assert.ok(contains(after.each, after["each/a"]) && contains(after.each, after["each/b"]));
});

test("框里的节点拖出框左边：框向左扩大装下它，其他节点不动", () => {
  const wf = loopWf({ a: { x: 0, y: 0 }, b: { x: 300, y: 0 } });
  const { nodes } = toFlow(wf, {});
  const a = nodes.find((n) => n.id === "each/a");
  const { before, after } = dragRoundTrip(wf, { "each/a": { x: a.position.x - 150, y: a.position.y } });
  assert.equal(after["each/a"].x, before["each/a"].x - 150);
  assert.equal(after["each/b"].x, before["each/b"].x);
  assert.equal(after.each.x, after["each/a"].x - PAD);
});

test("嵌套的框：里层框里的节点拖出去，两层框都包住它，别的节点不动", () => {
  const wf = { nodes: [{ id: "o", kind: "foreach", max_items: 5, body: { inputs: {}, outputs: {}, edges: [],
    nodes: [{ id: "i", kind: "foreach", max_items: 5, body: { inputs: {}, outputs: {}, edges: [],
      nodes: [{ id: "x", kind: "module", module: "x" }, { id: "y", kind: "module", module: "x" }], layout: { x: { x: 0, y: 0 }, y: { x: 300, y: 0 } } } },
    { id: "k", kind: "module", module: "x" }], layout: { i: { x: 0, y: 0 }, k: { x: 0, y: 400 } } } }], edges: [], layout: { o: { x: 0, y: 0 } } };
  const { nodes } = toFlow(wf, {});
  const x = nodes.find((n) => n.id === "o/i/x");
  const { before, after } = dragRoundTrip(wf, { "o/i/x": { x: x.position.x - 120, y: x.position.y } });
  assert.equal(after["o/i/x"].x, before["o/i/x"].x - 120);
  for (const id of ["o/i/y", "o/k"]) assert.deepEqual([after[id].x, after[id].y], [before[id].x, before[id].y], id);
  assert.ok(contains(after["o/i"], after["o/i/x"]) && contains(after.o, after["o/i"]));
});

test("旧数据里框内有负坐标：画出来正好包住；只拖其中一个，其他节点不动", () => {
  const wf = loopWf({ a: { x: -80, y: 0 }, b: { x: 200, y: 40 } });
  const { nodes } = toFlow(wf, {});
  const b = nodes.find((n) => n.id === "each/b");
  const { before, after } = dragRoundTrip(wf, { "each/b": { x: b.position.x + 30, y: b.position.y } });
  assert.equal(after["each/b"].x, before["each/b"].x + 30);
  assert.deepEqual([after["each/a"].x, after["each/a"].y], [before["each/a"].x, before["each/a"].y]);
  assert.ok(contains(after.each, after["each/a"]));
});

test("画图：没拉伸过的框画在 layout 位置加最小坐标处，正好包住框里节点", () => {
  const { nodes } = toFlow(loopWf({ a: { x: 200, y: 50 }, b: { x: 400, y: 150 } }), {});
  const abs = absolute(nodes);
  assert.equal(abs.each.x, abs["each/a"].x - PAD);
  assert.equal(abs.each.y, abs["each/a"].y - TOP);
  assert.equal(abs.each.x + abs.each.w, abs["each/b"].x + abs["each/b"].w + PAD);
  assert.equal(abs.each.y + abs.each.h, abs["each/b"].y + abs["each/b"].h + PAD);
});

// ---------- GPT 审查（重写后第 1 轮）的回归测试 ----------

test("拉伸过的外层框里，自动内层框拖出外层框左边：外层框只扩大一次，兄弟节点不跳", () => {
  const outer = box("o", 100, 0, 1000, 400, { type: "loop", data: { width: 1000, height: 400, manual: { w: 1000, h: 400 } } });
  const inner = box("o/i", -72, TOP, 252, 150, { type: "loop", parentId: "o" });
  const deep = box("o/i/x", PAD, TOP, 196, 60, { parentId: "o/i" });
  const sib = box("o/s", 600, TOP, 196, 60, { parentId: "o" });
  const sizes = new Map();
  const moves = settle([outer, inner, deep, sib], ["o/i"], { sizesOut: sizes });
  assert.equal(moves.o.x, 100 - (PAD + 72));
  assert.deepEqual(sizes.get("o"), { w: 1000 + PAD + 72, h: 400 });
  assert.equal(moves.o.x + moves["o/s"].x, 100 + 600, "兄弟节点在屏幕上不动");
});

test("拉伸过的外层框里有旧负坐标的自动内层框：拖内层框里的节点，写回重画后别的节点不动", () => {
  const wf = { nodes: [{ id: "o", kind: "foreach", max_items: 5, body: { inputs: {}, outputs: {}, edges: [],
    nodes: [{ id: "i", kind: "foreach", max_items: 5, body: { inputs: {}, outputs: {}, edges: [],
      nodes: [{ id: "x", kind: "module", module: "x" }, { id: "y", kind: "module", module: "x" }], layout: { x: { x: -80, y: 0 }, y: { x: 300, y: 0 } } } },
    { id: "k", kind: "module", module: "x" }], layout: { i: { x: -50, y: 0 }, k: { x: 0, y: 400 } } } }], edges: [],
    layout: { o: { x: 0, y: 0, w: 1200, h: 800 } } };
  const { nodes } = toFlow(wf, {});
  const x = nodes.find((n) => n.id === "o/i/x");
  const { before, after } = dragRoundTrip(wf, { "o/i/x": { x: x.position.x + 30, y: x.position.y } });
  assert.equal(after["o/i/x"].x, before["o/i/x"].x + 30);
  for (const id of ["o/i/y", "o/k"]) assert.deepEqual([after[id].x, after[id].y], [before[id].x, before[id].y], id);
});

test("画图：框里节点全在负坐标时，框也正好包住，不留大片空白", () => {
  const { nodes } = toFlow(loopWf({ a: { x: -500, y: -500 } }), {});
  const loop = nodes.find((n) => n.id === "each");
  const a = nodes.find((n) => n.id === "each/a");
  assert.equal(loop.data.width, Math.max(196, rectOf(a).w + PAD * 2));
  assert.equal(loop.data.height, rectOf(a).h + PAD + TOP);
});

test("首次打开（根层没保存位置）：自动伸缩的框按自动排版的位置画，不压到别的节点", () => {
  const wf = { inputs: { a: { type: "any" } }, outputs: { b: { type: "any" } },
    nodes: [{ id: "each", kind: "foreach", max_items: 5, body: { inputs: { item: { type: "any" } }, outputs: { result: { type: "any" } },
      nodes: [], edges: [{ from: "$in.item", to: "$out.result" }], layout: { $in: { x: 300, y: 0 }, $out: { x: 500, y: 0 } } } }],
    edges: [{ from: "$in.a", to: "each.items" }, { from: "each.results", to: "$out.b" }] };
  const abs = absolute(toFlow(wf, {}).nodes);
  const top = ["$in", "each", "$out"].map((id) => abs[id]);
  for (const a of top) for (const b of top) if (a !== b) assert.ok(!overlaps(a, b, 0), JSON.stringify([a, b]));
});
