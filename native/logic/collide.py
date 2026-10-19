"""画布上的碰撞：同一层里的节点（包括循环框、输入输出端点）不能互相重叠；连线不参与碰撞。
循环框的大小由框里的节点决定：框里的节点被拖到边上或拖出框时，框跟着扩大，扩大后挤到的邻居被推开。
这里只做纯计算（输入画布节点，输出新位置），不碰界面，便于单独测试。（移植自 web/src/collide.js）"""
import math

from ._js import js_round, truthy
from .graph import NODE_W, PAD, TOP

GAP = 10  # 节点之间至少留出的空隙

_ZERO = {"x": 0, "y": 0}


def _first(*vals):
    # JS 的 ??：取第一个不是 None 的
    for v in vals:
        if v is not None:
            return v
    return None


def rect_of(n):
    """节点在它那一层里的矩形 {x, y, w, h}。尺寸优先用排版时算的（拖动时和改动后重新画图时都一样，
    不会因为“量到的”和“算的”差几像素而来回推），没有才用画布实际量到的"""
    d = n.get("data") or {}
    m = n.get("measured") or {}
    w = _first(d.get("width"), m.get("width"), n.get("width"), NODE_W)
    h = _first(d.get("height"), m.get("height"), n.get("height"), 60)
    return {"x": n["position"]["x"], "y": n["position"]["y"], "w": w, "h": h}


def overlaps(a, b, gap=GAP):
    return (a["x"] < b["x"] + b["w"] + gap and b["x"] < a["x"] + a["w"] + gap
            and a["y"] < b["y"] + b["h"] + gap and b["y"] < a["y"] + a["h"] + gap)


def _hits_any(r, obstacles, gap):
    return any(overlaps(r, o, gap) for o in obstacles)


def slide(prev, next_, size, obstacles):
    """拖动中的一步：从 prev 移到 next_。撞上原来没碰着的节点时，先试只走横向、再试只走纵向（贴着边滑），都不行就停在原地。
    原来就压着别的节点（旧数据）时，只要不再撞上新的节点就放行，让它能被拖出来。
    能走到 next_ 时返回的就是 next_ 这个对象本身（slide_group 靠它判断）"""
    def at(p):
        return {**p, "w": size["w"], "h": size["h"]}
    here = at(prev)
    rest = [o for o in obstacles if not overlaps(here, o)]  # 原来就压着的不算
    for p in (next_, {"x": next_["x"], "y": prev["y"]}, {"x": prev["x"], "y": next_["y"]}):
        if not _hits_any(at(p), rest, GAP):
            return p
    return prev


def slide_group(items):
    """同时拖动多个节点：整组用同一个位移，先试完整位移、再试只横向、只纵向，哪个节点都不撞才走，避免队形被挡散。
    items：[{id, prev, next, size, obstacles}]，返回 {id: 位置}"""
    if len(items) == 1:
        it = items[0]
        return {it["id"]: slide(it["prev"], it["next"], it["size"], it["obstacles"])}
    d = {"x": items[0]["next"]["x"] - items[0]["prev"]["x"], "y": items[0]["next"]["y"] - items[0]["prev"]["y"]}

    def fits(dx, dy):
        for it in items:
            p = {"x": it["prev"]["x"] + dx, "y": it["prev"]["y"] + dy}
            if slide(it["prev"], p, it["size"], it["obstacles"]) is not p:
                return False
        return True
    dx, dy = next(((a, b) for a, b in ((d["x"], d["y"]), (d["x"], 0), (0, d["y"])) if fits(a, b)), (0, 0))
    return {it["id"]: {"x": it["prev"]["x"] + dx, "y": it["prev"]["y"] + dy} for it in items}


def guard_changes(changes, nodes, memo, passable_loops=False):
    """拖动中收到的位置变化 → 经过碰撞修正的变化。changes 的形状与 React Flow 相同：
    {"type": "position", "id", "position": {x, y}, "dragging": True/False}，其他类型的变化原样放行。
    memo（字典，调用方保存）记着每个节点修正后的位置：松手时还会再发一次 dragging 为假、带原始位置的变化，
    也换成修正后的位置，否则松手瞬间会压上别人。
    passable_loops：循环框不挡路（单个节点正要拖进框里时）"""
    by_id = {n["id"]: n for n in nodes}
    dragging = {c.get("id") for c in changes if c.get("type") == "position" and truthy(c.get("dragging"))}
    items = []
    for c in changes:
        if not (c.get("type") == "position" and truthy(c.get("dragging")) and truthy(c.get("position")) and c.get("id") in by_id):
            continue
        n = by_id[c["id"]]
        obstacles = [rect_of(o) for o in nodes
                     if o.get("parentId") == n.get("parentId") and o["id"] != n["id"] and o["id"] not in dragging
                     and not (passable_loops and o.get("type") == "loop")]
        items.append({"id": c["id"], "prev": n["position"], "next": c["position"], "size": rect_of(n), "obstacles": obstacles})
    allowed = slide_group(items) if items else {}
    for cid, p in allowed.items():
        memo[cid] = p
    out = []
    for c in changes:
        if c.get("type") != "position":
            out.append(c)
            continue
        p = allowed.get(c.get("id"))
        if p is None and not truthy(c.get("dragging")) and truthy(c.get("position")):
            p = memo.get(c.get("id"))
        out.append({**c, "position": p} if p is not None else c)
    return out


