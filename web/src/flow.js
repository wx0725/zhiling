// 前端流程上的小规则，单独放出来便于测试。

// 生成过程中的工作流能否交给画布：只有通过结构校验的版本才画（未通过的可能缺字段，画出来会出错）
// 没有模块节点、只有输入直通输出的图也是合法工作流；只有“无法实现”的回答不画
export function drawable(ev) {
  if (!ev?.workflow || !Array.isArray(ev.workflow.nodes) || isRefusal(ev.workflow)) return false;
  if (ev.type === "validated") return Array.isArray(ev.errors) && ev.errors.length === 0;
  if (ev.type === "done") return ev.valid === true;
  return false;
}

// 只认最新一次请求：切换工作流或重新运行后，旧请求晚到的事件被丢弃
export function latest() {
  let n = 0;
  return { next: () => ++n, isCurrent: (t) => t === n };
}

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// 生成器的“无法实现”回答：没有节点，名字以“无法实现”开头
export const isRefusal = (wf) => isObj(wf) && (wf.id === "not_feasible" || ((wf.nodes || []).length === 0 && String(wf.name || "").startsWith("无法实现")));

// 读进来的工作流先整理成页面能安全使用的形状：坏的端口声明不交给表单和画布，记为问题显示出来。
// 运行和保存仍发送原文，由后端给出完整的校验结果。
export function inspectWorkflow(wf) {
  const problems = [];
  if (!isObj(wf)) {
    return { wf: { id: "", name: "无法读取的工作流", intent: "", inputs: {}, outputs: {}, nodes: [], edges: [] },
             problems: ["文件内容不是一个工作流对象"] };
  }
  const ports = (decl, which) => {
    if (!isObj(decl)) {
      problems.push(decl === undefined ? `缺少 ${which}` : `${which} 应当是对象`);
      return {};
    }
    const out = {};
    for (const [k, p] of Object.entries(decl)) {
      if (isObj(p) && typeof p.type === "string") out[k] = p;
      else problems.push(`${which}.${k} 的声明不合法，应当写成 {"type": "类型"}`);
    }
    return out;
  };
  const clean = {
    ...wf,
    id: typeof wf.id === "string" ? wf.id : "",
    name: typeof wf.name === "string" ? wf.name : "未命名工作流",
    intent: typeof wf.intent === "string" ? wf.intent : "",
    inputs: ports(wf.inputs, "inputs"),
    outputs: ports(wf.outputs, "outputs"),
  };
  if (!Array.isArray(wf.nodes)) problems.push("nodes 应当是列表");
  if (!Array.isArray(wf.edges)) problems.push("edges 应当是列表");
  return { wf: clean, problems };
}

// 画布上的节点 id：顶层是 "fetch"，循环体里是 "each/fetch"
export function canvasIds(wf) {
  const ids = [];
  const walk = (g, prefix) => {
    for (const n of Array.isArray(g?.nodes) ? g.nodes : []) {
      if (!isObj(n) || typeof n.id !== "string") continue;
      const id = prefix ? `${prefix}/${n.id}` : n.id;
      ids.push(id);
      if (n.kind === "foreach" && isObj(n.body)) walk(n.body, id);
    }
  };
  walk(wf, "");
  return ids;
}

