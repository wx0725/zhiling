// 画布的尺寸控制：缩放条、小地图、打开时的初始视野。
import { useEffect, useMemo, useRef } from "react";
import { MiniMap, Panel, useNodes, useNodesInitialized, useReactFlow, useStore } from "@xyflow/react";
import { FIT, initialViewport, wholeGraphVisible } from "./viewport.js";

// 每打开一个工作流（由 key 区分）只设置一次初始视野，之后用户怎么缩放都不再改
export function InitialView() {
  const ready = useNodesInitialized();
  const { fitView, getNodes, getNodesBounds, setViewport } = useReactFlow();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  const done = useRef(false);
  useEffect(() => {
    if (done.current || !ready || !width || !height) return;
    const nodes = getNodes();
    if (!nodes.length) return;
    done.current = true;
    const v = initialViewport(getNodesBounds(nodes), width, height); // 用实例方法：循环框里的子节点按绝对位置计算
    if (v) setViewport(v);
    else fitView(FIT);
  }, [ready, width, height, fitView, getNodes, getNodesBounds, setViewport]);
  return null;
}

export function ZoomBar() {
  const { zoomIn, zoomOut, zoomTo, fitView } = useReactFlow();
  const zoom = useStore((s) => s.transform[2]);
  const d = { duration: 160 };
  return (
    <Panel position="bottom-left" className="zoombar" aria-label="缩放">
      <button type="button" onClick={() => zoomOut(d)} aria-label="缩小" title="缩小">−</button>
      <button type="button" className="zoom-pct" onClick={() => zoomTo(1, d)} title="恢复到 100%">{Math.round(zoom * 100)}%</button>
      <button type="button" onClick={() => zoomIn(d)} aria-label="放大" title="放大">+</button>
      <span className="zoombar-sep" aria-hidden="true" />
      <button type="button" onClick={() => fitView({ ...FIT, duration: 220 })} title="缩放到能看到整张图">看全图</button>
    </Panel>
  );
}

// 小地图：节点按运行状态着色，可拖动、缩放
const MINI = { running: "#f0a94a", completed: "#3fb57a", failed: "#e45c5c" };
export function Overview({ statuses }) {
  // 整张图都在视野里时不需要小地图；平移或放大后有部分看不到了再显示
  const nodes = useNodes();
  const { getNodesBounds } = useReactFlow();
  const bounds = useMemo(() => (nodes.length ? getNodesBounds(nodes) : null), [nodes, getNodesBounds]);
  const all = useStore((st) => wholeGraphVisible(bounds, st.transform, st.width, st.height));
  if (!bounds || all) return null;
  return (
    <MiniMap className="overview" pannable zoomable style={{ width: 168, height: 104 }} ariaLabel="整张图的缩略图" maskColor="rgba(20, 20, 22, 0.55)"
             nodeColor={(n) => (n.type === "terminal" ? "#ececf0" : MINI[statuses[n.id]?.state] || "#6b6b74")}
             nodeStrokeWidth={0} nodeBorderRadius={3} />
  );
}

// 从空白开始：画布右上角的分步清单（左边是“输入”端点，不能挡住它的插孔）。步骤状态由 guideSteps 根据程序算出，这里只负责显示和收起
export function BuildGuide({ guide, open, onToggle }) {
  const doneCount = guide.steps.filter((s) => s.done).length;
  return (
    <Panel position="top-right" className={`guide ${open ? "" : "closed"}`}>
      <div className="guide-head">
        <span className="guide-title">从空白搭一个工作流</span>
        <span className="guide-count">{doneCount}/{guide.steps.length}</span>
        <button type="button" className="link" onClick={onToggle} aria-expanded={open}>{open ? "收起" : "展开"}</button>
      </div>
      {open && (
        <ol className="guide-steps">
          {guide.steps.map((s, i) => (
            <li key={s.key} className={s.done ? "done" : i === guide.current ? "now" : ""}>
              <span className="guide-mark" aria-hidden="true">{s.done ? "✓" : i + 1}</span>
              <div>
                <div className="guide-step">{s.title}<span className="sr-only">{s.done ? "（已完成）" : i === guide.current ? "（下一步）" : ""}</span></div>
                {i === guide.current && <p className="guide-how">{s.how}</p>}
              </div>
            </li>
          ))}
        </ol>
      )}
      {open && guide.current === -1 && <p className="guide-how">都完成了。点右上角“保存到我的”把它存下来。</p>}
    </Panel>
  );
}
