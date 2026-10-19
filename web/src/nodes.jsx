import { createContext, useContext, useEffect } from "react";
import { typeLabel } from "./labels.js";
import { Handle, NodeResizeControl, NodeToolbar, Position, useUpdateNodeInternals } from "@xyflow/react";
import { portTop } from "./graph.js";

// 运行状态：节点 id → { state: idle|running|completed|skipped|failed, counts, reason }
export const StatusContext = createContext({});
// 生成过程中被实跑检查指出问题的节点：id → 问题列表
export const IssueContext = createContext(new Map());
// 能编辑时由 App 提供：拉伸循环框的开始、是否允许（压到框里节点时不允许）、结束，以及框顶菜单里的各项操作。
// 不能编辑时为 null，不显示拉伸点和菜单
export const BoxEditContext = createContext(null);
const RESIZE_POINTS = ["top-left", "top", "top-right", "right", "bottom-right", "bottom", "bottom-left", "left"];

function IssueBadge({ id }) {
  const issues = useContext(IssueContext).get(id);
  if (!issues) return null;
  return <span className="issue-badge" role="img" aria-label={`检查发现 ${issues.length} 个问题`} title={issues.join("\n")}>!</span>;
}
const useStatus = (id) => {
  const s = useContext(StatusContext);
  return (Object.hasOwn(s, id) && s[id]) || { state: "idle" };
};

const KIND_LABEL = { if: "条件分支", merge: "合并", foreach: "逐项循环", subflow: "子流程" };
const STATE_LABEL = { idle: "未运行", running: "运行中", completed: "完成", skipped: "跳过", failed: "出错" };

export function typeClass(type = "any") {
  if (type.startsWith("list<")) return "t-list";
  if (type === "integer" || type === "number") return "t-number";
  if (type === "boolean") return "t-boolean";
  if (type === "string" || type === "file") return "t-string";
  return "t-any";
}

// 悬停在端口上：名字、含义、类型；固定值显示全文
function portTip(p) {
  if (!p) return "";
  const lines = [`${p.name}（${typeLabel(p.type)}）${p.desc ? "：" + p.desc : ""}`];
  if (p.full !== undefined) lines.push(`固定为：${typeof p.full === "string" ? p.full : JSON.stringify(p.full)}`);
  return lines.join("\n");
}

function Led({ status }) {
  return <span className={`led led-${status.state}`} role="img" aria-label={STATE_LABEL[status.state]} title={status.reason || STATE_LABEL[status.state]} />;
}

function Counts({ status }) {
  if (!status.counts) return null;
  const c = status.counts;
  const parts = [c.completed && `${c.completed} 完成`, c.skipped && `${c.skipped} 跳过`, c.failed && `${c.failed} 出错`].filter(Boolean);
  return <div className="node-counts" title={`共 ${c.total} 项`}>{parts.join(" / ")}</div>;
}

function PortRows({ ins, outs }) {
  const rows = Math.max(ins.length, outs.length);
  return (
    <div className="ports">
      {Array.from({ length: rows }, (_, i) => (
        <div className="port-row" key={i}>
          <span className={`port-in ${ins[i]?.fixed ? "is-fixed" : ""}`} title={portTip(ins[i])}>
            {ins[i]?.name}
            {ins[i]?.fixed && <span className="fixed-val">{ins[i].fixed}</span>}
          </span>
          <span className="port-out" title={portTip(outs[i])}>{outs[i]?.name}</span>
        </div>
      ))}
      {ins.map((p, i) => (
        <Handle key={`in-${p.name}`} type="target" position={Position.Left} id={`in:${p.name}`} className={`jack ${typeClass(p.type)}`} style={{ top: portTop(i) }} />
      ))}
      {outs.map((p, i) => (
        <Handle key={`out-${p.name}`} type="source" position={Position.Right} id={`out:${p.name}`} className={`jack ${typeClass(p.type)}`} style={{ top: portTop(i) }} />
      ))}
    </div>
  );
}

export function ModuleNode({ id, data, selected }) {
  const status = useStatus(id);
  const { node, module } = data;
  const title = node.label || module?.name || KIND_LABEL[node.kind] || node.id;
  // 第二行写给人看：起了名字的节点注明用的是哪个模块；模块原名作标题时注明类别。程序写法（text.format@0）放在悬停提示里
  const sub = node.kind !== "module" ? KIND_LABEL[node.kind] : !module ? node.module : node.label ? module.name : `${module.category}模块`;
  const category = module?.category || node.kind;
  return (
    <div className={`node node-${node.kind} cat-${category} state-${status.state} ${selected ? "is-selected" : ""} ${useContext(IssueContext).has(id) ? "has-issue" : ""}`} style={{ width: data.width, height: data.height }}>
      <IssueBadge id={id} />
      <Handle type="target" position={Position.Top} id="ctrl" className="jack-ctrl" />
      <div className="node-head">
        <Led status={status} />
        <div className="node-titles">
          <div className="node-title" title={node.kind === "module" ? `${title}（${node.module}）` : title}>{title}</div>
          <div className="node-sub">{sub}</div>
        </div>
        <Counts status={status} />
      </div>
      <PortRows ins={data.ins} outs={data.outs} />
    </div>
  );
}

