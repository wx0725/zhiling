// 画布上的碰撞：同一层里的节点（包括循环框、输入输出端点）不能互相重叠；连线不参与碰撞。
// 循环框的大小由框里的节点决定：框里的节点被拖到边上或拖出框时，框跟着扩大，扩大后挤到的邻居被推开。
// 这里只做纯计算（输入画布节点，输出新位置），不碰 React，便于单独测试。
import { TOP, PAD, NODE_W, dict } from "./graph.js";

// 以节点 id 为键的表一律不带原型：id 可以叫 constructor 之类的名字，普通对象会读到原型上的东西
const byKey = (entries) => Object.assign(dict(), Object.fromEntries(entries));

export const GAP = 10; // 节点之间至少留出的空隙

// 节点在它那一层里的矩形。尺寸优先用排版时算的（拖动时和改动后重新画图时都一样，不会因为“量到的”和“算的”差几像素而来回推），
// 没有才用画布实际量到的
export function rectOf(n) {
  const w = n.data?.width ?? n.measured?.width ?? n.width ?? NODE_W;
  const h = n.data?.height ?? n.measured?.height ?? n.height ?? 60;
  return { x: n.position.x, y: n.position.y, w, h };
}

export const overlaps = (a, b, gap = GAP) =>
  a.x < b.x + b.w + gap && b.x < a.x + a.w + gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;

const hitsAny = (r, obstacles, gap) => obstacles.some((o) => overlaps(r, o, gap));

// 拖动中的一步：从 prev 移到 next。撞上原来没碰着的节点时，先试只走横向、再试只走纵向（贴着边滑），都不行就停在原地。
// 原来就压着别的节点（旧数据）时，只要不再撞上新的节点就放行，让它能被拖出来。
export function slide(prev, next, size, obstacles) {
  const at = (p) => ({ ...p, w: size.w, h: size.h });
  const already = obstacles.filter((o) => overlaps(at(prev), o));
  const free = (p) => !hitsAny(at(p), obstacles.filter((o) => !already.includes(o)), GAP);
  for (const p of [next, { x: next.x, y: prev.y }, { x: prev.x, y: next.y }]) if (free(p)) return p;
  return prev;
}

// 同时拖动多个节点：整组用同一个位移，先试完整位移、再试只横向、只纵向，哪个节点都不撞才走，避免队形被挡散。
// items：[{id, prev, next, size, obstacles}]，返回 {id: 位置}
export function slideGroup(items) {
  if (items.length === 1) {
    const [it] = items;
    return byKey([[it.id, slide(it.prev, it.next, it.size, it.obstacles)]]);
  }
  const d = { x: items[0].next.x - items[0].prev.x, y: items[0].next.y - items[0].prev.y };
  const fits = (dx, dy) => items.every((it) => {
    const p = { x: it.prev.x + dx, y: it.prev.y + dy };
    return slide(it.prev, p, it.size, it.obstacles) === p;
  });
  const [dx, dy] = [[d.x, d.y], [d.x, 0], [0, d.y]].find(([a, b]) => fits(a, b)) || [0, 0];
  return byKey(items.map((it) => [it.id, { x: it.prev.x + dx, y: it.prev.y + dy }]));
}

// 拖动中收到的位置变化 → 经过碰撞修正的变化。memo（Map）记着每个节点修正后的位置：
// 松手时 React Flow 还会再发一次 dragging 为假、带原始位置的变化，也换成修正后的位置，否则松手瞬间会压上别人
export function guardChanges(changes, nodes, memo) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const dragging = new Set(changes.filter((c) => c.type === "position" && c.dragging).map((c) => c.id));
  const items = changes.filter((c) => c.type === "position" && c.dragging && c.position && byId.has(c.id)).map((c) => {
    const n = byId.get(c.id);
    const obstacles = nodes.filter((o) => o.parentId === n.parentId && o.id !== n.id && !dragging.has(o.id)).map(rectOf);
    return { id: c.id, prev: n.position, next: c.position, size: rectOf(n), obstacles };
  });
  const allowed = items.length ? slideGroup(items) : dict();
  for (const [id, p] of Object.entries(allowed)) memo.set(id, p);
  return changes.map((c) => {
    if (c.type !== "position") return c;
    const p = allowed[c.id] || (!c.dragging && c.position && memo.get(c.id));
    return p ? { ...c, position: p } : c;
  });
}

