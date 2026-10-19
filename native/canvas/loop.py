"""循环框（“逐项循环”，节点 type 为 "loop"）：框、标题栏、插孔、问题标记，以及关掉自动收缩后的八个拉伸点和选中时框顶的菜单。
移植自 web/src/nodes.jsx 的 LoopNode、BoxMenu 和 App.jsx 的 boxEdit；具体改动都交给 Controller 的 box_* / drag_*。

对画布（view.py）的要求：
  1. 框里的节点是 LoopItem 的子图元（parentItem 是框），见 base.py。
  2. 拉伸点是 LoopItem 的子图元 BoxResizeHandle（属性 is_box_control = True）。画布若自己重写了 mousePressEvent 做点选、拖动，
     遇到光标下是这种图元时应直接交给 QGraphicsView.mousePressEvent（让场景把事件送给拉伸点），不要选中、不要开始拖动节点。
  3. 框顶菜单是画布 viewport() 的子控件（BoxMenu，每个画布一份，挂在 view._loop_box_menu），鼠标、按键不经过画布，
     所以点菜单不会选中、取消选中或拖动画布；Ctrl 组合键（撤销、重做）不拦。位置跟着视野、框的移动自己更新，画布不用管。
  4. 可选：画布有 led_phase（0..1）时运行中的状态灯按它闪烁。
画布无需调用额外方法。"""
import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QGraphicsItem, QGraphicsObject, QHBoxLayout,
                               QPushButton, QWidget)

from ..theme import C, FS, R, qc, ui_font
from .base import STATE_LABEL, NodeItem, draw_issue_badge, draw_led
from .items import paint_ctrl_jack, paint_jack

# 与网页版 .loop / .box-resize 的样式一致
BORDER = QColor("#a3abb4")
FILL = QColor(255, 255, 255, 13)          # rgba(255,255,255,0.05)
FOCUS_RING = QColor(110, 162, 240, 71)    # --focus-ring
HEAD_Y = 21                               # 标题栏中线（padding 12px + 一行字），插孔 y=22 与它对齐
CTRL_X = 72                               # 控制线插孔不放在顶边中点：那里是拉伸点
MENU_GAP = 14                             # 菜单底边到框顶边的距离（屏幕像素）

# 拉伸点：名字 → (x 方向, y 方向)，-1 表示拉左边/上边，1 表示右边/下边
RESIZE_POINTS = {"top-left": (-1, -1), "top": (0, -1), "top-right": (1, -1), "right": (1, 0),
                 "bottom-right": (1, 1), "bottom": (0, 1), "bottom-left": (-1, 1), "left": (-1, 0)}
_CURSOR = {(-1, -1): Qt.CursorShape.SizeFDiagCursor, (1, 1): Qt.CursorShape.SizeFDiagCursor,
           (1, -1): Qt.CursorShape.SizeBDiagCursor, (-1, 1): Qt.CursorShape.SizeBDiagCursor,
           (0, -1): Qt.CursorShape.SizeVerCursor, (0, 1): Qt.CursorShape.SizeVerCursor,
           (-1, 0): Qt.CursorShape.SizeHorCursor, (1, 0): Qt.CursorShape.SizeHorCursor}


def resized_rect(start, sx, sy, dx, dy):
    """start = (x, y, w, h)：开始拉伸时框在它那一层的位置和大小；按拉伸点方向和鼠标移动量算新的 {x, y, width, height}"""
    x, y, w, h = start
    if sx < 0:
        x, w = x + dx, w - dx
    elif sx > 0:
        w += dx
    if sy < 0:
        y, h = y + dy, h - dy
    elif sy > 0:
        h += dy
    return {"x": x, "y": y, "width": w, "height": h}