def nearest_free(rect, obstacles):
    """放下时：离 rect 最近、不与任何障碍重叠的位置（rect 本身不重叠就原地不动）。
    候选位置取附近障碍的左右边界（横坐标）与上下边界（纵坐标）的组合，所以斜着挪开更近时也能找到。"""
    if not _hits_any(rect, obstacles, GAP):
        return {"x": rect["x"], "y": rect["y"]}

    def dist(p):
        return math.hypot(p["x"] - rect["x"], p["y"] - rect["y"])

    def center(o):
        return math.hypot(o["x"] + o["w"] / 2 - rect["x"] - rect["w"] / 2, o["y"] + o["h"] / 2 - rect["y"] - rect["h"] / 2)
    for take in (12, 40):  # 先看最近的 12 个障碍，不行再看 40 个；节点很多时也不会卡
        near = sorted(obstacles, key=center)[:take]
        xs = [rect["x"]] + [v for o in near for v in (o["x"] - rect["w"] - GAP, o["x"] + o["w"] + GAP)]
        ys = [rect["y"]] + [v for o in near for v in (o["y"] - rect["h"] - GAP, o["y"] + o["h"] + GAP)]
        best = None
        for x in xs:
            for y in ys:
                p = {"x": x, "y": y}
                if (best is None or dist(p) < dist(best)) and not _hits_any({**rect, **p}, obstacles, GAP):
                    best = p
        if best:
            return best
    right = max(o["x"] + o["w"] for o in obstacles)  # 实在挤不下：放到所有节点右边
    return {"x": right + GAP, "y": rect["y"]}


# 把 others 里压着 fixed 的矩形推开，被推动的再去推别人（连锁），有上限防止失控。
# 推的方向：fixed 带着 grew（循环框刚向哪些方向扩大，或它自己刚被往哪推）时只往这些方向推，取其中最短的，
# 这样框向右扩大时右边的邻居被往右推、被推的再把后面的往右推；否则朝邻居所在的一侧推。
_DIRS = ["right", "left", "down", "up"]


def _shove(r, dx, dy):
    # 挪动一个矩形：x、y 是它占的地方，ax、ay 是它画在哪（自动伸缩的框两者不同：占的地方由框里节点决定）
    r["x"] += dx
    r["y"] += dy
    r["ax"] += dx
    r["ay"] += dy


def _push_away(fixed, others):
    queue = [fixed]
    guard = 0
    while queue and guard < 200:
        guard += 1
        f = queue.pop(0)
        for o in others:
            if o is f or not overlaps(f, o):
                continue
            moves = [
                (f["x"] + f["w"] + GAP - o["x"], 0), (f["x"] - GAP - (o["x"] + o["w"]), 0),
                (0, f["y"] + f["h"] + GAP - o["y"]), (0, f["y"] - GAP - (o["y"] + o["h"])),
            ]
            if f.get("grew"):
                i = min((_DIRS.index(d) for d in f["grew"]), key=lambda k: abs(moves[k][0]) + abs(moves[k][1]))
            else:
                sx = (o["x"] + o["w"] / 2 - (f["x"] + f["w"] / 2)) / (f["w"] + o["w"])
                sy = (o["y"] + o["h"] / 2 - (f["y"] + f["h"] / 2)) / (f["h"] + o["h"])
                i = (0 if sx >= 0 else 1) if abs(sx) >= abs(sy) else (2 if sy >= 0 else 3)
            _shove(o, moves[i][0], moves[i][1])
            o["grew"] = [_DIRS[i]]
            queue.append(o)


