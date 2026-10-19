"""把工作流 JSON 转成画布的节点和连线，没保存位置的节点自动排版（见 layered.py，代替网页版的 dagre）。
约定：画布节点 id 用“/”表示嵌套，例如循环体里的 fetch 为 "each/fetch"；
工作流自己的输入、输出画成两个端点节点（"$in"、"$out"），循环体里是 "each/$in"、"each/$out"。
工作流里保存了 layout（节点 id → {x, y}）时优先用保存的坐标，缺的才自动排版。
生成过程中的草稿可能缺字段或连到不存在的节点，这里只画能画的部分，不抛错。
（移植自 web/src/graph.js，节点、连线的字典形状与网页版完全一致。）"""
import re

from . import layered
from ._js import is_finite, json_stringify, truthy

NODE_W = 196
HEAD = 46  # 节点标题区高度
ROW = 22  # 每个端口一行
PAD = 28  # 循环框左、右、下的内边距
# 循环框里第一行节点到框顶的距离：标题栏下面只留一点空，四边看起来差不多宽（循环体里节点的画布位置 = 程序里的 layout 坐标 + (PAD, TOP)）
TOP = HEAD + 4


def node_height(n_in, n_out):
    return HEAD + max(n_in, n_out, 1) * ROW + 12


def port_top(i):
    return HEAD + i * ROW + ROW // 2


def _own(o, k):
    # Object.hasOwn：只认字典自己的键；键不是文字（坏数据）时当作没有
    if not isinstance(o, dict):
        return False
    try:
        return k in o
    except TypeError:
        return False


def _is_obj(v):
    return isinstance(v, dict)


def _arr(v):
    return v if isinstance(v, list) else []


def _type_of(p):
    return p["type"] if isinstance(p, dict) and isinstance(p.get("type"), str) else "any"


def _desc(p):
    return p.get("description") if isinstance(p, dict) else None


def _label(p):
    """端口在节点上显示的中文短名：声明里的 label；没有时，说明本身很短（如工作流输入“分数”“网址列表”）就用说明"""
    if not isinstance(p, dict):
        return None
    if isinstance(p.get("label"), str) and p["label"]:
        return p["label"]
    d = p.get("description")
    return d if isinstance(d, str) and 0 < len(d) <= 6 else None


def _is_pos(p):
    return _is_obj(p) and is_finite(p.get("x")) and is_finite(p.get("y"))


def _short(v):
    s = v if isinstance(v, str) else json_stringify(v)
    return s[:13] + "…" if len(s) > 14 else s