def _furthest(ok, lo, hi):
    """lo 允许、hi 不允许（整数）：找 lo 到 hi 之间离 lo 最远的允许值。允许的范围是一段连续区间，二分即可"""
    while abs(hi - lo) > 1:
        mid = lo + int((hi - lo) / 2)
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def clamp_resize(allow, start, sx, sy, last, target):
    """拉伸时压到框里节点就停在刚好不压到的位置（每个方向各自停），不会因为鼠标移得快而停在半路。
    allow(rect) → bool；last：上次被接受的 (dx, dy)；target：鼠标想要的 (dx, dy)。返回接受的 (dx, dy)"""
    tdx, tdy = target
    if allow(resized_rect(start, sx, sy, tdx, tdy)):
        return tdx, tdy
    adx, ady = last
    if allow(resized_rect(start, sx, sy, tdx, ady)):
        nx = tdx
    else:
        nx = _furthest(lambda v: allow(resized_rect(start, sx, sy, v, ady)), adx, tdx)
    if allow(resized_rect(start, sx, sy, nx, tdy)):
        ny = tdy
    else:
        ny = _furthest(lambda v: allow(resized_rect(start, sx, sy, nx, v)), ady, tdy)
    return nx, ny


class BoxResizeHandle(QGraphicsObject):
    """循环框的一个拉伸点：按下开始拉伸，拖动时改框的位置和大小（压到框里节点时停住），松手记下大小（一步撤销）"""
    is_box_control = True
    SIZE = 12

    def __init__(self, box, name):
        super().__init__(box)
        self.box = box
        self.name = name
        self.sx, self.sy = RESIZE_POINTS[name]
        self.setZValue(1e6)  # 在框里节点上面
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(_CURSOR[(self.sx, self.sy)])
        self.setToolTip("")
        self._hover = False
        self._st = None

    def boundingRect(self):
        s = self.SIZE * 1.25 / 2 + 2
        return QRectF(-s, -s, 2 * s, 2 * s)

    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE * (1.25 if self._hover or self._st else 1.0)
        r = QRectF(-s / 2, -s / 2, s, s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 102))  # 底下一点阴影
        p.drawRoundedRect(r.translated(0, 1).adjusted(-1, -1, 1, 1), 4, 4)
        p.setBrush(qc("focus"))          # 外面 1px 蓝圈
        p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 4, 4)
        p.setBrush(qc("canvas"))         # 2px 画布色边
        p.drawRoundedRect(r, 3, 3)
        p.setBrush(qc("focus"))
        p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 1.5, 1.5)

    def hoverEnterEvent(self, e):
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self._hover = False
        self.update()

    def _layer_pos(self, scene_pos):
        """鼠标在框所在那一层的坐标（框的位置也是这一层的坐标）"""
        par = self.box.parentItem()
        return par.mapFromScene(scene_pos) if par else scene_pos

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            e.ignore()
            return
        e.accept()
        n = self.box.node
        d = n.get("data") or {}
        self._st = {"id": n["id"], "start": (n["position"]["x"], n["position"]["y"], float(d.get("width") or 0), float(d.get("height") or 0)),
                    "grab": self._layer_pos(e.scenePos()), "last": (0, 0)}
        self._st["rect"] = resized_rect(self._st["start"], self.sx, self.sy, 0, 0)
        self.box.ctrl.box_resize_start(n["id"])
        self.update()

    def mouseMoveEvent(self, e):
        st = self._st
        if not st:
            return
        p = self._layer_pos(e.scenePos())
        target = (round(p.x() - st["grab"].x()), round(p.y() - st["grab"].y()))
        ctrl, bid = self.box.ctrl, st["id"]
        got = clamp_resize(lambda r: ctrl.box_resize_allow(bid, r), st["start"], self.sx, self.sy, st["last"], target)
        if got == st["last"]:
            return
        rect = resized_rect(st["start"], self.sx, self.sy, *got)
        if ctrl.box_resize_move(bid, rect):
            st["last"], st["rect"] = got, rect

    def mouseReleaseEvent(self, e):
        st, self._st = self._st, None
        self.update()
        if not st:
            return
        ctrl = self.box.ctrl
        # 推迟到事件处理完：记下大小会重画画布，可能删掉这个拉伸点自己
        QTimer.singleShot(0, lambda: ctrl.box_resize_end(st["id"], st["rect"]))


