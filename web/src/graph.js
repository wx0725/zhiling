// 把工作流 JSON 转成 React Flow 的节点和连线，并用 dagre 自动排版。
// 约定：画布节点 id 用“/”表示嵌套，例如循环体里的 fetch 为 "each/fetch"；
// 工作流自己的输入、输出画成两个端点节点（"$in"、"$out"），循环体里是 "each/$in"、"each/$out"。
// 工作流里保存了 layout（节点 id → {x, y}）时优先用保存的坐标，缺的才自动排版。
// 生成过程中的草稿可能缺字段或连到不存在的节点，这里只画能画的部分，不抛错。
import dagre from "@dagrejs/dagre";

export const NODE_W = 196;
export const HEAD = 46; // 节点标题区高度
const ROW = 22; // 每个端口一行
export const PAD = 28; // 循环框左、右、下的内边距
// 循环框里第一行节点到框顶的距离：标题栏（逐项循环、最多几项）下面只留一点空，四边看起来差不多宽（循环体里节点的画布位置 = 程序里的 layout 坐标 + (PAD, TOP)）
export const TOP = HEAD + 4;

export function nodeHeight(nIn, nOut) {
  return HEAD + Math.max(nIn, nOut, 1) * ROW + 12;
}
export const portTop = (i) => HEAD + i * ROW + ROW / 2;

// 以用户写的节点名、端口名为键的表一律不带原型，否则 "constructor" 这类名字会读到原型上的东西
export const dict = () => Object.create(null);
const own = (o, k) => o != null && typeof o === "object" && Object.hasOwn(o, k);
const typeOf = (p) => (p && typeof p.type === "string" ? p.type : "any");

// dagre 内部也用普通对象存节点，给它的键加前缀，避免与原型属性重名
const dk = (id) => `n:${id}`;

const short = (v) => {
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 14 ? s.slice(0, 13) + "…" : s;
};

// 节点的输入、输出端口：{name, type, fixed?}
// 模块节点只显示必填、已接线或已固定的输入，可选又没用到的（如 temperature）不画，免得淹没真正的连线
export function portsOf(node, modules, body, connected = new Set(), refPorts = {}) {
  switch (node.kind) {
    case "module": {
      const m = own(modules, node.module) ? modules[node.module] : null;
      if (!m) return { ins: [], outs: [] };
      const cfg = node.config && typeof node.config === "object" ? node.config : {};
      return {
        ins: Object.entries(m.inputs || {})
          // 画出：接了线的、写了固定值的、必填的，以及默认为空、本来就是用来接线的可选输入（如 text、value、b）。
          // 有具体默认值的设置项（超时、温度等）不画，在节点详情里改
          .filter(([name, p]) => connected.has(name) || own(cfg, name) || (p.required !== false && !("default" in p)) || p.default === null || p.default === "")
          .map(([name, p]) => ({ name, type: p.type, desc: p.description, fixed: own(cfg, name) ? short(cfg[name]) : null, full: own(cfg, name) ? cfg[name] : undefined })),
        outs: Object.entries(m.outputs || {}).map(([name, p]) => ({ name, type: p.type, desc: p.description })),
      };
    }
    case "if":
      return {
        ins: [{ name: "condition", type: "boolean", desc: "条件：真走 then，假走 else" }, { name: "value", type: "any", optional: true, desc: "要往下传的数据（可不接，只用控制线）" }],
        outs: [{ name: "then", type: "any", desc: "条件为真时走这一路" }, { name: "else", type: "any", desc: "条件为假时走这一路" }],
      };
    case "merge": {
      // ports 写错（不是文字列表）时按默认的 a、b 画，错误由校验报告
      const names = Array.isArray(node.ports) && node.ports.every((x) => typeof x === "string") ? node.ports : ["a", "b"];
      return { ins: names.map((name) => ({ name, type: "any", optional: true, desc: "各条支路之一，哪一路有值就用哪一路" })), outs: [{ name: "value", type: "any", desc: "实际走到的那一路的值" }] };
    }
    case "foreach":
      return { ins: [{ name: "items", type: `list<${own(body?.inputs, "item") ? typeOf(body.inputs.item) : "any"}>` }], outs: [{ name: "results", type: `list<${own(body?.outputs, "result") ? typeOf(body.outputs.result) : "any"}>` }] };
    default: { // subflow：内嵌的用 body，引用文件的用服务端读出的端口（/api/ports）
      if (!body && typeof node.ref === "string" && own(refPorts, node.ref)) body = refPorts[node.ref];
      const decl = (d) => (d && typeof d === "object" && !Array.isArray(d) ? Object.entries(d) : []);
      return {
        ins: decl(body?.inputs).map(([name, p]) => ({ name, type: typeOf(p), desc: p?.description })),
        outs: decl(body?.outputs).map(([name, p]) => ({ name, type: typeOf(p), desc: p?.description })),
      };
    }
  }
}