def ports_of(node, modules, body=None, connected=None, ref_ports=None):
    """节点的输入、输出端口：{"ins": [{name, type, desc, fixed?, full?}], "outs": [...]}。
    模块节点只显示必填、已接线或已固定的输入，可选又没用到的（如 temperature）不画，免得淹没真正的连线"""
    connected = connected if connected is not None else set()
    ref_ports = ref_ports if ref_ports is not None else {}
    kind = node.get("kind")
    if kind == "module":
        m = modules[node.get("module")] if _own(modules, node.get("module")) else None
        if not truthy(m):
            return {"ins": [], "outs": []}
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        decl_in = m.get("inputs") if isinstance(m, dict) and isinstance(m.get("inputs"), dict) else {}
        decl_out = m.get("outputs") if isinstance(m, dict) and isinstance(m.get("outputs"), dict) else {}
        ins = []
        for name, p in decl_in.items():
            p = p if isinstance(p, dict) else {}
            # 画出：接了线的、写了固定值的、必填的，以及默认为空、本来就是用来接线的可选输入（如 text、value、b）。
            # 有具体默认值的设置项（超时、温度等）不画，在节点详情里改
            show = (name in connected or _own(cfg, name)
                    or (p.get("required") is not False and "default" not in p)
                    or ("default" in p and p["default"] is None)
                    or (isinstance(p.get("default"), str) and p["default"] == ""))
            if show:
                fixed = _own(cfg, name)
                ins.append({"name": name, "type": p.get("type"), "desc": p.get("description"), "label": _label(p),
                            "fixed": _short(cfg[name]) if fixed else None, "full": cfg[name] if fixed else None})
        outs = [{"name": name, "type": (p if isinstance(p, dict) else {}).get("type"), "desc": _desc(p), "label": _label(p)}
                for name, p in decl_out.items()]
        return {"ins": ins, "outs": outs}
    if kind == "if":
        return {
            "ins": [{"name": "condition", "type": "boolean", "desc": "条件：真走 then，假走 else", "label": "是否"},
                    {"name": "value", "type": "any", "optional": True, "desc": "要往下传的数据（可不接，只用控制线）", "label": "数据"}],
            "outs": [{"name": "then", "type": "any", "desc": "条件为真时走这一路", "label": "是"},
                     {"name": "else", "type": "any", "desc": "条件为假时走这一路", "label": "否"}],
        }
    if kind == "merge":
        # ports 写错（不是文字列表）时按默认的 a、b 画，错误由校验报告
        ports = node.get("ports")
        names = ports if isinstance(ports, list) and all(isinstance(x, str) for x in ports) else ["a", "b"]
        return {"ins": [{"name": n, "type": "any", "optional": True, "desc": "各条支路之一，哪一路有值就用哪一路", "label": f"支路{n}"} for n in names],
                "outs": [{"name": "value", "type": "any", "desc": "实际走到的那一路的值", "label": "结果"}]}
    if kind == "foreach":
        b_in = body.get("inputs") if isinstance(body, dict) else None
        b_out = body.get("outputs") if isinstance(body, dict) else None
        return {"ins": [{"name": "items", "type": f"list<{_type_of(b_in['item']) if _own(b_in, 'item') else 'any'}>", "label": "列表"}],
                "outs": [{"name": "results", "type": f"list<{_type_of(b_out['result']) if _own(b_out, 'result') else 'any'}>", "label": "结果列表"}]}
    # subflow：内嵌的用 body，引用文件的用服务端读出的端口（/api/ports）
    if not truthy(body) and isinstance(node.get("ref"), str) and _own(ref_ports, node["ref"]):
        body = ref_ports[node["ref"]]

    def decl(d):
        return list(d.items()) if isinstance(d, dict) else []
    b = body if isinstance(body, dict) else {}
    return {
        "ins": [{"name": n, "type": _type_of(p), "desc": _desc(p), "label": _label(p)} for n, p in decl(b.get("inputs"))],
        "outs": [{"name": n, "type": _type_of(p), "desc": _desc(p), "label": _label(p)} for n, p in decl(b.get("outputs"))],
    }