class LoopItem(NodeItem):
    def __init__(self, view, node, parent=None):
        super().__init__(view, node, parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self._handles = {name: BoxResizeHandle(self, name) for name in RESIZE_POINTS}
        self._tip_at = None
        self._place_handles()
        BoxMenu.for_view(view).schedule()

    # ---- 形状 ----
    def visual(self):
        return (self.node.get("data") or {}).get("visual")

    def frame_rect(self):
        """框画出来的范围（图元坐标）：拖动框里节点时按 data.visual 偏移，平时就是 (0, 0, 宽, 高)"""
        v = self.visual() or {}
        w, h = self.size()
        return QRectF(float(v.get("x") or 0), float(v.get("y") or 0), w, h)

    def boundingRect(self):
        return self.frame_rect().adjusted(-14, -14, 14, 14)

    def shape(self):
        r = self.frame_rect()
        path = QPainterPath()
        path.addRoundedRect(r, R["xl"], R["xl"])
        for _, pt, _, _ in self.handles():
            path.addEllipse(pt, 9, 9)
        return path

    def handles(self):
        r = self.frame_rect()
        d = self.node.get("data") or {}
        ins, outs = d.get("ins") or [], d.get("outs") or []
        t_in = ins[0].get("type") if ins else None
        t_out = outs[0].get("type") if outs else None
        return [("in:items", QPointF(r.left(), r.top() + 22), "in", t_in),
                ("out:results", QPointF(r.right(), r.top() + 22), "out", t_out),
                ("ctrl", QPointF(r.left() + CTRL_X, r.top()), "ctrl", None)]

    # ---- 状态 ----
    def can_resize(self):
        """关掉自动收缩后才能拉伸：开着时框始终正好包住框里节点，拉大了也会马上收回"""
        d = self.node.get("data") or {}
        return bool(self.ctrl.editable) and bool(d.get("manual")) and self.visual() is None

    def menu_shown(self):
        return bool(self.ctrl.editable) and self.ctrl.selected == self.nid and self.visual() is None

    def _place_handles(self):
        r = self.frame_rect()
        on = self.can_resize()
        for h in self._handles.values():
            if h._st:  # 正在拉的拉伸点不藏
                h.setVisible(True)
            else:
                h.setVisible(on)
            h.setPos(r.center().x() + h.sx * r.width() / 2, r.center().y() + h.sy * r.height() / 2)

    def set_node(self, node):
        super().set_node(node)
        self._place_handles()
        BoxMenu.for_view(self.view).schedule()

    def refresh(self):
        self._place_handles()
        self.update()
        BoxMenu.for_view(self.view).schedule()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemScenePositionHasChanged:
            BoxMenu.for_view(self.view).schedule()
        return super().itemChange(change, value)

    # ---- 画 ----
    def paint(self, p, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.frame_rect()
        st = self.status() or {}
        state = st.get("state", "idle")
        sel = self.is_selected()
        rad = R["xl"]
        p.save()
        if state == "skipped":
            p.setOpacity(0.55)
        if sel:  # 选中：3px 蓝色光圈
            p.setPen(QPen(FOCUS_RING, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-2.25, -2.25, 2.25, 2.25), rad + 2, rad + 2)
        if self.issues():  # 检查发现问题：外面一圈红色虚线
            pen = QPen(qc("led_bad"), 2, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-4.75, -4.75, 4.75, 4.75), rad + 4, rad + 4)
        dropping = getattr(self.ctrl, "drop_box", None) == self.nid  # 正拖着节点经过：松手就放进来
        pen = QPen(qc("focus") if sel or dropping else BORDER, 2.5 if dropping else 1.5)
        if not sel and not dropping:
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([3, 2.5])
        p.setPen(pen)
        p.setBrush(FILL)
        p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), rad, rad)
        if dropping:
            tint = QColor(qc("focus"))
            tint.setAlpha(28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r.adjusted(0.75, 0.75, -0.75, -0.75), rad, rad)
            p.setPen(qc("focus"))
            p.setFont(ui_font(FS["md"], QFont.Weight.DemiBold))
            p.drawText(r.adjusted(0, 0, 0, -10), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "松手放进这个循环")

        # 标题栏：状态灯、名字、最多 N 项
        node = (self.node.get("data") or {}).get("node") or {}
        x = r.left() + 14
        cy = r.top() + HEAD_Y
        draw_led(p, QPointF(x + 6, cy), state, 6, getattr(self.view, "led_phase", 1.0))
        x += 12 + 10
        title = node.get("label") or "逐项循环"
        tf = ui_font(14, QFont.Weight.DemiBold)  # 网页版 13.5px、650
        limit = f"最多 {node.get('max_items')} 项"
        lf = ui_font(FS["sm"])
        lw = QFontMetricsF(lf).horizontalAdvance(limit)
        room = max(0.0, r.right() - 14 - x - 10 - lw)
        fm = QFontMetricsF(tf)
        shown = fm.elidedText(title, Qt.TextElideMode.ElideRight, room)
        p.setFont(tf)
        p.setPen(qc("ink"))
        tw = fm.horizontalAdvance(shown)
        p.drawText(QRectF(x, cy - 10, tw + 2, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, shown)
        p.setFont(lf)
        p.setPen(qc("muted"))
        p.drawText(QRectF(x + tw + 10, cy - 10, lw + 2, 20), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, limit)
        p.restore()

        # 插孔（与普通节点同一种画法；画布的 hot_handle 是正悬停、能拖线的插孔）
        hot = getattr(self.view, "hot_handle", None)
        for h, pt, kind, t in self.handles():
            on = hot == (self.nid, h)
            if kind == "ctrl":
                paint_ctrl_jack(p, pt, on or self._tip_at == "ctrl")
            else:
                paint_jack(p, pt, t, on)
        if self.issues():
            draw_issue_badge(p, QPointF(r.right() - 1, r.top() + 1))

    # ---- 悬停提示：状态灯、问题标记、控制插孔 ----
    def _spot(self, pos):
        r = self.frame_rect()
        if self.issues() and QPointF.dotProduct(pos - QPointF(r.right() - 1, r.top() + 1), pos - QPointF(r.right() - 1, r.top() + 1)) <= 100:
            return "issue"
        if abs(pos.x() - (r.left() + 20)) <= 8 and abs(pos.y() - (r.top() + HEAD_Y)) <= 8:
            return "led"
        if abs(pos.x() - (r.left() + CTRL_X)) <= 7 and abs(pos.y() - r.top()) <= 6:
            return "ctrl"
        return None

    def hoverMoveEvent(self, e):
        spot = self._spot(e.pos())
        if spot != self._tip_at:
            self._tip_at = spot
            if spot == "issue":
                self.setToolTip("\n".join(self.issues()))
            elif spot == "led":
                st = self.status() or {}
                self.setToolTip(st.get("reason") or STATE_LABEL.get(st.get("state", "idle"), ""))
            else:
                self.setToolTip("")
            self.update()
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e):
        if self._tip_at:
            self._tip_at = None
            self.setToolTip("")
            self.update()
        super().hoverLeaveEvent(e)


