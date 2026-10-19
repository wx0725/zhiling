// 生成过程：从问题文字找到图上的节点
import test from "node:test";
import assert from "node:assert/strict";
import { canvasIds, issueTargets } from "../src/flow.js";

const wf = {
  id: "batch",
  nodes: [
    { id: "each", kind: "foreach", body: { nodes: [{ id: "fetch" }, { id: "summarize" }, { id: "ok" }] } },
    { id: "join", kind: "module" },
    { id: "ok", kind: "module" },
  ],
};

test("画布节点 id 包括循环体里的节点", () => {
  assert.deepEqual(canvasIds(wf), ["each", "each/fetch", "each/summarize", "each/ok", "join", "ok"]);
});

test("审查意见、运行出错、结构校验三种写法都能找到节点", () => {
  assert.deepEqual(issueTargets("节点 join：拼接后没有再总结；改法：加一个思考节点", wf), ["join"]);
  assert.deepEqual(issueTargets("节点 summarize：收到的是网址而不是网页正文", wf), ["each/summarize"]); // 只在循环里有，唯一
  assert.deepEqual(issueTargets('示例输入 {"urls": []} 运行出错：节点 batch.each[1].fetch：network：连不上', wf), ["each/fetch"]);
  assert.deepEqual(issueTargets("batch > each.body：连线 4（fetch.text → summarize.text）：类型不兼容", wf).sort(), ["each/fetch", "each/summarize"]);
});

test("有歧义或不存在的名字不标", () => {
  assert.deepEqual(issueTargets("节点 ok：条件写反了", wf), ["ok"]); // 顶层就有 ok，精确匹配优先
  assert.deepEqual(issueTargets("节点 ghost：不存在", wf), []);
  assert.deepEqual(issueTargets("工作流输出 y 没有来源", wf), []);
  assert.deepEqual(issueTargets("连线 1（$in.x → join.items）", wf), ["join"]);
});

import { issueText } from "../src/flow.js";

test("问题文字去掉程序位置前缀", () => {
  assert.equal(issueText("fetch_and_summarize_urls > loop.body：连线 7：summarize.text 已经有来源"), "循环 loop 里：连线 7：summarize.text 已经有来源");
  assert.equal(issueText("wf：工作流输出 y 没有来源"), "工作流输出 y 没有来源");
  assert.equal(issueText("wf > a.body > b.body：节点 x：缺输入"), "循环 a 里、循环 b 里：节点 x：缺输入");
  assert.equal(issueText("节点 fmt：句子里没有写单位"), "节点 fmt：句子里没有写单位");
  assert.equal(issueText("结构错误 nodes/0：缺 id"), "结构错误 nodes/0：缺 id");
  assert.equal(issueText("wf > sub（child.workflow.json）：无法读取"), "wf > sub（child.workflow.json）：无法读取");
});

import { guideSteps, cardToRequirement, mergeAnswers } from "../src/flow.js";

test("从空白开始的清单：随程序状态逐步打勾", () => {
  const blank = { inputs: { input: { type: "string" } }, outputs: { output: { type: "string" } }, nodes: [], edges: [] };
  let g = guideSteps(blank);
  assert.deepEqual(g.steps.map((s) => s.done), [true, false, false, false, false]);
  assert.equal(g.current, 1);
  const withNode = { ...blank, nodes: [{ id: "format", kind: "module", module: "text.format@0" }] };
  assert.equal(guideSteps(withNode).current, 2);
  const wired = { ...withNode, edges: [{ from: "$in.input", to: "format.value" }, { from: "format.text", to: "$out.output" }] };
  assert.equal(guideSteps(wired, ["节点 format：必需输入 template 没有来源"]).current, 3);
  assert.equal(guideSteps(wired, []).current, 4);
  assert.equal(guideSteps(wired, [], true).current, -1);
  assert.equal(guideSteps({ ...blank, outputs: {} }).steps[0].done, false);
  assert.doesNotThrow(() => guideSteps(null));
});

test("理清需求：改过的卡片写成一段需求；回答并入之前的问答", () => {
  assert.equal(cardToRequirement({ given: ["一组网址"], wanted: ["每个一句总结"], steps: ["抓取", "总结"], cases: [" ", "打不开说明原因"] }),
    "给它：一组网址。要它产出：每个一句总结。做法：抓取，然后总结。特殊情况：打不开说明原因。");
  assert.equal(cardToRequirement({}), "");
  const qs = [{ ask: "分条还是一段？", options: ["分条", "一段"] }, { ask: "打不开怎么办？", options: ["跳过", "说明原因"] }];
  const prev = [{ ask: "分条还是一段？", answer: "一段" }, { ask: "要英文吗？", answer: "不要" }];
  assert.deepEqual(mergeAnswers(prev, qs, { 0: "分条", 1: " " }), [{ ask: "要英文吗？", answer: "不要" }, { ask: "分条还是一段？", answer: "分条" }]);
});
