// 输入契约的表单一侧（效率审查第五节 B）：表单发出去的值与 tests/test_contracts.py 里引擎接受的值一致
import test from "node:test";
import assert from "node:assert/strict";
import { initialValues, collectInputs } from "../src/form.js";

// 与 tests/test_contracts.py 的 DECL 相同
const DECL = {
  r: { type: "integer" },
  i: { type: "integer", default: 5 },
  b: { type: "boolean", required: false },
  s: { type: "string", default: "默认" },
  a: { type: "any", required: false },
};

test("不改表单直接运行：必填给示例，有默认值的发默认值，可选的不发", () => {
  const { values, errors } = collectInputs(DECL, initialValues(DECL));
  assert.deepEqual(errors, {});
  assert.deepEqual(values, { r: 85, i: 5, s: "默认" });
});

test("false、0、null 原样发送，不当作没填", () => {
  const raw = { ...initialValues(DECL), r: "0", i: "0", b: false, a: "null" };
  assert.deepEqual(collectInputs(DECL, raw).values, { r: 0, i: 0, b: false, s: "默认", a: null });
  assert.deepEqual(collectInputs(DECL, { ...raw, a: "0" }).values.a, 0);
  assert.deepEqual(collectInputs(DECL, { ...raw, a: '""' }).values.a, "");
});

test("可选文字留空表示不提供（后端补默认值）；必填留空报错", () => {
  const raw = { ...initialValues(DECL), s: "", a: "" };
  const { values } = collectInputs(DECL, raw);
  assert.equal("s" in values, false);
  assert.equal("a" in values, false);
  assert.ok(collectInputs(DECL, { ...raw, r: "" }).errors.r);
  assert.ok(collectInputs(DECL, { ...raw, r: "1.5" }).errors.r);
});
