// 画布编辑的纯函数：改完的 JSON 就是新的程序
import test from "node:test";
import assert from "node:assert/strict";
import { addNode, arrangeBody, connect, freezeLayout, moveNode, removeEdge, removeNode, setConfig, setLabel, setMaxItems } from "../src/edit.js";
import { toFlow } from "../src/graph.js";

const modules = {
  "text.format@0": { name: "转成文字", category: "处理", inputs: { template: { type: "string" }, value: { type: "any", required: false } }, outputs: { text: { type: "string" } } },
  "logic.compare@0": { name: "比较", category: "逻辑", inputs: { a: { type: "any" }, op: { type: "string" }, b: { type: "any", required: false } }, outputs: { result: { type: "boolean" } } },
};

const base = () => ({
  spec: "0.2", id: "w", name: "测试", intent: "",
  inputs: { x: { type: "integer" } }, outputs: { y: { type: "string" } },
  nodes: [
    { id: "f", kind: "module", module: "text.format@0", config: { template: "数是 {value}" } },
    { id: "each", kind: "foreach", max_items: 5, body: {
      inputs: { item: { type: "any" } }, outputs: { result: { type: "any" } },
      nodes: [{ id: "g", kind: "module", module: "text.format@0", config: { template: "{value}" } }],
      edges: [{ from: "$in.item", to: "g.value" }, { from: "g.text", to: "$out.result" }] } },
  ],
  edges: [{ from: "$in.x", to: "f.value" }, { from: "f.text", to: "$out.y" }],
});

test("不改动传入的对象", () => {
  const wf = base();
  const snapshot = JSON.stringify(wf);
  setConfig(wf, "f", "template", "新");
  removeNode(wf, "f");
  connect(wf, { source: "$in", sourceHandle: "out:x", target: "f", targetHandle: "in:template" });
  assert.equal(JSON.stringify(wf), snapshot);
});

test("改固定参数；清空就删掉这一项，config 空了整个删掉", () => {
  let wf = setConfig(base(), "f", "template", "值：{value}");
  assert.equal(wf.nodes[0].config.template, "值：{value}");
  wf = setConfig(wf, "f", "template", undefined);
  assert.equal("config" in wf.nodes[0], false);
  wf = setConfig(base(), "each/g", "template", "第 {value} 项");
  assert.equal(wf.nodes[1].body.nodes[0].config.template, "第 {value} 项");
});

test("连线：替换同一输入的旧来源，并去掉该输入的固定值", () => {
  const { wf } = connect(base(), { source: "$in", sourceHandle: "out:x", target: "f", targetHandle: "in:template" });
  assert.ok(wf.edges.some((e) => e.from === "$in.x" && e.to === "f.template"));
  assert.equal(wf.nodes[0].config, undefined); // template 改为接线，固定值去掉
  const r = connect(wf, { source: "f", sourceHandle: "out:text", target: "$out", targetHandle: "in:y" });
  assert.equal(r.wf.edges.filter((e) => e.to === "$out.y").length, 1);
});

test("连线：循环体里的节点之间可以连；跨层、连自己、普通节点拉控制线都拒绝并说明原因", () => {
  const ok = connect(base(), { source: "each/$in", sourceHandle: "out:item", target: "each/g", targetHandle: "in:template" });
  assert.ok(ok.wf.nodes[1].body.edges.some((e) => e.from === "$in.item" && e.to === "g.template"));
  assert.match(connect(base(), { source: "f", sourceHandle: "out:text", target: "each/g", targetHandle: "in:value" }).error, /同一层/);
  assert.match(connect(base(), { source: "f", sourceHandle: "out:text", target: "f", targetHandle: "in:value" }).error, /自己/);
  assert.match(connect(base(), { source: "f", sourceHandle: "out:text", target: "each", targetHandle: "ctrl" }).error, /控制线/);
});

test("条件分支的 then 可以拉控制线到节点顶部", () => {
  const { wf: w1, id } = addNode(base(), [], { kind: "if" }, { x: 10, y: 10 });
  const r = connect(w1, { source: id, sourceHandle: "out:then", target: "f", targetHandle: "ctrl" });
  assert.ok(r.wf.edges.some((e) => e.from === `${id}.then` && e.to === "f"));
});

test("删线、删节点（连着的线一起删）、端点不能删", () => {
  let wf = removeEdge(base(), { scope: [], from: "$in.x", to: "f.value" });
  assert.equal(wf.edges.length, 1);
  wf = removeNode(base(), "f");
  assert.deepEqual(wf.nodes.map((n) => n.id), ["each"]);
  assert.deepEqual(wf.edges, []);
  wf = removeNode(base(), "each/g");
  assert.deepEqual(wf.nodes[1].body.edges, []);
  assert.equal(removeNode(base(), "$in").nodes.length, 2);
});