const isObj = (v) => v && typeof v === "object" && !Array.isArray(v);
const arr = (v) => (Array.isArray(v) ? v : []);
const isPos = (p) => isObj(p) && Number.isFinite(p.x) && Number.isFinite(p.y);

function layoutLevel(g, prefix, modules, parentId, refPorts) {
  const nodes = [];
  const edges = [];
  const sizes = dict();
  const dg = new dagre.graphlib.Graph();
  dg.setGraph({ rankdir: "LR", nodesep: 26, ranksep: 48, marginx: 0, marginy: 0 });
  dg.setDefaultEdgeLabel(() => ({}));
  const id = (local) => (prefix ? `${prefix}/${local}` : local);

  // 端点节点：工作流（或循环体）的输入和输出
  const inPorts = Object.entries(isObj(g.inputs) ? g.inputs : {}).map(([name, p]) => ({ name, type: typeOf(p), desc: p?.description }));
  const outPorts = Object.entries(isObj(g.outputs) ? g.outputs : {}).map(([name, p]) => ({ name, type: typeOf(p), desc: p?.description }));
  const terminals = [
    { local: "$in", kind: "terminal_in", ins: [], outs: inPorts, title: prefix ? "每一项" : "输入" },
    { local: "$out", kind: "terminal_out", ins: outPorts, outs: [], title: prefix ? "这一项的结果" : "输出" },
  ];
  for (const t of terminals) {
    if (!t.ins.length && !t.outs.length) continue;
    const h = nodeHeight(t.ins.length, t.outs.length);
    sizes[t.local] = { w: 120, h };
    dg.setNode(dk(t.local), { width: 120, height: h });
    nodes.push({ id: id(t.local), type: "terminal", data: { title: t.title, ins: t.ins, outs: t.outs, terminal: t.kind, width: 120, height: h }, parentId });
  }

  const gNodes = arr(g.nodes).filter((n) => isObj(n) && typeof n.id === "string" && n.id);
  const known = new Set(gNodes.map((n) => n.id));
  for (const t of terminals) if (sizes[t.local]) known.add(t.local);
  // 只画两端都存在的连线
  const gEdges = arr(g.edges).filter((e) => {
    if (!isObj(e) || typeof e.from !== "string" || typeof e.to !== "string" || !e.from.includes(".")) return false;
    return known.has(e.from.split(".")[0]) && known.has(e.to.split(".")[0]);
  });

  const connected = dict(); // 节点 → 接了线的输入端口
  for (const e of gEdges) {
    if (!e.to.includes(".")) continue;
    const [tn, tp] = e.to.split(".");
    (connected[tn] ||= new Set()).add(tp);
  }

  const saved = isObj(g.layout) ? g.layout : {};
  for (const n of gNodes) {
    const body = isObj(n.body) ? n.body : null;
    const { ins, outs } = portsOf(n, modules, body, connected[n.id], refPorts);
    let w = NODE_W;
    let h = nodeHeight(ins.length, outs.length);
    let children = null;
    let manual = null;
    let offset = null;
    if (n.kind === "foreach" && body) {
      children = layoutLevel(body, id(n.id), modules, id(n.id), refPorts);
      const m = own(saved, n.id) ? saved[n.id] : null;
      if (isObj(m) && Number.isFinite(m.w) && Number.isFinite(m.h) && m.w > 0 && m.h > 0) {
        // 关掉了自动收缩的框：layout 里记着 w、h，框至少这么大（装不下框里的节点时照样变大），不自动回缩，可以拉伸。
        // 旧数据里框内节点可能在负坐标（框左边、上边之外）：整体右移、下移，框把它们全部装下
        manual = { w: m.w, h: m.h };
        children.shift = { x: Math.max(0, -children.minX), y: Math.max(0, -children.minY) };
        w = Math.max(NODE_W, children.width + children.shift.x + PAD * 2, m.w);
        h = Math.max(children.height + children.shift.y + PAD + TOP, m.h);
      } else {
        // 开着自动收缩的框（默认）：四边各自从外往对角线交点收，收到碰到框里的节点为止，即正好包住框里的节点（留出内边距）。
        // 框画在 layout 位置再加上框里节点的最小坐标（offset），框里节点相应减去它，所以框里节点在画布上的位置不受影响
        children.shift = { x: -children.minX, y: -children.minY };
        offset = { x: children.minX, y: children.minY };
        w = Math.max(NODE_W, children.width - children.minX + PAD * 2);
        h = children.height - children.minY + PAD + TOP;
      }
    }
    sizes[n.id] = { w, h };
    dg.setNode(dk(n.id), { width: w, height: h });
    const shift = children?.shift || { x: 0, y: 0 };
    nodes.push({
      id: id(n.id),
      type: n.kind === "foreach" ? "loop" : "module",
      data: { node: n, ins, outs, module: n.kind === "module" ? modules[n.module] : null, width: w, height: h, shift, manual, offset },
      parentId, // 不限制在框内：框里的节点拖到框外时，框会扩大把它装下（见 collide.js）
      style: n.kind === "foreach" ? { width: w, height: h } : undefined,
    });
    if (children) {
      for (const c of children.nodes) {
        if (c.parentId === id(n.id)) c.position = { x: c.position.x + children.shift.x + PAD, y: c.position.y + children.shift.y + TOP };
      }
      nodes.push(...children.nodes);
      edges.push(...children.edges);
    }
  }

  for (const [i, e] of gEdges.entries()) {
    const [sn, sp] = e.from.split(".");
    const control = !e.to.includes(".");
    const [tn, tp] = control ? [e.to, null] : e.to.split(".");
    dg.setEdge(dk(sn), dk(tn));
    edges.push({
      id: `${id("e" + i)}`,
      source: id(sn),
      sourceHandle: `out:${sp}`,
      target: id(tn),
      targetHandle: control ? "ctrl" : `in:${tp}`,
      type: "default", // 贝塞尔弧线：跟着节点位置自动弯曲
      data: { control, from: e.from, to: e.to, scope: prefix ? prefix.split("/") : [] },
      className: control ? "edge-control" : "edge-data",
    });
  }

  dagre.layout(dg);
  let width = -Infinity; // 框里节点的最右、最下边界（可能是负数：旧数据里节点全在左上方）
  let height = -Infinity;
  let minX = Infinity;
  let minY = Infinity;
  for (const nd of nodes) {
    const local = nd.id.slice(prefix ? prefix.length + 1 : 0);
    const p = dg.node(dk(local));
    if (!p || nd.position) continue;
    const s = sizes[local];
    // 自动伸缩的框：保存过位置时画在 layout 位置加 offset 处；没保存过时直接用自动排版的位置（排版已按框的实际大小留好间距），
    // 写回时减去 offset，下次再加回来，位置不变
    const off = nd.data?.offset || { x: 0, y: 0 };
    nd.position = own(saved, local) && isPos(saved[local]) ? { x: saved[local].x + off.x, y: saved[local].y + off.y }
      : { x: p.x - s.w / 2, y: p.y - s.h / 2 };
    width = Math.max(width, nd.position.x + s.w);
    height = Math.max(height, nd.position.y + s.h);
    minX = Math.min(minX, nd.position.x);
    minY = Math.min(minY, nd.position.y);
  }
  if (minX === Infinity) minX = minY = width = height = 0;
  return { nodes, edges, width, height, minX, minY };
}

