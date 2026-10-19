// 浏览器端到端测试（第三轮复检 N01、N02、N07、N08；效率审查 Q01、Q02 及测试缺口：真实模块运行、保存后重开再运行）：启动隔离测试服务和无界面 Edge，按真实点击操作检查页面。
// 用法：先 npx vite build，再 npm run e2e。
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";

const ROOT = resolve(fileURLToPath(import.meta.url), "../../..");
const PORT = 18900 + Math.floor(Math.random() * 90);
const BASE = `http://127.0.0.1:${PORT}`;
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const server = spawn(join(ROOT, ".venv/Scripts/python.exe"), ["-B", "-m", "tests.e2e_server", String(PORT)], { cwd: ROOT, stdio: "inherit" });
const cdpPort = 9800 + Math.floor(Math.random() * 100);
const profile = mkdtempSync(join(tmpdir(), "wf-e2e-edge-"));
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${cdpPort}`,
  `--user-data-dir=${profile}`, "--window-size=1440,900", "about:blank"], { stdio: "ignore" });

async function until(fn, what, ms = 8000) {
  for (let t = 0; t < ms; t += 100) {
    try { if (await fn()) return; } catch {}
    await sleep(100);
  }
  throw new Error(`等待超时：${what}`);
}

let failures = 0;
try {
  await until(async () => (await fetch(`${BASE}/api/workflows`)).ok, "测试服务启动", 20000);
  let target;
  await until(async () => (target = (await (await fetch(`http://127.0.0.1:${cdpPort}/json`)).json()).find((t) => t.type === "page")), "Edge 启动");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let seq = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } });
  const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "页面脚本出错");
    return r.result?.result?.value;
  };
  const size = (width, height) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  await size(1440, 900); // 命令行的 --window-size 在无界面模式下不生效，按桌面尺寸测试
  const go = async (path) => { await send("Page.navigate", { url: BASE + path }); await sleep(300); await until(() => js(`!!document.querySelector(".side .wf-item")`), "页面加载"); };
  const click = (text, sel = "button, [role=tab]") => js(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((b) => b.textContent.trim().includes(${JSON.stringify(text)})); if (!el) throw new Error("找不到 " + ${JSON.stringify(text)}); el.click(); return true; })()`);
  const type = (sel, v) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set; set.call(el, ${JSON.stringify(v)}); el.dispatchEvent(new Event("input", { bubbles: true })); return true; })()`);
  const val = (sel) => js(`document.querySelector(${JSON.stringify(sel)})?.value`);
  const title = () => js(`document.querySelector(".canvas h1")?.textContent`);
  // 真实的鼠标操作（拖线、点连线）：按元素中心点坐标发送鼠标事件
  const center = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect(); return r && { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  const mouse = (type, { x, y }, buttons = 1) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons, clickCount: 1 });
  const drag = async (fromSel, toSel) => {
    const a = await center(fromSel);
    const b = await center(toSel);
    if (!a || !b) throw new Error(`拖线找不到：${a ? toSel : fromSel}`);
    await mouse("mouseMoved", a, 0);
    await sleep(50);
    await mouse("mousePressed", a);
    let line = false;
    for (let i = 1; i <= 10; i++) {
      await mouse("mouseMoved", { x: a.x + ((b.x - a.x) * i) / 10, y: a.y + ((b.y - a.y) * i) / 10 });
      await sleep(30); // 给画布一帧时间更新连线状态（真实拖动也是逐帧的）
      if (i === 5) line = await js(`!!document.querySelector(".react-flow__connection")`);
    }
    await sleep(100);
    (drag.log ||= []).push({ line, at: await js(`(() => { const e = document.elementFromPoint(${a.x}, ${a.y}); return e && (e.className?.baseVal ?? e.className) + " " + (e.dataset?.handleid || ""); })()`), b: await js(`(() => { const e = document.elementFromPoint(${b.x}, ${b.y}); return e && (e.className?.baseVal ?? e.className) + " " + (e.dataset?.handleid || ""); })()`) });
    await mouse("mouseReleased", b, 0);
    await sleep(300);
  };
  const key = async (k, code, modifiers = 0) => {
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, modifiers, windowsVirtualKeyCode: { Delete: 46, z: 90 }[k] || 0 });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, modifiers });
  };
  const handle = (node, h) => `.react-flow__node[data-id="${node}"] .react-flow__handle[data-handleid="${h}"]`;
  const program = async () => { await click("程序", "[role=tab]"); return JSON.parse(await js(`document.querySelector(".program .code").textContent`)); };
  // 点循环框的标题栏选中它，等框顶菜单出现
  const pickBox = async (id) => {
    const c = await center(`.react-flow__node[data-id="${id}"] .loop-head`);
    await mouse("mouseMoved", c, 0);
    await mouse("mousePressed", c);
    await mouse("mouseReleased", c, 0);
    await until(() => js(`!!document.querySelector(".box-menu .box-menu-row")`), "出现框顶菜单");
  };
  // 框顶菜单里的自动收缩开关：设成 on
  const autoShrink = async (on) => {
    await js(`(() => { const el = document.querySelector(".box-menu input[role=switch]"); if (el.checked !== ${on}) el.click(); return true; })()`);
    await sleep(400);
  };
  const setField = (sel, v) => js(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set; set.call(el, ${JSON.stringify(v)}); el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new FocusEvent("focusout", { bubbles: true })); return true; })()`);

  const cases = {
    async "N01：查看节点、切换面板后，填好的运行参数保持不变"() {
      await go("/?wf=saved/form42.workflow.json");
      await until(async () => (await val("#in-n")) === "42", "表单出现默认值 42");
      await type("#in-n", "99");
      await click("节点", "[role=tab]");
      await click("生成过程", "[role=tab]");
      await click("运行", "[role=tab]");
      assert.equal(await val("#in-n"), "99");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("99")`), "运行结果 99");
      await click("节点", "[role=tab]");
      await click("运行", "[role=tab]");
      assert.equal(await val("#in-n"), "99");
      assert.ok(await js(`document.querySelector(".result").innerText.includes("99")`));
      await click("快的B", ".wf-item");
      await until(async () => (await title()) === "快的B", "切换到 B");
      assert.equal(await val("#in-n"), "42"); // 真正换了工作流才重置
    },
    async "N07：先点的慢请求晚到，不会覆盖后打开的工作流"() {
      await go("/");
      await click("慢的A", ".wf-item");
      await click("快的B", ".wf-item");
      await until(async () => (await title()) === "快的B", "B 先显示");
      await sleep(3800); // A 的响应在 3 秒后才到
      assert.equal(await title(), "快的B");
      assert.equal(await val("#in-n"), "42");
    },
    async "N02：主工作流输入声明损坏时显示问题，页面不白屏，还能打开别的"() {
      await go("/?wf=saved/badroot.workflow.json");
      await until(async () => (await title()) === "输入声明损坏", "打开坏工作流");
      assert.ok(await js(`document.body.innerText.includes("inputs.n 的声明不合法")`));
      await click("表单保持", ".wf-item");
      await until(async () => (await title()) === "表单保持", "还能打开正常工作流");
    },
    async "N08：生成出零节点直通的合法工作流，画在画布上并可以保存"() {
      await go("/");
      await type("#req", "把输入的整数原样输出");
      await click("生成工作流");
      await until(() => js(`document.body.innerText.includes("已显示在画布上")`), "生成完成");
      assert.equal(await title(), "直通");
      assert.ok(await js(`[...document.querySelectorAll(".canvas-actions button")].some((b) => b.textContent.includes("保存"))`));
      await until(() => js(`document.querySelectorAll(".react-flow__edge").length === 1`), "画出一条直通连线");
    },
    async "保存后重新打开并再次运行"() {
      await go("/");
      await type("#req", "把输入的整数原样输出");
      await click("生成工作流");
      await until(() => js(`document.body.innerText.includes("已显示在画布上")`), "生成完成");
      await click("保存", ".canvas-actions button");
      await until(() => js(`document.body.innerText.includes("已保存")`), "保存成功");
      const saved = await (await fetch(`${BASE}/api/workflows/saved/identity.workflow.json`)).json();
      assert.equal(saved.id, "identity");
      await click("表单保持", ".wf-item");
      await until(async () => (await title()) === "表单保持", "先切走");
      await click("直通", ".wf-item");
      await until(async () => (await title()) === "直通", "重新打开保存的工作流");
      await type("#in-x", "5");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("完成")`), "运行完成");
      assert.ok(await js(`document.querySelector(".result").innerText.includes("5")`));
    },
    async "真实模块：循环里比较、分支、合并，运行结果和节点状态正确"() {
      await go("/?wf=saved/grade.workflow.json");
      await until(async () => (await title()) === "逐个打分", "打开");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("完成")`), "运行完成", 30000);
      assert.ok(await js(`document.querySelector(".result").innerText.includes("及格 90；不及格 30")`));
      // 两项里各有一项走了不同分支：pass、fail 节点都应汇总为“完成”，并显示 1/2 这样的计数
      const counts = await js(`Object.fromEntries([...document.querySelectorAll(".react-flow__node")].map((n) => [n.dataset.id, n.querySelector(".node-counts")?.textContent || ""]))`);
      assert.equal(counts["each/cmp"], "2 完成");
      assert.equal(counts["each/pass"], "1 完成 / 1 跳过");
      assert.equal(counts["each/fail"], "1 完成 / 1 跳过");
      assert.equal(counts["each/m"], "2 完成");
    },
    async "写文件：运行前在屏幕中央弹出确认框，不同意就不运行，同意后才写"() {
      await go("/?wf=saved/save_note.workflow.json");
      await until(async () => (await title()) === "保存笔记", "打开");
      const target = (await (await fetch(`${BASE}/api/workflows/saved/save_note.workflow.json`)).json()).nodes[0].config.path;
      await click("运行", "form.run button");
      await until(() => js(`!!document.querySelector(".dialog")`), "弹出确认框");
      const text = await js(`document.querySelector(".dialog").innerText`);
      assert.ok(text.includes("改动你电脑上的文件") && text.includes("file.write@0"), text);
      await click("取消", ".dialog button");
      await sleep(300);
      assert.equal(await js(`!!document.querySelector(".dialog")`), false);
      assert.equal(existsSync(target), false); // 不同意：没有运行，也没写
      assert.equal(await js(`!!document.querySelector(".result")`), false);
      await click("运行", "form.run button");
      await until(() => js(`!!document.querySelector(".dialog")`), "再次弹出确认框");
      await click("确定运行", ".dialog button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("完成")`), "运行完成", 30000);
      assert.equal(readFileSync(target, "utf8"), "第一行");
    },
    async "写文件藏在引用的子流程里：运行前也能问到，同意后才写"() {
      await go("/?wf=saved/save_ref.workflow.json");
      await until(async () => (await title()) === "引用子流程保存", "打开");
      const inner = await (await fetch(`${BASE}/api/workflows/saved/save_inner.workflow.json`)).json();
      const target = inner.nodes[0].config.path;
      await click("运行", "form.run button");
      await until(() => js(`!!document.querySelector(".dialog")`), "运行前弹出确认框");
      assert.ok((await js(`document.querySelector(".dialog").innerText`)).includes("file.write@0"));
      assert.equal(await js(`!!document.querySelector(".result")`), false); // 还没开始运行
      await click("确定运行", ".dialog button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("完成")`), "运行完成", 30000);
      assert.equal(await js(`!!document.querySelector(".dialog")`), false); // 只问一次，不会执行到一半被拦下再问
      assert.equal(readFileSync(target, "utf8"), "来自子流程");
    },
    async "有改动没保存就切换工作流：屏幕中央弹出确认框，Esc 留下，确认后才离开"() {
      await go("/?wf=saved/edit_me.workflow.json");
      await until(async () => (await title()) === "拿来改的", "打开");
      await click("添加节点");
      await click("合并", ".menu button");
      await until(() => js(`document.body.innerText.includes("有改动")`), "出现改动");
      await click("直通", ".side .wf-item");
      await until(() => js(`!!document.querySelector(".dialog")`), "弹出确认框");
      const box = await js(`(() => { const r = document.querySelector(".dialog").getBoundingClientRect(); return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, w: innerWidth, h: innerHeight }; })()`);
      assert.ok(Math.abs(box.cx - box.w / 2) < 4 && Math.abs(box.cy - box.h / 2) < 4, `在屏幕中央 ${JSON.stringify(box)}`);
      await key("Escape", "Escape");
      await sleep(300);
      assert.equal(await js(`!!document.querySelector(".dialog")`), false);
      assert.equal(await title(), "拿来改的"); // 留下了
      // 用键盘选“留下”再按 Enter：也是留下（焦点不会被抢回“离开”）
      await click("直通", ".side .wf-item");
      await until(() => js(`!!document.querySelector(".dialog")`), "弹出确认框");
      await js(`[...document.querySelectorAll(".dialog button")].find((b) => b.textContent === "留下").focus(); true`);
      await sleep(600);
      await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
      await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
      await sleep(300);
      assert.equal(await title(), "拿来改的");
      // 确认框开着时按 Tab 只在确认框里转，背景按钮拿不到焦点
      await click("直通", ".side .wf-item");
      await until(() => js(`!!document.querySelector(".dialog")`), "再次弹出");
      for (let i = 0; i < 5; i++) {
        await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
        await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
        await sleep(50);
        assert.ok(await js(`!document.activeElement || document.activeElement === document.body || !!document.activeElement.closest(".dialog")`), `第 ${i + 1} 次 Tab 后焦点在确认框里`);
      }
      await click("离开，不保存", ".dialog button");
      await until(async () => (await title()) === "直通", "离开到别的工作流");
    },
    async "碰撞：节点放到别的节点上会让开；框里的节点拖出框，框跟着扩大并推开邻居；一步撤销"() {
      await go("/?wf=saved/grade_drag.workflow.json");
      await until(async () => (await title()) === "拖来拖去", "打开");
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="each/pass"]')`), "画出循环");
      const rect = (id) => js(`(() => { const r = document.querySelector('.react-flow__node[data-id="${id}"]').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const ids = () => js(`[...document.querySelectorAll(".react-flow__node")].map((n) => n.dataset.id)`);
      const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      const parent = (id) => (id.includes("/") ? id.slice(0, id.lastIndexOf("/")) : "");
      const assertNoOverlap = async () => {
        const all = await ids();
        const rs = Object.fromEntries(await Promise.all(all.map(async (id) => [id, await rect(id)])));
        for (const a of all) for (const b of all) {
          if (a < b && parent(a) === parent(b)) assert.ok(!hit(rs[a], rs[b]), `${a} 与 ${b} 重叠`);
        }
        return rs;
      };
      const dragNode = async (id, dx, dy) => { // 按住节点标题拖动
        const r = await rect(id);
        const a = { x: r.x + 30, y: r.y + 12 };
        await mouse("mouseMoved", a, 0);
        await mouse("mousePressed", a);
        for (let i = 1; i <= 12; i++) { await mouse("mouseMoved", { x: a.x + (dx * i) / 12, y: a.y + (dy * i) / 12 }); await sleep(25); }
        await mouse("mouseReleased", { x: a.x + dx, y: a.y + dy }, 0);
        await sleep(400);
      };
      await click("看全图");
      await sleep(500);
      await assertNoOverlap();
      // 1. 把“及格”往“比较”身上拖：拖动中被挡住，松手后不重叠
      const cmp = await rect("each/cmp");
      const pass = await rect("each/pass");
      await dragNode("each/pass", cmp.x - pass.x + 10, cmp.y - pass.y + 10);
      await assertNoOverlap(); // 一路被挡住，松手后也不会压上去（React Flow 松手时发来的原始位置已被换掉）
      // 2. 把“合并”拖出循环框右边：框扩大把它装下，框外的邻居被推开
      const loop0 = await rect("each");
      const m0 = await rect("each/m");
      await dragNode("each/m", 0, loop0.y + loop0.h - m0.y - m0.h - 20); // 先往下绕开框里右边的“这一项的结果”（拖动中会被挡住）
      const m1 = await rect("each/m");
      await dragNode("each/m", loop0.x + loop0.w - m1.x + 60, 0);
      let rs = await assertNoOverlap();
      assert.ok(rs["each/m"].x > loop0.x + loop0.w, `节点确实被拖到原来的框外 ${JSON.stringify({ loop0, m0, m1, now: rs["each/m"], loop: rs.each, out: rs["each/$out"] })}`);
      assert.ok(rs.each.w > loop0.w + 40 && rs.each.x + rs.each.w >= rs["each/m"].x + rs["each/m"].w, "框向右扩大装下了节点");
      // 3. 把“比较”拖出循环框左边：框向左扩大，其他框内节点在屏幕上不动
      const before = await rect("each/br");
      const cTop = await rect("each/cmp");
      await dragNode("each/cmp", 0, rs.each.y + rs.each.h - cTop.y + 30); // 先往下拖出框底（框向下扩大），绕开左边的“每一项”
      const c0 = await rect("each/cmp");
      await dragNode("each/cmp", rs.each.x - c0.x - 80, 0);
      rs = await assertNoOverlap();
      assert.ok(rs.each.x < loop0.x - 40 && rs.each.x <= rs["each/cmp"].x, `框向左扩大装下了节点 ${JSON.stringify({ loop0, c0, now: rs["each/cmp"], loop: rs.each })}`);
      const after = await rect("each/br");
      assert.ok(Math.abs(after.x - before.x) < 3 && Math.abs(after.y - before.y) < 3, `框里其他节点没跳动：${JSON.stringify([before, after])}`);
      if (process.env.E2E_SHOT) { // 自查用：保存拖动后的画面
        const { data } = (await send("Page.captureScreenshot", { format: "png" })).result;
        writeFileSync(process.env.E2E_SHOT, Buffer.from(data, "base64"));
      }
      // 撤销一步：回到第 3 次拖动之前
      const loopBefore3 = rs.each.x;
      await key("z", "KeyZ", 2);
      await sleep(400);
      assert.ok((await rect("each")).x > loopBefore3 + 20, "撤销后框回到原来的位置");
    },
    async "拉伸循环框：拉大后记下大小；往里压到框里节点时停住；拉左边时框里节点不动"() {
      await go("/?wf=saved/grade_box.workflow.json");
      await until(async () => (await title()) === "拉伸框", "打开");
      await click("看全图");
      await sleep(500);
      assert.equal(await js(`document.querySelectorAll('.react-flow__node[data-id="each"] .box-resize').length`), 0, "开着自动收缩时没有拉伸点");
      await pickBox("each");
      await autoShrink(false);
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="each"] .box-resize')`), "关掉自动收缩后出现拉伸点");
      assert.equal(await js(`document.querySelectorAll('.react-flow__node[data-id="each"] .react-flow__resize-control.handle').length`), 8);
      const rect = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const node = (id) => rect(`.react-flow__node[data-id="${id}"]`);
      const grip = (pos) => `.react-flow__node[data-id="each"] .react-flow__resize-control.handle.${pos.split("-").join(".")}`;
      const pull = async (pos, dx, dy) => {
        const r = await rect(grip(pos));
        const a = { x: r.x + r.w / 2, y: r.y + r.h / 2 };
        await mouse("mouseMoved", a, 0);
        await mouse("mousePressed", a);
        for (let i = 1; i <= 12; i++) { await mouse("mouseMoved", { x: a.x + (dx * i) / 12, y: a.y + (dy * i) / 12 }); await sleep(25); }
        await mouse("mouseReleased", { x: a.x + dx, y: a.y + dy }, 0);
        await sleep(400);
      };
      const kids = async () => {
        const ids = await js(`[...document.querySelectorAll(".react-flow__node")].map((n) => n.dataset.id).filter((id) => id.startsWith("each/"))`);
        return Object.fromEntries(await Promise.all(ids.map(async (id) => [id, await node(id)])));
      };
      const inside = (b, k) => k.x >= b.x - 1 && k.y >= b.y - 1 && k.x + k.w <= b.x + b.w + 1 && k.y + k.h <= b.y + b.h + 1;
      // 1. 拉右下角往外：框变大，程序里记下大小
      const b0 = await node("each");
      await pull("bottom-right", 120, 80);
      const b1 = await node("each");
      assert.ok(b1.w > b0.w + 30 && b1.h > b0.h + 20, `框变大了 ${JSON.stringify([b0, b1])}`);
      // 拉大后压到框右边的“合成文字”：它被推开，不重叠
      const join = await node("join");
      const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      assert.ok(!hit(b1, join), `拉大后邻居被推开 ${JSON.stringify([b1, join])}`);
      const prog = await program();
      const lay = prog.layout?.each;
      assert.ok(lay && lay.w > 0 && lay.h > 0, `layout 记下了大小 ${JSON.stringify(lay)}`);
      // 2. 拉右下角往里压过头：停在框里节点外面
      await pull("bottom-right", -900, -600);
      const b2 = await node("each");
      for (const [id, k] of Object.entries(await kids())) assert.ok(inside(b2, k), `${id} 仍在框里`);
      // 3. 拉左边往外：框向左变大，框里节点在屏幕上不动
      const before = await kids();
      await pull("left", -90, 0);
      const b3 = await node("each");
      assert.ok(b3.x < b2.x - 20, "框向左变大");
      for (const [id, k] of Object.entries(await kids())) {
        assert.ok(Math.abs(k.x - before[id].x) < 3 && Math.abs(k.y - before[id].y) < 3, `${id} 没动`);
      }
      // 4. 一步撤销：回到拉左边之前
      await key("z", "KeyZ", 2);
      await sleep(400);
      assert.ok(Math.abs((await node("each")).x - b2.x) < 3, "撤销后框的左边回到原处");
    },
    async "拉伸框中框：里层框拉出外层框左边，外层框跟着扩大，里层框留在放下的位置"() {
      await go("/?wf=saved/nested_box.workflow.json");
      await until(async () => (await title()) === "框中框", "打开");
      await click("看全图");
      await sleep(500);
      await pickBox("outer/inner");
      await autoShrink(false);
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="outer/inner"] .box-resize')`), "出现拉伸点");
      const rect = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const node = (id) => rect(`.react-flow__node[data-id="${id}"]`);
      const g = await rect('.react-flow__node[data-id="outer/inner"] .react-flow__resize-control.handle.left');
      const inner0 = await node("outer/inner");
      const f0 = await node("outer/inner/f");
      const outer0 = await node("outer");
      const a = { x: g.x + g.w / 2, y: g.y + g.h / 2 };
      const dx = -(inner0.x - outer0.x) - 60; // 拉过外层框左边 60 像素
      await mouse("mouseMoved", a, 0);
      await mouse("mousePressed", a);
      for (let i = 1; i <= 12; i++) { await mouse("mouseMoved", { x: a.x + (dx * i) / 12, y: a.y }); await sleep(25); }
      await mouse("mouseReleased", { x: a.x + dx, y: a.y }, 0);
      await sleep(500);
      const inner1 = await node("outer/inner");
      const outer1 = await node("outer");
      const f1 = await node("outer/inner/f");
      assert.ok(Math.abs(inner1.x - (inner0.x + dx)) < 6, `里层框左边留在放下的位置 ${JSON.stringify([inner0, inner1, dx])}`);
      assert.ok(Math.abs(f1.x - f0.x) < 3, "里层框里的节点没动");
      assert.ok(outer1.x < inner1.x, "外层框向左扩大装下里层框");
    },
    async "循环框顶部菜单：出现在框顶边外侧居中；自动收缩开关、收紧到节点、整理、选中框里全部"() {
      await go("/?wf=saved/grade_box.workflow.json");
      await until(async () => (await title()) === "拉伸框", "打开");
      await click("看全图");
      await sleep(500);
      const rect = (sel) => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const node = (id) => rect(`.react-flow__node[data-id="${id}"]`);
      await pickBox("each");
      const b0 = await node("each");
      const m = await rect(".box-menu .box-menu-row");
      assert.ok(m.y + m.h <= b0.y + 1 && Math.abs(m.x + m.w / 2 - (b0.x + b0.w / 2)) < 4, `菜单在框顶边外侧居中 ${JSON.stringify([b0, m])}`);
      assert.equal(await js(`document.querySelector(".box-menu input[role=switch]").checked`), true, "默认开着自动收缩");
      assert.equal(await js(`[...document.querySelectorAll(".box-menu button")].find((b) => b.textContent === "收紧到节点").disabled`), true, "开着自动收缩时不用收紧");
      // 关掉：框原样不动，程序里记下大小，出现拉伸点
      await autoShrink(false);
      const b1 = await node("each");
      assert.ok(Math.abs(b1.x - b0.x) < 3 && Math.abs(b1.w - b0.w) < 3 && Math.abs(b1.h - b0.h) < 3, `关掉后框不动 ${JSON.stringify([b0, b1])}`);
      assert.ok((await program()).layout?.each?.w > 0, "记下了大小");
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="each"] .box-resize')`), "出现拉伸点");
      // 拉大后点“收紧到节点”：回到正好包住，仍关着自动收缩
      const g = await rect('.react-flow__node[data-id="each"] .react-flow__resize-control.handle.bottom.right');
      const a = { x: g.x + g.w / 2, y: g.y + g.h / 2 };
      await mouse("mouseMoved", a, 0);
      await mouse("mousePressed", a);
      for (let i = 1; i <= 10; i++) { await mouse("mouseMoved", { x: a.x + i * 12, y: a.y + i * 8 }); await sleep(25); }
      await mouse("mouseReleased", { x: a.x + 120, y: a.y + 80 }, 0);
      await sleep(400);
      assert.ok((await node("each")).w > b0.w + 30, "拉大了");
      await pickBox("each");
      await click("收紧到节点", ".box-menu button");
      await sleep(400);
      const b2 = await node("each");
      assert.ok(Math.abs(b2.w - b0.w) < 4 && Math.abs(b2.h - b0.h) < 4, `收紧到正好包住 ${JSON.stringify([b0, b2])}`);
      assert.ok((await program()).layout?.each?.w > 0, "仍记着大小（自动收缩仍关着）");
      // 再拉大，然后打开自动收缩：框马上收回，程序里不再记大小，拉伸点消失
      await mouse("mouseMoved", a, 0);
      await mouse("mousePressed", a);
      for (let i = 1; i <= 10; i++) { await mouse("mouseMoved", { x: a.x + i * 12, y: a.y + i * 8 }); await sleep(25); }
      await mouse("mouseReleased", { x: a.x + 120, y: a.y + 80 }, 0);
      await sleep(400);
      await pickBox("each");
      await autoShrink(true);
      const b3 = await node("each");
      assert.ok(Math.abs(b3.w - b0.w) < 4 && Math.abs(b3.h - b0.h) < 4, `打开自动收缩后收回 ${JSON.stringify([b0, b3])}`);
      assert.equal((await program()).layout?.each?.w, undefined, "不再记大小");
      assert.equal(await js(`document.querySelectorAll('.react-flow__node[data-id="each"] .box-resize').length`), 0, "拉伸点消失");
      // 整理框里节点：程序里框里这一层的位置去掉，节点仍都在框里、不重叠
      await pickBox("each");
      await click("整理框里节点", ".box-menu button");
      await sleep(500);
      const prog = await program();
      const body = prog.nodes.find((n) => n.id === "each").body;
      assert.ok(Object.values(body.layout || {}).every((v) => Number.isFinite(v.x) && Number.isFinite(v.y)), `整理后的位置都写回了 ${JSON.stringify(body.layout)}`);
      const b4 = await node("each");
      const ids = await js(`[...document.querySelectorAll(".react-flow__node")].map((n) => n.dataset.id).filter((id) => id.startsWith("each/"))`);
      const ks = await Promise.all(ids.map(node));
      for (const k of ks) assert.ok(k.x >= b4.x - 1 && k.y >= b4.y - 1 && k.x + k.w <= b4.x + b4.w + 1 && k.y + k.h <= b4.y + b4.h + 1, "整理后仍在框里");
      // 选中框里全部
      await pickBox("each");
      await click("选中框里全部", ".box-menu button");
      await sleep(200);
      const sel = await js(`[...document.querySelectorAll(".react-flow__node.selected")].map((n) => n.dataset.id).sort()`);
      assert.deepEqual(sel, [...ids].sort(), "选中的正是框里的节点");
      // 按 Delete 不会删掉框（点菜单不算点了框）
      await key("Delete", "Delete");
      await sleep(300);
      assert.ok(await js(`!!document.querySelector('.react-flow__node[data-id="each"]')`), "框还在");
      // 一起拖动后仍是这一组选中
      const k0 = await node(ids[0]);
      const a2 = { x: k0.x + 30, y: k0.y + 12 };
      await mouse("mouseMoved", a2, 0);
      await mouse("mousePressed", a2);
      for (let i = 1; i <= 8; i++) { await mouse("mouseMoved", { x: a2.x, y: a2.y + i * 5 }); await sleep(25); }
      await mouse("mouseReleased", { x: a2.x, y: a2.y + 40 }, 0);
      await sleep(500);
      const sel2 = await js(`[...document.querySelectorAll(".react-flow__node.selected")].map((n) => n.dataset.id).sort()`);
      assert.deepEqual(sel2, [...ids].sort(), "拖动后仍选中这一组");
      // 之后加节点：只选中新加的那个，原来那一组不再选中
      await js(`document.querySelector(".edit-tools .add-menu > button").click(), true`);
      await click("合并", ".menu-item");
      await sleep(600);
      const sel3 = await js(`[...document.querySelectorAll(".react-flow__node.selected")].map((n) => n.dataset.id)`);
      assert.equal(sel3.length, 1, `只选中新加的节点 ${JSON.stringify(sel3)}`);
      assert.ok(!ids.includes(sel3[0]), "不是原来那一组里的");
      // 用鼠标真点菜单按钮（焦点留在按钮上）后，Ctrl+Z 照样能撤销
      await pickBox("each");
      const before = JSON.stringify((await program()).nodes.find((n) => n.id === "each").body.layout);
      await pickBox("each");
      const btn = await center(".box-menu input[role=switch]");
      await mouse("mouseMoved", btn, 0);
      await mouse("mousePressed", btn);
      await mouse("mouseReleased", btn, 0);
      await sleep(400);
      assert.equal(await js(`document.querySelector(".box-menu input[role=switch]")?.checked`), false, "真点开关关掉了自动收缩");
      assert.equal(await js(`document.activeElement?.closest(".box-menu") != null`), true, "焦点在菜单里");
      await key("z", "KeyZ", 2);
      await sleep(400);
      assert.equal(JSON.stringify((await program()).nodes.find((n) => n.id === "each").body.layout), before, "撤销回去了");
      assert.equal((await program()).layout?.each?.w, undefined, "撤销后又开着自动收缩");
    },
    async "按住框顶菜单的“按住拖动整个框”：框连同框里节点一起移动，不压到别人；一步撤销"() {
      await go("/?wf=saved/grade_box.workflow.json");
      await until(async () => (await title()) === "拉伸框", "打开");
      await click("看全图");
      await sleep(500);
      const node = (id) => js(`(() => { const r = document.querySelector('.react-flow__node[data-id="${id}"]').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const ids = await js(`[...document.querySelectorAll(".react-flow__node")].map((n) => n.dataset.id)`);
      const kidIds = ids.filter((id) => id.startsWith("each/"));
      const all0 = Object.fromEntries(await Promise.all(ids.map(async (id) => [id, await node(id)])));
      await pickBox("each");
      const g = await center(".box-menu .box-menu-grip");
      await mouse("mouseMoved", g, 0);
      await mouse("mousePressed", g);
      for (let i = 1; i <= 12; i++) { await mouse("mouseMoved", { x: g.x + i * 3, y: g.y + i * 10 }); await sleep(25); }
      await mouse("mouseReleased", { x: g.x + 36, y: g.y + 120 }, 0);
      await sleep(500);
      const b1 = await node("each");
      const dy = b1.y - all0.each.y;
      assert.ok(dy > 40, `框往下移了 ${JSON.stringify([all0.each, b1])}`);
      for (const id of kidIds) {
        const k = await node(id);
        assert.ok(Math.abs(k.x - all0[id].x - (b1.x - all0.each.x)) < 3 && Math.abs(k.y - all0[id].y - dy) < 3, `${id} 跟着框一起移动`);
      }
      const top = ids.filter((id) => !id.includes("/"));
      const rs = await Promise.all(top.map(node));
      const hit = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      rs.forEach((a, i) => rs.slice(i + 1).forEach((b, j) => assert.ok(!hit(a, b), `${top[i]} 与 ${top[i + 1 + j]} 不重叠`)));
      await key("z", "KeyZ", 2);
      await sleep(400);
      const back = await node("each");
      const ref = await node("$in"); // 改动后页头多出“有改动”标签，整个画布可能下移几像素：相对输入端点比较
      assert.ok(Math.abs(back.y - ref.y - (all0.each.y - all0.$in.y)) < 3 && Math.abs(back.x - ref.x - (all0.each.x - all0.$in.x)) < 3, `撤销后回到原处 ${JSON.stringify([all0.each, back])}`);
      // 再拖一次、按着时撤销（撤掉第一次拖动之前的那步不存在，先重做回第一次拖动），松手：这次拖动作废，不覆盖撤销的结果
      await key("z", "KeyZ", 10); // Ctrl+Shift+Z 重做：框回到第一次拖动后的位置
      await sleep(400);
      const redo = await node("each");
      await pickBox("each");
      const g2 = await center(".box-menu .box-menu-grip");
      await mouse("mouseMoved", g2, 0);
      await mouse("mousePressed", g2);
      for (let i = 1; i <= 6; i++) { await mouse("mouseMoved", { x: g2.x, y: g2.y + i * 10 }); await sleep(25); }
      await key("z", "KeyZ", 2);
      await sleep(300);
      for (let i = 7; i <= 10; i++) { await mouse("mouseMoved", { x: g2.x, y: g2.y + i * 10 }); await sleep(25); }
      await mouse("mouseReleased", { x: g2.x, y: g2.y + 100 }, 0);
      await sleep(500);
      const ref2 = await node("$in");
      const end = await node("each");
      assert.ok(redo.y > back.y + 20, "重做回到了第一次拖动后");
      assert.ok(Math.abs(end.y - ref2.y - (all0.each.y - all0.$in.y)) < 3, `按着时撤销，松手后不覆盖撤销的结果 ${JSON.stringify([all0.each, end])}`);
    },
    async "整理外层框：里层框关掉自动收缩时记着的大小留着，位置写回，程序仍合格式"() {
      await go("/?wf=saved/nested_box.workflow.json");
      await until(async () => (await title()) === "框中框", "打开");
      await click("看全图");
      await sleep(500);
      await pickBox("outer/inner");
      await autoShrink(false);
      await pickBox("outer");
      await click("整理框里节点", ".box-menu button");
      await sleep(600);
      const lay = (await program()).nodes.find((n) => n.id === "outer").body.layout;
      assert.ok(Object.values(lay).every((v) => Number.isFinite(v.x) && Number.isFinite(v.y)), `位置都写回了 ${JSON.stringify(lay)}`);
      assert.ok(lay.inner.w > 0 && lay.inner.h > 0, "里层框的大小留着");
      await sleep(900); // 改动后停手 300 毫秒才校验
      assert.equal(await js(`document.querySelector(".check-errors")?.textContent || ""`), "", "没有校验问题");
    },
    async "拖出去又拖回原处：框回到原来的大小"() {
      await go("/?wf=saved/grade_drag.workflow.json");
      await until(async () => (await title()) === "拖来拖去", "打开");
      await click("看全图");
      await sleep(500);
      const node = (id) => js(`(() => { const r = document.querySelector('.react-flow__node[data-id="${id}"]').getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
      const press = async (id, path) => { // 按住节点标题，沿 path（相对起点的位移）走一遍再松手
        const r = await node(id);
        const a = { x: r.x + 30, y: r.y + 12 };
        await mouse("mouseMoved", a, 0);
        await mouse("mousePressed", a);
        for (const [dx, dy] of path) { await mouse("mouseMoved", { x: a.x + dx, y: a.y + dy }); await sleep(25); }
        const [ex, ey] = path[path.length - 1];
        await mouse("mouseReleased", { x: a.x + ex, y: a.y + ey }, 0);
        await sleep(400);
      };
      await press("each/m", [[0, 4], [0, 8]]); // 先动一下，让布局固定下来
      const w0 = (await node("each")).w;
      const out = Array.from({ length: 10 }, (_, i) => [0, 8 + (i + 1) * 20]); // 往下拖出框，框跟着变高
      const back = [...Array.from({ length: 10 }, (_, i) => [0, 8 + 200 - (i + 1) * 20]), [0, 0]]; // 回到起点：程序没变
      const h0 = (await node("each")).h;
      await press("each/m", [...out, ...back]);
      const b = await node("each");
      assert.ok(Math.abs(b.h - h0) < 3 && Math.abs(b.w - w0) < 3, `框回到原来的大小 ${JSON.stringify([h0, b.h])}`);
    },
    async "图纸大小可控：缩放条、看全图、100%、小地图；列表和面板能收起"() {
      await go("/?wf=saved/grade.workflow.json");
      await until(() => js(`!!document.querySelector(".zoom-pct")`), "出现缩放条");
      const pct = () => js(`parseInt(document.querySelector(".zoom-pct").textContent)`);
      await until(async () => (await pct()) >= 75, "打开时缩放不小于 75%，字看得清");
      await click("看全图", ".zoombar button");
      await until(async () => (await pct()) < 75, "看全图会缩小到整张图放得下");
      await click("%", ".zoombar button");
      await until(async () => (await pct()) === 100, "点百分比回到 100%");
      await js(`document.querySelector(".zoombar button[aria-label='放大']").click(), true`);
      await until(async () => (await pct()) > 100, "放大");
      assert.ok(await js(`!!document.querySelector(".react-flow__minimap")`));
      const canvasW = () => js(`document.querySelector(".canvas").getBoundingClientRect().width`);
      const before = await canvasW();
      await click("收起列表");
      await click("收起面板");
      await until(async () => (await canvasW()) > before + 500, "收起后画布变宽").catch(async (e) => {
        throw new Error(`${e.message}（窗口 ${await js("innerWidth")}，收起前 ${before}，收起后 ${await canvasW()}）`);
      });
      await click("显示列表");
      await click("显示面板");
      await until(async () => Math.abs((await canvasW()) - before) < 2, "恢复原宽度");
    },
    async "窄屏：各区域上下排列，能打开工作流并运行"() {
      await size(500, 900);
      try {
        await go("/?wf=saved/form42.workflow.json");
        await until(async () => (await title()) === "表单保持", "打开");
        assert.ok(await js(`document.documentElement.scrollWidth <= innerWidth + 1`), "页面不应横向滚动");
        await click("运行", "form.run button");
        await until(() => js(`document.querySelector(".result")?.innerText.includes("42")`), "运行结果");
      } finally {
        await size(1440, 900);
      }
    },
    async "开始页：理清需求（问题、选项、卡片、做不到的部分）后按需求生成"() {
      await go("/");
      await setField("#idea", "把数字写成一句话");
      await click("帮我理清需求", ".start-actions button");
      await until(() => js(`!!document.querySelector(".question")`), "出现问题");
      let text = await js(`document.querySelector(".refine").innerText`);
      assert.ok(text.includes("句子里要不要带单位？") && text.includes("一个整数") && text.includes("发邮件通知"), text);
      await click("要带“个”", ".chip");
      await click("根据回答更新", ".questions button");
      await until(() => js(`document.querySelector(".final-text")?.textContent.includes("要带“个”")`), "按回答更新了需求");
      assert.ok(!(await js(`!!document.querySelector(".question")`)), "回答后不再提问");
      // 改卡片：需求描述改为按卡片拼出
      await setField('[aria-label="要它产出什么 第 1 条"]', "一句带单位的话");
      await until(() => js(`document.querySelector(".final-text").textContent.includes("要它产出：一句带单位的话")`), "改卡片后需求跟着变");
      await click("按这个需求生成", ".final button");
      await until(() => js(`document.body.innerText.includes("已显示在画布上")`), "生成完成");
      assert.ok((await val("#req")).includes("一句带单位的话"));
    },
    async "开始页：从示例开始；节点详情把默认参数收起"() {
      await go("/");
      await click("按分数写一句话", ".example-list .starter");
      await until(async () => (await title()) === "按分数写一句话", "从开始页打开示例");
      await go("/?wf=saved/grade.workflow.json");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("完成")`), "运行完成", 30000);
      await js(`document.querySelector('.react-flow__node[data-id="each/cmp"]').click(), true`);
      await until(() => js(`!!document.querySelector(".detail .record")`), "显示循环各项记录");
      const text = await js(`document.querySelector(".detail").innerText`);
      assert.ok(text.includes("共 2 项，完成 2"), text.slice(0, 200));
      assert.ok(text.includes("第 1 项") && text.includes("第 2 项"));
    },
    async "生成过程按轮显示；被检查指出问题的节点在图上标出，点“在图上看”定位"() {
      await go("/");
      await type("#req", "检查不过的需求");
      await click("生成工作流");
      await until(() => js(`document.querySelectorAll(".genlog .round").length === 2`), "两轮");
      await until(() => js(`!!document.querySelector(".gen-banner")`), "结束提示");
      const log = await js(`document.querySelector(".genlog").innerText`);
      assert.ok(log.includes("实跑检查") && log.includes("发现 1 个问题"), log);
      assert.ok(log.includes("交回模型"), "第 1 轮后应说明交回重写");
      assert.ok(log.includes("结构校验") && log.includes("工作流没有输出 z"));
      assert.equal(await title(), "数字写成一句话"); // 画布上是第 1 轮那版（第 2 轮结构不对，不画）
      assert.ok(await js(`!!document.querySelector('.react-flow__node[data-id="fmt"] .has-issue, .react-flow__node[data-id="fmt"].has-issue, .react-flow__node[data-id="fmt"] .issue-badge')`), "fmt 应被标出");
      await click("在图上看", ".focus-node");
      await until(() => js(`document.querySelector('.react-flow__node[data-id="fmt"]').classList.contains("selected")`), "定位并选中 fmt");
    },
    async "编辑：改固定参数后运行用的是新程序；撤销恢复"() {
      await go("/?wf=saved/grade.workflow.json");
      await until(async () => (await title()) === "逐个打分", "打开");
      await js(`document.querySelector('.react-flow__node[data-id="each/pass"]').click(), true`);
      await until(() => js(`!!document.querySelector("#param-template")`), "参数编辑框");
      await setField("#param-template", "通过 {value}");
      await until(() => js(`document.body.innerText.includes("有改动，还没保存")`), "标记有改动");
      let wf = await program();
      assert.equal(wf.nodes[0].body.nodes.find((n) => n.id === "pass").config.template, "通过 {value}");
      await click("运行", "[role=tab]");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("通过 90")`), "按新参数运行", 30000)
        .catch(async (e) => { throw new Error(`${e.message}：${await js(`document.querySelector(".result")?.innerText`)}`); });
      await click("撤销", ".edit-tools button");
      wf = await program();
      assert.equal(wf.nodes[0].body.nodes.find((n) => n.id === "pass").config.template, "及格 {value}");
      await click("重做", ".edit-tools button");
      wf = await program();
      assert.equal(wf.nodes[0].body.nodes.find((n) => n.id === "pass").config.template, "通过 {value}");
    },
    async "编辑：按 Delete 删节点，图上标出问题、不能保存；Ctrl+Z 撤销"() {
      await go("/?wf=saved/grade.workflow.json");
      await until(async () => (await title()) === "逐个打分", "打开");
      await js(`document.querySelector('.react-flow__node[data-id="join"]').click(), true`);
      await key("Delete", "Delete");
      await until(() => js(`!document.querySelector('.react-flow__node[data-id="join"]')`), "节点删掉");
      await until(() => js(`!!document.querySelector(".check-errors")`), "校验出问题");
      assert.ok(await js(`[...document.querySelectorAll(".canvas-actions button")].find((b) => b.textContent.includes("保存")).disabled`));
      await key("z", "KeyZ", 2); // Ctrl+Z
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="join"]')`), "撤销后节点回来");
      const wf = await program();
      assert.ok(wf.edges.some((e) => e.from === "join.text"));
    },
    async "编辑：加节点、拖线连接、删连线、保存后重新打开还在"() {
      await go("/?wf=saved/edit_me.workflow.json");
      await until(async () => (await title()) === "拿来改的", "打开");
      await click("添加节点", ".add-menu > button");
      await click("转成文字", ".menu-item");
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="format"]')`), "新节点出现");
      await setField("#param-template", "你好 {value}");
      await sleep(400);
      await drag(handle("$in", "out:x"), handle("format", "in:value"));
      await drag(handle("format", "out:text"), handle("$out", "in:y"));
      let wf = await program();
      assert.ok(wf.edges.some((e) => e.from === "$in.x" && e.to === "format.value"), JSON.stringify(wf.edges) + JSON.stringify(drag.log));
      assert.ok(wf.edges.some((e) => e.from === "format.text" && e.to === "$out.y"));
      assert.equal(wf.edges.filter((e) => e.to === "$out.y").length, 1); // 原来的直通线被替换
      await click("运行", "[role=tab]");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("你好 世界")`), "运行新程序", 30000);
      // 点中连线并删除，再撤销
      // 选中连 $out 的那条线（format.text → $out.y）
      await js(`(() => { const g = [...document.querySelectorAll(".react-flow__edge")].find((x) => x.getAttribute("aria-label")?.includes("$out") || x.dataset.id); g.dispatchEvent(new MouseEvent("click", { bubbles: true })); return true; })()`);
      await until(() => js(`document.body.innerText.includes("删除这条线")`), "选中连线");
      const before = (await program()).edges.length;
      await click("连线", "[role=tab]");
      await click("删除这条线", ".detail button");
      assert.equal((await program()).edges.length, before - 1);
      await click("撤销", ".edit-tools button");
      assert.equal((await program()).edges.length, before);
      await until(() => js(`![...document.querySelectorAll(".canvas-actions button")].find((b) => b.textContent.trim() === "保存")?.disabled`), "可以保存");
      await click("保存", ".canvas-actions button");
      await until(() => js(`document.body.innerText.includes("已保存")`), "保存成功");
      const savedWf = await (await fetch(`${BASE}/api/workflows/saved/edit_me.workflow.json`)).json();
      assert.ok(savedWf.nodes.some((n) => n.id === "format"), "写回了原文件");
      await click("表单保持", ".wf-item");
      await until(async () => (await title()) === "表单保持", "切走");
      await click("拿来改的", ".wf-item");
      await until(() => js(`!!document.querySelector('.react-flow__node[data-id="format"]')`), "重新打开后新节点还在");
    },
    async "编辑：改工作流名称、改输入名（连线跟着改）、加未接线的输出会报问题，删掉恢复"() {
      await go("/?wf=saved/edit_me.workflow.json");
      await until(async () => (await title()) === "拿来改的", "打开");
      await click("节点", "[role=tab]");
      await until(() => js(`!!document.querySelector("#wf-name")`), "工作流信息编辑");
      await setField("#wf-name", "改过名字的");
      await until(async () => (await title()) === "改过名字的", "标题跟着改");
      await js(`document.querySelector('.react-flow__node[data-id="$in"]').click(), true`);
      await until(() => js(`!!document.querySelector("#inputs-x-name")`), "输入编辑");
      await setField("#inputs-x-name", "word");
      let wf = await program();
      assert.deepEqual(Object.keys(wf.inputs), ["word"]);
      // 前一项测试保存过这个工作流，x 可能接到别的节点上：只检查改名后所有连线都用新名字
      assert.ok(wf.edges.some((e) => e.from === "$in.word"), JSON.stringify(wf.edges));
      assert.ok(!wf.edges.some((e) => e.from === "$in.x"));
      await click("运行", "[role=tab]");
      await until(() => js(`!!document.querySelector("#in-word")`), "运行表单换成新名字");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("世界")`), "运行", 30000);
      await js(`document.querySelector('.react-flow__node[data-id="$out"]').click(), true`);
      await click("添加一个输出", ".ports-editor button");
      await until(() => js(`document.querySelector(".check-errors")?.innerText.includes("1 个问题")`), "没接线的输出报问题");
      await js(`document.querySelector("#outputs-output-name").closest(".port-card").querySelector("button.danger").click(), true`);
      await until(() => js(`!document.querySelector(".check-errors")`), "删掉后问题消失");
    },
    async "从空白开始：画布上的清单随操作逐步打勾"() {
      await go("/");
      await click("新建空白工作流", ".start-other button");
      await until(async () => (await title()) === "新工作流", "新建");
      const count = () => js(`document.querySelector(".guide-count")?.textContent`);
      assert.equal(await count(), "1/5");
      assert.ok(await js(`document.querySelector(".guide-steps li.now").innerText.includes("加一个节点")`));
      await click("添加节点", ".add-menu > button");
      await click("转成文字", ".menu-item");
      await until(async () => (await count()) === "2/5", "加节点后打勾");
      await setField("#param-template", "你好 {value}");
      await sleep(400);
      await drag(handle("$in", "out:input"), handle("format", "in:value"));
      await drag(handle("format", "out:text"), handle("$out", "in:output"));
      await until(async () => (await count()) === "4/5", "连好线、校验通过后打勾", 8000)
        .catch(async (e) => { throw new Error(`${e.message}：${await count()} ${JSON.stringify((await program()).edges)} ${JSON.stringify(drag.log)}`); });
      await click("运行", "[role=tab]");
      await setField("#in-input", "世界");
      await click("运行", "form.run button");
      await until(() => js(`document.querySelector(".result")?.innerText.includes("你好 世界")`), "运行", 30000);
      await until(async () => (await count()) === "5/5", "运行后全部完成");
      assert.ok(await js(`[...document.querySelectorAll(".canvas-actions button")].some((b) => b.textContent.includes("保存到我的"))`));
    },
    async "Q01：合并端口写错的工作流不让页面白屏，还能打开别的"() {
      await go("/?wf=saved/badmerge.workflow.json");
      await until(async () => (await title()) === "合并端口写错", "打开");
      assert.ok(await js(`!!document.querySelector(".side .wf-item")`));
      await click("运行", "form.run button");
      await until(() => js(`!!document.querySelector(".result")`), "显示校验结果");
      assert.ok(await js(`document.querySelector(".result").innerText.includes("ports")`));
      await click("表单保持", ".wf-item");
      await until(async () => (await title()) === "表单保持", "还能打开正常工作流");
    },
    async "项目栏：新建、切换后改动还在、重命名、删除；首页没有点阵，画布才有"() {
      await go("/");
      const names = () => js(`[...document.querySelectorAll(".proj-row .proj-name")].map((x) => x.textContent)`);
      const active = () => js(`document.querySelector(".proj-row.on .proj-name")?.textContent`);
      const nodeCount = () => js(`document.querySelectorAll(".react-flow__node").length`);
      // 首页：开始页不铺点阵
      assert.equal(await js(`getComputedStyle(document.querySelector(".canvas > .start-wrap")).backgroundImage`), "none");
      const before = (await names()).length;
      await js(`document.querySelector(".group-add").click(), true`);
      await until(async () => (await active()) === "未命名项目", "新建的项目打开了");
      assert.equal((await names()).length, before + 1);
      assert.ok(await js(`!!document.querySelector(".start-wrap")`), "新项目先显示开始页");
      await click("拿来改的", ".wf-item");
      await until(async () => (await title()) === "拿来改的", "打开到这个项目里");
      assert.ok(await js(`!!document.querySelector(".react-flow__background")`), "画布上有点阵");
      await until(async () => (await active()) === "拿来改的", "项目跟着工作流起名");
      const n0 = await nodeCount();
      await click("添加节点");
      await click("合并", ".menu button");
      await until(async () => (await nodeCount()) === n0 + 1, "加了一个节点");
      // 不等自动保存：马上切走再切回，改动也不能丢（还没存好时以本机那份为准）
      await js(`document.querySelector(".group-add").click(), true`);
      await until(() => js(`!!document.querySelector(".start-wrap")`), "切到新项目");
      await click("拿来改的", ".proj-item");
      await until(async () => (await title()) === "拿来改的", "马上切回");
      await until(async () => (await nodeCount()) === n0 + 1, "马上切回时改动还在");
      // 再建一个项目，打开别的工作流
      await js(`document.querySelector(".group-add").click(), true`);
      await until(() => js(`!!document.querySelector(".start-wrap")`), "回到开始页");
      await click("表单保持", ".wf-item");
      await until(async () => (await title()) === "表单保持", "第二个项目");
      // 切回第一个项目：改动还在，不弹确认框
      await click("拿来改的", ".proj-item");
      await until(async () => (await title()) === "拿来改的", "切回第一个项目");
      assert.equal(await js(`!!document.querySelector(".dialog")`), false);
      await until(async () => (await nodeCount()) === n0 + 1, "加的节点还在");
      assert.ok(await js(`document.body.innerText.includes("有改动")`));
      // 重命名
      await js(`[...document.querySelectorAll(".proj-row")].find((r) => r.textContent.includes("拿来改的")).querySelector(".proj-more").click(), true`);
      await click("重命名", ".proj-menu button");
      await until(() => js(`!!document.querySelector(".proj-rename")`), "出现改名框");
      await setField(".proj-rename", "我的项目");
      await until(async () => (await names()).includes("我的项目"), "改了名");
      // 删除第二个项目：先问，确认后才删
      await js(`[...document.querySelectorAll(".proj-row")].find((r) => r.textContent.includes("表单保持")).querySelector(".proj-more").click(), true`);
      await click("删除", ".proj-menu button");
      await until(() => js(`!!document.querySelector(".dialog")`), "删除前先问");
      const same = async () => (await names()).filter((x) => x === "表单保持").length; // 前面的测试也打开过它，可能有好几个同名项目
      const had = await same();
      await click("删除", ".dialog button");
      await until(async () => (await same()) === had - 1, "删掉了");
      assert.equal(await active(), "我的项目");
      // 重新打开页面：项目和里面的改动都还在
      await go("/");
      assert.ok(await js(`!!document.querySelector(".start-wrap")`), "打开页面先到首页");
      await until(async () => (await names()).includes("我的项目"), "项目还在");
      await click("我的项目", ".proj-item");
      await until(async () => (await title()) === "拿来改的", "打开项目");
      await until(async () => (await nodeCount()) === n0 + 1, "改动还在");
      // 点品牌回到首页
      await click("工作流", "button.brand");
      await until(() => js(`!!document.querySelector(".start-wrap") && !document.querySelector(".proj-row.on")`), "回到首页");
    },
  };
  for (const [name, fn] of Object.entries(cases)) {
    try {
      await fn();
      console.log(`✔ ${name}`);
    } catch (e) {
      failures++;
      console.log(`✖ ${name}\n   ${e.message}`);
    }
  }
  ws.close();
} finally {
  edge.kill();
  server.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
console.log(failures ? `${failures} 项失败` : "全部通过");
process.exit(failures ? 1 : 0);
