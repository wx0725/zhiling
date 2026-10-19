import { Component, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlow, Background, useNodesState, useEdgesState } from "@xyflow/react";
import { BuildGuide, InitialView, Overview, ZoomBar } from "./canvas.jsx";
import { freeSpot } from "./viewport.js";
import { getJSON, postJSON, sendJSON, stream } from "./api.js";
import { toFlow, flowToLayout, layoutMoves, pathToId, dict, writeModules, unconfirmedModule, PAD, TOP } from "./graph.js";
import { addNode, addPort, arrangeBody, connect, freezeLayout, graphAt, setBoxSize, removeEdge, removeNode, removePort, renamePort, setConfig, setLabel, setMaxItems, setMeta, setPortFields, setPortType, splitId } from "./edit.js";
import { AddMenu, EdgeDetail, MetaEditor, PortsEditor, ProgramView } from "./editor.jsx";
import { RunStatus } from "./runStatus.js";
import { canResize, fitSize, fitWhileDragging, guardChanges, rectOf, settle } from "./collide.js";
import { StartPage } from "./start.jsx";
import { useConfirm } from "./dialog.jsx";
import { drawable, guideSteps, latest, inspectWorkflow, issueTargets, issueText } from "./flow.js";
import { nodeTypes, StatusContext, IssueContext, BoxEditContext } from "./nodes.jsx";
import { RunPanel, GenerationLog, NodeDetail } from "./panels.jsx";
import { ProjectList } from "./projects.jsx";