// 放下时：离 rect 最近、不与任何障碍重叠的位置（rect 本身不重叠就原地不动）。
// 候选位置取附近障碍的左右边界（横坐标）与上下边界（纵坐标）的组合，所以斜着挪开更近时也能找到。
export function nearestFree(rect, obstacles) {
  if (!hitsAny(rect, obstacles, GAP)) return { x: rect.x, y: rect.y };
  const dist = (p) => Math.hypot(p.x - rect.x, p.y - rect.y);
  const center = (o) => Math.hypot(o.x + o.w / 2 - rect.x - rect.w / 2, o.y + o.h / 2 - rect.y - rect.h / 2);
  for (const take of [12, 40]) { // 先看最近的 12 个障碍，不行再看 40 个；节点很多时也不会卡
    const near = [...obstacles].sort((a, b) => center(a) - center(b)).slice(0, take);
    const xs = [rect.x, ...near.flatMap((o) => [o.x - rect.w - GAP, o.x + o.w + GAP])];
    const ys = [rect.y, ...near.flatMap((o) => [o.y - rect.h - GAP, o.y + o.h + GAP])];
    let best = null;
    for (const x of xs) for (const y of ys) {
      const p = { x, y };
      if ((!best || dist(p) < dist(best)) && !hitsAny({ ...rect, ...p }, obstacles, GAP)) best = p;
    }
    if (best) return best;
  }
  const right = Math.max(...obstacles.map((o) => o.x + o.w)); // 实在挤不下：放到所有节点右边
  return { x: right + GAP, y: rect.y };
}

// 把 others 里压着 fixed 的矩形推开，被推动的再去推别人（连锁），有上限防止失控。
// 推的方向：fixed 带着 grew（循环框刚向哪些方向扩大，或它自己刚被往哪推）时只往这些方向推，取其中最短的，
// 这样框向右扩大时右边的邻居被往右推、被推的再把后面的往右推；否则朝邻居所在的一侧推。
const DIRS = ["right", "left", "down", "up"];
// 挪动一个矩形：x、y 是它占的地方，ax、ay 是它画在哪（自动伸缩的框两者不同：占的地方由框里节点决定）
const shove = (r, dx, dy) => { r.x += dx; r.y += dy; r.ax += dx; r.ay += dy; };
function pushAway(fixed, others) {
  const queue = [fixed];
  for (let guard = 0; queue.length && guard < 200; guard++) {
    const f = queue.shift();
    for (const o of others) {
      if (o === f || !overlaps(f, o)) continue;
      const moves = [
        { x: f.x + f.w + GAP - o.x, y: 0 }, { x: f.x - GAP - (o.x + o.w), y: 0 },
        { x: 0, y: f.y + f.h + GAP - o.y }, { x: 0, y: f.y - GAP - (o.y + o.h) },
      ];
      let i;
      if (f.grew?.length) {
        const len = (k) => Math.abs(moves[k].x) + Math.abs(moves[k].y);
        i = f.grew.map((d) => DIRS.indexOf(d)).sort((a, b) => len(a) - len(b))[0];
      } else {
        const sx = (o.x + o.w / 2 - (f.x + f.w / 2)) / (f.w + o.w);
        const sy = (o.y + o.h / 2 - (f.y + f.h / 2)) / (f.h + o.h);
        i = Math.abs(sx) >= Math.abs(sy) ? (sx >= 0 ? 0 : 1) : (sy >= 0 ? 2 : 3);
      }
      shove(o, moves[i].x, moves[i].y);
      o.grew = [DIRS[i]];
      queue.push(o);
    }
  }
}

