"""打开工作流时的初始视野（纯计算，便于单独测试）。（移植自 web/src/viewport.js）
最小缩放：再小节点上的字就看不清了。整张图放不下时，宁可从左边（输入）开始显示，用拖动和小地图看其余部分。"""
from ._js import js_div, truthy

READABLE_ZOOM = 0.75
FIT = {"padding": 0.08, "maxZoom": 1.1}


def initial_viewport(bounds, width, height, readable=READABLE_ZOOM):
    """bounds：整张图的范围 {x, y, width, height}。放得下返回 None（交给“适应画布”居中），否则返回 {zoom, x, y}"""
    pad = 32
    fit = min(js_div(width - pad * 2, bounds["width"]), js_div(height - pad * 2, bounds["height"]), FIT["maxZoom"])
    if fit >= readable:
        return None  # 放得下：交给 fitView 居中
    zoom = readable
    return {"zoom": zoom, "x": pad - bounds["x"] * zoom, "y": height / 2 - (bounds["y"] + bounds["height"] / 2) * zoom}


def whole_graph_visible(bounds, transform, width, height):
    """整张图（bounds）在当前视野（transform = (x, y, zoom)、画布宽高）里是否完全可见"""
    if not bounds or not truthy(width) or not truthy(height):
        return True
    x, y, zoom = transform
    left = bounds["x"] * zoom + x
    top = bounds["y"] * zoom + y
    return left >= -1 and top >= -1 and left + bounds["width"] * zoom <= width + 1 and top + bounds["height"] * zoom <= height + 1


# 新节点不要压在已有节点上（否则会挡住插孔，没法拉线）：从期望位置开始，往下找一块空地
NEW_W = 196
NEW_H = 90


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def free_spot(want, nodes):
    boxes = []
    for n in nodes:
        m = n.get("measured") or {}
        d = n.get("data") or {}
        boxes.append({"x": n["position"]["x"], "y": n["position"]["y"],
                      "w": _first(m.get("width"), d.get("width"), 200), "h": _first(m.get("height"), d.get("height"), 80)})

    def hit(p):
        return any(p["x"] < b["x"] + b["w"] + 24 and p["x"] + NEW_W + 24 > b["x"]
                   and p["y"] < b["y"] + b["h"] + 24 and p["y"] + NEW_H + 24 > b["y"] for b in boxes)
    for i in range(40):
        p = {"x": want["x"], "y": want["y"] + i * 60}
        if not hit(p):
            return p
    return want
