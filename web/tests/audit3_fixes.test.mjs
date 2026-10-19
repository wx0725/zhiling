// 第三轮复检报告（2026-10-17）N02、N08 的前端单元测试；N01、N02、N07、N08 的浏览器端到端测试见 e2e.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { drawable, inspectWorkflow, isRefusal } from "../src/flow.js";
import { initialValues, collectInputs } from "../src/form.js";
import { toFlow } from "../src/graph.js";

test("N02：主工作流输入声明损坏时整理成安全形状，并列出问题", () => {
  const cases = [
    [{ id: "a", name: "a", inputs: { n: null }, outputs: {}, nodes: [], edges: [] }, "inputs.n"],
    [{ id: "a", name: "a", inputs: { n: 3 }, outputs: {}, nodes: [], edges: [] }, "inputs.n"],
    [{ id: "a", name: "a", inputs: { n: {} }, outputs: {}, nodes: [], edges: [] }, "inputs.n"],
    [{ id: "a", name: "a", inputs: null, outputs: {}, nodes: [], edges: [] }, "inputs"],
    [{ id: "a", name: "a", inputs: [1], outputs: {}, nodes: [], edges: [] }, "inputs"],
    [{ id: "a", name: "a", outputs: {}, nodes: [], edges: [] }, "inputs"],
    [null, "不是一个工作流"],
    [{ id: 1, name: { x: 1 }, inputs: {}, outputs: {}, nodes: "x", edges: [] }, "nodes"],
  ];
  for (const [wf, hint] of cases) {
    const { wf: clean, problems } = inspectWorkflow(wf);
    assert.ok(problems.some((p) => p.includes(hint)), `${JSON.stringify(wf)} → ${problems}`);
    assert.equal(typeof clean.name, "string");
    assert.doesNotThrow(() => collectInputs(clean.inputs, initialValues(clean.inputs)));
    assert.doesNotThrow(() => toFlow(clean, {}));
  }
  const ok = { id: "a", name: "a", intent: "", inputs: { n: { type: "integer", default: 42 } }, outputs: {}, nodes: [], edges: [] };
  assert.deepEqual(inspectWorkflow(ok), { wf: ok, problems: [] });
});

test("N08：零节点直通的合法工作流可以显示，“无法实现”不显示", () => {
  const identity = { id: "id", name: "直通", inputs: { x: { type: "integer" } }, outputs: { y: { type: "integer" } },
    nodes: [], edges: [{ from: "$in.x", to: "$out.y" }] };
  assert.equal(drawable({ type: "validated", errors: [], workflow: identity }), true);
  assert.equal(drawable({ type: "done", ok: true, valid: true, workflow: identity }), true);
  assert.equal(toFlow(identity, {}).edges.length, 1);
  const refusal = { id: "not_feasible", name: "无法实现：发邮件", inputs: {}, outputs: {}, nodes: [], edges: [] };
  assert.equal(isRefusal(refusal), true);
  assert.equal(drawable({ type: "done", ok: true, valid: true, workflow: refusal }), false);
  assert.equal(drawable({ type: "done", ok: false, valid: false, workflow: {} }), false);
});