def _layout_level(g, prefix, modules, parent_id, ref_ports):
    nodes = []
    edges = []
    sizes = {}
    order = []  # 交给自动排版的节点（加入的顺序）

    def cid(local):
        return f"{prefix}/{local}" if prefix else local

    # 端点节点：工作流（或循环体）的输入和输出
    in_ports = [{"name": n, "type": _type_of(p), "desc": _desc(p), "label": _label(p)} for n, p in (g.get("inputs").items() if _is_obj(g.get("inputs")) else [])]
    out_ports = [{"name": n, "type": _type_of(p), "desc": _desc(p), "label": _label(p)} for n, p in (g.get("outputs").items() if _is_obj(g.get("outputs")) else [])]
    terminals = [
        {"local": "$in", "kind": "terminal_in", "ins": [], "outs": in_ports, "title": "每一项" if prefix else "输入"},
        {"local": "$out", "kind": "terminal_out", "ins": out_ports, "outs": [], "title": "这一项的结果" if prefix else "输出"},
    ]
    for t in terminals:
        if not t["ins"] and not t["outs"]:
            continue
        h = node_height(len(t["ins"]), len(t["outs"]))
        sizes[t["local"]] = {"w": 120, "h": h}
        order.append(t["local"])
        nodes.append({"id": cid(t["local"]), "type": "terminal",
                      "data": {"title": t["title"], "ins": t["ins"], "outs": t["outs"], "terminal": t["kind"], "width": 120, "height": h},
                      "parentId": parent_id})

    g_nodes = [n for n in _arr(g.get("nodes")) if _is_obj(n) and isinstance(n.get("id"), str) and n["id"]]
    known = {n["id"] for n in g_nodes}
    for t in terminals:
        if t["local"] in sizes:
            known.add(t["local"])

    # 只画两端都存在的连线
    def keep(e):
        if not _is_obj(e) or not isinstance(e.get("from"), str) or not isinstance(e.get("to"), str) or "." not in e["from"]:
            return False
        return e["from"].split(".")[0] in known and e["to"].split(".")[0] in known
    g_edges = [e for e in _arr(g.get("edges")) if keep(e)]

    connected = {}  # 节点 → 接了线的输入端口
    for e in g_edges:
        if "." not in e["to"]:
            continue
        tn, tp = e["to"].split(".")[:2]
        connected.setdefault(tn, set()).add(tp)

    saved = g.get("layout") if _is_obj(g.get("layout")) else {}
    for n in g_nodes:
        body = n.get("body") if _is_obj(n.get("body")) else None
        ports = ports_of(n, modules, body, connected.get(n["id"]), ref_ports)
        ins, outs = ports["ins"], ports["outs"]
        w = NODE_W
        h = node_height(len(ins), len(outs))
        children = None
        manual = None
        offset = None
        if n.get("kind") == "foreach" and body is not None:
            children = _layout_level(body, cid(n["id"]), modules, cid(n["id"]), ref_ports)
            m = saved[n["id"]] if _own(saved, n["id"]) else None
            if _is_obj(m) and is_finite(m.get("w")) and is_finite(m.get("h")) and m["w"] > 0 and m["h"] > 0:
                # 关掉了自动收缩的框：layout 里记着 w、h，框至少这么大（装不下框里的节点时照样变大），不自动回缩，可以拉伸。
                # 旧数据里框内节点可能在负坐标（框左边、上边之外）：整体右移、下移，框把它们全部装下
                manual = {"w": m["w"], "h": m["h"]}
                children["shift"] = {"x": max(0, -children["minX"]), "y": max(0, -children["minY"])}
                w = max(NODE_W, children["width"] + children["shift"]["x"] + PAD * 2, m["w"])
                h = max(children["height"] + children["shift"]["y"] + PAD + TOP, m["h"])
            else:
                # 开着自动收缩的框（默认）：四边各自往里收，收到碰到框里的节点为止，即正好包住框里的节点（留出内边距）。
                # 框画在 layout 位置再加上框里节点的最小坐标（offset），框里节点相应减去它，所以框里节点在画布上的位置不受影响
                children["shift"] = {"x": -children["minX"], "y": -children["minY"]}
                offset = {"x": children["minX"], "y": children["minY"]}
                w = max(NODE_W, children["width"] - children["minX"] + PAD * 2)
                h = children["height"] - children["minY"] + PAD + TOP
        sizes[n["id"]] = {"w": w, "h": h}
        order.append(n["id"])
        shift = children["shift"] if children else {"x": 0, "y": 0}
        is_loop = n.get("kind") == "foreach"
        mod = None
        if n.get("kind") == "module" and _own(modules, n.get("module")):
            mod = modules[n["module"]]
        fn = {
            "id": cid(n["id"]),
            "type": "loop" if is_loop else "module",
            "data": {"node": n, "ins": ins, "outs": outs, "module": mod, "width": w, "height": h,
                     "shift": shift, "manual": manual, "offset": offset},
            "parentId": parent_id,  # 不限制在框内：框里的节点拖到框外时，框会扩大把它装下（见 collide.py）
        }
        if is_loop:
            fn["style"] = {"width": w, "height": h}
        nodes.append(fn)
        if children:
            for c in children["nodes"]:
                if c.get("parentId") == cid(n["id"]):
                    c["position"] = {"x": c["position"]["x"] + children["shift"]["x"] + PAD,
                                     "y": c["position"]["y"] + children["shift"]["y"] + TOP}
            nodes.extend(children["nodes"])
            edges.extend(children["edges"])

    links = []
    for i, e in enumerate(g_edges):
        sn, sp = e["from"].split(".")[:2]
        control = "." not in e["to"]
        tn, tp = (e["to"], None) if control else e["to"].split(".")[:2]
        links.append((sn, tn))
        edges.append({
            "id": cid(f"e{i}"),
            "source": cid(sn),
            "sourceHandle": f"out:{sp}",
            "target": cid(tn),
            "targetHandle": "ctrl" if control else f"in:{tp}",
            "type": "default",  # 贝塞尔弧线：跟着节点位置自动弯曲
            "data": {"control": control, "from": e["from"], "to": e["to"], "scope": prefix.split("/") if prefix else []},
            "className": "edge-control" if control else "edge-data",
        })

    # 全部节点都保存过位置时不用自动排版（结果反正用不上）
    need = any(not (_own(saved, k) and _is_pos(saved[k])) for k in order)
    centers = layered.layout([(k, sizes[k]["w"], sizes[k]["h"]) for k in order], links, nodesep=26, ranksep=48) if need else {}
    inf = float("inf")
    width = -inf  # 框里节点的最右、最下边界（可能是负数：旧数据里节点全在左上方）
    height = -inf
    min_x = inf
    min_y = inf
    cut = len(prefix) + 1 if prefix else 0
    mine = [(nd, nd["id"][cut:]) for nd in nodes if nd["id"][cut:] in sizes and "position" not in nd]
    is_saved = {local for _, local in mine if _own(saved, local) and _is_pos(saved[local])}
    for nd, local in mine:
        if local in is_saved:
            # 自动伸缩的框：保存过位置时画在 layout 位置加 offset 处；没保存过时直接用自动排版的位置（排版已按框的实际大小留好间距），
            # 写回时减去 offset，下次再加回来，位置不变
            off = (nd.get("data") or {}).get("offset") or {"x": 0, "y": 0}
            nd["position"] = {"x": saved[local]["x"] + off["x"], "y": saved[local]["y"] + off["y"]}
    if centers:
        _place_unsaved(mine, is_saved, centers, sizes, links)
    for nd, local in mine:
        s = sizes[local]
        width = max(width, nd["position"]["x"] + s["w"])
        height = max(height, nd["position"]["y"] + s["h"])
        min_x = min(min_x, nd["position"]["x"])
        min_y = min(min_y, nd["position"]["y"])
    if min_x == inf:
        min_x = min_y = width = height = 0
    return {"nodes": nodes, "edges": edges, "width": width, "height": height, "minX": min_x, "minY": min_y}


