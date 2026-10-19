// 画布编辑：每个操作都是“旧工作流 JSON → 新工作流 JSON”，不改动传入的对象。
// 画布节点 id 用“/”表示所在的循环体（"each/fetch" 在循环 each 的 body 里），这里据此找到要改的那一层图。
// 工作流 JSON 是唯一的程序；画布只是它的样子，改完重新画。

const clone = (v) => structuredClone(v);

export function splitId(canvasId) {
  const parts = String(canvasId).split("/");
  return { scope: parts.slice(0, -1), local: parts[parts.length - 1] };
}

// 找到某一层图（scope 为空是工作流本身；否则依次进入各个循环的 body）
export function graphAt(wf, scope) {
  let g = wf;
  for (const id of scope) {
    const n = (g.nodes || []).find((x) => x.id === id);
    if (!n || n.kind !== "foreach" || !n.body) throw new Error(`找不到循环 ${id}`);
    g = n.body;
  }
  return g;
}

function nodeAt(wf, canvasId) {
  const { scope, local } = splitId(canvasId);
  const g = graphAt(wf, scope);
  const n = (g.nodes || []).find((x) => x.id === local);
  if (!n) throw new Error(`找不到节点 ${canvasId}`);
  return { g, n, scope, local };
}

// 改固定参数：value 为 undefined 时删掉这一项（回到默认值或改为接线）
export function setConfig(wf, canvasId, port, value) {
  const out = clone(wf);
  const { n } = nodeAt(out, canvasId);
  const cfg = { ...(n.config || {}) };
  if (value === undefined) delete cfg[port];
  else cfg[port] = value;
  if (Object.keys(cfg).length) n.config = cfg;
  else delete n.config;
  return out;
}

// 改节点的显示名称（label）；空字符串表示不起名字，用模块名
export function setLabel(wf, canvasId, label) {
  const out = clone(wf);
  const { n } = nodeAt(out, canvasId);
  if (label && label.trim()) n.label = label.trim();
  else delete n.label;
  return out;
}

// 改循环的最多项数
export function setMaxItems(wf, canvasId, max) {
  const out = clone(wf);
  const { n } = nodeAt(out, canvasId);
  n.max_items = max;
  return out;
}

// 画布上的一次连线（React Flow 的 source/sourceHandle/target/targetHandle）→ 程序里的一条边。
// 返回 { wf } 或 { error }。规则：
// - 两端必须在同一层图里（不能从循环外直接连进循环体里的节点）；
// - 一个输入只能有一个来源：接到已有来源的输入时替换旧线，并去掉这个输入的固定值；
// - 接到节点顶部（ctrl）的是控制线，只能从条件分支的 then / else 拉出。
export function connect(wf, { source, sourceHandle, target, targetHandle }) {
  const s = splitId(source);
  const t = splitId(target);
  if (s.scope.join("/") !== t.scope.join("/")) return { error: "只能连接同一层里的节点：循环里的节点要通过循环框里的“每一项”和“这一项的结果”连出去" };
  if (s.local === t.local) return { error: "不能把节点连回自己" };
  const sp = String(sourceHandle || "").replace(/^out:/, "");
  if (!sp) return { error: "请从节点右侧的输出插孔开始拉线" };
  const out = clone(wf);
  const g = graphAt(out, s.scope);
  g.edges = g.edges || [];
  const from = `${s.local}.${sp}`;
  if (targetHandle === "ctrl") {
    const src = (g.nodes || []).find((x) => x.id === s.local);
    if (!src || src.kind !== "if" || !["then", "else"].includes(sp)) return { error: "控制线只能从条件分支的 then 或 else 拉出" };
    if (g.edges.some((e) => e.from === from && e.to === t.local)) return { wf };
    g.edges.push({ from, to: t.local });
    return { wf: out };
  }
  const tp = String(targetHandle || "").replace(/^in:/, "");
  if (!tp) return { error: "请连到节点左侧的输入插孔" };
  const to = `${t.local}.${tp}`;
  if (g.edges.some((e) => e.from === from && e.to === to)) return { wf };
  g.edges = g.edges.filter((e) => e.to !== to); // 一个输入只接一条线
  g.edges.push({ from, to });
  const tn = (g.nodes || []).find((x) => x.id === t.local);
  if (tn?.config && tp in tn.config) {
    delete tn.config[tp]; // 接了线就不再用固定值
    if (!Object.keys(tn.config).length) delete tn.config;
  }
  return { wf: out };
}