export default function App() {
  const [modules, setModules] = useState({});
  const [list, setList] = useState([]);
  const [ask, confirmDialog] = useConfirm(); // 屏幕中央的确认框
  const [current, setCurrent] = useState(null); // { wf：整理后供页面使用, raw：原文（运行、保存时发送）, problems, source, file }
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selected, setSelected] = useState(null);
  const [hovered, setHovered] = useState(null);
  const [tab, setTab] = useState("run");

  // 运行事件先写进 RunStatus（每条 O(1)），每隔一小段时间才发布一次快照给画布（Q02）
  const runStatus = useRef(new RunStatus(""));
  const flushTimer = useRef(null);
  const [nodeStatus, setNodeStatus] = useState(() => dict());
  const flushStatus = useCallback(() => {
    clearTimeout(flushTimer.current);
    flushTimer.current = null;
    setNodeStatus(runStatus.current.snapshot());
  }, []);
  const resetStatus = useCallback((wfId) => {
    clearTimeout(flushTimer.current);
    flushTimer.current = null;
    runStatus.current = new RunStatus(wfId);
    setNodeStatus(dict());
  }, []);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  const [requirement, setRequirement] = useState("");
  const [check, setCheck] = useState(true);
  const [genEvents, setGenEvents] = useState([]);
  const [generating, setGenerating] = useState(false);
  const abortRef = useRef(null);
  const runAbortRef = useRef(null);
  const runSeq = useRef(latest()); // F04：只认最新一次运行
  const openSeq = useRef(latest()); // N07：只认最后一次打开
  const [refPorts, setRefPorts] = useState({}); // 引用文件的子流程 → 它的输入输出
  const [notice, setNotice] = useState("");
  const [viewSeq, setViewSeq] = useState(0); // 每换一次图（包括生成中的新草稿）就重新设置初始视野
  // 左侧列表、右侧面板可以收起，给画布腾地方；记在本机浏览器里
  const [sideOpen, setSideOpen] = useState(() => remembered("wf.side", true));
  const [panelOpen, setPanelOpen] = useState(() => remembered("wf.panel", true));
  useEffect(() => remember("wf.side", sideOpen), [sideOpen]);
  useEffect(() => remember("wf.panel", panelOpen), [panelOpen]);

  const refreshList = useCallback(() => getJSON("/api/workflows").then(setList), []);
  useEffect(() => {
    getJSON("/api/modules").then((ms) => setModules(Object.fromEntries(ms.map((m) => [m.key, m]))));
    refreshList();
    // 链接里带 ?wf=examples/xxx.workflow.json 时直接打开
    const wf = new URLSearchParams(location.search).get("wf");
    const m = wf && wf.match(/^(examples|saved)\/([\w-]+\.workflow\.json)$/);
    if (m) open({ source: m[1], file: m[2] });
  }, [refreshList]); // eslint-disable-line react-hooks/exhaustive-deps

  // 画布上正显示生成过程中哪一轮的草稿（{round, wf}）；打开别的工作流时清空
  const [drawn, setDrawn] = useState(null);
  const flow = useRef(null); // React Flow 实例，用来把画布定位到某个节点

  // ---------- 编辑：每次改动都改工作流 JSON（current.raw），再从它重新画图 ----------
  const history = useRef({ past: [], future: [] }); // 撤销、重做：保存改动前后的整份 JSON
  const [selectedEdge, setSelectedEdge] = useState(null); // 选中的连线（edge.data + id）
  const [checkErrors, setCheckErrors] = useState([]); // 改动后自动校验的结果
  const nodesRef = useRef([]);
  // 确认框是异步的：等使用者回答期间工作流可能已经换了（生成完成、别的操作），回答后要先核对再继续
  const currentRef = useRef(null);
  const tidyRef = useRef(null); // 改动后等图画好再整理重叠：{raw, focus}
  const builtRef = useRef(null); // 画布上的节点是按哪一份程序画的
  const guardedPos = useRef(new Map()); // 拖动中经过碰撞修正的位置（松手时以它为准）
  const dragBase = useRef(new Map()); // 拖动开始时各循环框的尺寸
  const selectedRef = useRef(null);
  const multiRef = useRef(new Set()); // “选中框里全部”选中的一组节点：重新画图后仍保持选中
  const selectedEdgeRef = useRef(null);

  // ---------- 项目：左侧项目栏里的一个个工作区。生成、打开、新建空白都落在正打开的项目里（在首页时先建一个） ----------
  const [projects, setProjects] = useState([]);
  const [project, setProject] = useState(null); // 正打开的项目编号；null：首页
  const projectRef = useRef(null);
  const creating = useRef(null); // 正在新建的项目：同时触发两次时只建一个
  const [genProject, setGenProject] = useState(null); // 正在生成的是哪个项目：项目栏里亮一个点
  const genProjectRef = useRef(null);
  const instSeq = useRef(0);
  const refreshProjects = useCallback(() => getJSON("/api/projects").then(setProjects).catch(() => {}), []);
  useEffect(() => { refreshProjects(); }, [refreshProjects]);
  const enter = (pid) => { projectRef.current = pid; setProject(pid); };

  // dirty：打开项目时恢复“有改动，还没保存进我保存的”
  const show = useCallback((wf, source, file, { dirty = false } = {}) => {
    setDrawn(null);
    history.current = { past: [], future: [] };
    setSelectedEdge(null);
    multiRef.current = new Set();
    setCheckErrors([]);
    runSeq.current.next(); // 换了工作流，旧的运行结果作废
    openSeq.current.next(); // 还没回来的“打开”请求也作废
    runAbortRef.current?.abort();
    setRunning(false);
    const { wf: clean, problems } = inspectWorkflow(wf);
    instSeq.current += 1; // 换了一份工作流（编辑不换）：晚到的“保存到我的”响应只认发起保存的那一份
    setCurrent({ wf: clean, raw: wf, problems, source, file, dirty, frozen: false, project: projectRef.current, inst: instSeq.current });
    setRanOk(false);
    setViewSeq((n) => n + 1);
    resetStatus(clean.id);
    setResult(null);
    setSelected(null);
  }, []);

  // 工作流或模块信息变化时重新排版
  // 画图转换出错时只在画布区显示原因，侧栏和右侧面板照常可用
  const [canvasError, setCanvasError] = useState("");
  useEffect(() => {
    if (!current?.wf || !Object.keys(modules).length) return;
    try {
      const { nodes, edges } = toFlow(current.wf, modules, refPorts);
      // 记下这一版画出的节点（按 data 对象认）：整理时要确认手上的节点正是按这一版程序画的，而不是还没换掉的旧节点
      builtRef.current = { raw: current.raw, data: new Map(nodes.map((n) => [n.id, n.data])) };
      // 重新画图后保留选中状态
      setNodes(nodes.map((n) => (n.id === selectedRef.current || multiRef.current.has(n.id) ? { ...n, selected: true } : n)));
      setEdges(edges.map((e) => (e.id === selectedEdgeRef.current ? { ...e, selected: true } : e)));
      setCanvasError("");
    } catch (e) {
      setNodes([]);
      setEdges([]);
      setCanvasError(String(e?.message || e));
    }
  }, [current, modules, refPorts, setNodes, setEdges]);

  // 引用文件的子流程：向服务端要它们的输入输出，才能画出插孔（F16）
  useEffect(() => {
    if (!current?.wf) return;
    let alive = true;
    setRefPorts({});
    postJSON("/api/ports", { workflow: current.wf, source: current.source }).then((p) => alive && setRefPorts(p)).catch(() => {});
    return () => { alive = false; };
  }, [current]);

  // 项目内容自动存到电脑上：停手半秒后存一次；只在内容真的变了时存（单是打开项目不算改动，不会把它顶到最前面）。
  // unsynced：每个项目还没确认存好的最新内容。存好之前切回这个项目，用的是这一份而不是服务端的旧版本；
  // 存失败了留着，隔几秒再试；关页面时把它们都发出去。rev 只增不减，服务端不让旧内容覆盖新内容
  const lastStored = useRef(null);
  const unsynced = useRef(new Map()); // pid → { body, rev }
  const latestBody = useRef(new Map()); // pid → { body, rev, inst }：本机最近一次交出去存的内容（存好了也留着：切回时比服务端新就用它）
  const inflight = useRef(new Map()); // pid → 正在发的请求：同一个项目同时只发一个
  const storeTimer = useRef(null);
  const retryTimer = useRef(null);
  const revSeq = useRef(0);
  // rev 取“当前时间”和“见过的最大编号 + 1”中较大的：电脑时间往回调了也照样递增
  const bumpRev = (floor = 0) => {
    revSeq.current = Math.max(Date.now() * 1000, revSeq.current + 1, floor + 1);
    return revSeq.current;
  };
  const stage = (pid, body, inst = null) => {
    const rev = bumpRev();
    unsynced.current.set(pid, { body, rev });
    latestBody.current.set(pid, { body, rev, inst });
  };
  const pushProject = (pid) => {
    if (inflight.current.has(pid)) return inflight.current.get(pid);
    const entry = unsynced.current.get(pid);
    if (!entry) return Promise.resolve();
    const done = () => { if (unsynced.current.get(pid) === entry) unsynced.current.delete(pid); };
    const req = sendJSON("PUT", `/api/projects/${pid}`, { ...entry.body, rev: entry.rev })
      .then(() => { done(); refreshProjects(); }, (e) => {
        if (e.status === 404) return done(); // 项目已删除
        if (e.status === 409) { // 服务端的编号更大（另一个窗口存过，或电脑时间往回调了）：换个更大的编号重发本机这份
          return getJSON(`/api/projects/${pid}`).then((doc) => {
            if (unsynced.current.get(pid) !== entry) return;
            const rev = bumpRev(Number.isFinite(doc.rev) ? doc.rev : 0);
            const next = { body: entry.body, rev };
            unsynced.current.set(pid, next);
            const last = latestBody.current.get(pid);
            if (last?.body === entry.body) latestBody.current.set(pid, { ...last, rev });
          }).catch(() => {});
        }
        setNotice(`没能存进项目，稍后自动重试：${e.message}`);
        clearTimeout(retryTimer.current);
        retryTimer.current = setTimeout(flushStore, 3000);
      })
      .finally(() => {
        inflight.current.delete(pid);
        const now = unsynced.current.get(pid);
        if (now && now !== entry) pushProject(pid); // 发的时候又有了新改动
      });
    inflight.current.set(pid, req);
    return req;
  };
  const flushStore = () => {
    clearTimeout(storeTimer.current);
    storeTimer.current = null;
    return Promise.all([...unsynced.current.keys()].map(pushProject));
  };
  useEffect(() => {
    const c = current;
    const l = lastStored.current;
    if (!c?.project || (l && l.project === c.project && l.raw === c.raw && l.source === c.source && l.file === c.file && l.dirty === c.dirty)) return;
    lastStored.current = { project: c.project, raw: c.raw, source: c.source, file: c.file, dirty: c.dirty };
    stage(c.project, { workflow: c.raw, source: c.source, file: c.file, dirty: c.dirty }, c.inst);
    clearTimeout(storeTimer.current);
    storeTimer.current = setTimeout(flushStore, 500);
  }, [current]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // 页面正在关闭：等不到正在发的请求，把每个项目最新的内容直接发出去，并要求关闭后也发完（rev 保证不会被旧的覆盖）
    const away = () => {
      for (const [pid, entry] of unsynced.current) {
        sendJSON("PUT", `/api/projects/${pid}`, { ...entry.body, rev: entry.rev }, { keepalive: true }).catch(() => {});
      }
    };
    window.addEventListener("pagehide", away);
    return () => window.removeEventListener("pagehide", away);
  }, []);

  // 没有打开项目时（首页）先建一个，返回它的编号。建好时这次动作已经过时（isCurrent() 为假：之后回了首页、切了项目）
  // 就返回 null，调用方放弃这次动作；建好的项目留在列表里，但不抢占当前页面
  const ensureProject = async (body, isCurrent) => {
    if (projectRef.current) return projectRef.current;
    if (!creating.current) creating.current = postJSON("/api/projects", body).finally(() => { creating.current = null; });
    const p = await creating.current;
    refreshProjects();
    if (projectRef.current) return projectRef.current === p.id ? p.id : null;
    if (!isCurrent()) return null;
    enter(p.id);
    return p.id;
  };

  // 画布回到开始页（首页，或还没有内容的项目）
  const clearCanvas = () => {
    setDrawn(null);
    history.current = { past: [], future: [] };
    runSeq.current.next();
    openSeq.current.next();
    runAbortRef.current?.abort();
    setRunning(false);
    setCurrent(null);
    setResult(null);
    setSelected(null);
    setSelectedEdge(null);
    setCheckErrors([]);
    resetStatus("");
    setNodes([]);
    setEdges([]);
    setNotice("");
  };

  const goHome = () => {
    flushStore();
    enter(null);
    clearCanvas();
    setRequirement("");
  };

  const newProject = async () => {
    flushStore();
    const token = openSeq.current.next();
    let p;
    try {
      p = await postJSON("/api/projects", {});
    } catch (e) {
      setNotice(`没能新建项目：${e.message}`);
      return;
    }
    refreshProjects();
    if (!openSeq.current.isCurrent(token)) return;
    enter(p.id);
    clearCanvas();
    setRequirement("");
  };

  const openProject = async (pid) => {
    if (pid === projectRef.current) return;
    flushStore();
    const token = openSeq.current.next();
    let doc;
    try {
      doc = await getJSON(`/api/projects/${pid}`);
    } catch {
      if (openSeq.current.isCurrent(token)) setNotice("没能打开这个项目，它可能已经被删除了");
      refreshProjects();
      return;
    }
    if (!openSeq.current.isCurrent(token)) return; // 之后又点了别的项目
    enter(pid);
    setRequirement(typeof doc.requirement === "string" ? doc.requirement : "");
    // 本机最近交出去的这份比服务端读到的新（还没存好，或读的请求比保存先到）就用本机的
    const serverRev = Number.isFinite(doc.rev) ? doc.rev : 0;
    bumpRev(serverRev); // 之后的保存编号一定比服务端存着的大
    const mine = latestBody.current.get(pid);
    const local = mine && mine.rev >= serverRev ? mine.body : null;
    const wf = local ? local.workflow : doc.workflow;
    if (wf && typeof wf === "object" && !Array.isArray(wf)) {
      const source = (local ? local.source : doc.source) || "new";
      const file = (local ? local.file : doc.file) || null;
      const dirty = !!(local ? local.dirty : doc.dirty);
      lastStored.current = { project: pid, raw: wf, source, file, dirty }; // 不是新改动，不用再存一遍
      show(wf, source, file, { dirty });
      if (local) latestBody.current.set(pid, { ...mine, inst: instSeq.current }); // 画布上这一份就是本机记着的那份
      setTab("run");
    } else {
      clearCanvas();
    }
  };

  const renameProject = (pid, name) => {
    setProjects((ps) => ps.map((p) => (p.id === pid ? { ...p, name } : p)));
    sendJSON("PUT", `/api/projects/${pid}`, { name })
      .then(() => refreshProjects(), (e) => { setNotice(`没能改名：${e.message}`); refreshProjects(); });
  };

  const deleteProject = async (p) => {
    const ok = await ask({ title: `删除项目“${p.name}”？`, message: "项目里的工作流和还没保存的改动都会删掉，不能恢复。已经存进“我保存的”的工作流不受影响。", ok: "删除", cancel: "留下", danger: true });
    if (!ok) return;
    unsynced.current.delete(p.id);
    latestBody.current.delete(p.id);
    if (genProjectRef.current === p.id) abortRef.current?.abort(); // 正在给它生成：停下
    if (projectRef.current === p.id) goHome();
    try {
      await inflight.current.get(p.id)?.catch(() => {});
      await sendJSON("DELETE", `/api/projects/${p.id}`);
    } catch (e) {
      if (e.status !== 404) setNotice(`没能删除：${e.message}`);
    }
    refreshProjects();
  };

  const newBlank = async () => {
    const at = currentRef.current;
    if (!(await confirmLeave()) || currentRef.current !== at) return;
    const blank = {
      spec: "0.2", id: "my_workflow", name: "新工作流", intent: "",
      inputs: { input: { type: "string", description: "运行时要提供的内容" } },
      outputs: { output: { type: "string", description: "最后得到的结果" } },
      nodes: [], edges: [],
      layout: { $in: { x: 0, y: 0 }, $out: { x: 620, y: 0 } }, // 左右摆开，中间留出放节点的地方
    };
    const token = openSeq.current.next();
    let pid;
    try {
      pid = await ensureProject({ workflow: blank, source: "new" }, () => openSeq.current.isCurrent(token));
    } catch (e) {
      setNotice(`没能新建项目：${e.message}`);
      return;
    }
    if (!pid || !openSeq.current.isCurrent(token)) return;
    show(blank, "new", null);
    setTab("node");
    setPanelOpen(true);
  };

  const open = async (item) => {
    const at = currentRef.current;
    if (!(await confirmLeave()) || currentRef.current !== at) return;
    const token = openSeq.current.next();
    let wf;
    try {
      wf = await getJSON(`/api/workflows/${item.source}/${item.file}`);
    } catch (e) {
      if (openSeq.current.isCurrent(token)) setNotice(`没能打开：${e.message}`);
      return;
    }
    if (!openSeq.current.isCurrent(token)) return; // 用户之后又打开了别的，晚到的旧响应不换图
    let pid;
    try {
      pid = await ensureProject({ workflow: wf, source: item.source, file: item.file }, () => openSeq.current.isCurrent(token));
    } catch (e) {
      setNotice(`没能新建项目：${e.message}`);
      return;
    }
    if (!pid || !openSeq.current.isCurrent(token)) return;
    show(wf, item.source, item.file);
    setTab("run");
  };

  const statuses = useMemo(() => {
    const s = Object.assign(dict(), nodeStatus);
    if (running || result) s["$in"] = { state: "completed" };
    if (result) s["$out"] = { state: result.status === "completed" ? "completed" : "skipped" };
    return s;
  }, [nodeStatus, result, running]);

  // 运行之后，信号走过的连线加深，没走到的变淡
  const recordsById = useMemo(() => {
    const m = dict();
    for (const r of result?.records || []) (m[pathToId(r.path, current.wf.id).id] ||= []).push(r);
    return m;
  }, [result, current]);
  const shownEdges = useMemo(() => {
    const focus = hovered || selected; // 悬停或选中的节点：它的连线高亮
    const mark = (e, cls) => (focus && (e.source === focus || e.target === focus) ? { ...e, className: `${cls} edge-hover`, zIndex: 1 } : cls === e.className ? e : { ...e, className: cls });
    if (!running && !result) return edges.map((e) => mark(e, e.className));
    return edges.map((e) => {
      const src = e.data.from.split(".");
      const srcId = e.source;
      const recs = recordsById[srcId];
      let live = src[0] === "$in" ? true : statuses[srcId]?.state === "completed";
      if (recs?.length && !src[0].startsWith("$")) live = recs.some((r) => r.status === "completed" && r.outputs?.[src[1]] !== "<跳过>");
      const dead = result && !live;
      return { ...mark(e, `${e.className} ${live ? "edge-live" : ""} ${dead ? "edge-dead" : ""}`), animated: running && live };
    });
  }, [edges, statuses, recordsById, running, result, hovered, selected]);

  // 会改动电脑上文件的模块，每次运行前都问一次；不同意就不运行
  const askWrite = (keys) => {
    const names = keys.map((k) => `· ${modules[k]?.name || k}（${k}）`).join("\n");
    return ask({ title: "这个工作流会改动你电脑上的文件", message: `${names}\n\n确定运行吗？`, ok: "确定运行" });
  };

  // 运行前先问服务端要确认哪些写模块（含引用的子流程里的）；问不到时退回只看本图
  const neededWrites = async () => {
    try {
      const keys = await postJSON("/api/writes", { workflow: current.raw, source: current.source });
      if (Array.isArray(keys)) return [...new Set([...keys, ...writeModules(current.raw, modules)])].sort();
    } catch {}
    return writeModules(current.raw, modules);
  };

  // approved：已经确认过的写模块（重跑时传入）；不传则先找出要确认的并询问
  const run = async (inputs, approved = null) => {
    const writes = approved ?? (await neededWrites());
    if (!approved && writes.length && (!(await askWrite(writes)) || currentRef.current !== current)) return;
    let final = null;
    const token = runSeq.current.next();
    const mine = () => runSeq.current.isCurrent(token);
    runAbortRef.current?.abort();
    const ctrl = new AbortController();
    runAbortRef.current = ctrl;
    resetStatus(current.wf.id);
    setResult(null);
    setRunning(true);
    setNotice("");
    try {
      await stream("/api/run", { workflow: current.raw, inputs, source: current.source, allow_write: writes }, (ev) => {
        if (!mine()) return;
        const state = { node_start: "running", node_end: "completed", node_skipped: "skipped", node_failed: "failed" }[ev.type];
        if (state) {
          runStatus.current.apply(ev.path, state, ev.reason || (ev.code && `${ev.code}：${ev.message}`));
          flushTimer.current ??= setTimeout(flushStatus, 60);
          return;
        }
        flushStatus(); // 结果到达前先把最后一批状态发布出去
        if (ev.type === "result") setResult((final = ev));
        else if (ev.type === "invalid") setResult({ status: "failed", error: { message: ev.errors.join("\n"), code: "invalid" }, outputs: {} });
        else if (ev.type === "error") setResult({ status: "failed", error: { message: ev.message, code: "server" }, outputs: {} });
      }, ctrl.signal);
    } catch (e) {
      if (mine() && e.name !== "AbortError") setResult({ status: "failed", error: { message: e.message, code: "network" }, outputs: {} });
    } finally {
      if (mine()) {
        flushStatus();
        setRunning(false);
      }
    }
    // 保险：运行前没查到的写模块（例如引用的文件刚被改过）被服务端拦下时，再问一次，同意就带上它重跑
    const missing = final?.error?.code === "not_confirmed" ? unconfirmedModule(final.error.message) : null;
    if (mine() && missing && !writes.includes(missing) && (await askWrite([missing])) && mine() && currentRef.current === current) return run(inputs, [...writes, missing]);
  };

  const generate = async (text = requirement) => {
    if (typeof text !== "string") text = requirement; // 作为事件处理函数调用时
    if (!text.trim() || generating) return;
    const at = currentRef.current;
    if (!(await confirmLeave()) || currentRef.current !== at) return;
    const token = openSeq.current.next();
    let pid;
    try {
      pid = await ensureProject({ requirement: text }, () => openSeq.current.isCurrent(token));
    } catch (e) {
      setNotice(`没能新建项目：${e.message}`);
      return;
    }
    if (!pid || !openSeq.current.isCurrent(token) || genProjectRef.current) return; // 切走了，或者已经在生成
    sendJSON("PUT", `/api/projects/${pid}`, { requirement: text }).catch(() => {});
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    genProjectRef.current = pid;
    setGenProject(pid);
    setGenerating(true);
    setGenEvents([]);
    setTab("generate");
    setPanelOpen(true); // 生成过程显示在右侧面板里
    setNotice("");
    try {
      setRequirement(text);
      await stream("/api/generate", { requirement: text, check }, (ev) => {
        setGenEvents((evs) => [...evs, ev]);
        // 只画通过结构校验的版本；没通过运行检查的只算草稿，不能保存
        if (drawable(ev)) {
          const source = ev.type === "done" && ev.ok ? "generated" : "draft";
          if (projectRef.current === pid) {
            show(ev.workflow, source, null);
            setDrawn({ round: ev.round ?? ev.rounds, wf: ev.workflow });
          } else {
            stage(pid, { workflow: ev.workflow, source, file: null, dirty: false }); // 使用者切到了别的项目：草稿直接存进它自己的项目
            pushProject(pid);
          }
        }
      }, ctrl.signal);
    } catch (e) {
      if (e.name !== "AbortError") setGenEvents((evs) => [...evs, { type: "error", message: e.message }]);
      else setGenEvents((evs) => [...evs, { type: "error", message: "已停止生成" }]);
    } finally {
      genProjectRef.current = null;
      setGenProject(null);
      setGenerating(false);
    }
  };

  const save = async () => {
    const pid = current.project;
    const inst = current.inst; // 保存的是哪一份工作流：响应回来前换成了别的工作流，就不再改它
    const raw = current.raw; // 保存的是这一版；响应回来前又改了的话，改动仍算没保存
    try {
      // 我保存的工作流：写回原文件；示例、生成的：另存到“我保存的”，不覆盖别的文件
      const r = await postJSON("/api/workflows", { workflow: raw, overwrite: current.source === "saved" });
      refreshList();
      const patch = (wf) => (r.renamed ? { ...wf, id: r.id } : wf);
      if (currentRef.current?.project === pid) {
        if (currentRef.current.inst !== inst) return;
        setCurrent((c) => (c?.project !== pid || c.inst !== inst ? c
          : { ...c, source: r.source, file: r.file, raw: patch(c.raw), wf: patch(c.wf), dirty: c.raw === raw ? false : c.dirty }));
        setNotice(r.renamed ? `已保存为 ${r.id}：已有同名的另一个工作流，没有覆盖它` : current.source === "saved" ? "已保存" : "已保存到“我保存的”");
      } else if (pid && latestBody.current.get(pid)?.inst === inst) { // 响应回来时已经切到别的项目：只改发起保存的那个项目存着的内容
        const last = latestBody.current.get(pid).body;
        stage(pid, { ...last, workflow: patch(last.workflow), source: r.source, file: r.file, dirty: last.workflow === raw ? false : last.dirty }, inst);
        pushProject(pid);
      }
    } catch (e) {
      if (currentRef.current?.project === pid) setNotice(`没能保存：${e.message}`);
    }
  };

  // 生成过程中，画布上这一版被实跑检查指出问题的节点：id → 问题列表
  const onCanvas = useMemo(() => new Set(nodes.map((n) => n.id)), [nodes]);
  const targetsFor = useCallback(
    (issue, round) => (drawn && drawn.round === round ? issueTargets(issue, drawn.wf).filter((id) => onCanvas.has(id)) : []),
    [drawn, onCanvas],
  );
  const issueMap = useMemo(() => {
    const m = new Map();
    for (const err of current?.dirty || current?.source === "new" ? checkErrors : []) { // 编辑后（或新建的）校验出的问题
      for (const id of issueTargets(err, current.raw).filter((x) => onCanvas.has(x))) m.set(id, [...(m.get(id) || []), issueText(err)]);
    }
    if (!drawn) return m;
    const checked = genEvents.find((e) => e.type === "checked" && e.round === drawn.round);
    for (const p of checked?.status === "failed" ? checked.problems || [] : []) {
      for (const id of targetsFor(p, drawn.round)) m.set(id, [...(m.get(id) || []), p]);
    }
    return m;
  }, [drawn, genEvents, targetsFor, checkErrors, current, onCanvas]);
  const focusNode = useCallback((id) => {
    multiRef.current = new Set();
    setSelected(id);
    setNodes((ns) => ns.map((n) => (n.selected === (n.id === id) ? n : { ...n, selected: n.id === id })));
    flow.current?.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.1, padding: 0.6 });
  }, [setNodes]);

  // 能不能编辑：生成过程中画布会被新草稿替换，先不让改
  const editable = !!current && !generating && !canvasError;
  selectedRef.current = selected;
  selectedEdgeRef.current = selectedEdge?.id || null;
  nodesRef.current = nodes;
  currentRef.current = current;

  async function confirmLeave() {
    return !current?.dirty || ask({ title: "改动还没保存", message: "当前工作流有改动还没保存，确定要离开吗？离开后改动会丢失。", ok: "离开，不保存", cancel: "留下", danger: true });
  }

  const toLayout = flowToLayout;
  const currentPositions = () => {
    const conv = toLayout(nodesRef.current);
    return Object.fromEntries(nodesRef.current.map((n) => [n.id, conv(n)]));
  };

  // 一次改动：fn(旧 JSON) → 新 JSON 或 {wf} 或 {error}。第一次改动前先把当前位置固定下来，之后重新画图时节点不会跳
  // focus：这次改动直接涉及的节点（拖动的、新加的），重新画图后整理重叠时优先保持它们的位置
  const edit = (fn, focus = []) => {
    if (!editable || !current) return false;
    const before = current.raw;
    const startFrom = current.frozen ? before : freezeLayout(before, currentPositions());
    let next;
    try {
      next = fn(startFrom);
    } catch (e) {
      setNotice(`没能修改：${e.message}`);
      return false;
    }
    if (next?.error) {
      setNotice(next.error);
      return false;
    }
    next = next?.wf ?? next;
    if (!next || JSON.stringify(next) === JSON.stringify(before)) return false;
    history.current.past.push(before);
    if (history.current.past.length > 100) history.current.past.shift();
    history.current.future = [];
    const { wf: clean, problems } = inspectWorkflow(next);
    tidyRef.current = { raw: next, focus };
    setCurrent({ ...current, raw: next, wf: clean, problems, dirty: true, frozen: true });
    setNotice("");
    setResult(null); // 程序改了，上一次的运行结果不再对应
    resetStatus(clean.id);
    return true;
  };

  // 画布坐标的新位置一次写回程序的 layout（整份只复制一次，移动很多节点也不卡）；
  // sizes：关掉自动收缩的框要改记的大小（框向左、向上扩大时，保持右边、下边不动）
  const applyMoves = (wf, moves, flowNodes, sizes = new Map()) => {
    let out = freezeLayout(wf, layoutMoves(flowNodes, moves));
    for (const [id, size] of sizes) out = setBoxSize(out, id, size);
    return out;
  };

  // 改动后重新画好图，再整理一次：框装不下就扩大、节点变高或新加后压到别人就推开。并入同一步改动，撤销时一起撤销
  useEffect(() => {
    const t = tidyRef.current;
    const built = builtRef.current;
    if (!t || built?.raw !== t.raw || current?.raw !== t.raw) return;
    if (nodes.length !== built.data.size || nodes.some((n) => built.data.get(n.id) !== n.data)) return; // 新画的节点还没到
    tidyRef.current = null;
    const sizes = new Map();
    const moves = settle(nodes, t.focus, { sizesOut: sizes, pinned: t.pinned || [] });
    // 刚整理过的框：框里这一层按自动排版画好了，把位置全部写回（程序里每个位置都要有 x、y）
    if (t.arranged) for (const n of nodes) if (n.parentId === t.arranged && !(n.id in moves)) moves[n.id] = n.position;
    if (!Object.keys(moves).length && !sizes.size) return;
    const next = applyMoves(t.raw, moves, nodes, sizes);
    const { wf: clean, problems } = inspectWorkflow(next);
    setCurrent((c) => (c?.raw === t.raw ? { ...c, raw: next, wf: clean, problems } : c));
  }, [nodes]); // eslint-disable-line react-hooks/exhaustive-deps

  const travel = (dir) => {
    const h = history.current;
    const from = dir === "undo" ? h.past : h.future;
    const to = dir === "undo" ? h.future : h.past;
    if (!from.length || !current || !editable) return;
    const target = from.pop();
    to.push(current.raw);
    const { wf: clean, problems } = inspectWorkflow(target);
    setCurrent({ ...current, raw: target, wf: clean, problems, dirty: true, frozen: true });
    setResult(null);
    resetStatus(clean.id);
  };

  // 改动后自动校验（停手 300 毫秒后）；问题标在图上
  useEffect(() => {
    if (!current?.dirty && current?.source !== "new") return;
    let alive = true;
    const t = setTimeout(() => {
      postJSON("/api/validate", { workflow: current.raw, source: current.source })
        .then((r) => alive && setCheckErrors(r.errors || []))
        .catch(() => {});
    }, 300);
    return () => { alive = false; clearTimeout(t); };
  }, [current?.raw, current?.dirty, current?.source]); // eslint-disable-line react-hooks/exhaustive-deps

  // 从空白开始的分步清单：新建的工作流，或者还没有节点的图
  const [guideOpen, setGuideOpen] = useState(() => remembered("wf.guide", true));
  useEffect(() => remember("wf.guide", guideOpen), [guideOpen]);
  const [ranOk, setRanOk] = useState(false);
  useEffect(() => { if (result?.status === "completed") setRanOk(true); }, [result]);
  const showGuide = editable && current && (current.source === "new" || (current.raw.nodes || []).length === 0) && current.source !== "draft";
  const guide = showGuide ? guideSteps(current.raw, checkErrors, ranOk) : null;

  const deleteSelected = () => {
    if (selectedEdge) {
      if (edit((wf) => removeEdge(wf, selectedEdge))) setSelectedEdge(null);
    } else if (selected && !splitId(selected).local.startsWith("$")) {
      if (edit((wf) => removeNode(wf, selected))) setSelected(null);
    }
  };

  // 键盘：Delete / Backspace 删除选中的节点或连线；Ctrl+Z 撤销，Ctrl+Shift+Z 或 Ctrl+Y 重做
  const keys = useRef(null);
  keys.current = { travel, deleteSelected, editable };
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      // 正在打字的地方不抢按键；开关、勾选框不算（点完框顶菜单的开关照样能撤销）
      if (t instanceof HTMLElement && t.closest("input:not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable]")) return;
      if (document.querySelector(".dialog-backdrop")) return; // 确认框开着时，删除、撤销等快捷键都不生效
      const k = keys.current;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        k.travel(e.shiftKey ? "redo" : "undo");
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        k.travel("redo");
      } else if ((e.key === "Delete" || e.key === "Backspace") && k.editable) {
        e.preventDefault();
        k.deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 选中的是循环（或循环里的节点）时，新节点加进那个循环里
  const loopScope = (() => {
    if (!selected) return null;
    const n = nodes.find((x) => x.id === selected);
    if (n?.type === "loop") return selected;
    return n?.parentId || null;
  })();
  const addAt = (spec) => {
    const scope = loopScope ? loopScope.split("/") : [];
    let newId = null;
    const focus = []; // 新加的节点：整理重叠时它不动，推开别人
    const ok = edit((wf) => {
      let pos;
      if (scope.length) { // 循环里：放在已有节点下方
        const g = graphAt(wf, scope);
        const ys = Object.values(g.layout || {}).map((p) => p.y);
        pos = { x: 140, y: ys.length ? Math.max(...ys) + 110 : 0 };
      } else { // 画布当前视野的中间
        const box = document.querySelector(".canvas .react-flow")?.getBoundingClientRect();
        const center = box ? flow.current?.screenToFlowPosition({ x: box.left + box.width / 2, y: box.top + box.height / 2 }) : null;
        pos = freeSpot(center ? { x: center.x - 98, y: center.y - 40 } : { x: 0, y: 0 }, nodesRef.current.filter((n) => !n.parentId));
      }
      const r = addNode(wf, scope, spec, pos);
      newId = r.id;
      focus.push(r.id);
      return r.wf;
    }, focus);
    if (ok && newId) {
      multiRef.current = new Set();
      setSelected(newId);
      setSelectedEdge(null);
      setTab("node");
      setPanelOpen(true);
    }
  };

  const onConnect = (c) => edit((wf) => connect(wf, c));
  const sameLayer = (c) => c.source !== c.target && splitId(c.source).scope.join("/") === splitId(c.target).scope.join("/");
  // 拖动中：撞上同层节点就贴着边滑，不重叠；框里的节点碰到框的右边、下边时框跟着变大
  const onNodesChangeGuarded = useCallback((changes) => {
    onNodesChange(guardChanges(changes, nodesRef.current, guardedPos.current));
    if (changes.some((c) => c.type === "position" && c.dragging)) setNodes((cur) => fitWhileDragging(cur));
  }, [onNodesChange, setNodes]);

  // 放下后：压着别的节点就挪到最近的空位；框装不下就扩大，扩大后挤到的邻居被推开。整个结果算一步，可以撤销
  const onNodeDragStop = (_, node, dragged) => {
    const ids = (dragged?.length ? dragged : [node]).map((n) => n.id);
    const live = nodesRef.current.map((n) => { // 用放下那一刻、经过碰撞修正的位置
      const d = guardedPos.current.get(n.id) || (dragged || [node]).find((x) => x.id === n.id)?.position;
      return d ? { ...n, position: d } : n;
    });
    guardedPos.current = new Map();
    const sizes = new Map();
    const moves = Object.assign(dict(), Object.fromEntries(ids.map((id) => [id, live.find((n) => n.id === id)?.position]).filter(([, p]) => p)),
      settle(live, ids, { base: dragBase.current, sizesOut: sizes }));
    // 程序没变（例如拖出去又拖回原处）：按当前程序重画，位置和拖动中临时撑大的框都回到程序里的样子
    if (!edit((wf) => applyMoves(wf, moves, live, sizes), ids)) setCurrent((c) => (c ? { ...c } : c));
  };

  // 拉伸循环框：四角、四边中点的拉伸点（关掉自动收缩后才有）。压到框里的节点时停住；松手后记下大小，
  // 拉大压到旁边的节点时把它们推开。拉左边、上边时画布库会让框里的节点在屏幕上不动
  const resizeStart = useRef(null);
  const boxDrag = useRef(null); // 正在用框顶菜单拖动框时，结束这次拖动的函数
  const dragStopRef = useRef(null); // 松手时用最新一次渲染的处理函数（按下时的那份拿着旧的工作流）
  dragStopRef.current = onNodeDragStop;
  useEffect(() => () => boxDrag.current?.(), []); // 页面卸下时收起拖动的监听
  // 把框改成 rect（{x, y, width, height}，框在它那一层的位置和大小）并记下大小、关掉自动收缩，算一步改动。
  // kidShift：框里节点相对框要挪的量（框左边、上边移动了而框里节点在屏幕上不动时，是框移动量的相反数）
  const commitBox = (id, { x, y, width, height }, kidShift = { x: 0, y: 0 }) => {
    const ns = nodesRef.current.map((n) => (n.parentId === id && (kidShift.x || kidShift.y)
      ? { ...n, position: { x: n.position.x + kidShift.x, y: n.position.y + kidShift.y } } : n));
    const moves = Object.assign(dict(), { [id]: { x, y } });
    for (const n of ns) if (n.parentId === id) moves[n.id] = n.position; // 拉左边、上边后框里节点的相对位置变了
    const own = { w: width, h: height };
    // 按“画在哪就写哪”换算：不再带自动伸缩时的 offset、shift
    const live = ns.map((n) => (n.id === id ? { ...n, position: { x, y }, data: { ...n.data, width, height, manual: own, offset: null, shift: { x: 0, y: 0 } } } : n));
    // 写回之前先整理一遍（用新的位置和大小）：嵌套的框拉出外层框的左边、上边时，外层框先扩大、坐标先补偿好，
    // 不会写进负坐标再被重画挪回去；拉大压到的邻居也在这里推开
    const sizes = new Map();
    Object.assign(moves, settle(live, [], { pinned: [id], sizesOut: sizes }));
    const final = sizes.get(id) || own;
    sizes.delete(id);
    const ok = edit((wf) => setBoxSize(applyMoves(wf, moves, live, sizes), id, final), []);
    if (ok) tidyRef.current = { ...tidyRef.current, pinned: [id] };
    return ok;
  };
  const boxEdit = {
    start: (id) => {
      const ns = nodesRef.current;
      const box = ns.find((n) => n.id === id);
      resizeStart.current = box && { id, x: box.position.x, y: box.position.y, kids: ns.filter((n) => n.parentId === id).map(rectOf) };
    },
    allow: (id, next) => {
      const st = resizeStart.current;
      return !st || st.id !== id || canResize(st, next);
    },
    end: (id, rect) => {
      const st = resizeStart.current;
      resizeStart.current = null;
      if (st && st.id === id) commitBox(id, rect);
    },
    // ---- 框顶菜单 ----
    // 自动收缩：打开时去掉记下的大小，框回到正好包住框里节点；关掉时按框现在画出来的样子记下大小
    setAuto: (id, on) => {
      const box = nodesRef.current.find((n) => n.id === id);
      if (!box) return;
      if (on) edit((wf) => setBoxSize(wf, id, null), []);
      else commitBox(id, { x: box.position.x, y: box.position.y, width: box.data.width, height: box.data.height });
    },
    // 收紧到节点：框收到正好包住框里节点（自动收缩仍关着，之后保持这个大小）
    fit: (id) => {
      const box = nodesRef.current.find((n) => n.id === id);
      const kids = nodesRef.current.filter((n) => n.parentId === id).map(rectOf);
      if (!box || !kids.length) return;
      const dx = Math.min(...kids.map((k) => k.x)) - PAD;
      const dy = Math.min(...kids.map((k) => k.y)) - TOP;
      const { w, h } = fitSize(kids);
      commitBox(id, { x: box.position.x + dx, y: box.position.y + dy, width: w, height: h }, { x: -dx, y: -dy });
    },
    arrange: (id) => { if (edit((wf) => arrangeBody(wf, id), [])) tidyRef.current = { ...tidyRef.current, arranged: id }; },
    // 选中框里的全部节点（不含框本身），可以一起拖动
    selectKids: (id) => {
      multiRef.current = new Set(nodesRef.current.filter((n) => n.parentId === id).map((n) => n.id));
      setSelected(null);
      setSelectedEdge(null);
      setNodes((ns) => ns.map((n) => {
        const on = n.parentId === id;
        return n.selected === on ? n : { ...n, selected: on };
      }));
    },
    // 按住“拖动整个框”：框连同框里的节点一起跟着鼠标走，和直接拖节点一样会贴边滑开、不重叠，松手后整理一次（一步撤销）。
    // 只认按下的那根指针；按着时缩放画布也跟得上（每次按当前视野换算）；切走窗口、按键已松开时就地结束
    drag: (id, e) => {
      if (e.button !== 0 || boxDrag.current || !flow.current) return;
      e.preventDefault();
      e.stopPropagation();
      const box = nodesRef.current.find((n) => n.id === id);
      if (!box) return;
      const toFlow = (ev) => flow.current.screenToFlowPosition({ x: ev.clientX, y: ev.clientY });
      const from = { pos: box.position, grab: toFlow(e), raw: currentRef.current?.raw, pointer: e.pointerId };
      dragBase.current = new Map(nodesRef.current.filter((n) => n.type === "loop").map((n) => [n.id, { w: n.data.width, h: n.data.height }]));
      let moved = false;
      const at = (ev) => { const p = toFlow(ev); return { x: from.pos.x + p.x - from.grab.x, y: from.pos.y + p.y - from.grab.y }; };
      const finish = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        window.removeEventListener("pointercancel", up);
        window.removeEventListener("blur", finish);
        boxDrag.current = null;
        const node = nodesRef.current.find((n) => n.id === id);
        if (!moved) return;
        // 拖动中程序变了（撤销、换了工作流；只是存好了不算）：这次拖动作废，按当前程序重画
        if (!node || currentRef.current?.raw !== from.raw) {
          guardedPos.current = new Map();
          setCurrent((c) => (c ? { ...c } : c));
          return;
        }
        dragStopRef.current(null, { ...node, position: guardedPos.current.get(id) || node.position });
      };
      const move = (ev) => {
        if (ev.pointerId !== from.pointer) return;
        if (!(ev.buttons & 1)) return finish(); // 没收到松手（例如在窗口外松开）
        moved = true;
        onNodesChangeGuarded([{ type: "position", id, position: at(ev), dragging: true }]);
      };
      const up = (ev) => { if (ev.pointerId === from.pointer) finish(); };
      boxDrag.current = finish;
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      window.addEventListener("pointercancel", up);
      window.addEventListener("blur", finish);
    },
    zoom: (id) => flow.current?.fitView({ nodes: [{ id }], duration: 300, padding: 0.15, maxZoom: 1.2 }),
  };

  // 选中节点在程序里的参数与来源，交给节点详情里的编辑器
  const nodeEdits = (() => {
    if (!selected || !current || !editable) return null;
    const { scope, local } = splitId(selected);
    if (local.startsWith("$")) return null;
    let g;
    try {
      g = graphAt(current.raw, scope);
    } catch {
      return null;
    }
    const n = (g.nodes || []).find((x) => x.id === local);
    if (!n) return null;
    const incoming = {};
    for (const e of g.edges || []) if (e.to.startsWith(`${local}.`)) incoming[e.to.slice(local.length + 1)] = e.from;
    return {
      node: n,
      incoming,
      setConfig: (port, v) => edit((wf) => setConfig(wf, selected, port, v)),
      setLabel: (v) => edit((wf) => setLabel(wf, selected, v)),
      setMaxItems: (v) => edit((wf) => setMaxItems(wf, selected, v)),
      remove: () => { if (edit((wf) => removeNode(wf, selected))) setSelected(null); },
    };
  })();

  // 工作流自己的输入（点“输入”端点）或输出（点“输出”端点）
  const portSide = editable && (selected === "$in" ? "inputs" : selected === "$out" ? "outputs" : null);
  const portOps = portSide && {
    add: () => edit((wf) => addPort(wf, portSide)),
    rename: (from, to) => edit((wf) => renamePort(wf, portSide, from, to)),
    type: (name, t) => edit((wf) => setPortType(wf, portSide, name, t)),
    fields: (name, f) => edit((wf) => setPortFields(wf, portSide, name, f)),
    remove: (name) => edit((wf) => removePort(wf, portSide, name)),
  };

  const saved = list.filter((i) => i.source === "saved");
  const examples = list.filter((i) => i.source === "examples");
  const selectedNode = nodes.find((n) => n.id === selected);

  return (
    <>
    {confirmDialog}
    {/* 确认框开着时整个页面不可操作：Tab 也只在确认框里转 */}
    <div className={`app ${sideOpen ? "" : "no-side"} ${panelOpen ? "" : "no-panel"}`} inert={confirmDialog ? true : undefined}>
      <header className="bar">
        <button type="button" className="brand" onClick={goHome} title="回到首页"><BrandMark />工作流</button>
        <form className="ask" onSubmit={(e) => { e.preventDefault(); generate(); }}>
          <div className={`ask-field ${generating ? "busy" : ""}`}>
            <label className="sr-only" htmlFor="req">需求</label>
            <input id="req" value={requirement} onChange={(e) => setRequirement(e.target.value)}
                   placeholder="用一句话说明要做什么，例如：给我一个网址，用一句中文总结网页内容" autoComplete="off" />
            {generating
              ? <button key="stop" type="button" className="btn ghost" onClick={(e) => { e.preventDefault(); abortRef.current?.abort(); }}>停止</button>
              : <button key="go" type="submit" className="btn primary" disabled={!requirement.trim()}>生成工作流</button>}
          </div>
          <label className="switch" title="生成后用示例输入实际运行一遍，由审查模型检查结果，发现问题会自动重写">
            <input type="checkbox" role="switch" checked={check} onChange={(e) => setCheck(e.target.checked)} />
            <span className="switch-track" aria-hidden="true" />
            生成后实跑检查
          </label>
        </form>
      </header>

      <aside className="side" hidden={!sideOpen}>
        <ProjectList projects={projects} active={project} busy={genProject} onNew={newProject} onOpen={openProject} onRename={renameProject} onDelete={deleteProject} />
        {notice && !current && <p className="notice side-notice" role="status">{notice}</p>}
        {saved.length > 0 && <WorkflowGroup title="我保存的" items={saved} current={current} onOpen={open} />}
        <WorkflowGroup title="示例" items={examples} current={current} onOpen={open} />
      </aside>

      <main className="canvas">
        {current ? (
          <>
            <div className="canvas-head">
              <div className="canvas-titles">
                <h1>{current.wf.name}</h1>
                {current.wf.intent && <p className="intent">{current.wf.intent}</p>}
                {current.problems.length > 0 && (
                  <ul className="issues" role="alert">{current.problems.map((x) => <li key={x}>{x}</li>)}</ul>
                )}
                {current.dirty && checkErrors.length > 0 && (
                  <details className="check-errors" role="alert">
                    <summary>这张图现在有 {checkErrors.length} 个问题，改好之前不能保存，运行也会被拒绝</summary>
                    <ul className="issues">{checkErrors.map((x) => <li key={x} title={x}>{issueText(x)}</li>)}</ul>
                  </details>
                )}
              </div>
              <div className="canvas-actions">
                {editable && (
                  <div className="edit-tools" role="group" aria-label="编辑">
                    <button type="button" className="btn small ghost" onClick={() => travel("undo")} disabled={!history.current.past.length} title="撤销（Ctrl+Z）">撤销</button>
                    <button type="button" className="btn small ghost" onClick={() => travel("redo")} disabled={!history.current.future.length} title="重做（Ctrl+Shift+Z）">重做</button>
                    <AddMenu modules={modules} loop={loopScope} onAdd={addAt} />
                  </div>
                )}
                {current.dirty && <span className="draft-tag">有改动，还没保存</span>}
                {current.source === "draft" && !current.dirty && <span className="draft-tag">{generating ? "草稿，正在检查" : "草稿：没有通过检查，不能保存"}</span>}
                {(current.source === "generated" || current.dirty) && (
                  <button className="btn primary" onClick={save} disabled={generating || (current.dirty && checkErrors.length > 0)}
                          title={current.dirty && checkErrors.length ? "先改好图上标出的问题再保存" : ""}>
                    {current.source === "saved" ? "保存" : "保存到我的"}
                  </button>
                )}
                {notice && <span className="notice" role="status">{notice}</span>}
                <LayoutToggles side={sideOpen} panel={panelOpen} onSide={() => setSideOpen((v) => !v)} onPanel={() => setPanelOpen((v) => !v)} />
              </div>
            </div>
            <StatusContext.Provider value={statuses}><IssueContext.Provider value={issueMap}><BoxEditContext.Provider value={editable ? boxEdit : null}>
              {canvasError ? <div className="empty"><p>这个工作流没法画出来：{canvasError}</p></div> : (
              <Guard what="工作流" key={viewSeq}>
              <ReactFlow onInit={(inst) => { flow.current = inst; }} nodes={nodes} edges={shownEdges} nodeTypes={nodeTypes} onNodesChange={onNodesChangeGuarded} onEdgesChange={onEdgesChange}
                         onNodeClick={(_, n) => { multiRef.current = new Set(); setSelected(n.id); setSelectedEdge(null); setTab("node"); setPanelOpen(true); }}
                         onEdgeClick={(_, e) => { multiRef.current = new Set(); setSelectedEdge({ id: e.id, ...e.data }); setSelected(null); setTab("node"); setPanelOpen(true); }}
                         onPaneClick={() => { multiRef.current = new Set(); setSelected(null); setSelectedEdge(null); }}
                         onConnect={onConnect} isValidConnection={sameLayer} onNodeDragStop={onNodeDragStop}
                         onNodeDragStart={() => { dragBase.current = new Map(nodesRef.current.filter((n) => n.type === "loop").map((n) => [n.id, { w: n.data.width, h: n.data.height }])); }}
                         nodesDraggable={editable} edgesFocusable={editable} connectionRadius={24}
                         onNodeMouseEnter={(_, n) => setHovered(n.id)} onNodeMouseLeave={() => setHovered(null)}
                         deleteKeyCode={null} minZoom={0.15} maxZoom={2} nodesConnectable={editable} proOptions={{ hideAttribution: true }}>
                <Background gap={48} size={3} color="var(--board-dot)" />
                <InitialView />
                <ZoomBar />
                {guide && <BuildGuide guide={guide} open={guideOpen} onToggle={() => setGuideOpen((v) => !v)} />}
                <Overview statuses={statuses} />
              </ReactFlow>
              </Guard>)}
            </BoxEditContext.Provider></IssueContext.Provider></StatusContext.Provider>
          </>
        ) : (
          <div className="empty start-wrap">
            {!sideOpen && <div className="empty-toggles"><LayoutToggles side={sideOpen} panel={panelOpen} onSide={() => setSideOpen(true)} onPanel={() => setPanelOpen(true)} /></div>}
            <StartPage key={project || "home"} idea={requirement} setIdea={setRequirement} generating={generating} examples={examples}
                       onGenerate={(text) => generate(text)} onOpen={open} onBlank={newBlank} />
          </div>
        )}
      </main>

      <section className="panel" hidden={!panelOpen}>
        <nav className="tabs" role="tablist">
          {[["run", "运行"], ["generate", "生成过程"], ["node", selectedEdge ? "连线" : "节点"], ["program", "程序"]].map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>
              {label}{k === "generate" && generating ? "…" : ""}
            </button>
          ))}
        </nav>
        <div className="panel-body">
          {/* 运行面板保持挂载，只切换显示：离开“运行”再回来，填好的参数不会被重置（N01） */}
          <Guard what="面板" key={current ? current.wf.id + (current.file || "") + (current.source || "") : "none"}>
            <div hidden={tab !== "run"}><RunPanel wf={current?.wf} running={running} result={result} onRun={run} /></div>
            {/* 生成过程和节点详情只在当前页签渲染，不在后台做无用计算 */}
            {tab === "generate" && <GenerationLog events={genEvents} generating={generating} targets={targetsFor} onFocus={focusNode} drawnRound={drawn?.round} />}
            {tab === "node" && (selectedEdge
              ? <EdgeDetail edge={selectedEdge} onDelete={editable ? deleteSelected : null} />
              : portSide ? (
                <div className="detail">
                  <h3>工作流的{portSide === "inputs" ? "输入" : "输出"}</h3>
                  <p className="desc">{portSide === "inputs" ? "运行时要使用者提供的值。改名字时，从它连出去的线会跟着改。" : "运行结束后交给使用者的结果。每个输出都要有一条线接进来。"}</p>
                  <PortsEditor side={portSide} ports={current.raw[portSide]} ops={portOps} />
                </div>
              )
              : !selected && editable ? <MetaEditor name={current.raw.name} intent={current.raw.intent} onSet={(m) => edit((wf) => setMeta(wf, m))} />
              : <NodeDetail node={selectedNode} status={selected && statuses[selected]} records={selected ? recordsById[selected] : null} edits={nodeEdits} />)}
            {tab === "program" && (current ? <ProgramView wf={current.raw} dirty={current.dirty} /> : <p className="muted">先打开或生成一个工作流。</p>)}
          </Guard>
        </div>
      </section>
    </div>
    </>
  );
}

