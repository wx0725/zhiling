// 界面精细化：初始视野与类型名称
import test from "node:test";
import assert from "node:assert/strict";
import { initialViewport, READABLE_ZOOM } from "../src/viewport.js";
import { typeLabel } from "../src/labels.js";

test("放得下的图交给 fitView 居中；放不下时用可读的缩放从左边开始", () => {
  assert.equal(initialViewport({ x: 0, y: 0, width: 600, height: 300 }, 1000, 700), null);
  const v = initialViewport({ x: 100, y: 50, width: 3000, height: 400 }, 1000, 700);
  assert.equal(v.zoom, READABLE_ZOOM);
  assert.equal(v.x, 32 - 100 * READABLE_ZOOM); // 图的左边缘贴着画布左边
  assert.equal(v.y, 350 - (50 + 200) * READABLE_ZOOM); // 垂直居中
});

test("类型显示成使用者看得懂的名字", () => {
  assert.equal(typeLabel("string"), "文字");
  assert.equal(typeLabel("integer"), "整数");
  assert.equal(typeLabel("list<string>"), "文字列表");
  assert.equal(typeLabel("list<list<integer>>"), "整数列表列表");
  assert.equal(typeLabel("any"), "任意");
  assert.equal(typeLabel(undefined), "任意");
  assert.equal(typeLabel("custom"), "custom");
});

import { wholeGraphVisible } from "../src/viewport.js";

test("整张图在视野里时判断为可见，任何一边超出就不是", () => {
  const b = { x: 0, y: 0, width: 500, height: 200 };
  assert.equal(wholeGraphVisible(b, [10, 10, 1], 800, 600), true);
  assert.equal(wholeGraphVisible(b, [10, 10, 2], 800, 600), false); // 放大后右边超出
  assert.equal(wholeGraphVisible(b, [-50, 10, 1], 800, 600), false); // 向左平移出界
  assert.equal(wholeGraphVisible(null, [0, 0, 1], 800, 600), true);
});