def _place_unsaved(mine, is_saved, centers, sizes, links):
    """没保存过位置的节点（例如示例只存了中间节点、没存输入输出端点）：自动排版的坐标和保存的坐标不是一套，直接用会压在一起。
    所以按自动排版里它和最近的、保存过位置的相连节点的相对位置摆放，再横着挪开仍然压到的节点"""
    def auto_pos(local):
        cx, cy = centers[local]
        return {"x": cx - sizes[local]["w"] / 2, "y": cy - sizes[local]["h"] / 2}
    pos = {local: nd["position"] for nd, local in mine if local in is_saved}
    near = {}
    for a, b in links:
        near.setdefault(a, []).append(b)
        near.setdefault(b, []).append(a)

    def anchor(local):
        # 沿连线找最近的保存过位置的节点；连线上没有时用任意一个保存过的节点
        seen, todo = {local}, [local]
        while todo:
            nxt = []
            for k in todo:
                for m in near.get(k, []):
                    if m in is_saved:
                        return m
                    if m not in seen and m in centers:
                        seen.add(m)
                        nxt.append(m)
            todo = nxt
        return next((k for _, k in mine if k in is_saved), None)

    for nd, local in mine:
        if local in is_saved:
            continue
        p = auto_pos(local)
        a = anchor(local)
        if a is not None:
            ap = auto_pos(a)
            p = {"x": p["x"] - ap["x"] + pos[a]["x"], "y": p["y"] - ap["y"] + pos[a]["y"]}
        w, h = sizes[local]["w"], sizes[local]["h"]
        # 仍然压到已经摆好的节点：朝自动排版里它所在的一侧挪开（在左边的往左挪，否则往右）
        for _ in range(len(pos) + 1):
            hit = next((k for k, q in pos.items() if p["x"] < q["x"] + sizes[k]["w"] + 26 and q["x"] < p["x"] + w + 26
                        and p["y"] < q["y"] + sizes[k]["h"] + 26 and q["y"] < p["y"] + h + 26), None)
            if hit is None:
                break
            q = pos[hit]
            if centers[local][0] < centers[hit][0]:
                p = {"x": q["x"] - w - 48, "y": p["y"]}
            else:
                p = {"x": q["x"] + sizes[hit]["w"] + 48, "y": p["y"]}
        nd["position"] = p
        pos[local] = p


def flow_to_layout(flow_nodes):
    """画布位置 → 程序里 layout 的坐标：循环体里的节点去掉框的边距，以及画图时为装下负坐标而整体移动的量（框的 data.shift）。
    与 to_flow 的换算互为逆运算，所以无论 layout 里是否有旧的负坐标，写回去再画出来位置都不变。
    返回 conv(画布节点, 位置 = 它当前的位置) → layout 坐标"""
    by_id = {n["id"]: n for n in flow_nodes}

    def conv(n, p=None):
        if p is None:
            p = n["position"]
        off = (n.get("data") or {}).get("offset") or {"x": 0, "y": 0}  # 自动伸缩的框自己画的时候加过 offset
        q = {"x": p["x"] - off["x"], "y": p["y"] - off["y"]}
        if not n.get("parentId"):
            return q
        parent = by_id.get(n["parentId"])
        s = ((parent or {}).get("data") or {}).get("shift") or {"x": 0, "y": 0}
        return {"x": q["x"] - PAD - s["x"], "y": q["y"] - TOP - s["y"]}
    return conv


