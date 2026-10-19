"""画布上的普通节点（模块面板 ModuleItem、输入输出端点 TerminalItem）和连线 EdgeItem。
样子移植自 web/src/nodes.jsx 的 ModuleNode、TerminalNode、PortRows、Led、Counts、IssueBadge 与 styles.css 的 .node / .jack / .terminal / .react-flow__edge。
图元只负责画和悬停提示；点选、拖动、拖线由画布（view.py）处理。"""
import json

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem

from ..logic.graph import HEAD, ROW, port_top
from ..logic.labels import port_labels, type_label
from ..theme import CATEGORY, FS, R, qc, type_color, ui_font
from .base import STATE_LABEL, NodeItem, draw_issue_badge, draw_led

KIND_LABEL = {"if": "条件分支", "merge": "合并", "foreach": "逐项循环", "subflow": "子流程"}
JACK_FILL = QColor("#141416")
FOCUS_RING = QColor(110, 162, 240, 71)    # --focus-ring
DASHED_BORDER = QColor("#6d6d76")         # .node-if / .node-merge
TERM_PORT_INK = QColor("#3f3f46")         # .terminal .port-row


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """color-mix(in srgb, a t, b)"""
    return QColor(round(a.red() * t + b.red() * (1 - t)), round(a.green() * t + b.green() * (1 - t)),
                  round(a.blue() * t + b.blue() * (1 - t)))


def _font(px, weight=QFont.Weight.Normal):
    return ui_font(px, weight)


F_TITLE = _font(14, QFont.Weight.DemiBold)   # .node-title 13.5px / 650
F_SUB = _font(FS["xs"])
F_PORT = _font(FS["sm"])
F_FIXED = _font(FS["xs"])
F_TERM = _font(FS["base"], QFont.Weight.DemiBold)
M_TITLE, M_SUB, M_PORT, M_FIXED, M_TERM = (QFontMetricsF(f) for f in (F_TITLE, F_SUB, F_PORT, F_FIXED, F_TERM))


def port_tip(p):
    """悬停在端口上：名字、含义、类型；固定值显示全文（nodes.jsx 的 portTip）"""
    if not p:
        return ""
    lines = [f"{type_label(p.get('type'))}（程序里叫 {p.get('name')}）{'：' + p['desc'] if p.get('desc') else ''}"]
    if p.get("full") is not None:
        full = p["full"]
        lines.append(f"固定为：{full if isinstance(full, str) else json.dumps(full, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines)


def counts_text(status):
    c = (status or {}).get("counts")
    if not c:
        return ""
    parts = [f"{c[k]} {lbl}" for k, lbl in (("completed", "完成"), ("skipped", "跳过"), ("failed", "出错")) if c.get(k)]
    return " / ".join(parts)


def paint_shadow(p: QPainter, rect: QRectF, radius, strong=False):
    """模块阴影（--shadow-module / --shadow-module-hover）：几层由近到远变淡的圆角矩形代替模糊阴影，便宜"""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    dy, blur, alpha = (10, 22, 0.4) if strong else (4, 10, 0.3)
    steps = 5
    for i in range(steps):
        k = (i + 1) / steps
        spread = blur * k * 0.5
        p.setBrush(QColor(0, 0, 0, int(255 * alpha / steps * (1.2 - k))))
        p.drawRoundedRect(rect.adjusted(-spread, -spread + dy * 0.6, spread, spread + dy * 0.6), radius + spread, radius + spread)
    p.setBrush(QColor(0, 0, 0, 90))  # 0 1px 2px：紧贴边的一圈
    p.drawRoundedRect(rect.adjusted(-0.5, 0.5, 0.5, 1.5), radius, radius)
    p.restore()


def paint_jack(p: QPainter, c: QPointF, t, hot=False):
    """数据插孔：13px，3px 类型色边、深色芯，外面 2px 面板色圈；能拖线时悬停多一圈焦点光环"""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    if hot:
        p.setBrush(FOCUS_RING)
        p.drawEllipse(c, 11.5, 11.5)
        p.setBrush(QColor("#9aa4ae"))
        p.drawEllipse(c, 7.5, 7.5)
    else:
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawEllipse(c + QPointF(0, 1), 10, 10)
        p.setBrush(qc("surface"))
        p.drawEllipse(c, 8.5, 8.5)
    p.setBrush(JACK_FILL)
    p.setPen(QPen(qc(type_color(t)), 3))
    p.drawEllipse(c, 5, 5)
    p.restore()


def paint_ctrl_jack(p: QPainter, c: QPointF, hot=False):
    """控制线插孔：顶边上的小横条，平时很淡"""
    p.save()
    p.setOpacity(1.0 if hot else 0.35)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc("cat_flow"))
    p.drawRoundedRect(QRectF(c.x() - 5, c.y() - 3, 10, 6), 2, 2)
    p.restore()