// 一条问题文字（结构校验、运行出错、审查意见）说的是图上哪些节点
// 认得的写法：“节点 fetch：”“节点 wf.each[1].fetch：”“连线 3（a.text → b.value）”
export function issueTargets(issue, wf, ids = canvasIds(wf)) {
  const set = new Set(ids);
  const found = new Set();
  const resolve = (token) => {
    let t = String(token).replace(/[(（].*$/, "");
    if (wf?.id && t.startsWith(`${wf.id}.`)) t = t.slice(wf.id.length + 1);
    t = t.replace(/\[\d+\]/g, "").split(".").join("/");
    if (!t || t.startsWith("$")) return;
    if (set.has(t)) return found.add(t);
    const last = t.split("/").pop();
    const same = ids.filter((id) => id.split("/").pop() === last);
    if (same.length === 1) found.add(same[0]);
  };
  for (const m of String(issue).matchAll(/节点\s*([^\s：:，,；;（(]+)/g)) resolve(m[1]);
  for (const m of String(issue).matchAll(/（([^\s.（）]+)\.[^\s]+\s*→\s*([^\s.（）]+)[.）]/g)) {
    resolve(m[1]);
    resolve(m[2]);
  }
  return [...found];
}

// 问题文字给人看的写法：去掉开头程序里的位置前缀（工作流 id、“> loop.body”），改成“循环 loop 里：”
export function issueText(issue) {
  let t = String(issue);
  const m = t.match(/^[A-Za-z_][\w-]*((?: > [^：]+)*)：/);
  if (!m) return t;
  const scopes = [...m[1].matchAll(/ > ([\w-]+)\.body/g)].map((x) => x[1]);
  const rest = t.slice(m[0].length);
  if (m[1] && !scopes.length) return t; // 认不出的写法保持原样
  return (scopes.length ? scopes.map((s) => `循环 ${s} 里`).join("、") + "：" : "") + rest;
}

// ---------- 开始页：理清需求 ----------
export const CARD_PARTS = [
  ["given", "给它什么"],
  ["wanted", "要它产出什么"],
  ["steps", "中间怎么做"],
  ["cases", "特殊情况"],
];

// 使用者改过卡片后，把卡片写成一段需求交给生成（模型给的描述已不再对应卡片）
export function cardToRequirement(card) {
  const part = (k) => (card?.[k] || []).map((x) => String(x).trim()).filter(Boolean);
  const out = [];
  if (part("given").length) out.push(`给它：${part("given").join("；")}。`);
  if (part("wanted").length) out.push(`要它产出：${part("wanted").join("；")}。`);
  if (part("steps").length) out.push(`做法：${part("steps").join("，然后")}。`);
  if (part("cases").length) out.push(`特殊情况：${part("cases").join("；")}。`);
  return out.join("");
}

// 本轮的回答（问题序号 → 选的或自己写的答案）并入之前的问答；同一个问题以最新回答为准
export function mergeAnswers(previous, questions, chosen) {
  const next = (previous || []).filter((a) => !questions.some((q) => q.ask === a.ask));
  questions.forEach((q, i) => {
    const answer = String(chosen?.[i] ?? "").trim();
    if (answer) next.push({ ask: q.ask, answer });
  });
  return next;
}

// ---------- 从空白开始：画布上的分步清单 ----------
// 按程序当前的状态判断每一步是否完成；current 是第一个没完成的步骤
export function guideSteps(wf, checkErrors = [], ranOnce = false) {
  const ins = Object.keys(isObj(wf?.inputs) ? wf.inputs : {});
  const outs = Object.keys(isObj(wf?.outputs) ? wf.outputs : {});
  const nodes = Array.isArray(wf?.nodes) ? wf.nodes : [];
  const edges = Array.isArray(wf?.edges) ? wf.edges : [];
  const fed = (o) => edges.some((e) => e?.to === `$out.${o}`);
  const steps = [
    { key: "io", title: "设好输入和输出", how: "点画布上的“输入”“输出”，改名字、类型和说明：输入是运行时你要提供的，输出是最后要得到的。",
      done: ins.length > 0 && outs.length > 0 },
    { key: "node", title: "加一个节点", how: "点上方“添加节点”，选一个模块，例如“抓取网页”“思考”“转成文字”。",
      done: nodes.length > 0 },
    { key: "wire", title: "把线连起来", how: "从左边节点右侧的插孔拖到右边节点左侧的插孔：从“输入”连进节点，再从节点连到“输出”。",
      done: nodes.length > 0 && outs.length > 0 && outs.every(fed) && edges.some((e) => String(e?.from).startsWith("$in.")) },
    { key: "valid", title: "改到没有问题", how: "按标题下列出的问题修改：点节点可以填参数，必填的参数要么填值、要么接线。",
      done: false },
    { key: "run", title: "运行一次", how: "在右侧“运行”里填好输入，点“运行”，看节点上的灯和结果。",
      done: ranOnce },
  ];
  steps[3].done = steps[2].done && checkErrors.length === 0;
  const current = steps.findIndex((s) => !s.done);
  return { steps, current };
}
