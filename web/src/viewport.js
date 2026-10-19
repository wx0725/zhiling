// 打开工作流时的初始视野（纯计算，便于单独测试）。
// 最小缩放：再小节点上的字就看不清了。整张图放不下时，宁可从左边（输入）开始显示，用拖动和小地图看其余部分。
export const READABLE_ZOOM = 0.75;
export const FIT = { padding: 0.08, maxZoom: 1.1 };

export function initialViewport(bounds, width, height, readable = READABLE_ZOOM) {
  const pad = 32;
  const fit = Math.min((width - pad * 2) / bounds.width, (height - pad * 2) / bounds.height, FIT.maxZoom);
  if (fit >= readable) return null; // 放得下：交给 fitView 居中
  const zoom = readable;
  return { zoom, x: pad - bounds.x * zoom, y: height / 2 - (bounds.y + bounds.height / 2) * zoom };
}

// 整张图（bounds）在当前视野（transform = [x, y, zoom]、画布宽高）里是否完全可见
export function wholeGraphVisible(bounds, [x, y, zoom], width, height) {
  if (!bounds || !width || !height) return true;
  const left = bounds.x * zoom + x;
  const top = bounds.y * zoom + y;
  return left >= -1 && top >= -1 && left + bounds.width * zoom <= width + 1 && top + bounds.height * zoom <= height + 1;
}

// 新节点不要压在已有节点上（否则会挡住插孔，没法拉线）：从期望位置开始，往下找一块空地
const NEW_W = 196;
const NEW_H = 90;
export function freeSpot(want, nodes) {
  const boxes = nodes.map((n) => ({
    x: n.position.x, y: n.position.y,
    w: n.measured?.width ?? n.data?.width ?? 200, h: n.measured?.height ?? n.data?.height ?? 80,
  }));
  const hit = (p) => boxes.some((b) => p.x < b.x + b.w + 24 && p.x + NEW_W + 24 > b.x && p.y < b.y + b.h + 24 && p.y + NEW_H + 24 > b.y);
  for (let i = 0; i < 40; i++) {
    const p = { x: want.x, y: want.y + i * 60 };
    if (!hit(p)) return p;
  }
  return want;
}