def settle(nodes, moved=(), base=None, sizes_out=None, pinned=()):
    """整理一次画布：moved 是刚拖动、刚加入或刚改过的节点 id（优先保持它们的位置）。
    返回 {id: 新位置}（画布坐标，框里的节点相对所在循环框），只含位置变了的节点。
    1. 拖动的节点若压着同层节点，挪到最近的空位；
    2. 循环框装不下框里的节点（拖到边上、拖出框、新加的节点在下面）就扩大：向左、向上扩大时框本身左移、上移，
       框里的节点相应右移、下移，看上去原地不动；嵌套的框由内向外一层层处理；
    3. 还有重叠的（框变大了、节点接线后变高了）：先处理的节点不动，把压到的邻居推开，被推的再推别人。
    4. 开着“自动收缩”的框跟着框里的节点回缩（四边都回缩，左边、上边回缩时框右移、下移，框里节点在屏幕上不动）；
       关掉自动收缩的框（data.manual 记着大小，可以拉伸）不回缩，只在装不下时变大。
    base：拖动开始时各循环框的尺寸 {id: {w, h}}（拖动中框已经跟着变大了，要和拖动前比才知道往哪边扩大了）
    sizes_out：传入字典时，关掉自动收缩的框因为扩大而要改记的大小写进去（{w, h}），保持框的右边、下边不动
    pinned：位置不能动、只能推开别人的节点（例如刚被拉伸的框：拉大压到邻居时推开邻居，而不是把框挪走）"""
    base = base if base is not None else {}
    moved = list(moved)
    pinned = list(pinned)
    rect = {}
    for n in nodes:
        r = rect_of(n)
        manual = (n.get("data") or {}).get("manual")
        rect[n["id"]] = {"id": n["id"], "parent": n.get("parentId"), "manual": manual if truthy(manual) else None,
                         **r, "ax": r["x"], "ay": r["y"]}
    start = {i: {"x": r["ax"], "y": r["ay"]} for i, r in rect.items()}
    all_ = list(rect.values())

    def kids(pid):
        return [r for r in all_ if r["parent"] == pid]

    def depth(r):
        p = r.get("parent")
        return 1 + depth(rect[p]) if p and p in rect else 0

    for nid in moved:
        r = rect.get(nid)
        if not r:
            continue
        p = nearest_free(r, [s for s in kids(r["parent"]) if s is not r and s["id"] not in moved])
        _shove(r, p["x"] - r["x"], p["y"] - r["y"])

    boxes = sorted((r for r in all_ if kids(r["id"])), key=lambda r: -depth(r))  # 由内向外
    levels = list(dict.fromkeys(r["parent"] for r in all_))
    levels.sort(key=lambda lv: -depth(rect.get(lv) or {}))

    # 先处理的不动：刚拖动、刚加的节点，以及刚扩大的循环框（框要保持框里节点在屏幕上不动，只能推开邻居、不能被邻居推走）
    def rank(r):
        return 0 if r["id"] in moved or r["id"] in pinned or r.get("expanded") else 1

    def snap():
        return [(r["x"], r["y"], r["w"], r["h"]) for r in all_]
    for pass_ in range(4):
        before = snap()
        for box in boxes:
            inner = kids(box["id"])
            l0 = box["x"]
            t0 = box["y"]
            first = pass_ == 0 and box["id"] in base
            r0 = box["x"] + (min(box["w"], base[box["id"]]["w"]) if first else box["w"])
            b0 = box["y"] + (min(box["h"], base[box["id"]]["h"]) if first else box["h"])
            if not box["manual"]:
                # 自动伸缩的框：四边各自往中心收，收到碰到框里的节点为止，正好包住它们（画图时也是这样画的）。
                # 框里节点的坐标相对框画的位置（ax、ay），不用挪；框画的位置也不因为伸缩而变
                mx = min(c["x"] for c in inner)
                my = min(c["y"] for c in inner)
                box["x"] = box["ax"] + mx - PAD
                box["y"] = box["ay"] + my - TOP
                box["w"] = max(NODE_W, max(c["x"] + c["w"] for c in inner) - mx + PAD * 2)
                box["h"] = max(c["y"] + c["h"] for c in inner) - my + PAD + TOP
            else:
                dx = max(0, PAD - min(c["x"] for c in inner))
                dy = max(0, TOP - min(c["y"] for c in inner))
                if dx or dy:  # 关掉自动收缩的框装不下：向左、向上扩大，右边、下边留在原处
                    _shove(box, -dx, -dy)
                    for c in inner:
                        _shove(c, dx, dy)
                    box["manual"] = {"w": box["manual"]["w"] + dx, "h": box["manual"]["h"] + dy}
                    if sizes_out is not None:
                        sizes_out[box["id"]] = box["manual"]
                need_w = max(NODE_W, *(c["x"] + c["w"] + PAD for c in inner))
                need_h = max(c["y"] + c["h"] + PAD for c in inner)
                box["w"] = max(box["manual"]["w"], need_w)  # 至少保持记下的大小
                box["h"] = max(box["manual"]["h"], need_h)
                if box["w"] > box["manual"]["w"] or box["h"] > box["manual"]["h"]:  # 被撑大后也不回缩：记下新的大小
                    box["manual"] = {"w": box["w"], "h": box["h"]}
                    if sizes_out is not None:
                        sizes_out[box["id"]] = box["manual"]
            box["grew"] = ((["left"] if box["x"] < l0 else []) + (["up"] if box["y"] < t0 else [])
                           + (["right"] if box["x"] + box["w"] > r0 else []) + (["down"] if box["y"] + box["h"] > b0 else []))
            if box["grew"]:
                box["expanded"] = True
        for level in levels:
            order = sorted(kids(level), key=lambda r: (rank(r), r["y"], r["x"]))
            for i, f in enumerate(order):
                _push_away(f, order[i + 1:])
        if snap() == before:
            break

    out = {}
    for nid, r in rect.items():
        s = start[nid]
        if js_round(r["ax"]) != js_round(s["x"]) or js_round(r["ay"]) != js_round(s["y"]):
            out[nid] = {"x": r["ax"], "y": r["ay"]}
    return out