test("加节点：id 不重复；循环带好 item/result；位置写进 layout；画得出来", () => {
  let r = addNode(base(), [], { kind: "module", module: "text.format@0" }, { x: 100.4, y: 50 });
  assert.equal(r.id, "format");
  r = addNode(r.wf, [], { kind: "module", module: "text.format@0" }, { x: 0, y: 0 });
  assert.equal(r.id, "format_2");
  assert.deepEqual(r.wf.layout.format, { x: 100, y: 50 });
  const loop = addNode(r.wf, [], { kind: "foreach" }, { x: 0, y: 0 });
  const body = loop.wf.nodes.find((n) => n.id === "each_2").body;
  assert.deepEqual(Object.keys(body.inputs), ["item"]);
  const inner = addNode(base(), ["each"], { kind: "merge" }, { x: 0, y: 200 });
  assert.equal(inner.id, "each/merge");
  assert.doesNotThrow(() => toFlow(loop.wf, modules));
  assert.ok(toFlow(inner.wf, modules).nodes.some((n) => n.id === "each/merge"));
});

test("拖动和冻结位置：之后重新画图位置不变", () => {
  const { nodes } = toFlow(base(), modules);
  const positions = Object.fromEntries(nodes.filter((n) => !n.parentId).map((n) => [n.id, n.position]));
  const frozen = freezeLayout(base(), positions);
  const again = toFlow(frozen, modules).nodes.filter((n) => !n.parentId);
  for (const n of again) assert.deepEqual(n.position, { x: Math.round(positions[n.id].x), y: Math.round(positions[n.id].y) });
  const moved = moveNode(frozen, "f", { x: 999, y: 7 });
  assert.deepEqual(toFlow(moved, modules).nodes.find((n) => n.id === "f").position, { x: 999, y: 7 });
});

test("改名称、改循环上限", () => {
  assert.equal(setLabel(base(), "f", " 写一句 ").nodes[0].label, "写一句");
  assert.equal("label" in setLabel(setLabel(base(), "f", "x"), "f", "").nodes[0], false);
  assert.equal(setMaxItems(base(), "each", 50).nodes[1].max_items, 50);
});

import { addPort, removePort, renamePort, setMeta, setPortFields, setPortType } from "../src/edit.js";

test("输入输出：加、改名（连线跟着改）、改类型（去掉旧默认值）、改说明和默认值、删（连线一起删）", () => {
  let { wf, name } = addPort(base(), "inputs");
  assert.equal(name, "input");
  assert.equal(addPort(wf, "inputs").name, "input_2");
  let r = renamePort(base(), "inputs", "x", "num");
  assert.deepEqual(Object.keys(r.wf.inputs), ["num"]);
  assert.ok(r.wf.edges.some((e) => e.from === "$in.num" && e.to === "f.value"));
  r = renamePort(base(), "outputs", "y", "sentence");
  assert.ok(r.wf.edges.some((e) => e.to === "$out.sentence"));
  assert.match(renamePort(base(), "inputs", "x", "Bad Name").error, /小写/);
  assert.match(renamePort(addPort(base(), "inputs").wf, "inputs", "input", "x").error, /已经有/);
  wf = setPortFields(base(), "inputs", "x", { description: "分数", default: 60 });
  assert.deepEqual(wf.inputs.x, { type: "integer", description: "分数", default: 60 });
  wf = setPortType(wf, "inputs", "x", "string");
  assert.deepEqual(wf.inputs.x, { type: "string", description: "分数" });
  wf = setPortFields(wf, "inputs", "x", { description: undefined });
  assert.deepEqual(wf.inputs.x, { type: "string" });
  wf = removePort(base(), "inputs", "x");
  assert.deepEqual(wf.inputs, {});
  assert.ok(!wf.edges.some((e) => e.from === "$in.x"));
  wf = removePort(base(), "outputs", "y");
  assert.ok(!wf.edges.some((e) => e.to === "$out.y"));
});

test("改工作流名称和说明", () => {
  const wf = setMeta(base(), { name: "新名字", intent: "做点什么" });
  assert.equal(wf.name, "新名字");
  assert.equal(wf.intent, "做点什么");
  assert.equal(setMeta(base(), { intent: "只改说明" }).name, "测试");
});

test("整理循环框里的节点：去掉框里这一层的位置，框里的框记着的大小留着", () => {
  const wf = base();
  wf.nodes[1].body.layout = { g: { x: 300, y: 40 }, $in: { x: 0, y: 0 } };
  const out = arrangeBody(wf, "each");
  assert.equal("layout" in out.nodes[1].body, false);
  assert.deepEqual(wf.nodes[1].body.layout.g, { x: 300, y: 40 }, "不改原来的");
  wf.nodes[1].body.nodes.push({ id: "box2", kind: "foreach", max_items: 3, body: { inputs: {}, outputs: {}, nodes: [], edges: [] } });
  wf.nodes[1].body.layout = { box2: { x: 1, y: 2, w: 300, h: 150 }, gone: { x: 10, y: 20, w: 400, h: 200 } };
  assert.deepEqual(arrangeBody(wf, "each").nodes[1].body.layout, { box2: { w: 300, h: 150 } }, "已经不在的节点的旧记录不留");
  wf.nodes[1].body.nodes.pop();
  wf.nodes[1].body.layout = { g: { x: 300, y: 40 } };
  assert.equal("layout" in arrangeBody(wf, "each").nodes[1].body, false);
  const { nodes } = toFlow(arrangeBody(wf, "each"), modules);
  assert.ok(nodes.find((n) => n.id === "each/g"), "整理后照样画得出来");
});