function remembered(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function remember(key, on) {
  try {
    localStorage.setItem(key, on ? "1" : "0");
  } catch {
    // 无法写入本机存储时，只是下次不记得，不影响使用
  }
}

// 品牌标记：两个模块用跳线相连，右下一盏状态灯（与桌面程序图标一致）
function BrandMark() {
  return (
    <svg className="brand-mark" viewBox="0 0 64 64" aria-hidden="true">
      <rect x="2" y="2" width="60" height="60" rx="14" fill="var(--board)" />
      <rect x="9" y="30" width="18" height="20" rx="3.5" fill="#e9edf0" />
      <rect x="37" y="14" width="18" height="20" rx="3.5" fill="#e9edf0" />
      <path d="M27 40h5V24h5" fill="none" stroke="#9aa6b1" strokeWidth="3.8" strokeLinejoin="round" />
      <circle cx="27" cy="40" r="3" fill="var(--t-string)" />
      <circle cx="37" cy="24" r="3" fill="var(--t-string)" />
      <circle cx="49" cy="46" r="5" fill="var(--led-ok)" />
    </svg>
  );
}

// 收起/显示左侧列表和右侧面板：图标按钮，文字留给读屏和悬停提示
function LayoutToggles({ side, panel, onSide, onPanel }) {
  const icon = (which, on) => (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="ico">
      <rect x="2.5" y="3.5" width="15" height="13" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <rect x={which === "side" ? 2.5 : 12.5} y="3.5" width="5" height="13" rx={on ? 1 : 0} fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" opacity={on ? 0.9 : 0.5} />
    </svg>
  );
  const sideText = side ? "收起列表" : "显示列表";
  const panelText = panel ? "收起面板" : "显示面板";
  return (
    <div className="toggles" role="group" aria-label="布局">
      <button type="button" className="icon-btn" aria-pressed={side} onClick={onSide} title={sideText}>{icon("side", side)}<span className="sr-only">{sideText}</span></button>
      <button type="button" className="icon-btn" aria-pressed={panel} onClick={onPanel} title={panelText}>{icon("panel", panel)}<span className="sr-only">{panelText}</span></button>
    </div>
  );
}

// 某一块出错时只影响这一块，不让整页白屏
class Guard extends Component {
  state = { error: null };
  static getDerivedStateFromError(error) {
    return { error };
  }
  render() {
    if (this.state.error) return <div className="empty"><p>这个{this.props.what}没法显示：{String(this.state.error.message || this.state.error)}</p></div>;
    return this.props.children;
  }
}

function WorkflowGroup({ title, items, current, onOpen }) {
  return (
    <div className="group">
      <h2>{title}</h2>
      <ul>
        {items.map((i) => (
          <li key={i.source + i.file}>
            <button className={`wf-item ${current?.file === i.file && current?.source === i.source ? "on" : ""}`} onClick={() => onOpen(i)}>
              <span className="wf-name">{i.name}</span>
              {i.intent && <span className="wf-intent">{i.intent}</span>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