// 画布位置 → 程序里 layout 的坐标：循环体里的节点去掉框的边距，以及画图时为装下负坐标而整体移动的量（框的 data.shift）。
// 与 toFlow 的换算互为逆运算，所以无论 layout 里是否有旧的负坐标，写回去再画出来位置都不变。
// 返回 (画布节点, 位置 = 它当前的位置) => layout 坐标
export function flowToLayout(flowNodes) {
  const byId = new Map(flowNodes.map((n) => [n.id, n]));
  return (n, p = n.position) => {
    const off = n.data?.offset || { x: 0, y: 0 }; // 自动伸缩的框自己画的时候加过 offset
    const q = { x: p.x - off.x, y: p.y - off.y };
    if (!n.parentId) return q;
    const s = byId.get(n.parentId)?.data?.shift || { x: 0, y: 0 };
    return { x: q.x - PAD - s.x, y: q.y - TOP - s.y };
  };
}

// 一批画布上的新位置（{画布 id: 位置}）→ 要写进 layout 的坐标（{画布 id: layout 坐标}）。
// 某个框画图时整体移动过（data.shift 不为零）而这次只动了框里的一部分节点：最小坐标会变、下次画图的移动量也会变，
// 所以把这一层的节点全部按不移动（shift 为零）重新写一遍，写完这一层就没有负坐标了，下次画出来位置不变
export function layoutMoves(flowNodes, moves) {
  const all = dict();
  for (const [id, p] of Object.entries(moves)) all[id] = p;
  // 只有关掉自动收缩的框需要这样：自动伸缩的框每次画图都按最小坐标重新对齐（offset），写一部分节点也不会错位
  const shifted = new Set(flowNodes.filter((n) => n.data?.manual && n.data?.shift && (n.data.shift.x || n.data.shift.y)).map((n) => n.id));
  // 动到的节点往上任何一层是这样的框（后代动了也会改变它的最小坐标），这一层都要整层重写
  const byId = new Map(flowNodes.map((n) => [n.id, n]));
  const touched = new Set();
  for (const id of Object.keys(all)) {
    for (let p = byId.get(id)?.parentId; p; p = byId.get(p)?.parentId) if (shifted.has(p)) touched.add(p);
  }
  for (const n of flowNodes) if (touched.has(n.parentId) && !(n.id in all)) all[n.id] = n.position;
  const conv = flowToLayout(flowNodes);
  const out = dict();
  for (const n of flowNodes) {
    if (!(n.id in all)) continue;
    const p = all[n.id];
    out[n.id] = touched.has(n.parentId) ? conv({ ...n, parentId: undefined }, { x: p.x - PAD, y: p.y - TOP }) : conv(n, p);
  }
  return out;
}

