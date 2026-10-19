"""画布编辑：每个操作都是“旧工作流 JSON → 新工作流 JSON”，不改动传入的对象。
画布节点 id 用“/”表示所在的循环体（"each/fetch" 在循环 each 的 body 里），这里据此找到要改的那一层图。
工作流 JSON 是唯一的程序；画布只是它的样子，改完重新画。
（移植自 web/src/edit.js。JS 里用 undefined 表示“删掉这一项”的地方，这里用 UNSET。）"""
import copy
import re

from ._js import UNSET, is_finite, js_round, js_str, truthy

__all__ = [
    "UNSET", "PORT_NAME", "split_id", "graph_at", "set_config", "set_label", "set_max_items", "connect",
    "remove_edge", "remove_edges", "remove_node", "unique_id", "add_node", "move_node", "set_box_size", "arrange_body",
    "reparent",
    "freeze_layout", "add_port", "rename_port", "set_port_type", "set_port_fields", "remove_port", "set_meta",
]


def _clone(v):
    return copy.deepcopy(v)


def _find(nodes, nid):
    for x in nodes or []:
        if isinstance(x, dict) and x.get("id") == nid:
            return x
    return None


def split_id(canvas_id):
    """"each/fetch" → {"scope": ["each"], "local": "fetch"}"""
    parts = js_str(canvas_id).split("/")
    return {"scope": parts[:-1], "local": parts[-1]}


def graph_at(wf, scope):
    """找到某一层图（scope 为空是工作流本身；否则依次进入各个循环的 body）；找不到时抛 ValueError"""
    g = wf
    for nid in scope:
        n = _find(g.get("nodes"), nid)
        if not n or n.get("kind") != "foreach" or not truthy(n.get("body")):
            raise ValueError(f"找不到循环 {nid}")
        g = n["body"]
    return g


def _node_at(wf, canvas_id):
    s = split_id(canvas_id)
    g = graph_at(wf, s["scope"])
    n = _find(g.get("nodes"), s["local"])
    if not n:
        raise ValueError(f"找不到节点 {canvas_id}")
    return g, n


def set_config(wf, canvas_id, port, value=UNSET):
    """改固定参数：value 不给（UNSET）时删掉这一项（回到默认值或改为接线）；None 是 JSON 的 null，照样写进去"""
    out = _clone(wf)
    _, n = _node_at(out, canvas_id)
    cfg = dict(n["config"]) if isinstance(n.get("config"), dict) else {}
    if value is UNSET:
        cfg.pop(port, None)
    else:
        cfg[port] = value
    if cfg:
        n["config"] = cfg
    else:
        n.pop("config", None)
    return out


def set_label(wf, canvas_id, label):
    """改节点的显示名称（label）；空字符串表示不起名字，用模块名"""
    out = _clone(wf)
    _, n = _node_at(out, canvas_id)
    if isinstance(label, str) and label.strip():
        n["label"] = label.strip()
    else:
        n.pop("label", None)
    return out


def set_max_items(wf, canvas_id, max_items):
    """改循环的最多项数"""
    out = _clone(wf)
    _, n = _node_at(out, canvas_id)
    n["max_items"] = max_items
    return out


def _handle(h, prefix):
    return re.sub(rf"^{prefix}:", "", js_str(h) if truthy(h) else "", count=1)