// 删一条线：scope 是所在那一层（画布节点 id 的前缀），from/to 与程序里写的一致
export function removeEdge(wf, { scope = [], from, to }) {
  const out = clone(wf);
  const g = graphAt(out, scope);
  g.edges = (g.edges || []).filter((e) => !(e.from === from && e.to === to));
  return out;
}

// 删节点：连着它的线一起删；位置记录也删
export function removeNode(wf, canvasId) {
  const { scope, local } = splitId(canvasId);
  if (local.startsWith("$")) return wf; // 工作流的输入、输出端点不能删
  const out = clone(wf);
  const g = graphAt(out, scope);
  g.nodes = (g.nodes || []).filter((n) => n.id !== local);
  const touches = (end) => end === local || end.startsWith(`${local}.`);
  g.edges = (g.edges || []).filter((e) => !touches(e.from) && !touches(e.to));
  if (g.layout) delete g.layout[local];
  return out;
}

export function uniqueId(g, base) {
  const taken = new Set((g.nodes || []).map((n) => n.id));
  const clean = String(base).replace(/[^\w]/g, "_").replace(/^(\d)/, "n$1") || "node";
  if (!taken.has(clean)) return clean;
  for (let i = 2; ; i++) if (!taken.has(`${clean}_${i}`)) return `${clean}_${i}`;
}

// 加节点。spec：{kind: "module", module: "text.format@0"} 或 {kind: "if" | "merge" | "foreach"}
// position：在这一层图里的位置（与 layout 同一坐标）。返回 { wf, id（画布 id） }
export function addNode(wf, scope, spec, position) {
  const out = clone(wf);
  const g = graphAt(out, scope);
  g.nodes = g.nodes || [];
  g.edges = g.edges || [];
  const base = spec.kind === "module" ? spec.module.split("@")[0].split(".").pop() : { if: "branch", merge: "merge", foreach: "each" }[spec.kind];
  const id = uniqueId(g, base);
  const node = { id, kind: spec.kind };
  if (spec.kind === "module") node.module = spec.module;
  if (spec.kind === "foreach") {
    node.max_items = 20;
    node.body = { inputs: { item: { type: "any" } }, outputs: { result: { type: "any" } }, nodes: [], edges: [] };
  }
  g.nodes.push(node);
  if (position) g.layout = { ...(g.layout || {}), [id]: round(position) };
  return { wf: out, id: [...scope, id].join("/") };
}

const round = (p) => ({ x: Math.round(p.x), y: Math.round(p.y) });

// 拖动后记下位置（只影响显示，不影响运行）
export function moveNode(wf, canvasId, position) {
  const { scope, local } = splitId(canvasId);
  const out = clone(wf);
  const g = graphAt(out, scope);
  g.layout = { ...(g.layout || {}), [local]: { ...keepSize(g.layout?.[local]), ...round(position) } };
  return out;
}

// 关掉自动收缩的循环框 layout 里还记着 w、h：只改位置时要留着它们
const keepSize = (old) => (old && Number.isFinite(old.w) && Number.isFinite(old.h) ? { w: old.w, h: old.h } : {});

// 关掉自动收缩（或拉伸）后记下循环框的大小（只影响显示）；size 为 null 时去掉，回到“跟着框里节点自动伸缩”
export function setBoxSize(wf, canvasId, size) {
  const { scope, local } = splitId(canvasId);
  const out = clone(wf);
  const g = graphAt(out, scope);
  const old = g.layout?.[local];
  const pos = old && Number.isFinite(old.x) && Number.isFinite(old.y) ? { x: old.x, y: old.y } : { x: 0, y: 0 };
  g.layout = { ...(g.layout || {}), [local]: size ? { ...pos, w: Math.round(size.w), h: Math.round(size.h) } : pos };
  return out;
}