// 整理一次画布：moved 是刚拖动、刚加入或刚改过的节点 id（优先保持它们的位置）。
// 返回 {id: 新位置}（画布坐标，框里的节点相对所在循环框），只含位置变了的节点。
// 1. 拖动的节点若压着同层节点，挪到最近的空位；
// 2. 循环框装不下框里的节点（拖到边上、拖出框、新加的节点在下面）就扩大：向左、向上扩大时框本身左移、上移，
//    框里的节点相应右移、下移，看上去原地不动；嵌套的框由内向外一层层处理；
// 3. 还有重叠的（框变大了、节点接线后变高了）：先处理的节点不动，把压到的邻居推开，被推的再推别人。
// 4. 开着“自动收缩”的框跟着框里的节点回缩（四边都回缩，左边、上边回缩时框右移、下移，框里节点在屏幕上不动）；
//    关掉自动收缩的框（data.manual 记着大小，可以拉伸）不回缩，只在装不下时变大。
// opts.base：拖动开始时各循环框的尺寸（拖动中框已经跟着变大了，要和拖动前比才知道往哪边扩大了）
// opts.sizesOut：传入 Map 时，关掉自动收缩的框因为向左、向上扩大而要改记的大小写进去（{w, h}），保持框的右边、下边不动
// opts.pinned：位置不能动、只能推开别人的节点（例如刚被拉伸的框：拉大压到邻居时推开邻居，而不是把框挪走）
export function settle(nodes, moved = [], { base = new Map(), sizesOut = null, pinned = [] } = {}) {
  const rect = new Map(nodes.map((n) => {
    const r = rectOf(n);
    return [n.id, { id: n.id, parent: n.parentId, manual: n.data?.manual || null, ...r, ax: r.x, ay: r.y }];
  }));
  const start = new Map([...rect].map(([id, r]) => [id, { x: r.ax, y: r.ay }]));
  const all = [...rect.values()];
  const kids = (pid) => all.filter((r) => r.parent === pid);
  const depth = (r) => (r.parent && rect.has(r.parent) ? 1 + depth(rect.get(r.parent)) : 0);

  for (const id of moved) {
    const r = rect.get(id);
    if (!r) continue;
    const p = nearestFree(r, kids(r.parent).filter((s) => s !== r && !moved.includes(s.id)));
    shove(r, p.x - r.x, p.y - r.y);
  }

  const boxes = all.filter((r) => kids(r.id).length).sort((a, b) => depth(b) - depth(a)); // 由内向外
  const levels = [...new Set(all.map((r) => r.parent))].sort((a, b) => depth(rect.get(b) || {}) - depth(rect.get(a) || {}));
  // 先处理的不动：刚拖动、刚加的节点，以及刚扩大的循环框（框要保持框里节点在屏幕上不动，只能推开邻居、不能被邻居推走）
  const rank = (r) => (moved.includes(r.id) || pinned.includes(r.id) || r.expanded ? 0 : 1);
  for (let pass = 0; pass < 4; pass++) {
    const before = JSON.stringify(all.map((r) => [r.x, r.y, r.w, r.h]));
    for (const box of boxes) {
      const inner = kids(box.id);
      const L0 = box.x;
      const T0 = box.y;
      const R0 = box.x + (pass === 0 && base.has(box.id) ? Math.min(box.w, base.get(box.id).w) : box.w);
      const B0 = box.y + (pass === 0 && base.has(box.id) ? Math.min(box.h, base.get(box.id).h) : box.h);
      const minX = () => Math.min(...inner.map((c) => c.x));
      const minY = () => Math.min(...inner.map((c) => c.y));
      if (!box.manual) {
        // 自动伸缩的框：四边各自往中心收，收到碰到框里的节点为止，正好包住它们（画图时也是这样画的）。
        // 框里节点的坐标相对框画的位置（ax、ay），不用挪；框画的位置也不因为伸缩而变
        const mx = minX();
        const my = minY();
        box.x = box.ax + mx - PAD;
        box.y = box.ay + my - TOP;
        box.w = Math.max(NODE_W, Math.max(...inner.map((c) => c.x + c.w)) - mx + PAD * 2);
        box.h = Math.max(...inner.map((c) => c.y + c.h)) - my + PAD + TOP;
      } else {
        const shiftKids = (dx, dy) => { for (const c of inner) shove(c, dx, dy); };
        const dx = Math.max(0, PAD - minX());
        const dy = Math.max(0, TOP - minY());
        if (dx || dy) { // 关掉自动收缩的框装不下：向左、向上扩大，右边、下边留在原处
          shove(box, -dx, -dy);
          shiftKids(dx, dy);
          box.manual = { w: box.manual.w + dx, h: box.manual.h + dy };
          sizesOut?.set(box.id, box.manual);
        }
        const needW = Math.max(NODE_W, ...inner.map((c) => c.x + c.w + PAD));
        const needH = Math.max(...inner.map((c) => c.y + c.h + PAD));
        box.w = Math.max(box.manual.w, needW); // 至少保持记下的大小
        box.h = Math.max(box.manual.h, needH);
        if (box.w > box.manual.w || box.h > box.manual.h) { // 被撑大后也不回缩：记下新的大小
          box.manual = { w: box.w, h: box.h };
          sizesOut?.set(box.id, box.manual);
        }
      }
      box.grew = [...(box.x < L0 ? ["left"] : []), ...(box.y < T0 ? ["up"] : []),
        ...(box.x + box.w > R0 ? ["right"] : []), ...(box.y + box.h > B0 ? ["down"] : [])];
      if (box.grew.length) box.expanded = true;
    }
    for (const level of levels) {
      const order = kids(level).sort((a, b) => rank(a) - rank(b) || a.y - b.y || a.x - b.x);
      order.forEach((f, i) => pushAway(f, order.slice(i + 1)));
    }
    if (JSON.stringify(all.map((r) => [r.x, r.y, r.w, r.h])) === before) break;
  }

  const out = dict();
  for (const [id, r] of rect) {
    const s = start.get(id);
    if (Math.round(r.ax) !== Math.round(s.x) || Math.round(r.ay) !== Math.round(s.y)) out[id] = { x: r.ax, y: r.ay };
  }
  return out;
}