export function toFlow(wf, modules, refPorts = {}) {
  if (!isObj(wf)) return { nodes: [], edges: [] };
  const { nodes, edges } = layoutLevel(wf, "", modules, undefined, refPorts);
  // React Flow 要求父节点排在子节点之前
  nodes.sort((a, b) => (a.parentId ? a.parentId.split("/").length : 0) - (b.parentId ? b.parentId.split("/").length : 0));
  return { nodes, edges };
}

// 运行记录的路径（如 "summarize_many.each[1].fetch"）→ 画布节点 id（"each/fetch"）与第几项
export function pathToId(path, wfId) {
  const rest = path.startsWith(wfId + ".") ? path.slice(wfId.length + 1) : path;
  const items = [...rest.matchAll(/\[(\d+)\]/g)].map((m) => Number(m[1]));
  return { id: rest.replace(/\[\d+\]/g, "").split(".").join("/"), items };
}

// 图里（含循环体、内嵌子流程）用到的、会写文件或产生外部影响的模块，例如 ["file.write@0"]。运行前要使用者确认
export function writeModules(wf, modules) {
  const found = new Set();
  const walk = (g) => {
    for (const n of Array.isArray(g?.nodes) ? g.nodes : []) {
      if (n?.kind === "module" && own(modules, n.module) && modules[n.module].side_effects === "write") found.add(n.module);
      if (n?.body && typeof n.body === "object") walk(n.body);
    }
  };
  walk(wf);
  return [...found].sort();
}

// 服务端因为没确认而拦下的写模块：not_confirmed 的报错是“模块 file.write@0 会写文件或产生外部影响，未获得确认”
export function unconfirmedModule(message) {
  const m = typeof message === "string" ? message.match(/模块 (\S+@\d+) 会写文件/) : null;
  return m ? m[1] : null;
}