// 整理循环框里的节点：去掉框里这一层记下的位置，重新画图时按连线自动排版。框里的框记着的大小留着。
// 留下的只有大小、没有位置，不合程序格式：画好后由调用方把这一层的位置全部写回（见 App 里 arranged 的整理）
export function arrangeBody(wf, canvasId) {
  const { scope, local } = splitId(canvasId);
  const out = clone(wf);
  const body = graphAt(out, [...scope, local]);
  const boxes = new Set((body.nodes || []).filter((n) => n.kind === "foreach").map((n) => n.id)); // 已经不在的节点的旧记录不留
  const kept = Object.entries(body.layout || {}).filter(([k]) => boxes.has(k)).map(([k, v]) => [k, keepSize(v)]).filter(([, v]) => "w" in v);
  if (kept.length) body.layout = Object.fromEntries(kept);
  else delete body.layout;
  return out;
}

// 第一次编辑前，把画布上当前的位置全部写进 layout：之后加线、删节点时其余节点不会因为重新排版而跳动。
// positions：画布 id → 该层坐标（循环体里的节点已减去循环框的边距）
export function freezeLayout(wf, positions) {
  const out = clone(wf);
  for (const [canvasId, pos] of Object.entries(positions)) {
    const { scope, local } = splitId(canvasId);
    let g;
    try {
      g = graphAt(out, scope);
    } catch {
      continue;
    }
    g.layout = { ...(g.layout || {}), [local]: { ...keepSize(g.layout?.[local]), ...round(pos) } };
  }
  return out;
}

// ---------- 工作流自己的输入、输出（画布上的“输入”“输出”端点）和名称说明 ----------
// side："inputs" 或 "outputs"。改名、删除时，连着它的线（$in.名字 / $out.名字）跟着改或删。

export const PORT_NAME = /^[a-z][a-z0-9_]*$/;
const endpoint = (side, name) => `${side === "inputs" ? "$in" : "$out"}.${name}`;

function checkName(ports, name, except) {
  if (!PORT_NAME.test(name)) return "名字只能用小写英文字母开头，后面跟小写字母、数字或下划线，例如 url、score_2";
  if (name !== except && Object.hasOwn(ports, name)) return `已经有叫 ${name} 的了`;
  return "";
}

export function addPort(wf, side, type = "string") {
  const out = clone(wf);
  out[side] = { ...(out[side] || {}) };
  const base = side === "inputs" ? "input" : "output";
  let name = base;
  for (let i = 2; Object.hasOwn(out[side], name); i++) name = `${base}_${i}`;
  out[side][name] = { type };
  return { wf: out, name };
}

export function renamePort(wf, side, from, to) {
  if (from === to) return { wf };
  const error = checkName(wf[side] || {}, to, from);
  if (error) return { error };
  const out = clone(wf);
  // 保持原来的顺序，只换这一个键
  out[side] = Object.fromEntries(Object.entries(out[side]).map(([k, v]) => [k === from ? to : k, v]));
  const a = endpoint(side, from);
  const b = endpoint(side, to);
  out.edges = (out.edges || []).map((e) => (side === "inputs" ? (e.from === a ? { ...e, from: b } : e) : (e.to === a ? { ...e, to: b } : e)));
  return { wf: out };
}

// 改类型：原来的默认值多半不再合适，一起去掉
export function setPortType(wf, side, name, type) {
  const out = clone(wf);
  const p = { ...out[side][name], type };
  if (p.type !== wf[side][name].type) delete p.default;
  out[side][name] = p;
  return out;
}

// 改说明、是否必填、默认值：fields 里值为 undefined 的项删掉
export function setPortFields(wf, side, name, fields) {
  const out = clone(wf);
  const p = { ...out[side][name] };
  for (const [k, v] of Object.entries(fields)) {
    if (v === undefined || v === "") delete p[k];
    else p[k] = v;
  }
  out[side][name] = p;
  return out;
}

export function removePort(wf, side, name) {
  const out = clone(wf);
  out[side] = { ...out[side] };
  delete out[side][name];
  const a = endpoint(side, name);
  out.edges = (out.edges || []).filter((e) => (side === "inputs" ? e.from !== a : e.to !== a));
  return out;
}

export function setMeta(wf, { name, intent }) {
  const out = clone(wf);
  if (name !== undefined) out.name = name;
  if (intent !== undefined) out.intent = intent;
  return out;
}