def connect(wf, conn):
    """画布上的一次连线（{"source","sourceHandle","target","targetHandle"}）→ 程序里的一条边。
    返回 {"wf": 新工作流} 或 {"error": 原因}；连线已存在时返回的 wf 就是传入的那个。规则：
    - 两端必须在同一层图里（不能从循环外直接连进循环体里的节点）；
    - 一个输入只能有一个来源：接到已有来源的输入时替换旧线，并去掉这个输入的固定值；
    - 接到节点顶部（ctrl）的是控制线，只能从条件分支的 then / else 拉出。"""
    s = split_id(conn.get("source"))
    t = split_id(conn.get("target"))
    if "/".join(s["scope"]) != "/".join(t["scope"]):
        return {"error": "只能连接同一层里的节点：循环里的节点要通过循环框里的“每一项”和“这一项的结果”连出去"}
    if s["local"] == t["local"]:
        return {"error": "不能把节点连回自己"}
    sp = _handle(conn.get("sourceHandle"), "out")
    if not sp:
        return {"error": "请从节点右侧的输出插孔开始拉线"}
    out = _clone(wf)
    g = graph_at(out, s["scope"])
    g["edges"] = g.get("edges") or []
    frm = f"{s['local']}.{sp}"
    edges = g["edges"]

    def has(to):
        return any(isinstance(e, dict) and e.get("from") == frm and e.get("to") == to for e in edges)
    if conn.get("targetHandle") == "ctrl":
        src = _find(g.get("nodes"), s["local"])
        if not src or src.get("kind") != "if" or sp not in ("then", "else"):
            return {"error": "控制线只能从条件分支的 then 或 else 拉出"}
        if has(t["local"]):
            return {"wf": wf}
        edges.append({"from": frm, "to": t["local"]})
        return {"wf": out}
    tp = _handle(conn.get("targetHandle"), "in")
    if not tp:
        return {"error": "请连到节点左侧的输入插孔"}
    to = f"{t['local']}.{tp}"
    if has(to):
        return {"wf": wf}
    g["edges"] = [e for e in edges if not (isinstance(e, dict) and e.get("to") == to)]  # 一个输入只接一条线
    g["edges"].append({"from": frm, "to": to})
    tn = _find(g.get("nodes"), t["local"])
    if tn and isinstance(tn.get("config"), dict) and tp in tn["config"]:
        del tn["config"][tp]  # 接了线就不再用固定值
        if not tn["config"]:
            del tn["config"]
    return {"wf": out}


def remove_edge(wf, edge):
    """删一条线：edge = {"scope": 所在那一层（画布节点 id 的前缀）, "from", "to"}，from/to 与程序里写的一致"""
    out = _clone(wf)
    g = graph_at(out, edge.get("scope") or [])
    frm, to = edge.get("from"), edge.get("to")
    g["edges"] = [e for e in (g.get("edges") or []) if not (isinstance(e, dict) and e.get("from") == frm and e.get("to") == to)]
    return out


def remove_edges(wf, edges):
    """一次删几条线（每条的写法同 remove_edge），算作一步改动"""
    out = wf
    for e in edges:
        out = remove_edge(out, e)
    return out


def remove_node(wf, canvas_id):
    """删节点：连着它的线一起删；位置记录也删。输入、输出端点不能删（原样返回传入的 wf）"""
    s = split_id(canvas_id)
    local = s["local"]
    if local.startswith("$"):
        return wf
    out = _clone(wf)
    g = graph_at(out, s["scope"])
    g["nodes"] = [n for n in (g.get("nodes") or []) if not (isinstance(n, dict) and n.get("id") == local)]

    def touches(end):
        return isinstance(end, str) and (end == local or end.startswith(f"{local}."))
    g["edges"] = [e for e in (g.get("edges") or []) if not (isinstance(e, dict) and (touches(e.get("from")) or touches(e.get("to"))))]
    if isinstance(g.get("layout"), dict):
        g["layout"].pop(local, None)
    return out


def reparent(wf, canvas_id, scope, position=None):
    """把一个节点挪到另一层图（拖进循环框 = 挪进那个循环的 body；“移出循环框” = 挪到外面一层）。
    不同层之间不能连线，所以连着它的线都断开（返回里 dropped 是断了几条）；节点的参数、名字原样带过去，
    新的一层里重名时改个 id。逐项循环不能放进别的循环（不支持嵌套），输入、输出端点不能挪。
    position：在新一层里的 layout 坐标。返回 {"wf", "id"（新的画布 id）, "dropped"}；不能挪时返回 {"error": 原因}"""
    s = split_id(canvas_id)
    local = s["local"]
    if local.startswith("$"):
        return {"error": "输入、输出端点不能挪进或挪出循环框"}
    if list(scope) == s["scope"]:
        return {"error": "已经在这一层了"}
    out = _clone(wf)
    src = graph_at(out, s["scope"])
    node = _find(src.get("nodes"), local)
    if not node:
        raise ValueError(f"找不到节点 {canvas_id}")
    if node.get("kind") == "foreach" and scope:
        return {"error": "逐项循环不能放进另一个循环里"}
    if scope and scope[: len(s["scope"]) + 1] == [*s["scope"], local]:
        return {"error": "不能把循环挪进它自己里面"}

    def touches(end):
        return isinstance(end, str) and (end == local or end.startswith(f"{local}."))
    edges = src.get("edges") or []
    kept = [e for e in edges if not (isinstance(e, dict) and (touches(e.get("from")) or touches(e.get("to"))))]
    dropped = len(edges) - len(kept)
    src["edges"] = kept
    src["nodes"] = [n for n in (src.get("nodes") or []) if n is not node]
    if isinstance(src.get("layout"), dict):
        src["layout"].pop(local, None)
    dst = graph_at(out, scope)
    dst["nodes"] = dst.get("nodes") or []
    dst["edges"] = dst.get("edges") or []
    nid = unique_id(dst, local)
    dst["nodes"].append({**node, "id": nid})
    if truthy(position):
        dst["layout"] = {**(dst.get("layout") or {}), nid: _round(position)}
    return {"wf": out, "id": "/".join([*scope, nid]), "dropped": dropped}