def layout_moves(flow_nodes, moves):
    """一批画布上的新位置（{画布 id: 位置}）→ 要写进 layout 的坐标（{画布 id: layout 坐标}）。
    某个框画图时整体移动过（data.shift 不为零）而这次只动了框里的一部分节点：最小坐标会变、下次画图的移动量也会变，
    所以把这一层的节点全部按不移动（shift 为零）重新写一遍，写完这一层就没有负坐标了，下次画出来位置不变"""
    all_ = dict(moves)
    # 只有关掉自动收缩的框需要这样：自动伸缩的框每次画图都按最小坐标重新对齐（offset），写一部分节点也不会错位
    shifted = set()
    for n in flow_nodes:
        d = n.get("data") or {}
        if truthy(d.get("manual")) and d.get("shift") and (truthy(d["shift"].get("x")) or truthy(d["shift"].get("y"))):
            shifted.add(n["id"])
    # 动到的节点往上任何一层是这样的框（后代动了也会改变它的最小坐标），这一层都要整层重写
    by_id = {n["id"]: n for n in flow_nodes}
    touched = set()
    for nid in all_:
        p = (by_id.get(nid) or {}).get("parentId")
        while p:
            if p in shifted:
                touched.add(p)
            p = (by_id.get(p) or {}).get("parentId")
    for n in flow_nodes:
        if n.get("parentId") in touched and n["id"] not in all_:
            all_[n["id"]] = n["position"]
    conv = flow_to_layout(flow_nodes)
    out = {}
    for n in flow_nodes:
        if n["id"] not in all_:
            continue
        p = all_[n["id"]]
        if n.get("parentId") in touched:
            out[n["id"]] = conv(dict(n, parentId=None), {"x": p["x"] - PAD, "y": p["y"] - TOP})
        else:
            out[n["id"]] = conv(n, p)
    return out


def to_flow(wf, modules, ref_ports=None):
    """工作流 → {"nodes": 画布节点, "edges": 画布连线}"""
    if not _is_obj(wf):
        return {"nodes": [], "edges": []}
    res = _layout_level(wf, "", modules if modules is not None else {}, None, ref_ports if ref_ports is not None else {})
    # 父节点排在子节点之前（稳定排序，同一层保持原来的先后）
    nodes = sorted(res["nodes"], key=lambda n: len(n["parentId"].split("/")) if n.get("parentId") else 0)
    return {"nodes": nodes, "edges": res["edges"]}


_ITEM = re.compile(r"\[(\d+)\]", re.ASCII)


def path_to_id(path, wf_id):
    """运行记录的路径（如 "summarize_many.each[1].fetch"）→ {"id": 画布节点 id（"each/fetch"）, "items": [第几项]}"""
    rest = path[len(wf_id) + 1:] if path.startswith(f"{wf_id}.") else path
    items = [int(m.group(1)) for m in _ITEM.finditer(rest)]
    return {"id": "/".join(_ITEM.sub("", rest).split(".")), "items": items}


def write_modules(wf, modules):
    """图里（含循环体、内嵌子流程）用到的、会写文件或产生外部影响的模块，例如 ["file.write@0"]。运行前要使用者确认"""
    found = set()

    def walk(g):
        for n in (g.get("nodes") if isinstance(g, dict) and isinstance(g.get("nodes"), list) else []):
            if not isinstance(n, dict):
                continue
            mod = n.get("module")
            if n.get("kind") == "module" and _own(modules, mod) and isinstance(modules[mod], dict) and modules[mod].get("side_effects") == "write":
                found.add(mod)
            if isinstance(n.get("body"), (dict, list)) and n.get("body") is not None:
                walk(n["body"])
    walk(wf)
    return sorted(found)


_UNCONFIRMED = re.compile(r"模块 (\S+@\d+) 会")  # “会写文件…”或“会读取…”，未获得确认


def unconfirmed_module(message):
    """服务端因为没确认而拦下的写模块：not_confirmed 的报错是“模块 file.write@0 会写文件或产生外部影响，未获得确认”"""
    m = _UNCONFIRMED.search(message) if isinstance(message, str) else None
    return m.group(1) if m else None
