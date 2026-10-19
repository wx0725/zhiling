// 扩充模块库：运行前找出会写文件的模块
import test from "node:test";
import assert from "node:assert/strict";
import { writeModules, unconfirmedModule } from "../src/graph.js";

const modules = {
  "file.write@0": { side_effects: "write" },
  "file.read@0": { side_effects: "read" },
  "text.join@0": { side_effects: "none" },
};

test("找出图里（含循环体）会写文件的模块，去重排序", () => {
  const wf = {
    nodes: [
      { id: "r", kind: "module", module: "file.read@0" },
      { id: "each", kind: "foreach", body: { nodes: [{ id: "w", kind: "module", module: "file.write@0" }] } },
      { id: "w2", kind: "module", module: "file.write@0" },
    ],
  };
  assert.deepEqual(writeModules(wf, modules), ["file.write@0"]);
});

test("没有写模块、草稿缺字段、不认识的模块都不算", () => {
  assert.deepEqual(writeModules({ nodes: [{ kind: "module", module: "text.join@0" }] }, modules), []);
  assert.deepEqual(writeModules({ nodes: [null, { kind: "module", module: "constructor" }, { kind: "foreach", body: 3 }] }, modules), []);
  assert.deepEqual(writeModules({}, modules), []);
});

test("从服务端的 not_confirmed 报错里取出被拦下的写模块（引用的子流程里的写模块网页事先看不到）", () => {
  assert.equal(unconfirmedModule("模块 file.write@0 会写文件或产生外部影响，未获得确认"), "file.write@0");
  assert.equal(unconfirmedModule("别的错误"), null);
  assert.equal(unconfirmedModule(undefined), null);
});