def unique_id(g, base):
    """这一层里没人用的节点 id：base 里非字母数字的字符换成下划线，数字开头加 n，重名时加 _2、_3……"""
    taken = {n.get("id") for n in (g.get("nodes") or []) if isinstance(n, dict)}
    clean = re.sub(r"[^\w]", "_", js_str(base), flags=re.ASCII)
    clean = re.sub(r"^(\d)", r"n\1", clean, flags=re.ASCII) or "node"
    if clean not in taken:
        return clean
    i = 2
    while f"{clean}_{i}" in taken:
        i += 1
    return f"{clean}_{i}"


def _round(p):
    return {"x": js_round(p["x"]), "y": js_round(p["y"])}


def add_node(wf, scope, spec, position=None):
    """加节点。spec：{"kind": "module", "module": "text.format@0"} 或 {"kind": "if" | "merge" | "foreach"}
    position：在这一层图里的位置（与 layout 同一坐标）。返回 {"wf", "id"（画布 id）}"""
    out = _clone(wf)
    g = graph_at(out, scope)
    g["nodes"] = g.get("nodes") or []
    g["edges"] = g.get("edges") or []
    kind = spec.get("kind")
    if kind == "module":
        base = spec["module"].split("@")[0].split(".")[-1]
    else:
        base = {"if": "branch", "merge": "merge", "foreach": "each"}.get(kind, UNSET)
    nid = unique_id(g, base)
    node = {"id": nid, "kind": kind}
    if kind == "module":
        node["module"] = spec["module"]
    if kind == "foreach":
        node["max_items"] = 20
        node["body"] = {"inputs": {"item": {"type": "any"}}, "outputs": {"result": {"type": "any"}}, "nodes": [], "edges": []}
    g["nodes"].append(node)
    if truthy(position):
        g["layout"] = {**(g.get("layout") or {}), nid: _round(position)}
    return {"wf": out, "id": "/".join([*scope, nid])}


def _keep_size(old):
    # 关掉自动收缩的循环框 layout 里还记着 w、h：只改位置时要留着它们
    if isinstance(old, dict) and is_finite(old.get("w")) and is_finite(old.get("h")):
        return {"w": old["w"], "h": old["h"]}
    return {}


def _old(g, local):
    lay = g.get("layout")
    return lay.get(local) if isinstance(lay, dict) else None


def move_node(wf, canvas_id, position):
    """拖动后记下位置（只影响显示，不影响运行）"""
    s = split_id(canvas_id)
    out = _clone(wf)
    g = graph_at(out, s["scope"])
    local = s["local"]
    g["layout"] = {**(g.get("layout") or {}), local: {**_keep_size(_old(g, local)), **_round(position)}}
    return out


def set_box_size(wf, canvas_id, size):
    """关掉自动收缩（或拉伸）后记下循环框的大小（只影响显示）；size 为 None 时去掉，回到“跟着框里节点自动伸缩”"""
    s = split_id(canvas_id)
    out = _clone(wf)
    g = graph_at(out, s["scope"])
    local = s["local"]
    old = _old(g, local)
    pos = {"x": old["x"], "y": old["y"]} if isinstance(old, dict) and is_finite(old.get("x")) and is_finite(old.get("y")) else {"x": 0, "y": 0}
    val = {**pos, "w": js_round(size["w"]), "h": js_round(size["h"])} if truthy(size) else pos
    g["layout"] = {**(g.get("layout") or {}), local: val}
    return out


def arrange_body(wf, canvas_id):
    """整理循环框里的节点：去掉框里这一层记下的位置，重新画图时按连线自动排版。框里的框记着的大小留着。
    留下的只有大小、没有位置，不合程序格式：画好后由调用方把这一层的位置全部写回"""
    s = split_id(canvas_id)
    out = _clone(wf)
    body = graph_at(out, [*s["scope"], s["local"]])
    boxes = {n.get("id") for n in (body.get("nodes") or []) if isinstance(n, dict) and n.get("kind") == "foreach"}  # 已经不在的节点的旧记录不留
    lay = body.get("layout") if isinstance(body.get("layout"), dict) else {}
    kept = [(k, _keep_size(v)) for k, v in lay.items() if k in boxes]
    kept = [(k, v) for k, v in kept if "w" in v]
    if kept:
        body["layout"] = dict(kept)
    else:
        body.pop("layout", None)
    return out