_MENU_QSS = f"""
QFrame#boxMenu {{ background: {C['surface']}; border: 1px solid {C['line_strong']}; border-radius: {R['md']}px; }}
QFrame#boxMenu QPushButton {{ font-size: 12px; color: {C['ink']}; background: transparent; border: 0; border-radius: {R['sm']}px; padding: 4px 8px; }}
QFrame#boxMenu QPushButton:hover:!disabled {{ background: {C['hover']}; }}
QFrame#boxMenu QPushButton:disabled {{ color: {C['muted']}; }}
QFrame#boxMenu QPushButton:focus {{ border: 0; }}
QFrame#boxMenu QCheckBox {{ font-size: 12px; color: {C['ink']}; spacing: 4px; padding: 4px 8px; border-radius: {R['sm']}px; }}
QFrame#boxMenu QCheckBox:hover {{ background: {C['hover']}; }}
QFrame#boxMenu QFrame#boxMenuSep {{ background: {C['line']}; min-width: 1px; max-width: 1px; border: 0; }}
"""


class _Switch(QCheckBox):
    """小开关（网页版是 role=switch 的勾选框）：左边胶囊、右边文字"""
    W, H = 24, 14

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(8 + self.W + 6 + fm.horizontalAdvance(self.text()) + 8, max(fm.height(), self.H) + 8)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc("hover"))
            p.drawRoundedRect(QRectF(self.rect()), R["sm"], R["sm"])
        y = (self.height() - self.H) / 2
        pill = QRectF(8, y, self.W, self.H)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc("led_ok") if self.isChecked() else qc("line_strong"))
        p.drawRoundedRect(pill, self.H / 2, self.H / 2)
        k = self.H - 4
        kx = pill.right() - 2 - k if self.isChecked() else pill.left() + 2
        p.setBrush(qc("ink"))
        p.drawEllipse(QRectF(kx, y + 2, k, k))
        p.setPen(qc("ink"))
        p.setFont(self.font())
        p.drawText(QRectF(8 + self.W + 6, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class BoxMenu(QFrame):
    """循环框顶部菜单：收缩（自动收缩开关）、拖动（按住拖动整个框）、收紧（到节点）、整理（框里节点）、全选（框里节点）、总览（看全这个框）。
    每个画布一份，是 viewport 的子控件；选中能编辑的框（且没在拖动框里节点）时出现在框顶边外侧居中"""

    @classmethod
    def for_view(cls, view):
        m = getattr(view, "_loop_box_menu", None)
        if m is None or not shiboken6.isValid(m):
            m = cls(view)
            view._loop_box_menu = m
        return m

    def __init__(self, view):
        super().__init__(view.viewport())
        self.view = view
        self.ctrl = view.ctrl
        self.owner = None   # 菜单所属的 LoopItem
        self._drag = None   # 正按着“拖动整个框”：{id, pos, grab, parent, moved}
        self.setObjectName("boxMenu")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_MENU_QSS)
        self.setAccessibleName("循环框")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        self.auto = _Switch("收缩")
        self.auto.setFont(ui_font(12))
        self.auto.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.auto.setToolTip("自动收缩：开着时框始终正好包住框里的节点；关掉后框保持当前大小，可以拖四角、四边拉伸")
        self.auto.setCursor(Qt.CursorShape.PointingHandCursor)
        self.auto.clicked.connect(lambda on: self._act(lambda bid: self.ctrl.box_set_auto(bid, on)))
        lay.addWidget(self.auto)
        sep = QFrame()
        sep.setObjectName("boxMenuSep")
        sep.setFixedHeight(18)
        lay.addWidget(sep)

        def btn(text, tip, fn=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            if fn:
                b.clicked.connect(lambda: self._act(fn))
            lay.addWidget(b)
            return b

        self.grip = btn("拖动", "按住拖动整个框：框连同框里的全部节点一起移动")
        self.grip.setCursor(Qt.CursorShape.OpenHandCursor)
        self.fit = btn("收紧", "收紧到节点：把框收到正好包住框里的节点（只收这一次，之后仍保持大小）", self.ctrl.box_fit)
        btn("整理", "整理框里节点：把框里的节点按连线从左到右重新排整齐", self.ctrl.box_arrange)
        btn("全选", "选中框里全部：选中框里的全部节点，可以一起拖动", self.ctrl.box_select_kids)
        btn("总览", "看全这个框：把画布移到这个框，正好看全", self.ctrl.box_zoom)
        self.auto.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)
        self.hide()

        self._timer = QTimer(self, singleShot=True, interval=0, timeout=self.sync)
        for sig in (self.ctrl.selection_changed, self.ctrl.current_changed, self.ctrl.flow_changed, self.ctrl.gen_changed):
            sig.connect(self.schedule)
        self.ctrl.nodes_moved.connect(lambda _ids: self.schedule())
        # 平移、缩放、改窗口大小后画布都会重画：跟着重新摆位置
        view.viewport().installEventFilter(self)
        for sb in (view.horizontalScrollBar(), view.verticalScrollBar()):
            sb.valueChanged.connect(self.schedule)

    def schedule(self, *_):
        if shiboken6.isValid(self) and not self._timer.isActive():
            self._timer.start()

    # ---- 找到所属的框、摆位置 ----
    def _find_owner(self):
        o = self.owner
        sel = self.ctrl.selected
        want = self._drag["id"] if self._drag else sel
        if o is not None and shiboken6.isValid(o) and o.scene() is self.view.scene() and o.nid == want:
            return o
        self.owner = None
        if not want or self.view.scene() is None:
            return None
        for it in self.view.scene().items():
            if isinstance(it, LoopItem) and it.nid == want:
                self.owner = it
                return it
        return None

    def sync(self):
        if not shiboken6.isValid(self) or not shiboken6.isValid(self.view):
            return
        o = self._find_owner()
        # 按着拖动时菜单不藏：藏起来按钮就收不到松手了
        if o is None or not (self._drag or o.menu_shown()):
            self.hide()
            return
        auto = not (o.node.get("data") or {}).get("manual")
        self.auto.setChecked(auto)
        self.fit.setEnabled(not auto)
        self.adjustSize()
        r = o.frame_rect()
        top = self.view.mapFromScene(o.mapToScene(QPointF(r.center().x(), r.top())))
        x = top.x() - self.width() // 2
        y = top.y() - MENU_GAP - self.height()
        if self.pos() != QPoint(x, y):
            self.move(x, y)
        if not self.isVisible():
            self.show()
            self.raise_()

    def _act(self, fn):
        o = self._find_owner()
        if o is not None:
            fn(o.nid)

    # ---- 按住拖动整个框 ----
    def _layer_at(self, gpos, parent):
        sp = self.view.mapToScene(self.view.viewport().mapFromGlobal(gpos))  # 每次按当前视野换算：按着时缩放也跟得上
        if parent is not None and shiboken6.isValid(parent):
            return parent.mapFromScene(sp)
        return sp

    def _drag_begin(self, gpos):
        o = self._find_owner()
        if self._drag or o is None:
            return
        parent = o.parentItem()
        pos = o.node["position"]
        self._drag = {"id": o.nid, "pos": (pos["x"], pos["y"]), "parent": parent,
                      "grab": self._layer_at(gpos, parent), "moved": False, "last": gpos}
        self.grip.setCursor(Qt.CursorShape.ClosedHandCursor)
        QApplication.instance().installEventFilter(self)  # 只在按着时看全程序的鼠标移动、松手、切走窗口
        self.ctrl.drag_start()

    def _drag_move(self, gpos):
        d = self._drag
        if not (QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            return self._drag_end()  # 没收到松手（例如在窗口外松开）
        p = self._layer_at(gpos, d["parent"])
        at = {"x": d["pos"][0] + p.x() - d["grab"].x(), "y": d["pos"][1] + p.y() - d["grab"].y()}
        d["moved"] = True
        self.ctrl.drag_move({d["id"]: at})

    def _drag_end(self):
        d, self._drag = self._drag, None
        if not d:
            return
        QApplication.instance().removeEventFilter(self)
        self.grip.setCursor(Qt.CursorShape.OpenHandCursor)
        if d["moved"]:
            # 推迟：放下后整理一次会重画画布
            QTimer.singleShot(0, lambda: self.ctrl.drag_stop([d["id"]]))
        self.schedule()

    # ---- 事件：拖动跟踪；菜单上的点击、按键不传给画布 ----
    def eventFilter(self, obj, e):
        t = e.type()
        if obj is self.grip and t == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
            self._drag_begin(e.globalPosition().toPoint())  # 只认左键按下；按下那一刻的鼠标位置是拖动的起点
        if self._drag:
            if t == QEvent.Type.MouseMove:
                g = e.globalPosition().toPoint()
                if g != self._drag.get("last"):  # 同一次移动沿父控件传递时会收到好几遍
                    self._drag["last"] = g
                    self._drag_move(g)
            elif t == QEvent.Type.MouseButtonRelease and e.button() == Qt.MouseButton.LeftButton:
                self._drag_end()
            elif t in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate):
                self._drag_end()  # 切走窗口：就地结束
            elif t == QEvent.Type.ApplicationStateChange and QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
                self._drag_end()
        if shiboken6.isValid(self.view) and obj is self.view.viewport():
            if t in (QEvent.Type.Paint, QEvent.Type.Resize):
                self.schedule()
            return False
        if isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
            if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
                ctrl_key = e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
                if not ctrl_key and t == QEvent.Type.ShortcutOverride:
                    e.accept()  # 普通按键（删除、方向键）留在菜单里，不触发画布的快捷键
        return False

    def keyPressEvent(self, e):
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            e.ignore()  # 撤销、重做照常
        else:
            e.accept()

    def keyReleaseEvent(self, e):
        self.keyPressEvent(e)

    # 点在菜单空白处、双击、右键：不传给画布（否则会取消选中、开始拖动画布）
    def mousePressEvent(self, e):
        e.accept()

    def mouseReleaseEvent(self, e):
        e.accept()

    def mouseDoubleClickEvent(self, e):
        e.accept()

    def mouseMoveEvent(self, e):
        e.accept()

    def contextMenuEvent(self, e):
        e.accept()
