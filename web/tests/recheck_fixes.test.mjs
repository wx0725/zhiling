// 复检报告（2026-10-17）R01、R02、R03 的前端回归测试
import test from "node:test";
import assert from "node:assert/strict";
import { toFlow } from "../src/graph.js";
import { initialValues, collectInputs } from "../src/form.js";

const modules = {
  "text.format@0": { inputs: { template: { type: "string" }, value: { type: "any", required: false } }, outputs: { text: { type: "string" } } },
};

test("R01：节点名叫 constructor 等原型属性名时照常画图", () => {
  for (const id of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
    const wf = { id: "c", inputs: { x: { type: "string" } }, outputs: { y: { type: "string" } },
      nodes: [{ id, kind: "module", module: "text.format@0", config: { template: "hello {value}" } }],
      edges: [{ from: "$in.x", to: `${id}.value` }, { from: `${id}.text`, to: "$out.y" }] };
    const { nodes, edges } = toFlow(wf, modules);
    assert.ok(nodes.some((n) => n.id === id), id);
    assert.equal(edges.length, 2, id);
  }
});

test("R02：引用子流程的端口声明损坏时不抛错", () => {
  const wf = { id: "o", inputs: {}, outputs: {}, nodes: [{ id: "s", kind: "subflow", ref: "child.workflow.json" }], edges: [] };
  for (const bad of [{ inputs: { x: null }, outputs: {} }, { inputs: [], outputs: "x" }, null]) {
    assert.doesNotThrow(() => toFlow(wf, modules, { "child.workflow.json": bad }));
  }
  assert.doesNotThrow(() => toFlow({ ...wf, inputs: { a: null }, outputs: { b: 3 } }, modules));
});

test("R03：any 类型的默认值原样往返，不被改成别的值或报错", () => {
  for (const d of ["hello", "123", "true", "null", 'say "hi"', 123, true, null, [1, "a"], { k: "v" }]) {
    const inputs = { v: { type: "any", required: false, default: d } };
    const { values, errors } = collectInputs(inputs, initialValues(inputs));
    assert.deepEqual(errors, {}, JSON.stringify(d));
    if (d === null) assert.equal("v" in values, false); // 默认值为 null：不发送，由后端补
    else assert.deepEqual(values, { v: d }, JSON.stringify(d));
  }
  const s = { t: { type: "string", default: "123" } };
  assert.deepEqual(collectInputs(s, initialValues(s)).values, { t: "123" });
});
