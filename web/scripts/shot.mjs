// 用无界面 Edge 截全尺寸图，可先执行一串页面操作（开发自查用）。
// 用法：node scripts/shot.mjs <网址> <输出.png> [宽] [高] [操作 JSON 文件]
// 操作：[{"click": "按钮文字"}, {"wait": 毫秒}, {"waitText": "出现的文字"}, {"type": ["#req", "文字"]}]
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [url, out, w = "1440", h = "900", stepsFile] = process.argv.slice(2);
const steps = stepsFile ? JSON.parse(readFileSync(stepsFile, "utf8").replace(/^﻿/, "")) : [];
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const port = 9300 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "shot-"));
const edge = spawn(EDGE, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, `--window-size=${w},${h}`, "about:blank"], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws;
try {
  let target;
  for (let i = 0; i < 50 && !target; i++) {
    await sleep(200);
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find((t) => t.type === "page"); } catch {}
  }
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let seq = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((r) => { const id = ++seq; pending.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  const evaluate = async (expr) => (await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

  await send("Emulation.setDeviceMetricsOverride", { width: +w, height: +h, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url });
  await sleep(2500);
  for (const s of steps) {
    if (s.wait) await sleep(s.wait);
    if (s.click) {
      const ok = await evaluate(`(() => { const el = [...document.querySelectorAll('button, [role=tab], .react-flow__node')].find(b => b.textContent.trim().includes(${JSON.stringify(s.click)})); if (!el) return false; el.click(); return true; })()`);
      if (!ok) console.error("找不到可点击的：", s.click);
    }
    if (s.clickSel) {
      const ok = await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(s.clickSel)}); if (!el) return false; el.click(); return true; })()`);
      if (!ok) console.error("找不到：", s.clickSel);
    }
    if (s.type) await evaluate(`(() => { const el = document.querySelector(${JSON.stringify(s.type[0])}); const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value').set; set.call(el, ${JSON.stringify(s.type[1])}); el.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    if (s.waitText) {
      for (let i = 0; i < (s.timeout || 600); i++) {
        if (await evaluate(`document.body.innerText.includes(${JSON.stringify(s.waitText)})`)) break;
        await sleep(500);
      }
    }
  }
  await sleep(600);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  console.log("截图：", out);

} finally {
  // 无论成功或出错，都关闭浏览器并删掉临时配置目录
  ws?.close();
  edge.kill();
  await sleep(500);
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
process.exit(0);