def _elide(fm, text, width):
    return fm.elidedText(text or "", Qt.TextElideMode.ElideRight, max(0.0, width))


class _PortsMixin:
    """端口行（PortRows）：左边输入、右边输出，都用类型的中文短词显示（固定值显示成小标签）；插孔在左右边上"""

    def ports(self):
        d = self.node.get("data") or {}
        return d.get("ins") or [], d.get("outs") or []

    def port_handles(self):
        w, _ = self.size()
        ins, outs = self.ports()
        out = [(f"in:{p['name']}", QPointF(0, port_top(i)), "in", p.get("type")) for i, p in enumerate(ins)]
        out += [(f"out:{p['name']}", QPointF(w, port_top(i)), "out", p.get("type")) for i, p in enumerate(outs)]
        return out

    def paint_ports(self, p: QPainter, ink: QColor, muted: QColor):
        w, _ = self.size()
        ins, outs = self.ports()
        in_names, out_names = port_labels(ins), port_labels(outs)
        top = HEAD + 2  # .ports 上边 2px
        for i in range(max(len(ins), len(outs))):
            y = top + i * ROW
            row = QRectF(14, y, w - 28, ROW)
            out_w = 0.0
            if i < len(outs):
                name = out_names[i]
                out_w = M_PORT.horizontalAdvance(name)
                p.setFont(F_PORT)
                p.setPen(ink)
                p.drawText(row, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, name)
            if i < len(ins):
                pi = ins[i]
                room = row.width() - (out_w + 8 if out_w else 0)
                name = _elide(M_PORT, in_names[i], room)
                nw = M_PORT.horizontalAdvance(name)
                p.setFont(F_PORT)
                p.setPen(muted if pi.get("fixed") else ink)
                p.drawText(QRectF(row.left(), y, nw + 1, ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name)
                fixed = pi.get("fixed")
                left = room - nw - 6
                if fixed and left > 18:
                    txt = _elide(M_FIXED, str(fixed), left - 14)
                    pw = M_FIXED.horizontalAdvance(txt) + 14
                    pill = QRectF(row.left() + nw + 6, y + (ROW - 17) / 2, pw, 17)
                    p.setPen(QPen(qc("line_strong"), 1))
                    p.setBrush(qc("hover"))
                    p.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), R["xs"], R["xs"])
                    p.setFont(F_FIXED)
                    p.setPen(qc("ink"))
                    p.drawText(pill, Qt.AlignmentFlag.AlignCenter, txt)

    def port_at(self, pos: QPointF):
        """鼠标在哪个端口行上：(端口 dict) 或 None"""
        w, _ = self.size()
        ins, outs = self.ports()
        i = int((pos.y() - HEAD - 2) // ROW)
        if pos.y() < HEAD + 2 or i < 0 or i >= max(len(ins), len(outs)):
            return None
        if pos.x() >= w / 2 and i < len(outs):
            return outs[i]
        if pos.x() < w / 2 and i < len(ins):
            return ins[i]
        return None


class ModuleItem(_PortsMixin, NodeItem):
    """模块面板（以及条件分支、合并、子流程）：顶部类别色条、状态灯、标题、第二行、计数、端口行、插孔"""

    def __init__(self, view, node, parent=None):
        super().__init__(view, node, parent)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)  # 平移时不重画文字
        self._tip_key = None

    def boundingRect(self):
        w, h = self.size()
        return QRectF(-14, -14, w + 28, h + 14 + 36)  # 插孔、问题标记伸出边框，下方留出阴影

    def shape(self):
        w, h = self.size()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), R["lg"], R["lg"])
        for _, pt, kind, _ in self.handles():
            if kind == "ctrl":
                path.addRect(QRectF(pt.x() - 7, pt.y() - 5, 14, 10))
            else:
                path.addEllipse(pt, 8, 8)
        if self.issues():
            path.addEllipse(QPointF(w - 1, 1), 10, 10)
        return path

    def handles(self):
        w, _ = self.size()
        return [("ctrl", QPointF(w / 2, 0), "ctrl", None)] + self.port_handles()

    # ---- 文字 ----
    def _info(self):
        d = self.node.get("data") or {}
        n = d.get("node") or {}
        m = d.get("module")
        kind = n.get("kind")
        title = n.get("label") or (m or {}).get("name") or KIND_LABEL.get(kind) or n.get("id") or ""
        if kind != "module":
            sub = KIND_LABEL.get(kind, "")
        elif not m:
            sub = n.get("module") or ""
        elif n.get("label"):
            sub = m.get("name") or ""
        else:
            sub = f"{m.get('category')}模块"
        category = (m or {}).get("category") or kind
        return n, title, sub, category

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.size()
        n, title, sub, category = self._info()
        st = self.status()
        state = st.get("state", "idle")
        sel = self.is_selected()
        hover = self.ctrl.hovered == self.nid
        rad = R["lg"]
        body = QRectF(0, 0, w, h)
        cat = qc(CATEGORY.get(category, "cat_flow"))
        surface = qc("surface")
        if state == "skipped":
            p.setOpacity(0.55)

        paint_shadow(p, body, rad, hover)
        if sel:  # 选中：3px 蓝色光圈
            p.setPen(QPen(FOCUS_RING, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(-1.5, -1.5, 1.5, 1.5), rad + 1.5, rad + 1.5)

        # 面板：标题区带一点类别色，下面是面板色
        clip = QPainterPath()
        clip.addRoundedRect(body, rad, rad)
        p.save()
        p.setClipPath(clip)
        p.fillRect(body, surface)
        p.fillRect(QRectF(0, 0, w, HEAD), _mix(cat, surface, 0.16))
        p.fillRect(QRectF(0, 0, w, 5), cat)  # 顶部类别色条
        p.restore()
        # 标题区和插孔区之间一条细线
        p.setPen(QPen(_mix(cat, qc("line"), 0.3), 1))
        p.drawLine(QPointF(10, HEAD + 0.5), QPointF(w - 10, HEAD + 0.5))
        # 边框：选中蓝、出错红、条件/合并虚线
        if sel:
            pen = QPen(qc("focus"), 1)
        elif state == "failed":
            pen = QPen(qc("led_bad"), 1)
        elif n.get("kind") in ("if", "merge"):
            pen = QPen(DASHED_BORDER, 1, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3, 3])
        else:
            pen = QPen(qc("line_strong"), 1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
        if self.issues():  # 检查发现问题：外面一圈红色虚线
            pen = QPen(qc("led_bad"), 2, Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.drawRoundedRect(body.adjusted(-4, -4, 4, 4), rad + 4, rad + 4)

        # 标题行：灯、标题、第二行、计数
        draw_led(p, QPointF(18, 20), state, 6, getattr(self.view, "led_phase", 1.0))
        counts = counts_text(st)
        cw = M_SUB.horizontalAdvance(counts) if counts else 0.0
        tx = 34
        room = w - tx - 12 - (cw + 10 if counts else 0)
        p.setFont(F_TITLE)
        p.setPen(qc("ink"))
        p.drawText(QRectF(tx, 10, room, 18), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, _elide(M_TITLE, title, room))
        p.setFont(F_SUB)
        p.setPen(qc("muted"))
        p.drawText(QRectF(tx, 27, room, 15), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, _elide(M_SUB, sub, room))
        if counts:
            p.drawText(QRectF(w - 12 - cw - 1, 11, cw + 2, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, counts)

        self.paint_ports(p, qc("ink_2"), qc("muted"))
        p.setOpacity(1.0)
        self.paint_jacks(p, state == "skipped")
        if self.issues():
            draw_issue_badge(p, QPointF(w - 1, 1))

    def led_rect(self):
        """状态灯连光晕的范围：运行中闪烁时只重画这里"""
        return QRectF(18 - 17, 20 - 17, 34, 34)

    def paint_jacks(self, p, faded=False):
        hot = getattr(self.view, "hot_handle", None)
        if faded:
            p.setOpacity(0.55)
        for hname, pt, kind, t in self.handles():
            on = hot == (self.nid, hname)
            if kind == "ctrl":
                paint_ctrl_jack(p, pt, on)
            else:
                paint_jack(p, pt, t, on)

    # ---- 悬停提示 ----
    def tip_at(self, pos: QPointF):
        w, _ = self.size()
        n, title, _, _ = self._info()
        st = self.status()
        if self.issues() and (pos - QPointF(w - 1, 1)).manhattanLength() <= 14:
            return "issue", "\n".join(self.issues())
        if pos.y() < HEAD:
            if abs(pos.x() - 18) <= 9 and abs(pos.y() - 20) <= 9:
                return "led", st.get("reason") or STATE_LABEL.get(st.get("state", "idle"), "")
            counts = counts_text(st)
            if counts and pos.x() > w - 12 - M_SUB.horizontalAdvance(counts) - 4:
                return "counts", f"共 {st['counts'].get('total')} 项"
            if 8 <= pos.y() <= 29 and pos.x() >= 30:
                return "title", f"{title}（{n.get('module')}）" if n.get("kind") == "module" else title
            return None, ""
        port = self.port_at(pos)
        if port:
            return ("port", port.get("name"), pos.x() < w / 2), port_tip(port)
        return None, ""

    def hoverMoveEvent(self, e):
        key, tip = self.tip_at(e.pos())
        if key != self._tip_key:
            self._tip_key = key
            self.setToolTip(tip)
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        self._tip_key = None
        self.setToolTip("")
        super().hoverLeaveEvent(e)


class TerminalItem(_PortsMixin, NodeItem):
    """工作流（或循环体）的输入、输出端点：浅色实底的小卡片，标题“输入”“输出”“每一项”“这一项的结果”"""

    def __init__(self, view, node, parent=None):
        super().__init__(view, node, parent)
        self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        self._tip_key = None

    def size(self):
        d = self.node.get("data") or {}
        return float(d.get("width") or 120), float(d.get("height") or 60) - 2  # 网页版按内容高，比 data.height 少 2px

    def boundingRect(self):
        w, h = self.size()
        return QRectF(-14, -14, w + 28, h + 14 + 20)

    def shape(self):
        w, h = self.size()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), R["lg"], R["lg"])
        for _, pt, _, _ in self.handles():
            path.addEllipse(pt, 8, 8)
        return path

    def handles(self):
        return self.port_handles()

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.size()
        d = self.node.get("data") or {}
        state = self.status().get("state", "idle")
        body = QRectF(0, 0, w, h)
        rad = R["lg"]
        if state == "skipped":
            p.setOpacity(0.55)
        p.setPen(Qt.PenStyle.NoPen)
        if state == "completed":  # 运行完：一圈绿光
            p.setBrush(QColor(47, 158, 99, 89))
            p.drawRoundedRect(body.adjusted(-3, -3, 3, 3), rad + 3, rad + 3)
        else:  # 浅色实底加一圈半透明白边，在深画布上收边
            paint_shadow(p, body, rad)
            p.setBrush(QColor(255, 255, 255, 128))
            p.drawRoundedRect(body.adjusted(-1, -1, 1, 1), rad + 1, rad + 1)
        p.setBrush(qc("ink"))
        p.drawRoundedRect(body, rad, rad)
        p.setFont(F_TERM)
        p.setPen(qc("on_ink"))
        p.drawText(QRectF(14, 12, w - 28, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   _elide(M_TERM, str(d.get("title") or ""), w - 28))
        self.paint_ports(p, TERM_PORT_INK, TERM_PORT_INK)
        hot = getattr(self.view, "hot_handle", None)
        for hname, pt, _, t in self.handles():
            paint_jack(p, pt, t, hot == (self.nid, hname))

    def hoverMoveEvent(self, e):
        port = self.port_at(e.pos())
        key = port.get("name") if port else None
        if key != self._tip_key:
            self._tip_key = key
            self.setToolTip(port_tip(port) if port else "")
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        self._tip_key = None
        self.setToolTip("")
        super().hoverLeaveEvent(e)


class BoxFallbackItem(NodeItem):
    """loop.py 载入失败时循环框的替代：只画虚线框和名字，框里节点照常显示"""

    def frame_rect(self):
        v = (self.node.get("data") or {}).get("visual") or {}
        w, h = self.size()
        return QRectF(float(v.get("x") or 0), float(v.get("y") or 0), w, h)

    def boundingRect(self):
        return self.frame_rect().adjusted(-12, -12, 12, 12)

    def shape(self):
        path = QPainterPath()
        path.addRoundedRect(self.frame_rect(), R["xl"], R["xl"])
        return path

    def handles(self):
        r = self.frame_rect()
        d = self.node.get("data") or {}
        ins, outs = d.get("ins") or [{}], d.get("outs") or [{}]
        return [("in:items", QPointF(r.left(), r.top() + 22), "in", ins[0].get("type")),
                ("out:results", QPointF(r.right(), r.top() + 22), "out", outs[0].get("type")),
                ("ctrl", QPointF(r.left() + 72, r.top()), "ctrl", None)]

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.frame_rect()
        sel = self.is_selected()
        pen = QPen(qc("focus") if sel else QColor("#a3abb4"), 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 13))
        p.drawRoundedRect(r, R["xl"], R["xl"])
        node = (self.node.get("data") or {}).get("node") or {}
        draw_led(p, QPointF(r.left() + 20, r.top() + 21), self.status().get("state", "idle"), 6, getattr(self.view, "led_phase", 1.0))
        p.setFont(F_TITLE)
        p.setPen(qc("ink"))
        p.drawText(QRectF(r.left() + 36, r.top() + 11, r.width() - 50, 20), Qt.AlignmentFlag.AlignVCenter,
                   f"{node.get('label') or '逐项循环'}  最多 {node.get('max_items')} 项")
        for _, pt, kind, t in self.handles():
            paint_ctrl_jack(p, pt) if kind == "ctrl" else paint_jack(p, pt, t)


# ---------- 连线 ----------
EDGE_DATA = QColor("#a9b3bd")
EDGE_DEAD = QColor("#626269")
EDGE_DRAFT = QColor("#c9d1d8")   # 正在拖的线


def _offset(distance, curvature=0.25):
    """React Flow 贝塞尔连线的控制点距离（getBezierPath 的 calculateControlOffset）"""
    return 0.5 * distance if distance >= 0 else curvature * 25 * (-distance) ** 0.5


def bezier(s: QPointF, t: QPointF, target_top=False, source_kind="out") -> QPainterPath:
    """与 React Flow 默认连线一样的弧线：从源插孔（右边）出发，进目标插孔（左边，控制线为顶边）"""
    if source_kind == "out":
        c1 = QPointF(s.x() + _offset(t.x() - s.x()), s.y())
    elif source_kind == "ctrl":
        c1 = QPointF(s.x(), s.y() - _offset(s.y() - t.y()))
    else:  # 从输入插孔往回拖
        c1 = QPointF(s.x() - _offset(s.x() - t.x()), s.y())
    if target_top:
        c2 = QPointF(t.x(), t.y() - _offset(t.y() - s.y()))
    else:
        c2 = QPointF(t.x() - _offset(t.x() - s.x()), t.y())
    path = QPainterPath(s)
    path.cubicTo(c1, c2, t)
    return path


class EdgeItem(QGraphicsItem):
    """一条连线。restyle(state, selected) 按 Controller.edge_state 的结果换样式；update_path() 跟着节点位置重算弧线"""
    HIT = 20  # 可点中的宽度（React Flow 的 interactionWidth）

    def __init__(self, view, edge, parent=None):
        super().__init__(parent)
        self.view = view
        self.edge = edge
        self.state = {"live": False, "dead": False, "animated": False, "hover": False}
        self.selected = False
        self._path = QPainterPath()
        self._shape = QPainterPath()
        self._rect = QRectF()
        self.setAcceptHoverEvents(False)
        self.setZValue(1)

    @property
    def control(self):
        return self.edge.get("targetHandle") == "ctrl"

    def update_path(self):
        items = self.view.node_items
        s, t = items.get(self.edge["source"]), items.get(self.edge["target"])
        if s is None or t is None:
            return
        par = self.parentItem()

        def local(it, h):
            sp = it.handle_scene_pos(h)
            return par.mapFromScene(sp) if par else sp
        a = local(s, self.edge.get("sourceHandle"))
        b = local(t, self.edge.get("targetHandle"))
        path = bezier(a, b, self.control)
        if path == self._path:
            return
        self.prepareGeometryChange()
        self._path = path
        st = QPainterPathStroker()
        st.setWidth(self.HIT)
        self._shape = st.createStroke(path)
        self._rect = self._shape.boundingRect().adjusted(-2, -2, 2, 2)
        self.update()

    def restyle(self, state, selected):
        if state == self.state and selected == self.selected:
            return
        self.state = state
        self.selected = selected
        self.setZValue(1.5 if (state.get("hover") or selected) else 1)
        self.update()

    def boundingRect(self):
        return self._rect

    def shape(self):
        return self._shape

    def pen(self):
        # 与 styles.css 的层叠顺序一致：基本样式 → 走过（加深加粗）→ 没走到（变淡）→ 悬停节点的线 → 选中的线
        s = self.state
        col, w = (qc("cat_flow"), 1.5) if self.control else (EDGE_DATA, 2)
        if s.get("live"):
            col, w = qc("ink"), 2.4
        if s.get("dead"):
            col = EDGE_DEAD
        if s.get("hover"):
            col, w = qc("focus"), 2.6
        if self.selected:
            col, w = qc("focus"), 2.8
        pen = QPen(col, w)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        # 控制线始终是 6 5 的虚线；运行中信号正走过的线虚线流动（React Flow 的 animated：dasharray 5，0.5 秒走 10px）
        if self.control:
            pen.setDashPattern([6 / w, 5 / w])
        elif s.get("animated"):
            pen.setDashPattern([5 / w, 5 / w])
        if s.get("animated"):
            pen.setDashOffset(getattr(self.view, "dash_offset", 0.0) / w)
        return pen

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(self.pen())
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._path)


class DraftEdge(QGraphicsItem):
    """从插孔拖出、还没放下的线"""

    def __init__(self):
        super().__init__()
        self._path = QPainterPath()
        self.setZValue(1e7)

    def set_path(self, path):
        self.prepareGeometryChange()
        self._path = path
        self.update()

    def boundingRect(self):
        return self._path.boundingRect().adjusted(-3, -3, 3, 3)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(EDGE_DRAFT, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._path)


__all__ = ["ModuleItem", "TerminalItem", "BoxFallbackItem", "EdgeItem", "DraftEdge", "bezier", "port_tip", "counts_text"]