// 拉伸循环框：next（{x, y, width, height}，框在它那一层的新位置和大小）会不会压到框里的节点。
// start：开始拉伸时框的位置 {x, y} 和框里各节点相对框的矩形 kids。拉左边、上边时画布库会让框里节点在屏幕上不动
export function canResize(start, next) {
  if (next.width < NODE_W || next.height < TOP + PAD) return false;
  if (!start.kids.length) return true;
  const dx = next.x - start.x;
  const dy = next.y - start.y;
  return start.kids.every((k) => k.x - dx >= PAD && k.y - dy >= TOP
    && k.x - dx + k.w + PAD <= next.width && k.y - dy + k.h + PAD <= next.height);
}

// 框正好装下框里节点时的大小（自动伸缩的框就是这么大）
export function fitSize(kids) {
  if (!kids.length) return { w: NODE_W, h: TOP + PAD };
  const minX = Math.min(...kids.map((k) => k.x));
  const minY = Math.min(...kids.map((k) => k.y));
  return { w: Math.max(NODE_W, Math.max(...kids.map((k) => k.x + k.w)) - minX + PAD * 2),
    h: Math.max(...kids.map((k) => k.y + k.h)) - minY + PAD + TOP };
}

// 拖动中也时时伸缩：开着自动收缩的框始终正好包住框里的节点（四边各自收到碰到节点为止），关掉的框装不下时向右、向下变大。
// 拖动中不挪框的位置（挪了会打乱画布库拖动时的坐标缓存，造成卡顿、松手错位），只改框“画出来的范围”：
// data.visual = {x, y}（相对框位置的偏移）和 data.width/height；松手写回后按程序重画，框的位置自然对齐。由内向外处理
export function fitWhileDragging(nodes) {
  const list = nodes.map((n) => n);
  const index = new Map(list.map((n, i) => [n.id, i]));
  const kidsOf = new Map();
  for (const n of list) if (n.parentId) (kidsOf.get(n.parentId) || kidsOf.set(n.parentId, []).get(n.parentId)).push(n.id);
  const depth = (n) => (n.parentId && index.has(n.parentId) ? 1 + depth(list[index.get(n.parentId)]) : 0);
  const loops = list.filter((n) => n.type === "loop" && kidsOf.has(n.id)).sort((a, b) => depth(b) - depth(a));
  let changed = false;
  for (const loop of loops) {
    const n = list[index.get(loop.id)];
    // 里层框画出来的范围也算进去（它的 visual 偏移）
    const inner = kidsOf.get(n.id).map((id) => {
      const c = list[index.get(id)];
      const r = rectOf(c);
      const v = c.data?.visual || { x: 0, y: 0 };
      return { ...r, x: r.x + v.x, y: r.y + v.y };
    });
    let visual = { x: 0, y: 0 };
    let w;
    let h;
    if (!n.data.manual) {
      const mx = Math.min(...inner.map((c) => c.x));
      const my = Math.min(...inner.map((c) => c.y));
      visual = { x: mx - PAD, y: my - TOP };
      w = Math.max(NODE_W, Math.max(...inner.map((c) => c.x + c.w)) - mx + PAD * 2);
      h = Math.max(...inner.map((c) => c.y + c.h)) - my + PAD + TOP;
    } else {
      w = Math.max(n.data.width, ...inner.map((c) => c.x + c.w + PAD));
      h = Math.max(n.data.height, ...inner.map((c) => c.y + c.h + PAD));
    }
    const old = n.data.visual || { x: 0, y: 0 };
    if (visual.x === old.x && visual.y === old.y && w === n.data.width && h === n.data.height) continue;
    changed = true;
    list[index.get(n.id)] = { ...n, data: { ...n.data, width: w, height: h, visual } };
  }
  return changed ? list : nodes;
}