def freeze_layout(wf, positions):
    """第一次编辑前，把画布上当前的位置全部写进 layout：之后加线、删节点时其余节点不会因为重新排版而跳动。
    positions：画布 id → 该层坐标（循环体里的节点已减去循环框的边距）；找不到所在那一层的跳过"""
    out = _clone(wf)
    for canvas_id, pos in positions.items():
        s = split_id(canvas_id)
        try:
            g = graph_at(out, s["scope"])
        except Exception:
            continue
        local = s["local"]
        g["layout"] = {**(g.get("layout") or {}), local: {**_keep_size(_old(g, local)), **_round(pos)}}
    return out


# ---------- 工作流自己的输入、输出（画布上的“输入”“输出”端点）和名称说明 ----------
# side："inputs" 或 "outputs"。改名、删除时，连着它的线（$in.名字 / $out.名字）跟着改或删。

PORT_NAME = re.compile(r"[a-z][a-z0-9_]*")  # 用 fullmatch 判断


def _endpoint(side, name):
    return f"{'$in' if side == 'inputs' else '$out'}.{name}"


def _check_name(ports, name, except_):
    if not isinstance(name, str) or not PORT_NAME.fullmatch(name):
        return "名字只能用小写英文字母开头，后面跟小写字母、数字或下划线，例如 url、score_2"
    if name != except_ and name in ports:
        return f"已经有叫 {name} 的了"
    return ""


def add_port(wf, side, type="string"):
    """加一个输入或输出，名字 input / input_2 …… 返回 {"wf", "name"}"""
    out = _clone(wf)
    out[side] = dict(out.get(side) or {})
    base = "input" if side == "inputs" else "output"
    name = base
    i = 2
    while name in out[side]:
        name = f"{base}_{i}"
        i += 1
    out[side][name] = {"type": type}
    return {"wf": out, "name": name}


def rename_port(wf, side, frm, to):
    """改名，连线跟着改；返回 {"wf"} 或 {"error"}；名字没变时返回的 wf 就是传入的那个"""
    if frm == to:
        return {"wf": wf}
    error = _check_name(wf.get(side) or {}, to, frm)
    if error:
        return {"error": error}
    out = _clone(wf)
    # 保持原来的顺序，只换这一个键
    out[side] = {(to if k == frm else k): v for k, v in out[side].items()}
    a = _endpoint(side, frm)
    b = _endpoint(side, to)

    def fix(e):
        if not isinstance(e, dict):
            return e
        if side == "inputs":
            return {**e, "from": b} if e.get("from") == a else e
        return {**e, "to": b} if e.get("to") == a else e
    out["edges"] = [fix(e) for e in (out.get("edges") or [])]
    return {"wf": out}


def set_port_type(wf, side, name, type):
    """改类型：原来的默认值多半不再合适，一起去掉"""
    out = _clone(wf)
    p = {**out[side][name], "type": type}
    if p["type"] != wf[side][name].get("type"):
        p.pop("default", None)
    out[side][name] = p
    return out


def set_port_fields(wf, side, name, fields):
    """改说明、是否必填、默认值：fields 里值为 UNSET、None 或空字符串的项删掉"""
    out = _clone(wf)
    p = dict(out[side][name])
    for k, v in fields.items():
        if v is UNSET or v is None or v == "":
            p.pop(k, None)
        else:
            p[k] = v
    out[side][name] = p
    return out


def remove_port(wf, side, name):
    """删一个输入或输出，连着它的线一起删"""
    out = _clone(wf)
    out[side] = dict(out[side])
    out[side].pop(name, None)
    a = _endpoint(side, name)

    def keep(e):
        if not isinstance(e, dict):
            return True
        return e.get("from") != a if side == "inputs" else e.get("to") != a
    out["edges"] = [e for e in (out.get("edges") or []) if keep(e)]
    return out


def set_meta(wf, name=None, intent=None):
    """改工作流名称和说明：不给（None）的保持不变"""
    out = _clone(wf)
    if name is not None:
        out["name"] = name
    if intent is not None:
        out["intent"] = intent
    return out