def can_resize(start, next_):
    """拉伸循环框：next_（{x, y, width, height}，框在它那一层的新位置和大小）会不会压到框里的节点。
    start：开始拉伸时框的位置 {x, y} 和框里各节点相对框的矩形 kids。拉左边、上边时框里节点在屏幕上不动"""
    if next_["width"] < NODE_W or next_["height"] < TOP + PAD:
        return False
    if not start["kids"]:
        return True
    dx = next_["x"] - start["x"]
    dy = next_["y"] - start["y"]
    return all(k["x"] - dx >= PAD and k["y"] - dy >= TOP
               and k["x"] - dx + k["w"] + PAD <= next_["width"] and k["y"] - dy + k["h"] + PAD <= next_["height"]
               for k in start["kids"])


def fit_size(kids):
    """框正好装下框里节点时的大小 {w, h}（自动伸缩的框就是这么大）"""
    if not kids:
        return {"w": NODE_W, "h": TOP + PAD}
    min_x = min(k["x"] for k in kids)
    min_y = min(k["y"] for k in kids)
    return {"w": max(NODE_W, max(k["x"] + k["w"] for k in kids) - min_x + PAD * 2),
            "h": max(k["y"] + k["h"] for k in kids) - min_y + PAD + TOP}


def fit_while_dragging(nodes):
    """拖动中也时时伸缩：开着自动收缩的框始终正好包住框里的节点（四边各自收到碰到节点为止），关掉的框装不下时向右、向下变大。
    拖动中不挪框的位置，只改框“画出来的范围”：data.visual = {x, y}（相对框位置的偏移）和 data.width/height；
    松手写回后按程序重画，框的位置自然对齐。由内向外处理。
    没变时返回传入的那个列表本身；变了返回新列表，没变的节点仍是原来的对象，变了的框换成新字典"""
    lst = list(nodes)
    index = {n["id"]: i for i, n in enumerate(lst)}
    kids_of = {}
    for n in lst:
        if n.get("parentId"):
            kids_of.setdefault(n["parentId"], []).append(n["id"])

    def depth(n):
        p = n.get("parentId")
        return 1 + depth(lst[index[p]]) if p and p in index else 0
    loops = sorted((n for n in lst if n.get("type") == "loop" and n["id"] in kids_of), key=lambda n: -depth(n))
    changed = False
    for loop in loops:
        n = lst[index[loop["id"]]]
        # 里层框画出来的范围也算进去（它的 visual 偏移）
        inner = []
        for cid in kids_of[n["id"]]:
            c = lst[index[cid]]
            r = rect_of(c)
            v = (c.get("data") or {}).get("visual") or _ZERO
            inner.append({**r, "x": r["x"] + v["x"], "y": r["y"] + v["y"]})
        data = n["data"]
        visual = {"x": 0, "y": 0}
        if not truthy(data.get("manual")):
            mx = min(c["x"] for c in inner)
            my = min(c["y"] for c in inner)
            visual = {"x": mx - PAD, "y": my - TOP}
            w = max(NODE_W, max(c["x"] + c["w"] for c in inner) - mx + PAD * 2)
            h = max(c["y"] + c["h"] for c in inner) - my + PAD + TOP
        else:
            w = max(data["width"], *(c["x"] + c["w"] + PAD for c in inner))
            h = max(data["height"], *(c["y"] + c["h"] + PAD for c in inner))
        old = data.get("visual") or _ZERO
        if visual["x"] == old["x"] and visual["y"] == old["y"] and w == data.get("width") and h == data.get("height"):
            continue
        changed = True
        lst[index[n["id"]]] = {**n, "data": {**data, "width": w, "height": h, "visual": visual}}
    return lst if changed else nodes
