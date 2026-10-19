// 前端回归测试（代码审查 F01、F04、F07、F16、A04）：node --test tests
import test from "node:test";
import assert from "node:assert/strict";
import { toFlow } from "../src/graph.js";
import { drawable, latest } from "../src/flow.js";
import { initialValues, collectInputs } from "../src/form.js";

const modules = {
  "text.format@0": { inputs: { template: { type: "string" }, value: { type: "any", required: false } }, outputs: { text: { type: "string" } } },
};

test("F01：缺 edges 或连到不存在节点的草稿不会让画图抛错", () => {
  const noEdges = { id: "x", inputs: {}, outputs: {}, nodes: [{ id: "f", kind: "module", module: "text.format@0" }] };
  assert.doesNotThrow(() => toFlow(noEdges, modules));
  const dangling = { ...noEdges, edges: [{ from: "ghost.text", to: "f.value" }, { from: "f.text" }, null] };
  assert.equal(toFlow(dangling, modules).edges.length, 0);
  assert.doesNotThrow(() => toFlow({ id: "y", nodes: "坏的", edges: {} }, modules));
  assert.deepEqual(toFlow(null, modules), { nodes: [], edges: [] });
});

test("F01：只有通过结构校验的草稿才交给画布", () => {
  const wf = { nodes: [{ id: "f" }] };
  assert.equal(drawable({ type: "validated", errors: ["缺 edges"], workflow: wf }), false);
  assert.equal(drawable({ type: "validated", errors: [], workflow: wf }), true);
  assert.equal(drawable({ type: "done", ok: false, valid: false, workflow: wf }), false);
  assert.equal(drawable({ type: "done", ok: false, valid: true, workflow: wf }), true);
});

test("F04：旧请求的令牌在新请求开始后失效", () => {
  const seq = latest();
  const a = seq.next();
  const b = seq.next();
  assert.equal(seq.isCurrent(a), false);
  assert.equal(seq.isCurrent(b), true);
});

test("F07：表单使用声明的默认值，可选输入留空时不发送", () => {
  const inputs = {
    score: { type: "integer", required: false, default: 60 },
    flag: { type: "boolean", required: false },
    note: { type: "string", required: false },
    url: { type: "string" },
  };
  const init = initialValues(inputs);
  assert.equal(init.score, "60");
  assert.equal(init.flag, null);
  const { values, errors } = collectInputs(inputs, init);
  assert.deepEqual(errors, {});
  assert.deepEqual(values, { score: 60, url: "https://example.com" });
});

test("F16：引用文件的子流程按服务端给的端口画出插孔和连线", () => {
  const wf = { id: "o", inputs: { x: { type: "string" } }, outputs: { y: { type: "string" } },
    nodes: [{ id: "sub", kind: "subflow", ref: "hello.workflow.json" }],
    edges: [{ from: "$in.x", to: "sub.x" }, { from: "sub.y", to: "$out.y" }] };
  const ports = { "hello.workflow.json": { inputs: { x: { type: "string" } }, outputs: { y: { type: "string" } } } };
  const sub = toFlow(wf, modules, ports).nodes.find((n) => n.id === "sub");
  assert.deepEqual(sub.data.ins.map((p) => p.name), ["x"]);
  assert.deepEqual(sub.data.outs.map((p) => p.name), ["y"]);
});

test("A04：保存的 layout 坐标优先于自动排版", () => {
  const wf = { id: "l", inputs: {}, outputs: {}, layout: { f: { x: 1000, y: 2000 } },
    nodes: [{ id: "f", kind: "module", module: "text.format@0", config: { template: "hi" } }], edges: [] };
  assert.deepEqual(toFlow(wf, modules).nodes.find((n) => n.id === "f").position, { x: 1000, y: 2000 });
});