export function LoopNode({ id, data, selected }) {
  const status = useStatus(id);
  const box = useContext(BoxEditContext);
  const { node } = data;
  // 拖动中框画出来的范围变了，插孔跟着挪：让画布库重新量插孔位置，连线端点才跟得上
  const updateInternals = useUpdateNodeInternals();
  useEffect(() => { updateInternals(id); }, [id, data.visual?.x, data.visual?.y, data.width, data.height, updateInternals]);
  return (
    // 平时宽高跟着外层节点走（拉伸时画布库改的是外层的大小）；拖动框里节点时框实时伸缩，只改画出来的范围（data.visual）
    <div className={`loop state-${status.state} ${selected ? "is-selected" : ""} ${useContext(IssueContext).has(id) ? "has-issue" : ""}`}
      style={data.visual ? { position: "absolute", left: data.visual.x, top: data.visual.y, width: data.width, height: data.height } : { width: "100%", height: "100%" }}>
      {/* 选中框时，框顶边外侧居中出现菜单 */}
      {box && <NodeToolbar isVisible={selected && !data.visual} position={Position.Top} offset={14} className="box-menu nodrag nopan">
        <BoxMenu id={id} auto={!data.manual} box={box} />
      </NodeToolbar>}
      {/* 关掉自动收缩后才能拉伸：开着时框始终正好包住框里节点，拉大了也会马上收回 */}
      {box && !data.visual && data.manual && RESIZE_POINTS.map((p) => (
        <NodeResizeControl key={p} position={p} className="box-resize" minWidth={10} minHeight={10}
          onResizeStart={() => box.start(id)} shouldResize={(_, next) => box.allow(id, next)} onResizeEnd={(_, end) => box.end(id, end)} />
      ))}
      <IssueBadge id={id} />
      {/* 控制线插孔不放在顶边中点：那里是拉伸点 */}
      <Handle type="target" position={Position.Top} id="ctrl" className="jack-ctrl" style={{ left: 72 }} />
      <div className="loop-head">
        <Led status={status} />
        <span className="node-title">{node.label || "逐项循环"}</span>
        <span className="loop-limit">最多 {node.max_items} 项</span>
      </div>
      <Handle type="target" position={Position.Left} id="in:items" className={`jack ${typeClass(data.ins[0]?.type)}`} style={{ top: 22 }} />
      <Handle type="source" position={Position.Right} id="out:results" className={`jack ${typeClass(data.outs[0]?.type)}`} style={{ top: 22 }} />
    </div>
  );
}

// 循环框顶部菜单：自动收缩开关、按住拖动整个框、收紧到节点、整理框里节点、选中框里全部节点、看全这个框
function BoxMenu({ id, auto, box }) {
  return (
    // 菜单画在框外（Portal），但 React 事件仍会冒泡到框节点：点菜单不能再触发“点了框”（选中框、按键挪框）；Ctrl 组合键（撤销、重做）照常
    <div className="box-menu-row" role="toolbar" aria-label="循环框" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => { if (!e.ctrlKey && !e.metaKey) e.stopPropagation(); }}
      onDoubleClick={(e) => e.stopPropagation()} onContextMenu={(e) => e.stopPropagation()}>
      <label className="box-menu-switch" title="开着时框始终正好包住框里的节点；关掉后框保持当前大小，可以拖四角、四边拉伸">
        <input type="checkbox" role="switch" checked={auto} onChange={(e) => box.setAuto(id, e.target.checked)} />
        自动收缩
      </label>
      <button type="button" className="box-menu-grip" onPointerDown={(e) => box.drag(id, e)} title="按住鼠标拖动：框连同框里的全部节点一起移动">按住拖动整个框</button>
      <button type="button" disabled={auto} onClick={() => box.fit(id)} title="把框收到正好包住框里的节点（只收这一次，之后仍保持大小）">收紧到节点</button>
      <button type="button" onClick={() => box.arrange(id)} title="把框里的节点按连线从左到右重新排整齐">整理框里节点</button>
      <button type="button" onClick={() => box.selectKids(id)} title="选中框里的全部节点，可以一起拖动">选中框里全部</button>
      <button type="button" onClick={() => box.zoom(id)} title="把画布移到这个框，正好看全">看全这个框</button>
    </div>
  );
}

export function TerminalNode({ id, data }) {
  const status = useStatus(id);
  return (
    <div className={`terminal ${data.terminal} state-${status.state}`}>
      <div className="terminal-head">{data.title}</div>
      <PortRows ins={data.ins} outs={data.outs} />
    </div>
  );
}

export const nodeTypes = { module: ModuleNode, loop: LoopNode, terminal: TerminalNode };
